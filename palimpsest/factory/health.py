"""Read-only operational health checks for the active factory state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from palimpsest.factory.core.recipe import Recipe, load as load_recipe
from palimpsest.factory.publication_bundle import epub_is_current, load_book
from palimpsest.factory.workspace.layout import artifact_path


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def terminal_product_status(doc_id: str, item_status: str, library_root: Path) -> str:
    if item_status != "complete":
        return "n/a"
    book_path = artifact_path(doc_id, "book", None, library_root)
    if not book_path.is_file():
        return "missing-book"
    try:
        load_book(book_path)
    except (OSError, TypeError, ValueError):
        return "invalid-book"
    epub_path = artifact_path(doc_id, "book_epub", None, library_root)
    return "ready" if epub_is_current(book_path, epub_path) else "missing-or-stale-epub"


def inspect_factory(
    *,
    factory_db: Path,
    catalog_db: Path,
    library_root: Path,
    recipes_root: Path,
) -> dict[str, Any]:
    """Return machine-readable checks without mutating factory state."""
    checks: list[HealthCheck] = []
    factory_ready = _database_check(checks, "factory.database", factory_db)
    catalog_ready = _database_check(checks, "catalog.database", catalog_db)

    if factory_ready:
        _factory_checks(checks, factory_db, library_root)
    if catalog_ready:
        _catalog_checks(checks, catalog_db)

    recipes = _recipe_checks(checks, recipes_root)
    if recipes is not None and factory_ready:
        _production_configuration_checks(checks, factory_db, recipes)

    overall = "fail" if any(check.status == "fail" for check in checks) else "pass"
    if overall == "pass" and any(check.status == "warn" for check in checks):
        overall = "warn"
    return {"status": overall, "checks": [check.as_dict() for check in checks]}


def _database_check(checks: list[HealthCheck], name: str, path: Path) -> bool:
    if not path.is_file():
        checks.append(HealthCheck(name, "fail", f"missing: {path}"))
        return False
    try:
        with closing(sqlite3.connect(path)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as error:
        checks.append(HealthCheck(name, "fail", f"SQLite error: {error}"))
        return False
    if result != "ok":
        checks.append(HealthCheck(name, "fail", f"integrity_check={result}"))
        return False
    checks.append(HealthCheck(name, "pass", f"integrity_check=ok ({path})"))
    return True


def _factory_checks(
    checks: list[HealthCheck], database_path: Path, library_root: Path
) -> None:
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            items = connection.execute(
                "SELECT doc_id, recipe, status FROM items ORDER BY created_at"
            ).fetchall()
            running = connection.execute(
                "SELECT doc_id, owner, heartbeat_at FROM work_runs "
                "WHERE status = 'running' ORDER BY doc_id"
            ).fetchall()
    except sqlite3.Error as error:
        checks.append(HealthCheck("factory.ledger", "fail", f"query failed: {error}"))
        return

    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    failed = [item["doc_id"] for item in items if item["status"] == "failed"]
    active = [item["doc_id"] for item in items if item["status"] == "active"]
    if failed:
        checks.append(
            HealthCheck("factory.work_orders", "fail", f"failed: {', '.join(failed)}")
        )
    elif active:
        checks.append(
            HealthCheck(
                "factory.work_orders",
                "warn",
                f"statuses={json.dumps(counts, sort_keys=True)}; queued/active: {', '.join(active)}",
            )
        )
    else:
        checks.append(
            HealthCheck(
                "factory.work_orders",
                "pass",
                f"statuses={json.dumps(counts, sort_keys=True)}",
            )
        )

    unready = [
        f"{item['doc_id']}={terminal_product_status(item['doc_id'], item['status'], library_root)}"
        for item in items
        if item["status"] == "complete"
        and terminal_product_status(item["doc_id"], item["status"], library_root)
        != "ready"
    ]
    checks.append(
        HealthCheck(
            "factory.products",
            "fail" if unready else "pass",
            "; ".join(unready)
            if unready
            else "every complete work order has a current Book and EPUB",
        )
    )
    if running:
        detail = "; ".join(
            f"{row['doc_id']} owner={row['owner']} heartbeat={row['heartbeat_at']}"
            for row in running
        )
        checks.append(HealthCheck("factory.claims", "warn", detail))
    else:
        checks.append(HealthCheck("factory.claims", "pass", "no running work claims"))


def _catalog_checks(checks: list[HealthCheck], database_path: Path) -> None:
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM catalog_records WHERE tombstoned = 0"
            ).fetchone()[0]
            sources = connection.execute(
                "SELECT COUNT(*) FROM catalog_sources WHERE enabled = 1"
            ).fetchone()[0]
            running = connection.execute(
                "SELECT COUNT(*) FROM catalog_sync_runs WHERE status = 'running'"
            ).fetchone()[0]
    except sqlite3.Error as error:
        checks.append(
            HealthCheck("catalog.inventory", "fail", f"query failed: {error}")
        )
        return
    status = "warn" if running else "pass"
    checks.append(
        HealthCheck(
            "catalog.inventory",
            status,
            f"enabled_sources={sources} active_records={active} running_syncs={running}",
        )
    )


def _recipe_checks(
    checks: list[HealthCheck], recipes_root: Path
) -> tuple[Recipe, ...] | None:
    try:
        import palimpsest.factory.stations  # noqa: F401

        paths = sorted(recipes_root.glob("*.yaml"))
        if not paths:
            raise ValueError(f"no recipe YAML files under {recipes_root}")
        recipes = tuple(
            load_recipe(path.stem, recipes_dir=recipes_root) for path in paths
        )
    except (OSError, TypeError, ValueError) as error:
        checks.append(HealthCheck("recipes", "fail", str(error)))
        return None
    checks.append(
        HealthCheck(
            "recipes",
            "pass",
            f"validated {len(recipes)} recipe(s): {', '.join(recipe.name for recipe in recipes)}",
        )
    )
    return recipes


def _production_configuration_checks(
    checks: list[HealthCheck],
    database_path: Path,
    recipes: tuple[Recipe, ...],
) -> None:
    from palimpsest.factory import prompt_store
    from palimpsest.factory.core.conductor import station_config_fingerprints

    expected: dict[str, dict[str, str]] = {}
    try:
        for recipe in recipes:
            slots: dict[str, str] = {}
            for step in recipe.steps:
                prompt = (
                    None
                    if step.prompt_name is None
                    else prompt_store.load(step.prompt_name)
                )
                slots[step.station.name] = station_config_fingerprints(step, prompt)[0]
            expected[recipe.name] = slots
        with closing(sqlite3.connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            items = connection.execute(
                "SELECT doc_id, recipe FROM items WHERE status = 'complete' "
                "ORDER BY doc_id"
            ).fetchall()
            states = connection.execute(
                "SELECT doc_id, station, config_fingerprint FROM stage_state "
                "ORDER BY doc_id, station, page_id"
            ).fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        checks.append(
            HealthCheck(
                "production.configuration", "fail", f"inspection failed: {error}"
            )
        )
        return

    observed: dict[tuple[str, str], list[str]] = {}
    for row in states:
        observed.setdefault((row["doc_id"], row["station"]), []).append(
            row["config_fingerprint"]
        )
    outdated: list[str] = []
    for item in items:
        slots = expected.get(item["recipe"])
        if slots is None:
            outdated.append(f"{item['doc_id']}:unknown-recipe={item['recipe']}")
            continue
        for station, config_fingerprint in slots.items():
            values = observed.get((item["doc_id"], station), [])
            current = sum(value == config_fingerprint for value in values)
            if not values or current != len(values):
                outdated.append(
                    f"{item['doc_id']}:{station}={current}/{len(values)} current"
                )
    if outdated:
        shown = outdated[:20]
        suffix = (
            ""
            if len(outdated) <= len(shown)
            else f"; +{len(outdated) - len(shown)} more"
        )
        checks.append(
            HealthCheck(
                "production.configuration",
                "warn",
                "completed products need explicit station refresh: "
                + "; ".join(shown)
                + suffix,
            )
        )
    else:
        checks.append(
            HealthCheck(
                "production.configuration",
                "pass",
                "every completed work order matches its selected recipe configuration",
            )
        )
