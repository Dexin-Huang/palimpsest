"""Protocol-first catalog source heads and their registry."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol
from xml.etree import ElementTree

import requests

from palimpsest.catalog.records import NormalizedRecord, RecordPage, SourceRecord


class SourceHead(Protocol):
    """A resumable source convention that emits canonical record pages."""

    def pages(self, cursor: str | None = None) -> Iterator[RecordPage]: ...


def build_head(name: str, config: Mapping[str, Any]) -> SourceHead:
    try:
        head_type = _HEAD_TYPES[name]
    except KeyError:
        known = ", ".join(sorted(_HEAD_TYPES))
        raise ValueError(
            f"unknown catalog head {name!r}; known heads: {known}"
        ) from None
    return head_type(config)


class NormalizedJsonlHead:
    """Import a strict canonical JSONL envelope without losing its raw payload."""

    def __init__(self, config: Mapping[str, Any]):
        unknown = sorted(set(config) - {"path", "page_size"})
        if unknown:
            raise ValueError(f"unknown normalized-jsonl config: {', '.join(unknown)}")
        try:
            self._path = Path(config["path"])
        except KeyError:
            raise ValueError("normalized-jsonl head requires config.path") from None
        self._page_size = int(config.get("page_size", 100))
        if self._page_size < 1:
            raise ValueError("normalized-jsonl page_size must be positive")

    def pages(self, cursor: str | None = None) -> Iterator[RecordPage]:
        start_line = int(cursor or 0)
        if start_line < 0:
            raise ValueError("normalized-jsonl cursor must not be negative")
        batch: list[SourceRecord] = []
        line_number = 0
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, text in enumerate(handle, start=1):
                if line_number <= start_line or not text.strip():
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"invalid JSON in {self._path} line {line_number}: {error.msg}"
                    ) from error
                batch.append(_jsonl_record(value, self._path, line_number))
                if len(batch) == self._page_size:
                    yield RecordPage(tuple(batch), str(line_number))
                    batch.clear()
        if batch:
            yield RecordPage(tuple(batch), str(line_number))


def _jsonl_record(value: Any, path: Path, line_number: int) -> SourceRecord:
    if not isinstance(value, dict):
        raise ValueError(f"{path} line {line_number} must be a JSON object")
    allowed = {"source_key", "source_url", "source_modified_at", "record", "raw"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"{path} line {line_number} has unknown envelope fields: {', '.join(unknown)}"
        )
    if "record" not in value:
        raise ValueError(f"{path} line {line_number} is missing record")
    try:
        source_key = value["source_key"]
    except KeyError:
        raise ValueError(f"{path} line {line_number} is missing source_key") from None
    return SourceRecord(
        source_key=source_key,
        source_url=value.get("source_url"),
        source_modified_at=value.get("source_modified_at"),
        normalized=NormalizedRecord.from_mapping(value["record"]),
        raw=value.get("raw", value["record"]),
    )


_GALLICA_FIELDS = {
    "title": "titles",
    "description": "descriptions",
    "subject": "subjects",
    "language": "languages",
    "creator": "contributors",
    "contributor": "contributors",
    "identifier": "identifiers",
    "relation": "relations",
    "rights": "rights",
}
_ARK_PATTERN = re.compile(r"ark:/12148/[^/?#\s]+")
_DATE_RANGE_PATTERN = re.compile(r"^\s*(\d{3,4})\s*[-–/]\s*(\d{3,4})\s*$")
_LANGUAGE_CODES = {"chi": "zh", "fre": "fr", "tib": "bo", "san": "sa"}


class GallicaSruHead:
    """Harvest a Gallica SRU result set politely and resumably."""

    def __init__(self, config: Mapping[str, Any]):
        allowed = {
            "base_url",
            "query",
            "repository",
            "collection",
            "page_size",
            "minimum_interval_seconds",
            "timeout_seconds",
        }
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError(f"unknown gallica-sru config: {', '.join(unknown)}")
        try:
            self._query = str(config["query"])
        except KeyError:
            raise ValueError("gallica-sru head requires config.query") from None
        if not self._query.strip():
            raise ValueError("gallica-sru query must not be empty")
        self._base_url = str(config.get("base_url", "https://gallica.bnf.fr/SRU"))
        self._repository = str(
            config.get("repository", "Bibliothèque nationale de France")
        )
        self._collection = str(config.get("collection", "Gallica"))
        self._page_size = int(config.get("page_size", 50))
        self._minimum_interval = float(config.get("minimum_interval_seconds", 1.0))
        self._timeout = float(config.get("timeout_seconds", 45.0))
        if not 1 <= self._page_size <= 50:
            raise ValueError("gallica-sru page_size must be between 1 and 50")
        if self._minimum_interval < 0:
            raise ValueError("minimum_interval_seconds must not be negative")
        self._session = requests.Session()
        self._last_request_at: float | None = None

    def pages(self, cursor: str | None = None) -> Iterator[RecordPage]:
        start_record = int(cursor or 1)
        if start_record < 1:
            raise ValueError("gallica-sru cursor must be positive")
        while True:
            response_text = self._request(start_record)
            page, total = parse_gallica_response(
                response_text,
                repository=self._repository,
                collection=self._collection,
            )
            if not page.records:
                if start_record <= total:
                    raise ValueError(
                        f"Gallica returned no records at {start_record} of {total}"
                    )
                return
            yield page
            if page.next_cursor is None:
                return
            next_record = int(page.next_cursor)
            if next_record <= start_record:
                raise ValueError(
                    f"Gallica returned non-advancing cursor {page.next_cursor!r}"
                )
            start_record = next_record

    def _request(self, start_record: int) -> str:
        for attempt in range(5):
            if self._last_request_at is not None:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < self._minimum_interval:
                    time.sleep(self._minimum_interval - elapsed)
            response = self._session.get(
                self._base_url,
                params={
                    "version": "1.2",
                    "operation": "searchRetrieve",
                    "query": self._query,
                    "startRecord": start_record,
                    "maximumRecords": self._page_size,
                },
                headers={"User-Agent": "palimpsest-catalog/1.0"},
                timeout=self._timeout,
            )
            self._last_request_at = time.monotonic()
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response.text
            if attempt == 4:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else 2**attempt
            )
            time.sleep(max(self._minimum_interval, delay))
        raise RuntimeError("unreachable")


def parse_gallica_response(
    xml_text: str,
    *,
    repository: str,
    collection: str,
) -> tuple[RecordPage, int]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise ValueError(f"invalid Gallica SRU XML: {error}") from error
    total_text = _first_local_text(root, "numberOfRecords")
    if total_text is None or not total_text.isdigit():
        raise ValueError("Gallica SRU response is missing numberOfRecords")
    total = int(total_text)
    records: list[SourceRecord] = []
    next_cursor = _first_local_text(root, "nextRecordPosition")
    for element in root.iter():
        if _local_name(element.tag) != "record":
            continue
        position = _first_local_text(element, "recordPosition")
        record_data = next(
            (child for child in element if _local_name(child.tag) == "recordData"),
            None,
        )
        if record_data is None:
            continue
        fields = _leaf_fields(record_data)
        records.append(
            _gallica_record(
                fields,
                raw_xml=ElementTree.tostring(record_data, encoding="unicode"),
                position=position,
                repository=repository,
                collection=collection,
            )
        )
    return RecordPage(tuple(records), next_cursor), total


def _gallica_record(
    fields: Mapping[str, list[str]],
    *,
    raw_xml: str,
    position: str | None,
    repository: str,
    collection: str,
) -> SourceRecord:
    identifiers = fields.get("identifier", [])
    ark = next(
        (
            match.group(0)
            for value in identifiers
            if (match := _ARK_PATTERN.search(value))
        ),
        None,
    )
    if not identifiers:
        raise ValueError(f"Gallica record {position or '?'} has no stable identifier")
    source_key = ark or identifiers[0]
    if ark:
        catalog_url = f"https://gallica.bnf.fr/{ark}"
        manifest_url = f"https://gallica.bnf.fr/iiif/{ark}/manifest.json"
    else:
        catalog_url = next(
            (
                value
                for value in identifiers
                if value.startswith(("http://", "https://"))
            ),
            None,
        )
        manifest_url = None

    lists: dict[str, list[str]] = {}
    for source_field, normalized_field in _GALLICA_FIELDS.items():
        lists.setdefault(normalized_field, []).extend(fields.get(source_field, []))
    lists["languages"] = [
        _LANGUAGE_CODES.get(value.casefold(), value) for value in lists["languages"]
    ]

    date_values = fields.get("date", [])
    date_label = "; ".join(date_values) or None
    date_start = date_end = None
    if len(date_values) == 1 and (match := _DATE_RANGE_PATTERN.match(date_values[0])):
        date_start, date_end = int(match.group(1)), int(match.group(2))

    type_text = " ".join(fields.get("type", []) + fields.get("format", [])).casefold()
    title_text = " ".join(fields.get("title", [])).casefold()
    record_type = "unknown"
    if "manuscrit" in type_text or "manuscript" in type_text:
        record_type = (
            "manuscript_fragment" if "fragment" in title_text else "manuscript"
        )
    rights_text = " ".join(lists["rights"]).casefold()
    access = (
        "open"
        if "domaine public" in rights_text or "public domain" in rights_text
        else "unknown"
    )

    normalized = NormalizedRecord.from_mapping(
        {
            "record_type": record_type,
            **lists,
            "repository": repository,
            "collection": collection,
            "catalog_url": catalog_url,
            "manifest_url": manifest_url,
            "access": access,
            "date_label": date_label,
            "date_start": date_start,
            "date_end": date_end,
            "material": "; ".join(fields.get("format", [])) or None,
        }
    )
    return SourceRecord(
        source_key=source_key,
        source_url=catalog_url,
        normalized=normalized,
        raw={"record_position": position, "record_data_xml": raw_xml},
    )


def _leaf_fields(root: ElementTree.Element) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for element in root.iter():
        if len(element) or element.text is None:
            continue
        text = " ".join(element.text.split())
        if text:
            fields.setdefault(_local_name(element.tag), []).append(text)
    return fields


def _first_local_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name and element.text:
            return element.text.strip()
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_HEAD_TYPES: dict[str, type[SourceHead]] = {
    "gallica-sru": GallicaSruHead,
    "normalized-jsonl": NormalizedJsonlHead,
}
