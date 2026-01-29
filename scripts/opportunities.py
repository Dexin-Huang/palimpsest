#!/usr/bin/env python3
"""
Opportunities module: scan metadata into DB and triage with Gemini Flash.

Usage:
  python scripts/opportunities.py scan --collection "Pal.lat" --range 1200-1300 --db discovery/manuscripts.db
  python scripts/opportunities.py triage --db discovery/manuscripts.db --only-new-days 14 --limit 10
  python scripts/opportunities.py list --db discovery/manuscripts.db --status new --limit 20
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from palimpsest.discovery import DiscoveryDB, Manuscript

DIGI_VATLIB_BASE = "https://digi.vatlib.it"
IIIF_BASE = f"{DIGI_VATLIB_BASE}/iiif"

# Collections in the Vatican Library
COLLECTIONS = {
    "Pal.lat": {"name": "Palatini latini", "range": (1, 2000)},
    "Vat.lat": {"name": "Vaticani latini", "range": (1, 15000)},
    "Borgh": {"name": "Borghesiani", "range": (1, 500)},
    "Reg.lat": {"name": "Reginenses latini", "range": (1, 2100)},
    "Ott.lat": {"name": "Ottoboniani latini", "range": (1, 3500)},
    "Chig": {"name": "Chigiani", "range": (1, 800)},
    "Barb.lat": {"name": "Barberiniani latini", "range": (1, 4000)},
    "Urb.lat": {"name": "Urbinates latini", "range": (1, 1800)},
    "Ross": {"name": "Rossiani", "range": (1, 1200)},
    "Vat.gr": {"name": "Vaticani graeci", "range": (1, 2400)},
    "Pal.gr": {"name": "Palatini graeci", "range": (1, 450)},
    "Vat.ebr": {"name": "Vaticani ebraici", "range": (1, 700)},
    "Vat.ar": {"name": "Vaticani arabici", "range": (1, 1700)},
}

# Keywords that indicate potentially interesting content
INTERESTING_KEYWORDS = {
    # Alchemy & Chemistry
    "alchim": 8, "alquim": 8, "lapis philosophorum": 10, "transmutatio": 9,
    "elixir": 8, "aqua vitae": 7, "distillatio": 6,

    # Magic & Occult
    "magi": 7, "necromant": 9, "daemon": 8, "incantation": 8,
    "talismanic": 8, "astrolog": 6, "geomant": 7, "chiromant": 7,

    # Secret Knowledge
    "secret": 6, "arcana": 7, "occult": 7, "mysterium": 6,
    "cabala": 8, "kabbal": 8, "hermet": 7,

    # Science
    "astronomia": 5, "cosmograph": 5, "mathemat": 4,
    "medicin": 4, "chirurg": 5, "anatom": 5,
    "natural": 4, "experiment": 6, "optic": 5,

    # Unusual
    "monstru": 7, "mirabili": 6, "prodig": 6,
    "prophetia": 6, "apocalyp": 5, "visio": 5,
    "autograph": 6, "original": 5,

    # Languages (rare ones score higher)
    "arabic": 5, "hebraic": 5, "graec": 3,
    "coptic": 7, "syriac": 7, "ethiop": 8,
    "armenian": 6, "persian": 6,

    # Historical figures
    "albertus magnus": 6, "roger bacon": 7, "raimundus lullus": 7,
    "arnaldus": 5, "avicenna": 4, "geber": 7,
}

# Common/boring keywords (reduce score)
BORING_KEYWORDS = {
    "biblia": -3, "missale": -3, "breviarium": -3,
    "psalterium": -2, "evangeliar": -2, "liturgic": -2,
    "homilia": -2, "sermones": -1, "epistola": -1,
    "graduale": -2, "antiphon": -2,
}

PROMPTS_DIR = PROJECT_ROOT / "palimpsest" / "prompts"


def load_prompt(prompt_name: str) -> str:
    path = PROMPTS_DIR / f"{prompt_name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def parse_list_field(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    if isinstance(value, list):
        return value
    parts = []
    for chunk in str(value).replace(";", ",").split(","):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts or None


def fetch_manifest_metadata(shelfmark: str, timeout: int = 15, retries: int = 3) -> Optional[dict]:
    """Fetch just the metadata from a manifest (no images)."""
    mss_id = f"MSS_{shelfmark}"
    url = f"{IIIF_BASE}/{mss_id}/manifest.json"

    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                manifest = resp.json()
                return extract_metadata(manifest, shelfmark)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 404:
                return None
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
            continue
    return None


def extract_metadata(manifest: dict, shelfmark: str) -> dict:
    """Extract relevant metadata from manifest."""
    metadata = {
        "shelfmark": shelfmark,
        "label": manifest.get("label", ""),
        "description": manifest.get("description", ""),
        "attribution": manifest.get("attribution", ""),
        "canvas_count": 0,
        "fields": {},
    }

    sequences = manifest.get("sequences", [])
    if sequences:
        metadata["canvas_count"] = len(sequences[0].get("canvases", []))

    for item in manifest.get("metadata", []):
        label = item.get("label", "")
        value = item.get("value", "")
        if label and value:
            if isinstance(value, list):
                value = "; ".join(str(v) for v in value)
            metadata["fields"][label] = str(value)

    return metadata


def score_metadata(metadata: dict) -> dict:
    """Score manuscript based on metadata content."""
    score = 5
    reasons = []

    all_text = " ".join([
        metadata.get("label", ""),
        metadata.get("description", ""),
        " ".join(str(v) for v in metadata.get("fields", {}).values())
    ]).lower()

    for keyword, points in INTERESTING_KEYWORDS.items():
        if keyword.lower() in all_text:
            score += points
            reasons.append(f"+{points}: {keyword}")

    for keyword, points in BORING_KEYWORDS.items():
        if keyword.lower() in all_text:
            score += points
            reasons.append(f"{points}: {keyword}")

    canvas_count = metadata.get("canvas_count", 0)
    if 20 <= canvas_count <= 100:
        score += 1
        reasons.append("+1: good size (20-100 pages)")
    elif 0 < canvas_count < 20:
        score += 2
        reasons.append("+2: small manuscript")

    score = max(1, min(10, score))

    return {"score": score, "reasons": reasons}


def scan_collection_range(prefix: str, start: int, end: int, parallel: int = 6) -> list:
    """Scan a range of manuscripts in parallel."""
    results = []
    shelfmarks = [f"{prefix}.{n}" for n in range(start, end + 1)]

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {executor.submit(fetch_manifest_metadata, sm): sm for sm in shelfmarks}
        for future in as_completed(futures):
            metadata = future.result()
            if metadata:
                scoring = score_metadata(metadata)
                metadata["initial_score"] = scoring["score"]
                metadata["score_reasons"] = scoring["reasons"]
                results.append(metadata)

    return results


def upsert_from_metadata(db: DiscoveryDB, metadata: dict, min_score: int) -> str:
    """Upsert manuscript and opportunity from metadata scan."""
    now = datetime.utcnow().isoformat() + "Z"
    shelfmark = metadata["shelfmark"]
    prefix = shelfmark.rsplit(".", 1)[0]
    collection_name = COLLECTIONS.get(prefix, {}).get("name", prefix)
    ms_id = f"vat_{shelfmark.lower().replace('.', '_')}"

    title = metadata.get("fields", {}).get("Title", metadata.get("label", ""))
    date_range = metadata.get("fields", {}).get("Date", "")
    languages = parse_list_field(metadata.get("fields", {}).get("Language"))

    ms = db.get_manuscript_by_shelfmark(shelfmark)
    if not ms:
        ms = Manuscript(
            id=ms_id,
            shelfmark=shelfmark,
            repository="BAV",
            collection=collection_name,
            title=title or None,
            date_range=date_range or None,
            languages=languages,
            description=metadata.get("description") or None,
            iiif_manifest_url=f"{IIIF_BASE}/MSS_{shelfmark}/manifest.json",
            canvas_count=metadata.get("canvas_count", 0),
            interest_score=metadata.get("initial_score"),
            discovered_at=now,
            updated_at=now,
        )
        db.add_manuscript(ms, agent="opportunities_scan")
    else:
        updates = {
            "collection": collection_name,
            "title": title or None,
            "date_range": date_range or None,
            "languages": languages,
            "description": metadata.get("description") or None,
            "iiif_manifest_url": f"{IIIF_BASE}/MSS_{shelfmark}/manifest.json",
            "canvas_count": metadata.get("canvas_count", 0),
            "interest_score": metadata.get("initial_score"),
        }
        db.update_manuscript(ms.id, updates, agent="opportunities_scan")
        ms_id = ms.id

    db.ensure_opportunity(ms_id, first_seen_at=now, last_seen_at=now)
    opp = db.get_opportunity(ms_id)

    initial_score = metadata.get("initial_score")
    initial_interest = bool(initial_score >= min_score) if initial_score is not None else None
    db.update_opportunity(ms_id, {
        "initial_score": initial_score,
        "initial_interest": initial_interest,
    })

    return ms_id


def fetch_first_page_image(shelfmark: str, size: int = 1200) -> Optional[bytes]:
    """Download the first content page of a manuscript (skipping covers)."""
    mss_id = f"MSS_{shelfmark}"
    manifest_url = f"{IIIF_BASE}/{mss_id}/manifest.json"

    try:
        resp = requests.get(manifest_url, timeout=30)
        if resp.status_code != 200:
            return None

        manifest = resp.json()
        sequences = manifest.get("sequences", [])
        if not sequences:
            return None

        canvases = sequences[0].get("canvases", [])
        if not canvases:
            return None

        skip_labels = ["piatto", "contropiatto", "risguardia", "dorso", "taglio", "guard"]

        first_canvas = None
        for i, canvas in enumerate(canvases):
            label = str(canvas.get("label", "")).lower()
            if any(skip in label for skip in skip_labels):
                continue

            try:
                service = canvas["images"][0]["resource"].get("service", {})
                service_id = service.get("@id", "").lower()
                if "_cy_" in service_id:
                    continue
                if "_fa_" in service_id or "_f0" in service_id:
                    first_canvas = canvas
                    break
            except (KeyError, IndexError):
                pass

            if first_canvas is None and any(c.isdigit() for c in label) and i >= 5:
                first_canvas = canvas
                break

        if not first_canvas:
            first_canvas = canvases[min(8, len(canvases) - 1)]

        images = first_canvas.get("images", [])
        if not images:
            return None

        resource = images[0].get("resource", {})
        service = resource.get("service", {})
        service_id = service.get("@id", "")

        if not service_id:
            service_id = resource.get("@id", "")
            if service_id:
                service_id = service_id.rsplit("/full/", 1)[0]

        if not service_id:
            return None

        image_url = f"{service_id}/full/{size},/0/default.jpg"
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code == 200:
            return img_resp.content
        return None

    except Exception:
        return None


def triage_with_gemini(
    client: genai.Client,
    shelfmark: str,
    metadata: dict,
    model: str,
    prompt_name: str,
    image_bytes: Optional[bytes] = None,
) -> dict:
    """Run Gemini Flash triage and return parsed JSON."""
    meta_lines = [
        f"Shelfmark: {shelfmark}",
        f"Title: {metadata.get('title', '')}",
        f"Date: {metadata.get('date_range', '')}",
        f"Languages: {', '.join(metadata.get('languages') or [])}",
        f"Pages: {metadata.get('canvas_count', '')}",
        f"Description: {metadata.get('description', '')}",
        f"First seen: {metadata.get('first_seen_at', '')}",
        f"Last seen: {metadata.get('last_seen_at', '')}",
    ]
    prompt = load_prompt(prompt_name) + "\n\nMetadata:\n" + "\n".join(meta_lines)

    contents = [prompt]
    if image_bytes:
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        contents.append(image_part)

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    return json.loads(response.text)


def list_opportunities(db: DiscoveryDB, status: Optional[str], limit: int) -> None:
    rows = db.list_opportunities(status=status, limit=limit)
    if not rows:
        print("No opportunities found.")
        return

    for opp in rows:
        print(f"{opp.manuscript_id}  status={opp.status}  initial_score={opp.initial_score}  interest_score={opp.interest_score}")


def run_scan(args: argparse.Namespace) -> None:
    if not args.collection or not args.range:
        print("scan requires --collection and --range")
        return

    start, end = map(int, args.range.split("-"))
    results = scan_collection_range(args.collection, start, end, parallel=args.parallel)

    db = DiscoveryDB(args.db)
    created = 0
    for meta in results:
        ms_id = upsert_from_metadata(db, meta, args.min_score)
        if ms_id:
            created += 1

    print(f"Upserted {created} manuscripts into {args.db}")


def run_triage(args: argparse.Namespace) -> None:
    # Load env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    db = DiscoveryDB(args.db)

    # Build query
    params = []
    query = """
        SELECT o.*, m.shelfmark, m.title, m.date_range, m.languages, m.description, m.canvas_count
        FROM opportunities o
        JOIN manuscripts m ON o.manuscript_id = m.id
        WHERE 1=1
    """

    if args.status:
        query += " AND o.status = ?"
        params.append(args.status)
    if args.min_initial_score is not None:
        query += " AND o.initial_score >= ?"
        params.append(args.min_initial_score)
    if args.only_new_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=args.only_new_days)
        query += " AND o.first_seen_at >= ?"
        params.append(cutoff.isoformat() + "Z")

    query += " ORDER BY o.initial_score DESC, o.first_seen_at DESC LIMIT ?"
    params.append(args.limit)

    cursor = db.conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    if not rows:
        print("No opportunities to triage.")
        return

    for row in rows:
        shelfmark = row["shelfmark"]
        print(f"Triage {shelfmark}...")

        image_bytes = None
        if args.use_image:
            image_bytes = fetch_first_page_image(shelfmark, size=args.image_size)
            if not image_bytes:
                print(f"  Skipped: could not fetch first page for {shelfmark}")
                continue

        metadata = {
            "title": row["title"],
            "date_range": row["date_range"],
            "languages": json.loads(row["languages"]) if row["languages"] else None,
            "description": row["description"],
            "canvas_count": row["canvas_count"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        }

        now = datetime.utcnow().isoformat() + "Z"
        try:
            result = triage_with_gemini(
                client,
                shelfmark,
                metadata,
                args.model,
                args.prompt,
                image_bytes=image_bytes,
            )
        except Exception as e:
            print(f"  Gemini error: {e}")
            continue

        # Normalize fields
        interest_score = result.get("interest_score")
        if isinstance(interest_score, str) and interest_score.isdigit():
            interest_score = int(interest_score)
        if isinstance(interest_score, (int, float)):
            interest_score = max(0, min(10, int(interest_score)))
        else:
            interest_score = None

        db.update_opportunity(row["manuscript_id"], {
            "interest_score": interest_score,
            "interest_reason": "; ".join(result.get("reasons", [])) if isinstance(result.get("reasons"), list) else result.get("interest_reason"),
            "triage_method": "gemini_flash",
            "triage_model": args.model,
            "triage_at": now,
            "triage_json": json.dumps(result),
            "status": "triaged",
        })

        db.log_action(row["manuscript_id"], "triaged", "gemini_flash", {
            "interest_score": interest_score,
            "recommendation": result.get("recommendation")
        })

        print(f"  Done: score={interest_score}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Opportunities module CLI")
    sub = parser.add_subparsers(dest="cmd")

    scan = sub.add_parser("scan", help="Scan IIIF metadata into the DB")
    scan.add_argument("--collection", help="Collection prefix (e.g., Pal.lat)")
    scan.add_argument("--range", help="Number range (e.g., 1200-1300)")
    scan.add_argument("--parallel", type=int, default=4, help="Parallel requests")
    scan.add_argument("--min-score", type=int, default=6, help="Initial interest score cutoff")
    scan.add_argument("--db", default="discovery/manuscripts.db", help="DB path")

    triage = sub.add_parser("triage", help="Run Gemini Flash triage")
    triage.add_argument("--db", default="discovery/manuscripts.db", help="DB path")
    triage.add_argument("--model", default="gemini-3-flash-preview", help="Gemini model")
    triage.add_argument("--prompt", default="opportunity_triage", help="Prompt file name (no extension)")
    triage.add_argument("--status", default="new", help="Opportunity status filter")
    triage.add_argument("--min-initial-score", type=int, default=6, help="Minimum initial score")
    triage.add_argument("--only-new-days", type=int, help="Only items first seen in last N days")
    triage.add_argument("--limit", type=int, default=10, help="Max items to triage")
    triage.add_argument("--use-image", action="store_true", help="Include first page image (optional)")
    triage.add_argument("--image-size", type=int, default=1200, help="IIIF image size")

    list_cmd = sub.add_parser("list", help="List opportunities")
    list_cmd.add_argument("--db", default="discovery/manuscripts.db", help="DB path")
    list_cmd.add_argument("--status", help="Filter by status")
    list_cmd.add_argument("--limit", type=int, default=20, help="Max items")

    args = parser.parse_args()

    if args.cmd == "scan":
        run_scan(args)
    elif args.cmd == "triage":
        run_triage(args)
    elif args.cmd == "list":
        db = DiscoveryDB(args.db)
        list_opportunities(db, status=args.status, limit=args.limit)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
