from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from lxml import html

from .adapter import DiscoverySourceAdapter, SourceCollection, SourceDocumentRef


BASE_URL = "https://idp.bl.uk"
COLLECTION_SEARCH_URL = f"{BASE_URL}/collection/"


@dataclass(frozen=True)
class IDPCollectionDefinition:
    key: str
    label: str
    params: tuple[tuple[str, str], ...]
    notes: str | None = None
    automation_fit: int = 3
    north_star_fit: int = 4
    access: str = "public_open_web_iiif"

    def listing_url(self, page: int | None = None) -> str:
        params = list(self.params)
        if page and page > 1:
            params.append(("page", str(page)))
        query = urlencode(params, doseq=True)
        return f"{COLLECTION_SEARCH_URL}?{query}" if query else COLLECTION_SEARCH_URL


CURATED_COLLECTIONS: tuple[IDPCollectionDefinition, ...] = (
    IDPCollectionDefinition(
        key="stein_dunhuang_chinese",
        label="Stein Dunhuang Chinese Manuscripts",
        params=(
            ("classification[]", "Manuscript (inc. blockprint)"),
            ("term_provenance[]", "mc:stein"),
            ("site[]", "Dunhuang Mogao"),
            ("language_or_script[]", "Chinese (lang.)"),
        ),
        notes="Broad Dunhuang/Stein Chinese manuscript lane.",
        automation_fit=3,
        north_star_fit=4,
    ),
    IDPCollectionDefinition(
        key="chinese_astronomy_divination",
        label="Chinese Astronomy And Divination",
        params=(
            ("classification[]", "Manuscript (inc. blockprint)"),
            ("language_or_script[]", "Chinese (lang.)"),
            ("subject[]", "Astronomy and Divination"),
        ),
        notes="High-fit lane for cosmology, calendrics, and omen culture.",
        automation_fit=3,
        north_star_fit=5,
    ),
    IDPCollectionDefinition(
        key="chinese_medicine",
        label="Chinese Medicine",
        params=(
            ("classification[]", "Manuscript (inc. blockprint)"),
            ("language_or_script[]", "Chinese (lang.)"),
            ("subject[]", "Medicine"),
        ),
        notes="Medical and practical-knowledge lane.",
        automation_fit=3,
        north_star_fit=5,
    ),
    IDPCollectionDefinition(
        key="chinese_magic",
        label="Chinese Magic",
        params=(
            ("classification[]", "Manuscript (inc. blockprint)"),
            ("language_or_script[]", "Chinese (lang.)"),
            ("subject[]", "magic"),
        ),
        notes="Ritual, talismanic, or magical-material lane.",
        automation_fit=3,
        north_star_fit=5,
    ),
    IDPCollectionDefinition(
        key="chinese_prayers",
        label="Chinese Prayers",
        params=(
            ("classification[]", "Manuscript (inc. blockprint)"),
            ("language_or_script[]", "Chinese (lang.)"),
            ("subject[]", "Prayers"),
        ),
        notes="Prayer and devotional manuscript lane.",
        automation_fit=3,
        north_star_fit=4,
    ),
    IDPCollectionDefinition(
        key="chinese_daoism",
        label="Chinese Daoism",
        params=(
            ("classification[]", "Manuscript (inc. blockprint)"),
            ("language_or_script[]", "Chinese (lang.)"),
            ("subject[]", "Daoism"),
        ),
        notes="Daoist manuscripts and related ritual/cosmology material.",
        automation_fit=3,
        north_star_fit=5,
    ),
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _truncate_text(value: str | None, *, max_chars: int = 1200) -> str | None:
    if not value:
        return value
    text = _normalize_text(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _collection_map() -> dict[str, IDPCollectionDefinition]:
    return {collection.key: collection for collection in CURATED_COLLECTIONS}


def _extract_object_id(href: str) -> str | None:
    match = re.search(r"/collection/([A-Z0-9]+)/", href)
    return match.group(1) if match else None


def _extract_card_summary(link: html.HtmlElement) -> dict[str, Any]:
    text = _normalize_text(link.text_content())
    summary: dict[str, Any] = {"search_summary": text}

    title_match = re.search(r"Title (.*?) Material ", text)
    material_match = re.search(r"Material (.*?) Pressmark ", text)
    pressmark_match = re.search(r"Pressmark (.*?)(?: Item type |$)", text)
    item_type_match = re.search(r"Item type (.*)$", text)

    if title_match:
        summary["title"] = _normalize_text(title_match.group(1))
    if material_match:
        summary["material"] = _normalize_text(material_match.group(1))
    if pressmark_match:
        summary["shelfmark"] = _normalize_text(pressmark_match.group(1))
    if item_type_match:
        summary["item_type"] = _normalize_text(item_type_match.group(1))

    return summary


def _parse_detail_page(object_id: str, viewer_url: str, tree: html.HtmlElement) -> dict[str, Any]:
    field_map: dict[str, Any] = {}

    for section in tree.xpath("//div[contains(@class, 'detaildropdown__section')]"):
        label = _normalize_text(" ".join(section.xpath(".//h4//text()")))
        if not label:
            continue

        link_values = [
            _normalize_text(" ".join(anchor.xpath(".//text()")))
            for anchor in section.xpath(".//a")
        ]
        values = [value for value in link_values if value]
        if not values:
            raw_value = _normalize_text(" ".join(section.xpath(".//p//text()")))
            if raw_value:
                values = [raw_value]
        if not values:
            continue

        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        values = [_truncate_text(value) or "" for value in values]
        values = [value for value in values if value]
        if key == "find_site_description":
            values = [
                _normalize_text(value.split(":", 1)[1] if ":" in value else value)
                for value in values
            ]
        field_map[key] = values if len(values) > 1 else values[0]

    manifest_url = tree.xpath(
        "string(//a[contains(@class, 'socialicon--iiif')]/@href)"
    ).strip()
    thumbnail_url = tree.xpath("string(//meta[@property='og:image']/@content)").strip()
    shelfmark = tree.xpath(
        "string(//div[contains(@class, 'collectionheader__pressmark')]//h1)"
    ).strip()
    material = tree.xpath(
        "string(//div[contains(@class, 'collectionheader__material')]//h2)"
    ).strip()
    item_type = tree.xpath(
        "string(//div[contains(@class, 'collectionheader__form')])"
    ).strip()

    return {
        "manifest_url": manifest_url or f"https://data.idp.bl.uk/iiif/3/manifest/{object_id}",
        "thumbnail_url": thumbnail_url or None,
        "shelfmark": _normalize_text(shelfmark) or object_id,
        "material_header": _normalize_text(material) or None,
        "item_type": _normalize_text(item_type) or None,
        "source_catalog": {
            "object_id": object_id,
            "source_url": viewer_url,
            "iiif_manifest_url": manifest_url or f"https://data.idp.bl.uk/iiif/3/manifest/{object_id}",
            "title": field_map.get("title"),
            "date": field_map.get("date"),
            "find_site": field_map.get("find_site"),
            "measurement": field_map.get("measurement"),
            "material": field_map.get("material") or _normalize_text(material) or None,
            "language_or_script": field_map.get("language_script"),
            "subject": field_map.get("subject"),
            "institution": field_map.get("institution"),
            "provenance": field_map.get("provenance"),
            "find_site_identifier": field_map.get("find_site_identifier"),
            "find_site_description": field_map.get("find_site_description"),
            "short_description": field_map.get("short_description"),
            "references": field_map.get("bibliography"),
            "item_type": _normalize_text(item_type) or None,
            "source_fields": field_map,
        },
    }


def scrape_collection_listing(
    collection: str,
    *,
    delay: float = 0.2,
    max_pages: int = 1,
    limit: int | None = None,
    include_details: bool = True,
) -> list[SourceDocumentRef]:
    collection_def = _collection_map().get(collection)
    if not collection_def:
        available = ", ".join(sorted(_collection_map()))
        raise KeyError(f"Unknown IDP collection {collection!r}. Available: {available}")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Palimpsest discovery adapter)"})

    refs: list[SourceDocumentRef] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        search_url = collection_def.listing_url(page=page)
        response = session.get(search_url, timeout=30)
        response.raise_for_status()
        tree = html.fromstring(response.content)

        links = tree.xpath("//a[@href and contains(@href, '/collection/') and contains(@href, '?return=')]")
        if not links:
            break

        page_added = 0
        for link in links:
            href = link.get("href", "")
            object_id = _extract_object_id(href)
            if not object_id or object_id in seen:
                continue

            viewer_url = urljoin(BASE_URL, href.split("?", 1)[0])
            search_summary = _extract_card_summary(link)

            manifest_url = f"https://data.idp.bl.uk/iiif/3/manifest/{object_id}"
            thumbnail_url = None
            source_catalog: dict[str, Any] | None = {
                "object_id": object_id,
                "search_url": search_url,
                "search_summary": search_summary.get("search_summary"),
                "title": search_summary.get("title"),
                "material": search_summary.get("material"),
                "item_type": search_summary.get("item_type"),
            }
            shelfmark = search_summary.get("shelfmark") or object_id

            if include_details:
                detail_response = session.get(viewer_url, timeout=30)
                detail_response.raise_for_status()
                detail_tree = html.fromstring(detail_response.content)
                details = _parse_detail_page(object_id, viewer_url, detail_tree)
                manifest_url = details["manifest_url"]
                thumbnail_url = details["thumbnail_url"]
                shelfmark = details["shelfmark"] or shelfmark
                source_catalog = details["source_catalog"]
                if search_summary.get("search_summary"):
                    source_catalog["search_summary"] = search_summary["search_summary"]

                if delay:
                    time.sleep(delay)

            refs.append(
                SourceDocumentRef(
                    source_id="idp",
                    collection=collection_def.key,
                    shelfmark=shelfmark,
                    manuscript_id=f"idp_{object_id.lower()}",
                    manifest_url=manifest_url,
                    viewer_url=viewer_url,
                    thumbnail_url=thumbnail_url,
                    quality="high" if manifest_url else "medium",
                    source_catalog=source_catalog,
                    extra={"object_id": object_id, "search_url": search_url},
                )
            )
            seen.add(object_id)
            page_added += 1

            if limit and len(refs) >= limit:
                return refs

        if page_added == 0:
            break

        if delay:
            time.sleep(delay)

    return refs


@dataclass
class IDPSourceAdapter(DiscoverySourceAdapter):
    """Curated search-surface adapter for the International Dunhuang Programme."""

    source_id: str = "idp"
    label: str = "International Dunhuang Programme"
    automation_fit: int = 3
    north_star_fit: int = 4
    access: str = "public_open_web_iiif"

    def list_collections(self) -> list[SourceCollection]:
        return [
            SourceCollection(
                source_id=self.source_id,
                key=collection.key,
                label=collection.label,
                listing_url=collection.listing_url(),
                notes=collection.notes,
                automation_fit=collection.automation_fit,
                north_star_fit=collection.north_star_fit,
                access=collection.access,
            )
            for collection in CURATED_COLLECTIONS
        ]

    def scrape_collection(self, collection: str, **kwargs) -> list[SourceDocumentRef]:
        return scrape_collection_listing(collection, **kwargs)
