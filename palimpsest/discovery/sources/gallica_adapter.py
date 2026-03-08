from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests

from .adapter import DiscoverySourceAdapter, SourceCollection, SourceDocumentRef


BASE_URL = "https://gallica.bnf.fr"
SRU_URL = f"{BASE_URL}/SRU"
DEFAULT_PAGE_SIZE = 25
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

SRU_NS = {"srw": "http://www.loc.gov/zing/srw/"}
DC_NS = "{http://purl.org/dc/elements/1.1/}"
OAIDC_NS = "{http://www.openarchives.org/OAI/2.0/oai_dc/}"


@dataclass(frozen=True)
class GallicaCollectionDefinition:
    key: str
    label: str
    query: str
    notes: str | None = None
    automation_fit: int | None = None
    north_star_fit: int | None = None
    access: str | None = None

    def listing_url(self, *, start_record: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> str:
        params = {
            "operation": "searchRetrieve",
            "version": "1.2",
            "query": self.query,
            "maximumRecords": str(page_size),
            "startRecord": str(start_record),
        }
        return f"{SRU_URL}?{urlencode(params)}"


CURATED_COLLECTIONS: tuple[GallicaCollectionDefinition, ...] = (
    GallicaCollectionDefinition(
        key="chinese_manuscripts",
        label="Chinese Manuscripts",
        query='(dc.type all "manuscrit") and ((gallica all "chine") or (gallica all "chinois"))',
        notes="Broad Chinese-manuscript lane inside Gallica's public SRU/IIIF stack.",
        automation_fit=4,
        north_star_fit=4,
        access="public_sru_iiif",
    ),
    GallicaCollectionDefinition(
        key="chinese_medicine",
        label="Chinese Medicine Manuscripts",
        query='(dc.type all "manuscrit") and ((gallica all "chine") or (gallica all "chinois")) and ((gallica all "medecine") or (gallica all "médicine") or (gallica all "pharmacopée"))',
        notes="High-signal lane for Chinese practical and medical manuscript material.",
        automation_fit=4,
        north_star_fit=5,
        access="public_sru_iiif",
    ),
    GallicaCollectionDefinition(
        key="chinese_divination",
        label="Chinese Divination Manuscripts",
        query='(dc.type all "manuscrit") and ((gallica all "chine") or (gallica all "chinois")) and ((gallica all "divination") or (gallica all "trigrammes") or (gallica all "yi king"))',
        notes="Chinese divination, omen, and trigram-rich manuscript lane.",
        automation_fit=4,
        north_star_fit=5,
        access="public_sru_iiif",
    ),
    GallicaCollectionDefinition(
        key="tibetan_manuscripts",
        label="Tibetan Manuscripts",
        query='(dc.type all "manuscrit") and ((gallica all "tibet") or (gallica all "tibétain"))',
        notes="Broad Tibetan manuscript lane for public Gallica holdings.",
        automation_fit=4,
        north_star_fit=4,
        access="public_sru_iiif",
    ),
    GallicaCollectionDefinition(
        key="japanese_manuscripts",
        label="Japanese Manuscripts",
        query='(dc.type all "manuscrit") and ((gallica all "japon") or (gallica all "japonais"))',
        notes="Broad Japanese manuscript lane inside Gallica.",
        automation_fit=4,
        north_star_fit=4,
        access="public_sru_iiif",
    ),
    GallicaCollectionDefinition(
        key="southeast_asian_manuscripts",
        label="Southeast Asian Manuscripts",
        query='(dc.type all "manuscrit") and ((gallica all "annam") or (gallica all "vietnam") or (gallica all "siam") or (gallica all "cambodge") or (gallica all "laos"))',
        notes="A narrower lane for Southeast Asian manuscript witnesses in Gallica.",
        automation_fit=4,
        north_star_fit=4,
        access="public_sru_iiif",
    ),
)


def _collection_map() -> dict[str, GallicaCollectionDefinition]:
    return {collection.key: collection for collection in CURATED_COLLECTIONS}


def _first_text(root: ET.Element, tag: str) -> str | None:
    node = root.find(f"{DC_NS}{tag}")
    if node is None or not node.text:
        return None
    return re.sub(r"\s+", " ", node.text).strip() or None


def _all_texts(root: ET.Element, tag: str) -> list[str]:
    values = []
    for node in root.findall(f"{DC_NS}{tag}"):
        if node.text:
            text = re.sub(r"\s+", " ", node.text).strip()
            if text:
                values.append(text)
    return values


def _ark_from_identifier(values: Iterable[str]) -> str | None:
    for value in values:
        match = re.search(r"ark:/12148/([^/?#]+)", value)
        if match:
            return match.group(1)
    return None


def _extract_view_count(formats: Iterable[str]) -> int | None:
    for value in formats:
        match = re.search(r"Nombre total de vues\s*:\s*(\d+)", value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _build_source_catalog(dc_root: ET.Element, *, viewer_url: str, manifest_url: str, ark_id: str) -> dict:
    titles = _all_texts(dc_root, "title")
    creators = _all_texts(dc_root, "creator")
    descriptions = _all_texts(dc_root, "description")
    languages = _all_texts(dc_root, "language")
    formats = _all_texts(dc_root, "format")
    relations = _all_texts(dc_root, "relation")
    rights = _all_texts(dc_root, "rights")
    types = _all_texts(dc_root, "type")

    return {
        "ark_id": ark_id,
        "source_url": viewer_url,
        "iiif_manifest_url": manifest_url,
        "title": titles[0] if titles else None,
        "creator": creators,
        "date": _first_text(dc_root, "date"),
        "description": descriptions,
        "language_or_script": languages,
        "format": formats,
        "relation": relations,
        "rights": rights,
        "type": types,
        "source": _first_text(dc_root, "source"),
        "publisher": _first_text(dc_root, "publisher"),
        "view_count": _extract_view_count(formats),
    }


def scrape_collection_listing(
    collection: str,
    *,
    max_pages: int = 1,
    limit: int | None = None,
    delay: float = 0.2,
    page_size: int = DEFAULT_PAGE_SIZE,
    **_: object,
) -> list[SourceDocumentRef]:
    collection_def = _collection_map().get(collection)
    if not collection_def:
        available = ", ".join(sorted(_collection_map()))
        raise KeyError(f"Unknown Gallica collection {collection!r}. Available: {available}")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    refs: list[SourceDocumentRef] = []
    seen: set[str] = set()

    for page_index in range(max_pages):
        start_record = page_index * page_size + 1
        response = session.get(
            collection_def.listing_url(start_record=start_record, page_size=page_size),
            timeout=30,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        records = root.findall(".//srw:record", SRU_NS)
        if not records:
            break

        page_added = 0
        for record in records:
            dc_root = record.find(f".//{OAIDC_NS}dc")
            if dc_root is None:
                continue

            identifiers = _all_texts(dc_root, "identifier")
            ark_id = _ark_from_identifier(identifiers)
            if not ark_id or ark_id in seen:
                continue

            viewer_url = f"{BASE_URL}/ark:/12148/{ark_id}"
            manifest_url = f"{BASE_URL}/iiif/ark:/12148/{ark_id}/manifest.json"
            source_catalog = _build_source_catalog(
                dc_root,
                viewer_url=viewer_url,
                manifest_url=manifest_url,
                ark_id=ark_id,
            )
            source_catalog["query"] = collection_def.query

            source = source_catalog.get("source") or ""
            shelfmark = source.split(",")[-1].strip() if "," in source else source.strip()
            if not shelfmark:
                shelfmark = ark_id

            refs.append(
                SourceDocumentRef(
                    source_id="gallica",
                    collection=collection_def.key,
                    shelfmark=shelfmark,
                    manuscript_id=f"gallica_{ark_id.lower()}",
                    manifest_url=manifest_url,
                    viewer_url=viewer_url,
                    thumbnail_url=None,
                    quality="high",
                    source_catalog=source_catalog,
                    extra={"ark_id": ark_id},
                )
            )
            seen.add(ark_id)
            page_added += 1

            if limit and len(refs) >= limit:
                return refs

        if page_added == 0:
            break

        if delay:
            import time

            time.sleep(delay)

    return refs


@dataclass
class GallicaSourceAdapter(DiscoverySourceAdapter):
    """Curated Gallica adapter using the public SRU search plus IIIF manifests."""

    source_id: str = "gallica"
    label: str = "Gallica / Bibliothèque nationale de France"
    automation_fit: int = 4
    north_star_fit: int = 4
    access: str = "public_sru_iiif"

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
