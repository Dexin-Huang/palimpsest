"""Isolated end-to-end canaries for qualified recipe proposals."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from palimpsest.factory import site as site_builder
from palimpsest.factory.config import FACTORY_DB_PATH, LIBRARY_ROOT, RECIPES_DIR
from palimpsest.factory.core.conductor import (
    DEFAULT_WORKERS,
    CellReport,
    Conductor,
    RunReport,
)
from palimpsest.factory.core.contracts import validate_doc_id, validate_payload
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.core.recipe import Recipe, load as load_recipe
from palimpsest.factory.evaluation.promotion import (
    CanaryEvidence,
    CanaryOutcome,
    PromotionError,
    RecipeProposal,
    _verify_proposal,
    record_canary_evidence,
)
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import (
    artifact_path,
    doc_dir,
    metadata_path,
    page_list_path,
)


class CanaryError(PromotionError):
    """A proposal cannot be exercised as an isolated production canary."""


def run_proposal_canary(
    proposal: RecipeProposal,
    *,
    doc_id: str,
    canary_root: Path,
    library_root: Path = LIBRARY_ROOT,
    db_path: Path = FACTORY_DB_PATH,
    recipe_root: Path = RECIPES_DIR,
    executor: str = "inline",
    workers: int = DEFAULT_WORKERS,
    human_review_required: bool = False,
    human_review_passed: bool | None = None,
) -> CanaryEvidence:
    """Exercise one proposal without changing production state.

    ``canary_root`` is a single-run directory and must not already exist.  A
    successful preflight clones the canonical document there, creates a new
    ledger, retains the exact proposed recipe and run report, and builds the
    static reader in the same isolated tree.  Preflight failures create no
    canary directory; execution and validation failures retain it for audit.
    """

    proposed_recipe, page_ids, source_workspace = _preflight(
        proposal,
        doc_id=doc_id,
        canary_root=canary_root,
        library_root=library_root,
        db_path=db_path,
        recipe_root=recipe_root,
    )

    isolated_library = canary_root / "library"
    isolated_workspace = doc_dir(doc_id, isolated_library)
    isolated_recipe_root = canary_root / "recipes"
    isolated_recipe_source = isolated_recipe_root / f"{proposal.recipe}.yaml"
    isolated_db = canary_root / "factory.db"

    canary_root.mkdir(parents=True)
    shutil.copytree(source_workspace, isolated_workspace, copy_function=shutil.copy2)
    isolated_recipe_root.mkdir()
    isolated_recipe_source.write_bytes(proposal.proposed_source.encode("utf-8"))

    def proposal_loader(name: str) -> Recipe:
        if name != proposal.recipe:
            raise CanaryError(
                f"Canary work order requested recipe {name!r}, not proposal recipe "
                f"{proposal.recipe!r}"
            )
        if (
            _sha256(isolated_recipe_source.read_bytes())
            != proposal.proposed_recipe_hash
        ):
            raise CanaryError("Isolated canary recipe changed before execution")
        recipe = load_recipe(name, recipes_dir=isolated_recipe_root)
        if recipe.name != proposal.recipe:
            raise CanaryError(
                f"Proposed recipe declares name {recipe.name!r}, expected "
                f"{proposal.recipe!r}"
            )
        return recipe

    with Ledger(isolated_db) as ledger:
        ledger.adopt(doc_id, recipe=proposal.recipe)
        report = Conductor(
            ledger,
            library_root=isolated_library,
            workers=workers,
            model_workers=workers,
            refresh=frozenset({proposal.station}),
            executor=executor,
            recipe_loader=proposal_loader,
        ).run(doc_id)

    if _sha256(isolated_recipe_source.read_bytes()) != proposal.proposed_recipe_hash:
        raise CanaryError("Canary did not retain the exact proposed recipe source")

    changed_index = _station_index(proposed_recipe, proposal.station)
    outcomes = _downstream_outcomes(
        report,
        proposed_recipe,
        changed_index=changed_index,
        page_ids=page_ids,
    )
    _verify_refresh(report, proposed_recipe, changed_index, page_ids)
    known_cost, unknown_cost = _cost_evidence(report)

    book_valid = _validate_book(doc_id, isolated_library, page_ids, proposed_recipe)
    epub_valid = _validate_epub(doc_id, isolated_library, proposed_recipe)
    site_valid = _validate_site(
        doc_id,
        isolated_library,
        canary_root / "site",
        required=_produces(proposed_recipe, "book"),
    )

    status = _status(
        report,
        outcomes,
        unknown_cost=unknown_cost,
        book_valid=book_valid,
        epub_valid=epub_valid,
        site_valid=site_valid,
        human_review_required=human_review_required,
        human_review_passed=human_review_passed,
    )
    run_id = _work_run_id(isolated_db, doc_id)
    _save_run_report(canary_root / "run-report.json", report, run_id=run_id)

    return record_canary_evidence(
        work_order_id=doc_id,
        doc_id=doc_id,
        run_id=run_id,
        recipe_hash=proposal.proposed_recipe_hash,
        refreshed_station=proposal.station,
        status=status,
        downstream_outcomes=outcomes,
        known_cost_usd=known_cost,
        unknown_cost=unknown_cost,
        book_valid=book_valid,
        epub_valid=epub_valid,
        site_valid=site_valid,
        human_review_required=human_review_required,
        human_review_passed=human_review_passed,
    )


def _preflight(
    proposal: RecipeProposal,
    *,
    doc_id: str,
    canary_root: Path,
    library_root: Path,
    db_path: Path,
    recipe_root: Path,
) -> tuple[Recipe, tuple[str, ...], Path]:
    _verify_proposal(proposal)
    try:
        validate_doc_id(doc_id)
    except ValueError as error:
        raise CanaryError(str(error)) from error

    canary_root = canary_root.resolve()
    library_root = library_root.resolve()
    source_workspace = doc_dir(doc_id, library_root)
    if canary_root.exists():
        raise CanaryError(f"Canary root already exists: {canary_root}")
    if canary_root.is_relative_to(library_root) or library_root.is_relative_to(
        canary_root
    ):
        raise CanaryError("Canary root and production library must not overlap")
    if not source_workspace.is_dir():
        raise CanaryError(f"Canonical document workspace not found: {source_workspace}")

    production_source = recipe_root / f"{proposal.recipe}.yaml"
    try:
        current_source = production_source.read_bytes()
    except OSError as error:
        raise CanaryError(
            f"Production recipe source is unavailable: {production_source}"
        ) from error
    if _sha256(current_source) != proposal.current_recipe_hash:
        raise CanaryError("Production recipe source no longer matches the proposal")

    _verify_work_order(db_path, doc_id, proposal.recipe)
    try:
        metadata = read_json(metadata_path(doc_id, library_root))
        page_list = read_json(page_list_path(doc_id, library_root))
        validate_payload("metadata", metadata, expected_doc_id=doc_id)
        validate_payload("page_list", page_list, expected_doc_id=doc_id)
    except (OSError, ValueError) as error:
        raise CanaryError(
            f"Canonical document {doc_id!r} is invalid: {error}"
        ) from error

    recipe = _parse_proposed_recipe(proposal)
    _station_index(recipe, proposal.station)
    page_ids = tuple(
        page["page_id"]
        for page in sorted(page_list["pages"], key=lambda page: page.get("order", 0))
    )
    return recipe, page_ids, source_workspace


def _verify_work_order(db_path: Path, doc_id: str, recipe_name: str) -> None:
    if not db_path.is_file():
        raise CanaryError(f"Production ledger not found: {db_path}")
    try:
        connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            item = connection.execute(
                "SELECT doc_id, recipe FROM items WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise CanaryError(f"Production ledger cannot be read: {db_path}") from error
    if item is None:
        raise CanaryError(f"No canonical work order for document {doc_id!r}")
    if item["recipe"] != recipe_name:
        raise CanaryError(
            f"Document {doc_id!r} uses recipe {item['recipe']!r}, not proposal "
            f"recipe {recipe_name!r}"
        )


def _parse_proposed_recipe(proposal: RecipeProposal) -> Recipe:
    with tempfile.TemporaryDirectory(prefix="palimpsest-canary-") as directory:
        recipe_dir = Path(directory)
        (recipe_dir / f"{proposal.recipe}.yaml").write_bytes(
            proposal.proposed_source.encode("utf-8")
        )
        try:
            recipe = load_recipe(proposal.recipe, recipes_dir=recipe_dir)
        except (KeyError, TypeError, ValueError) as error:
            raise CanaryError(f"Proposed recipe is invalid: {error}") from error
    if recipe.name != proposal.recipe:
        raise CanaryError(
            f"Proposed recipe declares name {recipe.name!r}, expected {proposal.recipe!r}"
        )
    return recipe


def _station_index(recipe: Recipe, station: str) -> int:
    matches = [
        index for index, spec in enumerate(recipe.steps) if spec.station.name == station
    ]
    if len(matches) != 1:
        raise CanaryError(
            f"Proposed recipe must contain refreshed station {station!r} exactly once"
        )
    return matches[0]


def _expected_cell_keys(
    recipe: Recipe, changed_index: int, page_ids: tuple[str, ...]
) -> tuple[tuple[str, str | None], ...]:
    keys: list[tuple[str, str | None]] = []
    for spec in recipe.steps[changed_index:]:
        if spec.station.grain == "page":
            keys.extend((spec.station.name, page_id) for page_id in page_ids)
        else:
            keys.append((spec.station.name, None))
    return tuple(keys)


def _downstream_outcomes(
    report: RunReport,
    recipe: Recipe,
    *,
    changed_index: int,
    page_ids: tuple[str, ...],
) -> tuple[CanaryOutcome, ...]:
    by_key = {(cell.station, cell.page_id): cell for cell in report.cells}
    outcomes = []
    for station, page_id in _expected_cell_keys(recipe, changed_index, page_ids):
        cell = by_key.get((station, page_id))
        name = station if page_id is None else f"{station}/{page_id}"
        outcomes.append(
            CanaryOutcome(
                name=name,
                status=(
                    "passed"
                    if cell is not None and cell.action != "failed"
                    else "failed"
                ),
            )
        )
    return tuple(outcomes)


def _verify_refresh(
    report: RunReport,
    recipe: Recipe,
    changed_index: int,
    page_ids: tuple[str, ...],
) -> None:
    station = recipe.steps[changed_index].station
    expected_pages: tuple[str | None, ...] = (
        page_ids if station.grain == "page" else (None,)
    )
    cells = {
        cell.page_id: cell for cell in report.cells if cell.station == station.name
    }
    for page_id in expected_pages:
        cell = cells.get(page_id)
        if cell is not None and cell.action not in {"ran", "failed"}:
            raise CanaryError(
                f"Refreshed station {station.name!r} was not forced for cell "
                f"{page_id or 'manuscript'}: {cell.action}"
            )


def _cost_evidence(report: RunReport) -> tuple[float, bool]:
    charged = [cell for cell in report.cells if cell.action in {"ran", "failed"}]
    known = sum(cell.cost_usd for cell in charged if cell.cost_usd is not None)
    return known, any(cell.cost_usd is None for cell in charged)


def _produces(recipe: Recipe, kind: str) -> bool:
    return any(spec.station.produces == kind for spec in recipe.steps)


def _validate_book(
    doc_id: str,
    library_root: Path,
    page_ids: tuple[str, ...],
    recipe: Recipe,
) -> bool | None:
    if not _produces(recipe, "book"):
        return None
    path = artifact_path(doc_id, "book", None, library_root)
    if not path.is_file():
        return None
    try:
        book = read_json(path)
        validate_payload("book", book, expected_doc_id=doc_id)
        if book.get("doc_id") != doc_id:
            raise ValueError("book doc_id does not match canary document")
        folios = book.get("folios")
        if not isinstance(folios, list):
            raise ValueError("book folios are missing")
        covered = tuple(
            folio.get("page_id") for folio in folios if isinstance(folio, dict)
        )
        if covered != page_ids:
            raise ValueError("book folios do not cover the canonical pages in order")
        sections = book.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValueError("book has no sections")
        cited = {
            page_id
            for section in sections
            if isinstance(section, dict)
            for page_id in section.get("folio_ids", [])
        }
        if cited != set(page_ids):
            raise ValueError("book sections do not cite every canonical page")
    except (OSError, TypeError, ValueError):
        return False
    return True


def _validate_epub(doc_id: str, library_root: Path, recipe: Recipe) -> bool | None:
    if not _produces(recipe, "book_epub"):
        return None
    path = artifact_path(doc_id, "book_epub", None, library_root)
    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if archive.testzip() is not None:
                return False
            if archive.read("mimetype") != b"application/epub+zip":
                return False
            if "META-INF/container.xml" not in names:
                return False
            if not any(name.endswith(".opf") for name in names):
                return False
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return True


class _References(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = (
            "href"
            if tag in {"a", "link"}
            else "src"
            if tag in {"img", "script"}
            else None
        )
        if wanted is None:
            return
        self.values.extend(value for key, value in attrs if key == wanted and value)


def _validate_site(
    doc_id: str,
    library_root: Path,
    site_root: Path,
    *,
    required: bool,
) -> bool | None:
    if not required:
        return None
    try:
        shelved = site_builder.build(library_root, site_root)
        if shelved != [doc_id]:
            return False
        if not _local_references_exist(site_root):
            return False
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return True


def _local_references_exist(site_root: Path) -> bool:
    resolved_root = site_root.resolve()
    for html_path in site_root.rglob("*.html"):
        parser = _References()
        parser.feed(html_path.read_text(encoding="utf-8"))
        for value in parser.values:
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (html_path.parent / unquote(parsed.path)).resolve()
            if not target.is_relative_to(resolved_root):
                return False
            if parsed.path.endswith("/"):
                target /= "index.html"
            if not target.exists():
                return False
    return True


def _status(
    report: RunReport,
    outcomes: tuple[CanaryOutcome, ...],
    *,
    unknown_cost: bool,
    book_valid: bool | None,
    epub_valid: bool | None,
    site_valid: bool | None,
    human_review_required: bool,
    human_review_passed: bool | None,
) -> str:
    if (
        report.count("failed")
        or any(outcome.status == "failed" for outcome in outcomes)
        or any(value is False for value in (book_valid, epub_valid, site_valid))
        or (human_review_required and human_review_passed is False)
    ):
        return "failed"
    if (
        unknown_cost
        or any(value is None for value in (book_valid, epub_valid, site_valid))
        or (human_review_required and human_review_passed is None)
        or any(outcome.status == "unknown" for outcome in outcomes)
    ):
        return "unknown"
    return "passed"


def _work_run_id(db_path: Path, doc_id: str) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT work_run_id FROM work_runs WHERE doc_id = ? "
            "ORDER BY work_run_id DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
    if row is None:
        raise CanaryError("Isolated ledger did not record a canary work run")
    return str(row[0])


def _save_run_report(path: Path, report: RunReport, *, run_id: str) -> None:
    atomic_write_json(
        path,
        {
            "run_id": run_id,
            "doc_id": report.doc_id,
            "recipe": report.recipe,
            "cells": [_cell_record(cell) for cell in report.cells],
        },
    )


def _cell_record(cell: CellReport) -> dict[str, object]:
    return {
        "station": cell.station,
        "page_id": cell.page_id,
        "action": cell.action,
        "error": cell.error,
        "cost_usd": cell.cost_usd,
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
