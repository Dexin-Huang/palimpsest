"""Enrichment pipeline for manuscript discovery.

Gathers signals from various sources to determine if a manuscript
is truly unstudied vs. just poorly cataloged.

Sources:
- IIIF manifest metadata analysis
- VIAF (Virtual International Authority File) for author lookup
- Google Search for web mentions
- Europeana API for aggregation status
- Text analysis for common text detection
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import quote_plus

import requests

from palimpsest.discovery.database import Enrichment


# Common text patterns to detect (deprioritize these)
COMMON_TEXT_PATTERNS = {
    "physiologus": [r"physiolog", r"bestiar"],
    "bible": [r"biblia", r"evangeli", r"genesis", r"exodus", r"psalm"],
    "psalter": [r"psalter", r"psalterii"],
    "liturgical": [r"missal", r"gradual", r"breviar", r"antiphon", r"sacramentar"],
    "patristic": [r"augustin", r"hieronym", r"gregor.*magn", r"ambros"],
    "classical": [r"virgil", r"vergil", r"ovid", r"cicero", r"boethi"],
    "hagiography": [r"legenda.*aurea", r"vita.*sanct", r"passio"],
    "book_of_hours": [r"horae", r"book.*hours", r"livre.*heures"],
}

# Original content signals (prioritize these)
ORIGINAL_CONTENT_PATTERNS = [
    r"epistol",          # letters
    r"registr",          # registers
    r"cartular",         # charters
    r"computus",         # calculations/accounts
    r"recept",           # recipes
    r"experiment",       # experiments
    r"notae",            # notes
    r"journal",          # journals
    r"diari",            # diaries
    r"chronic",          # chronicles (local)
    r"annales",          # annals
    r"formula",          # formularies
    r"glossar",          # glossaries (can be unique)
    r"herbar",           # herbals
    r"alchim",           # alchemy
    r"astrol",           # astrology
    r"medic",            # medical
    r"chirurg",          # surgical
    r"agricult",         # agricultural
]


@dataclass
class GoogleSearchResult:
    """Result from Google search."""
    total_hits: int
    top_results: List[Dict[str, str]]  # [{title, url, snippet}]
    scholar_hits: int


def analyze_manifest_metadata(manifest: dict) -> dict:
    """Analyze IIIF manifest for metadata completeness.

    Returns:
        Dict with metadata_fields count and specific field presence
    """
    fields_present = 0
    has_fields = {}

    # Check common IIIF metadata fields
    check_fields = [
        "label", "description", "attribution", "metadata",
        "navDate", "logo", "license", "related", "seeAlso",
    ]

    for field in check_fields:
        if manifest.get(field):
            fields_present += 1
            has_fields[field] = True

    # Check metadata array for specific fields
    metadata = manifest.get("metadata", [])
    if isinstance(metadata, list):
        for item in metadata:
            label = item.get("label", "")
            if isinstance(label, dict):
                label = label.get("@value", "") or list(label.values())[0] if label else ""
            label_lower = str(label).lower()

            if "author" in label_lower or "creator" in label_lower:
                has_fields["has_author"] = True
                fields_present += 1
            if "date" in label_lower:
                has_fields["has_date"] = True
                fields_present += 1
            if "language" in label_lower:
                has_fields["has_language"] = True
                fields_present += 1
            if "subject" in label_lower:
                has_fields["has_subject"] = True
                fields_present += 1
            if "bibliography" in label_lower or "reference" in label_lower:
                has_fields["has_bibliography"] = True
                fields_present += 1
            if "incipit" in label_lower:
                has_fields["has_incipit"] = True
                fields_present += 1

    return {
        "metadata_fields": fields_present,
        "has_bibliography": has_fields.get("has_bibliography", False),
        "has_incipit": has_fields.get("has_incipit", False),
        "has_author": has_fields.get("has_author", False),
    }


def detect_common_text(title: str, description: str = "") -> Optional[str]:
    """Detect if title/description indicates a common text type.

    Returns:
        Text type key (e.g., "physiologus") or None
    """
    combined = f"{title} {description}".lower()

    for text_type, patterns in COMMON_TEXT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return text_type

    return None


def detect_original_content_signals(title: str, description: str = "") -> List[str]:
    """Detect signals that suggest original/unique content.

    Returns:
        List of detected signal keywords
    """
    combined = f"{title} {description}".lower()
    signals = []

    for pattern in ORIGINAL_CONTENT_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            signals.append(pattern.replace(r".*", ""))

    return signals


def search_viaf(author_name: str, timeout: int = 10) -> dict:
    """Search VIAF for author name.

    Args:
        author_name: Name to search for

    Returns:
        Dict with in_viaf (bool) and viaf_id (str or None)
    """
    if not author_name or len(author_name) < 3:
        return {"in_viaf": None, "viaf_id": None}

    try:
        # VIAF auto-suggest API
        url = f"https://viaf.org/viaf/AutoSuggest?query={quote_plus(author_name)}"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        data = response.json()
        results = data.get("result", [])

        if results:
            # Check if any result is a good match
            for result in results[:5]:
                term = result.get("term", "").lower()
                if author_name.lower() in term or term in author_name.lower():
                    return {
                        "in_viaf": True,
                        "viaf_id": result.get("viafid"),
                    }

        return {"in_viaf": False, "viaf_id": None}

    except Exception:
        return {"in_viaf": None, "viaf_id": None}


def search_europeana(shelfmark: str, api_key: str = "", timeout: int = 10) -> bool:
    """Check if manuscript appears in Europeana.

    Note: Requires API key for full access. Without key, returns None.
    """
    if not api_key:
        return None

    try:
        query = quote_plus(shelfmark)
        url = f"https://api.europeana.eu/record/v2/search.json?query={query}&wskey={api_key}"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        data = response.json()
        return data.get("totalResults", 0) > 0

    except Exception:
        return None


def search_google(query: str, site_filter: str = "") -> GoogleSearchResult:
    """Search Google for mentions of manuscript.

    This is a placeholder - in production you'd use:
    - SerpApi (paid)
    - Google Custom Search API (limited free tier)
    - Or feed to LLM with web search capability

    Returns:
        GoogleSearchResult with hit counts
    """
    # This will be called by the triage LLM with web search capability
    # For now, return empty result
    return GoogleSearchResult(
        total_hits=0,
        top_results=[],
        scholar_hits=0,
    )


def compute_studied_score(enrichment: Enrichment) -> int:
    """Compute a 0-10 'studied' score from enrichment signals.

    Higher = more studied (less interesting for discovery)
    Lower = less studied (more interesting)
    """
    score = 5  # Start neutral

    # Decrease score (less studied = more interesting)
    if enrichment.google_scholar_hits == 0:
        score -= 2
    if enrichment.google_search_hits is not None and enrichment.google_search_hits < 10:
        score -= 1
    if not enrichment.in_viaf:
        score -= 1  # Unknown author
    if not enrichment.in_europeana:
        score -= 1  # Not aggregated
    if enrichment.manifest_metadata_fields is not None and enrichment.manifest_metadata_fields < 5:
        score -= 1  # Sparse metadata
    if enrichment.original_content_signals:
        score -= len(enrichment.original_content_signals)  # Original content signals

    # Increase score (more studied = less interesting)
    if enrichment.google_scholar_hits and enrichment.google_scholar_hits > 5:
        score += 2
    if enrichment.google_search_hits and enrichment.google_search_hits > 100:
        score += 2
    if enrichment.transcription_exists:
        score += 3
    if enrichment.common_text_detected:
        score += 2  # Common text type
    if enrichment.has_bibliography:
        score += 1
    if enrichment.in_viaf and enrichment.author_famous:
        score += 1

    # Clamp to 0-10
    return max(0, min(10, score))


def enrich_from_manifest(
    manuscript_id: str,
    manifest: dict,
    title: str = "",
    author: str = "",
    description: str = "",
) -> Enrichment:
    """Create enrichment record from IIIF manifest and metadata.

    This does local analysis only (no API calls).
    """
    # Analyze manifest
    manifest_analysis = analyze_manifest_metadata(manifest)

    # Detect text types
    common_text = detect_common_text(title, description)
    original_signals = detect_original_content_signals(title, description)

    # Count description words (proxy for catalog depth)
    description_words = len(description.split()) if description else 0

    enrichment = Enrichment(
        manuscript_id=manuscript_id,
        catalog_description_words=description_words,
        manifest_metadata_fields=manifest_analysis["metadata_fields"],
        has_bibliography=manifest_analysis["has_bibliography"],
        has_incipit=manifest_analysis["has_incipit"],
        common_text_detected=common_text,
        original_content_signals=original_signals if original_signals else None,
    )

    return enrichment


def enrich_with_apis(
    enrichment: Enrichment,
    author: str = "",
    shelfmark: str = "",
    europeana_key: str = "",
) -> Enrichment:
    """Enhance enrichment with API calls (VIAF, Europeana).

    Call this separately as it makes network requests.
    """
    # VIAF lookup
    if author:
        viaf_result = search_viaf(author)
        enrichment.in_viaf = viaf_result["in_viaf"]
        enrichment.viaf_id = viaf_result["viaf_id"]
        # If in VIAF, probably famous
        enrichment.author_famous = viaf_result["in_viaf"]

    # Europeana lookup
    if shelfmark and europeana_key:
        enrichment.in_europeana = search_europeana(shelfmark, europeana_key)

    # Compute studied score
    enrichment.studied_score = compute_studied_score(enrichment)

    return enrichment


def build_triage_context(
    manuscript_id: str,
    shelfmark: str,
    title: str,
    author: str,
    description: str,
    date_range: str,
    languages: List[str],
    canvas_count: int,
    enrichment: Optional[Enrichment],
) -> str:
    """Build context string for LLM triage.

    This is what gets passed to the triage prompt.
    """
    context = {
        "manuscript_id": manuscript_id,
        "shelfmark": shelfmark,
        "title": title,
        "author": author,
        "description": description,
        "date_range": date_range,
        "languages": languages,
        "canvas_count": canvas_count,
    }

    if enrichment:
        context["enrichment"] = {
            "catalog_description_words": enrichment.catalog_description_words,
            "manifest_metadata_fields": enrichment.manifest_metadata_fields,
            "has_bibliography": enrichment.has_bibliography,
            "has_incipit": enrichment.has_incipit,
            "in_viaf": enrichment.in_viaf,
            "viaf_id": enrichment.viaf_id,
            "in_europeana": enrichment.in_europeana,
            "google_scholar_hits": enrichment.google_scholar_hits,
            "google_search_hits": enrichment.google_search_hits,
            "transcription_exists": enrichment.transcription_exists,
            "common_text_detected": enrichment.common_text_detected,
            "original_content_signals": enrichment.original_content_signals,
            "author_famous": enrichment.author_famous,
            "studied_score": enrichment.studied_score,
        }

    return json.dumps(context, indent=2, ensure_ascii=False)
