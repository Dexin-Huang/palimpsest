"""Vatican Digital Library collection listing scraper.

Instead of iterating through number ranges, this module scrapes the
collection listing pages at https://digi.vatlib.it/mss/ to get a
complete inventory of digitized manuscripts.

Usage:
    from palimpsest.discovery.sources.vatican_listings import scrape_collection_listing

    # Get all manuscripts in a collection
    items = scrape_collection_listing("Pal.lat")

    # Get available collections
    collections = get_available_collections()
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import requests
from lxml import html


BASE_URL = "https://digi.vatlib.it"
COLLECTIONS_URL = f"{BASE_URL}/mss"


@dataclass
class ListingItem:
    """A single manuscript from collection listing."""

    shelfmark: str
    manuscript_id: str
    manifest_url: str
    viewer_url: str
    collection: str
    thumbnail_url: Optional[str] = None
    quality: Optional[str] = None  # "high" or "low" from class


def _normalize_shelfmark(raw: str) -> str:
    """Normalize a shelfmark extracted from listing."""
    # Clean up whitespace and common formatting issues
    return re.sub(r"\s+", " ", raw.strip())


def _shelfmark_to_manuscript_id(shelfmark: str) -> str:
    """Convert shelfmark to manuscript_id format."""
    # e.g., "Pal.lat.1267" -> "vat_pal_lat_1267"
    normalized = shelfmark.lower()
    normalized = re.sub(r"[.\s]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return f"vat_{normalized}"


def _extract_mss_identifier(href: str) -> Optional[str]:
    """Extract MSS_* identifier from URL path."""
    match = re.search(r"(MSS_[^/]+)", href)
    return match.group(1) if match else None


def scrape_collection_listing(
    collection: str,
    delay: float = 0.5,
    max_pages: Optional[int] = None,
) -> list[ListingItem]:
    """Scrape all manuscripts from a Vatican collection listing.

    Args:
        collection: Collection prefix (e.g., "Pal.lat", "Vat.lat")
        delay: Delay between page requests
        max_pages: Maximum number of listing pages to fetch (for testing)

    Returns:
        List of ListingItem objects
    """
    items: list[ListingItem] = []
    page = 1
    collection_url = f"{COLLECTIONS_URL}/{collection}"

    while True:
        if max_pages and page > max_pages:
            break

        url = f"{collection_url}?pg={page}" if page > 1 else collection_url

        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            break

        tree = html.fromstring(resp.content)

        # Find manuscript items - Vatican uses a thumbnail list layout
        # XPath targets: //div[@class='item-thumbnail-list']//a
        links = tree.xpath(
            "//div[contains(@class, 'item-thumbnail-list')]//a[@href]"
        )

        if not links:
            # Try alternate structure
            links = tree.xpath("//div[contains(@class, 'item')]//a[@href]")

        if not links:
            break

        found_new = False
        for link in links:
            href = link.get("href", "")
            if not href or "/view/" not in href:
                continue

            mss_id = _extract_mss_identifier(href)
            if not mss_id:
                continue

            # Extract shelfmark from link text or title
            text = link.text_content().strip() if hasattr(link, "text_content") else ""
            title = link.get("title", "")
            shelfmark = _normalize_shelfmark(title or text)

            if not shelfmark:
                # Try to derive from MSS_id
                # MSS_Pal.lat.1267 -> Pal.lat.1267
                shelfmark = mss_id.replace("MSS_", "").replace("_", ".")

            manuscript_id = _shelfmark_to_manuscript_id(shelfmark)

            # Check for quality indicator
            classes = link.get("class", "")
            quality = "low" if "low-quality" in classes else "high"

            # Build URLs
            viewer_url = f"{BASE_URL}{href}" if href.startswith("/") else href
            manifest_url = f"{BASE_URL}/iiif/{mss_id}/manifest.json"

            # Try to get thumbnail
            img = link.find(".//img")
            thumbnail_url = None
            if img is not None:
                src = img.get("src", "")
                if src:
                    thumbnail_url = f"{BASE_URL}{src}" if src.startswith("/") else src

            items.append(
                ListingItem(
                    shelfmark=shelfmark,
                    manuscript_id=manuscript_id,
                    manifest_url=manifest_url,
                    viewer_url=viewer_url,
                    collection=collection,
                    thumbnail_url=thumbnail_url,
                    quality=quality,
                )
            )
            found_new = True

        if not found_new:
            break

        # Check for next page
        next_link = tree.xpath("//a[contains(@class, 'next') or contains(text(), 'Next')]")
        if not next_link:
            # Also check for pagination numbers
            page_links = tree.xpath(f"//a[contains(@href, 'pg={page + 1}')]")
            if not page_links:
                break

        page += 1
        if delay:
            time.sleep(delay)

    # Deduplicate by manuscript_id
    seen = set()
    unique_items = []
    for item in items:
        if item.manuscript_id not in seen:
            seen.add(item.manuscript_id)
            unique_items.append(item)

    return unique_items


def get_available_collections(delay: float = 0.5) -> list[dict]:
    """Get list of available manuscript collections from Vatican Digital Library.

    Returns:
        List of dicts with collection name and URL
    """
    try:
        resp = requests.get(COLLECTIONS_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    tree = html.fromstring(resp.content)

    # Look for collection links
    collections: list[dict] = []
    links = tree.xpath("//a[@href]")

    for link in links:
        href = link.get("href", "")
        if "/mss/" not in href:
            continue

        # Extract collection name from URL
        match = re.search(r"/mss/([^/?]+)", href)
        if not match:
            continue

        collection_name = match.group(1)
        text = link.text_content().strip() if hasattr(link, "text_content") else ""

        collections.append(
            {
                "name": collection_name,
                "label": text or collection_name,
                "url": f"{BASE_URL}{href}" if href.startswith("/") else href,
            }
        )

    # Deduplicate
    seen = set()
    unique = []
    for coll in collections:
        if coll["name"] not in seen:
            seen.add(coll["name"])
            unique.append(coll)

    return unique


def scrape_all_collections(
    collections: Optional[list[str]] = None,
    delay: float = 0.5,
    verbose: bool = True,
) -> Iterator[ListingItem]:
    """Scrape manuscripts from multiple collections.

    Args:
        collections: List of collection names. If None, scrapes all available.
        delay: Delay between requests
        verbose: Print progress

    Yields:
        ListingItem objects
    """
    if collections is None:
        available = get_available_collections(delay=delay)
        collections = [c["name"] for c in available]

    for collection in collections:
        if verbose:
            print(f"Scraping collection: {collection}")

        items = scrape_collection_listing(collection, delay=delay)

        if verbose:
            print(f"  Found {len(items)} manuscripts")

        for item in items:
            yield item

        if delay:
            time.sleep(delay)


def listing_to_record(item: ListingItem) -> dict:
    """Convert ListingItem to registry record format."""
    discovered_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    return {
        "manuscript_id": item.manuscript_id,
        "shelfmark": item.shelfmark,
        "repository": "BAV",
        "collection": item.collection,
        "iiif": {
            "manifest_url": item.manifest_url,
            "viewer_url": item.viewer_url,
        },
        "discovery": {
            "discovered_date": discovered_at.split("T")[0],
            "first_seen_at": discovered_at,
            "last_seen_at": discovered_at,
            "discovered_by": "vatican_listings.py",
            "method": "listing_scrape",
            "quality": item.quality,
        },
        "status": "discovered",
    }


def save_inventory_jsonl(items: list[ListingItem], output_path: Path) -> int:
    """Save inventory to JSONL file.

    Args:
        items: List of ListingItem objects
        output_path: Path to output file

    Returns:
        Number of records written
    """
    import json

    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with output_path.open("w", encoding="utf-8") as f:
        for item in items:
            record = listing_to_record(item)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    return count
