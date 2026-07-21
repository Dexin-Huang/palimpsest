"""Deterministic conformance metrics for non-model station outputs.

The scorers consume artifact-shaped mappings plus scorer-only gold.  Binary EPUB
outputs are supplied as ``{"epub_bytes": bytes}``; no scorer opens a network
connection.  Site checks consume evidence collected from a built static reader,
because ``site`` is a library derivation rather than a production station.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

from palimpsest.factory.core.contracts import validate_payload

from ..metrics import Metric, MetricDirection, MetricRegistry


_XML_MEDIA_TYPES = {"application/xhtml+xml", "application/x-dtbncx+xml"}
_REQUIRED_COLOPHON_FIELDS = {
    "transcribed_by",
    "translated_by",
    "referenced_by",
    "emended_by",
    "pipeline",
    "cost_usd_total",
    "cost_usd_known",
    "cost_complete",
    "pages",
}


def _nested(record: Mapping[str, object], *keys: str) -> object | None:
    value: object = record
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _identity(actual: object, expected: object) -> float:
    return float(actual == expected)


def _score_acquire_byte_identity(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual = output.get("content_sha256")
    expected = gold.get("content_sha256")
    if not isinstance(actual, str) or not isinstance(expected, str):
        return None
    return _identity(actual, expected)


def _score_acquire_source_identity(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = gold.get("source_url")
    requested = output.get("requested_url")
    delivered = output.get("delivered_url")
    status = output.get("http_status")
    media_type = output.get("media_type")
    expected_media_type = gold.get("media_type")
    if not all(isinstance(value, str) for value in (expected, requested, delivered)):
        return None
    if not isinstance(status, int) or isinstance(status, bool):
        return None
    if not isinstance(media_type, str) or not isinstance(expected_media_type, str):
        return None
    return float(
        requested == expected
        and delivered == expected
        and status == 200
        and media_type.split(";", 1)[0].strip().lower() == expected_media_type.lower()
    )


def _score_acquire_retry_conformance(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    attempts = output.get("attempts")
    expected_statuses = gold.get("attempt_statuses")
    if (
        not isinstance(attempts, Sequence)
        or isinstance(attempts, (str, bytes))
        or not isinstance(expected_statuses, Sequence)
        or isinstance(expected_statuses, (str, bytes))
    ):
        return None
    statuses: list[int] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            return None
        status = attempt.get("status")
        published = attempt.get("published")
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or type(published) is not bool
        ):
            return None
        statuses.append(status)
    if not all(
        isinstance(status, int) and not isinstance(status, bool)
        for status in expected_statuses
    ):
        return None
    publication_is_atomic = all(not attempt["published"] for attempt in attempts[:-1])
    final_is_published = bool(attempts) and attempts[-1]["published"] is True
    return float(
        statuses == list(expected_statuses)
        and publication_is_atomic
        and final_is_published
        and output.get("partial_published") is False
    )


def _assembled_text(record: Mapping[str, object], side: str) -> str | None:
    value = _nested(record, side, "text")
    return value if isinstance(value, str) else None


def _score_assembled_source_identity(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual, expected = (
        _assembled_text(output, "original"),
        _assembled_text(gold, "original"),
    )
    return None if actual is None or expected is None else _identity(actual, expected)


def _score_assembled_translation_identity(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual = _assembled_text(output, "translation")
    expected = _assembled_text(gold, "translation")
    return None if actual is None or expected is None else _identity(actual, expected)


def _tokens(record: Mapping[str, object]) -> list[str] | None:
    original = _assembled_text(record, "original")
    translation = _assembled_text(record, "translation")
    if original is None or translation is None:
        return None
    return (original + "\n" + translation).split()


def _score_assembled_seam_correctness(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual_seam = _nested(output, "original", "seam")
    expected_seam = _nested(gold, "original", "seam")
    actual_original = _assembled_text(output, "original")
    expected_original = _assembled_text(gold, "original")
    if actual_original is None or expected_original is None:
        return None
    return float(actual_seam == expected_seam and actual_original == expected_original)


def _score_assembled_order_integrity(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual, expected = _tokens(output), _tokens(gold)
    if actual is None or expected is None:
        return None
    expected_unique = list(dict.fromkeys(expected))
    positions = []
    for token in expected_unique:
        try:
            positions.append(actual.index(token))
        except ValueError:
            return 0.0
    return float(positions == sorted(positions))


def _score_assembled_duplication_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual, expected = _tokens(output), _tokens(gold)
    if actual is None or expected is None:
        return None
    excess = sum((Counter(actual) - Counter(expected)).values())
    return excess / max(len(expected), 1)


def _score_assembled_omission_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual, expected = _tokens(output), _tokens(gold)
    if actual is None or expected is None:
        return None
    missing = sum((Counter(expected) - Counter(actual)).values())
    return missing / max(len(expected), 1)


def _book_schema_is_valid(book: Mapping[str, object]) -> bool:
    try:
        validate_payload("book", book)
    except ValueError:
        return False
    language = book.get("language")
    chapters = book.get("chapters")
    evidence = book.get("evidence")
    colophon = book.get("colophon")
    if not isinstance(language, Mapping) or not all(
        isinstance(language.get(key), str) and language.get(key)
        for key in ("original", "translation")
    ):
        return False
    if (
        not isinstance(chapters, Sequence)
        or isinstance(chapters, (str, bytes))
        or not chapters
    ):
        return False
    seen: set[str] = set()
    for chapter in chapters:
        if not isinstance(chapter, Mapping):
            return False
        required = ("id", "heading", "translation", "original", "pages", "source_pages")
        if any(key not in chapter for key in required):
            return False
        chapter_id = chapter.get("id")
        pages = chapter.get("pages")
        source_pages = chapter.get("source_pages")
        if not isinstance(chapter_id, str) or not chapter_id or chapter_id in seen:
            return False
        seen.add(chapter_id)
        if not all(
            isinstance(chapter.get(key), str)
            for key in ("heading", "translation", "original")
        ):
            return False
        if not isinstance(pages, Mapping) or not all(
            isinstance(pages.get(key), str) and pages.get(key) for key in ("from", "to")
        ):
            return False
        if not isinstance(source_pages, Sequence) or isinstance(
            source_pages, (str, bytes)
        ):
            return False
    return (
        isinstance(evidence, Mapping)
        and isinstance(evidence.get("pages"), Sequence)
        and not isinstance(evidence.get("pages"), (str, bytes))
        and isinstance(colophon, Mapping)
    )


def _score_book_schema_validity(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float:
    return float(_book_schema_is_valid(output))


def _score_book_content_identity(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = gold.get("chapters")
    actual = output.get("chapters")
    if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes)):
        return None
    if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)):
        return None
    return _identity(actual, expected)


def _score_book_evidence_coverage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    required = gold.get("required_source_pages")
    evidence = _nested(output, "evidence", "pages")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return None
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        return None
    available = {
        page.get("page_id")
        for page in evidence
        if isinstance(page, Mapping)
        and isinstance(page.get("page_id"), str)
        and isinstance(page.get("source_image_url"), str)
        and isinstance(page.get("diplomatic"), str)
    }
    expected = [page for page in required if isinstance(page, str)]
    if len(expected) != len(required):
        return None
    return sum(page in available for page in expected) / max(len(expected), 1)


def _score_book_provenance_completeness(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    required = gold.get("required_stations")
    pipeline = _nested(output, "colophon", "pipeline")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return None
    if not isinstance(pipeline, Sequence) or isinstance(pipeline, (str, bytes)):
        return None
    complete: set[str] = set()
    for entry in pipeline:
        if not isinstance(entry, Mapping):
            continue
        station = entry.get("station")
        configurations = entry.get("configurations")
        if (
            isinstance(station, str)
            and isinstance(configurations, Sequence)
            and not isinstance(configurations, (str, bytes))
            and configurations
            and all(
                isinstance(configuration, Mapping)
                and isinstance(configuration.get("station_fingerprint"), str)
                and isinstance(configuration.get("config_fingerprint"), str)
                for configuration in configurations
            )
        ):
            complete.add(station)
    expected = [station for station in required if isinstance(station, str)]
    if len(expected) != len(required):
        return None
    return sum(station in complete for station in expected) / max(len(expected), 1)


def _score_book_colophon_completeness(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float:
    colophon = output.get("colophon")
    if not isinstance(colophon, Mapping) or not _REQUIRED_COLOPHON_FIELDS <= set(
        colophon
    ):
        return 0.0
    cost_complete = colophon.get("cost_complete")
    if type(cost_complete) is not bool:
        return 0.0
    if cost_complete:
        valid_cost = isinstance(colophon.get("cost_usd_total"), (int, float))
    else:
        valid_cost = colophon.get("cost_usd_total") is None
    return float(
        valid_cost
        and isinstance(colophon.get("cost_usd_known"), (int, float))
        and isinstance(colophon.get("pages"), int)
        and not isinstance(colophon.get("pages"), bool)
    )


def _epub_bytes(record: Mapping[str, object]) -> bytes | None:
    value = record.get("epub_bytes")
    return bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else None


def _epub_entries(data: bytes) -> tuple[zipfile.ZipFile, dict[str, bytes]] | None:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
        entries = {name: archive.read(name) for name in archive.namelist()}
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        return None
    return archive, entries


def _opf_location(entries: Mapping[str, bytes]) -> str | None:
    try:
        root = ElementTree.fromstring(entries["META-INF/container.xml"])
    except (KeyError, ElementTree.ParseError):
        return None
    rootfile = root.find(".//{*}rootfile")
    location = None if rootfile is None else rootfile.get("full-path")
    return location if location in entries else None


def _score_epub_container_conformance(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float:
    data = _epub_bytes(output)
    if data is None:
        return 0.0
    parsed = _epub_entries(data)
    if parsed is None:
        return 0.0
    archive, entries = parsed
    infos = archive.infolist()
    return float(
        bool(infos)
        and infos[0].filename == "mimetype"
        and infos[0].compress_type == zipfile.ZIP_STORED
        and entries.get("mimetype") == b"application/epub+zip"
        and _opf_location(entries) is not None
    )


def _opf_model(
    data: bytes,
) -> tuple[dict[str, tuple[str, str, str]], list[str], dict[str, bytes]] | None:
    parsed = _epub_entries(data)
    if parsed is None:
        return None
    _, entries = parsed
    opf_path = _opf_location(entries)
    if opf_path is None:
        return None
    try:
        root = ElementTree.fromstring(entries[opf_path])
    except ElementTree.ParseError:
        return None
    base = PurePosixPath(opf_path).parent
    manifest: dict[str, tuple[str, str, str]] = {}
    for item in root.findall(".//{*}manifest/{*}item"):
        item_id, href, media_type = (
            item.get("id"),
            item.get("href"),
            item.get("media-type"),
        )
        if item_id and href and media_type:
            manifest[item_id] = (
                (base / href).as_posix(),
                media_type,
                item.get("properties", ""),
            )
    spine = [item.get("idref", "") for item in root.findall(".//{*}spine/{*}itemref")]
    return manifest, spine, entries


def _score_epub_navigation_correctness(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float:
    data = _epub_bytes(output)
    expected = gold.get("navigation_labels")
    if (
        data is None
        or not isinstance(expected, Sequence)
        or isinstance(expected, (str, bytes))
    ):
        return 0.0
    model = _opf_model(data)
    if model is None:
        return 0.0
    manifest, spine, entries = model
    nav_items = [item for item in manifest.values() if "nav" in item[2].split()]
    if len(nav_items) != 1 or any(item_id not in manifest for item_id in spine):
        return 0.0
    try:
        nav_text = entries[nav_items[0][0]].decode("utf-8")
    except (KeyError, UnicodeDecodeError):
        return 0.0
    labels = [label for label in expected if isinstance(label, str)]
    return float(
        len(labels) == len(expected) and all(label in nav_text for label in labels)
    )


def _visible_xml_text(data: bytes) -> str:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""
    return " ".join(part.strip() for part in root.itertext() if part.strip())


def _score_epub_content_equivalence(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float:
    data = _epub_bytes(output)
    expected = gold.get("content_strings")
    if (
        data is None
        or not isinstance(expected, Sequence)
        or isinstance(expected, (str, bytes))
    ):
        return 0.0
    model = _opf_model(data)
    if model is None:
        return 0.0
    manifest, _spine, entries = model
    documents = [
        _visible_xml_text(entries[path])
        for path, media_type, _properties in manifest.values()
        if media_type in _XML_MEDIA_TYPES and path in entries
    ]
    corpus = "\n".join(documents)
    strings = [value for value in expected if isinstance(value, str) and value]
    return float(
        len(strings) == len(expected)
        and all(corpus.count(value) == 1 for value in strings)
    )


class _SiteDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []
        self.has_viewport = False
        self.keyboard_buttons = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and isinstance(values.get("href"), str):
            self.links.append(values["href"])
        elif tag == "img" and isinstance(values.get("src"), str):
            self.images.append(values["src"])
        elif (
            tag == "meta"
            and values.get("name") == "viewport"
            and "width=device-width" in (values.get("content") or "")
        ):
            self.has_viewport = True
        elif tag == "button" and values.get("onclick"):
            self.keyboard_buttons += 1


def collect_site_conformance(site_root: str | Path) -> Mapping[str, object]:
    """Collect local, deterministic evidence from a built static library.

    External archive links are recorded as source links but never fetched.  Local
    links and source-image files are resolved under ``site_root`` and hashed.
    """

    root = Path(site_root).resolve()
    html_files = sorted(root.rglob("*.html"))
    broken: list[str] = []
    external: list[str] = []
    image_hashes: dict[str, str] = {}
    has_viewport = True
    keyboard_controls = 0
    for html_path in html_files:
        parser = _SiteDocument()
        parser.feed(html_path.read_text(encoding="utf-8"))
        has_viewport = has_viewport and parser.has_viewport
        keyboard_controls += parser.keyboard_buttons
        for reference in (*parser.links, *parser.images):
            parsed = urlsplit(reference)
            if parsed.scheme in {"http", "https"}:
                external.append(reference)
                continue
            if parsed.scheme or reference.startswith("#"):
                continue
            target = (html_path.parent / parsed.path).resolve()
            if reference.endswith("/"):
                target = target / "index.html"
            if not target.is_relative_to(root) or not target.exists():
                broken.append(f"{html_path.relative_to(root).as_posix()}->{reference}")
        for reference in parser.images:
            parsed = urlsplit(reference)
            if parsed.scheme or reference.startswith("#"):
                continue
            target = (html_path.parent / parsed.path).resolve()
            if target.is_file() and target.is_relative_to(root):
                image_hashes[target.relative_to(root).as_posix()] = hashlib.sha256(
                    target.read_bytes()
                ).hexdigest()
    css_path = root / "style.css"
    css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
    responsive_css = bool(
        re.search(r"max-width\s*:", css)
        and re.search(r"\.source-image\s*\{[^}]*width\s*:\s*100%", css, re.S)
    )
    book_hashes = {
        path.parent.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.glob("*/book.json"))
    }
    return {
        "html_documents": len(html_files),
        "broken_local_links": broken,
        "external_source_links": sorted(set(external)),
        "source_image_sha256": image_hashes,
        "keyboard_controls": keyboard_controls,
        "viewport_meta_complete": has_viewport and bool(html_files),
        "responsive_css": responsive_css,
        "book_json_sha256": book_hashes,
    }


def _score_site_link_integrity(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float | None:
    broken = output.get("broken_local_links")
    documents = output.get("html_documents")
    if not isinstance(broken, Sequence) or isinstance(broken, (str, bytes)):
        return None
    if not isinstance(documents, int) or isinstance(documents, bool):
        return None
    return float(documents > 0 and not broken)


def _score_site_source_image_conformance(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual = output.get("source_image_sha256")
    expected = gold.get("source_image_sha256")
    if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
        return None
    return _identity(dict(actual), dict(expected))


def _score_site_keyboard_conformance(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual = output.get("keyboard_controls")
    minimum = gold.get("minimum_keyboard_controls")
    if not isinstance(actual, int) or isinstance(actual, bool):
        return None
    if not isinstance(minimum, int) or isinstance(minimum, bool):
        return None
    return float(actual >= minimum)


def _score_site_responsive_conformance(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float | None:
    viewport = output.get("viewport_meta_complete")
    css = output.get("responsive_css")
    if type(viewport) is not bool or type(css) is not bool:
        return None
    return float(viewport and css)


def _score_site_book_equality(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    actual = output.get("book_json_sha256")
    expected = gold.get("book_json_sha256")
    if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
        return None
    return _identity(dict(actual), dict(expected))


def register_deterministic_metrics(registry: MetricRegistry) -> None:
    """Register all trusted deterministic station and library metrics."""

    for metric in (
        Metric(
            "acquire_byte_identity",
            MetricDirection.MAXIMIZE,
            _score_acquire_byte_identity,
        ),
        Metric(
            "acquire_source_identity",
            MetricDirection.MAXIMIZE,
            _score_acquire_source_identity,
        ),
        Metric(
            "acquire_retry_conformance",
            MetricDirection.MAXIMIZE,
            _score_acquire_retry_conformance,
        ),
        Metric(
            "assembled_source_identity",
            MetricDirection.MAXIMIZE,
            _score_assembled_source_identity,
        ),
        Metric(
            "assembled_translation_identity",
            MetricDirection.MAXIMIZE,
            _score_assembled_translation_identity,
        ),
        Metric(
            "assembled_seam_correctness",
            MetricDirection.MAXIMIZE,
            _score_assembled_seam_correctness,
        ),
        Metric(
            "assembled_order_integrity",
            MetricDirection.MAXIMIZE,
            _score_assembled_order_integrity,
        ),
        Metric(
            "assembled_duplication_rate",
            MetricDirection.MINIMIZE,
            _score_assembled_duplication_rate,
        ),
        Metric(
            "assembled_omission_rate",
            MetricDirection.MINIMIZE,
            _score_assembled_omission_rate,
        ),
        Metric(
            "book_schema_validity",
            MetricDirection.MAXIMIZE,
            _score_book_schema_validity,
        ),
        Metric(
            "book_content_identity",
            MetricDirection.MAXIMIZE,
            _score_book_content_identity,
        ),
        Metric(
            "book_evidence_coverage",
            MetricDirection.MAXIMIZE,
            _score_book_evidence_coverage,
        ),
        Metric(
            "book_provenance_completeness",
            MetricDirection.MAXIMIZE,
            _score_book_provenance_completeness,
        ),
        Metric(
            "book_colophon_completeness",
            MetricDirection.MAXIMIZE,
            _score_book_colophon_completeness,
        ),
        Metric(
            "epub_container_conformance",
            MetricDirection.MAXIMIZE,
            _score_epub_container_conformance,
        ),
        Metric(
            "epub_navigation_correctness",
            MetricDirection.MAXIMIZE,
            _score_epub_navigation_correctness,
        ),
        Metric(
            "epub_content_equivalence",
            MetricDirection.MAXIMIZE,
            _score_epub_content_equivalence,
        ),
        Metric(
            "site_link_integrity", MetricDirection.MAXIMIZE, _score_site_link_integrity
        ),
        Metric(
            "site_source_image_conformance",
            MetricDirection.MAXIMIZE,
            _score_site_source_image_conformance,
        ),
        Metric(
            "site_keyboard_conformance",
            MetricDirection.MAXIMIZE,
            _score_site_keyboard_conformance,
        ),
        Metric(
            "site_responsive_conformance",
            MetricDirection.MAXIMIZE,
            _score_site_responsive_conformance,
        ),
        Metric(
            "site_book_equality", MetricDirection.MAXIMIZE, _score_site_book_equality
        ),
    ):
        registry.register(metric)
