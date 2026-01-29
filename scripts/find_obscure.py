#!/usr/bin/env python3
"""
Find obscure manuscripts by having AI analyze metadata.

Reads a CSV of manuscript metadata and asks AI to identify:
- Completely unknown/unidentifiable texts
- Unusual or rare content
- Things that make you go "what the heck?"

Uses cheap batch processing - just text, no images.

Usage:
    python scripts/find_obscure.py --csv discovery/pal_lat_full.csv --output discovery/obscure_candidates.jsonl
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)

from google import genai
from google.genai import types

BATCH_SIZE = 50  # Manuscripts per API call

ANALYSIS_PROMPT = """You are a medieval manuscript scholar. I'm going to show you metadata for {count} Vatican manuscripts.

For each one, rate how OBSCURE or UNUSUAL it is on a scale of 1-5:

1 = WELL-KNOWN: Famous text, standard author, extensively studied (Bible, Avicenna's Canon, Augustine, etc.)
2 = COMMON: Typical medieval content, probably has editions (liturgy, standard theology, common medical texts)
3 = MODERATE: Less common but identifiable, might have some scholarship
4 = UNUSUAL: Rare content, unclear authorship, possibly understudied
5 = OBSCURE/WTF: Completely unclear what this is, mysterious, "what the heck?", possibly unique

Pay special attention to:
- Vague titles like "Codice miscellaneo" with unclear contents
- Unknown or pseudonymous authors
- Unusual subject combinations
- Very short or fragmentary texts
- Anything that seems weird or hard to categorize

Return JSON array with ONLY the manuscripts scoring 4 or 5:
[
  {{"shelfmark": "Pal.lat.XXX", "score": 5, "reason": "brief explanation of why this is obscure/unusual"}}
]

If none score 4-5, return empty array: []

Here are the manuscripts:

{manuscripts}
"""


def format_manuscript(row: dict) -> str:
    """Format a manuscript row for the prompt."""
    parts = [f"**{row['shelfmark']}** ({row.get('pages', '?')} pages)"]

    if row.get("title"):
        parts.append(f"Title: {row['title']}")
    if row.get("author"):
        parts.append(f"Author: {row['author']}")
    if row.get("date"):
        parts.append(f"Date: {row['date']}")
    if row.get("subject"):
        parts.append(f"Subject: {row['subject']}")
    if row.get("description"):
        parts.append(f"Desc: {row['description'][:200]}")

    return "\n".join(parts)


def analyze_batch(rows: list, client: genai.Client, model: str) -> list:
    """Analyze a batch of manuscripts."""
    manuscripts_text = "\n\n---\n\n".join(format_manuscript(r) for r in rows)

    prompt = ANALYSIS_PROMPT.format(count=len(rows), manuscripts=manuscripts_text)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            )
        )

        # Parse JSON response
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        results = json.loads(text)
        return results if isinstance(results, list) else []

    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(description="Find obscure manuscripts via AI analysis")
    parser.add_argument("--csv", required=True, help="Input CSV file")
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--model", default="gemini-2.0-flash", help="Model for analysis")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Manuscripts per API call")
    parser.add_argument("--limit", type=int, help="Limit total manuscripts to analyze")

    args = parser.parse_args()

    # Check API key
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # Load CSV
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Error: {csv_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if args.limit:
        rows = rows[:args.limit]

    print(f"Analyzing {len(rows)} manuscripts in batches of {args.batch_size}...")

    # Process in batches
    all_obscure = []
    batches = [rows[i:i + args.batch_size] for i in range(0, len(rows), args.batch_size)]

    for i, batch in enumerate(batches):
        print(f"  Batch {i+1}/{len(batches)} ({batch[0]['shelfmark']} - {batch[-1]['shelfmark']})...", end=" ", flush=True)

        results = analyze_batch(batch, client, args.model)

        if results:
            print(f"found {len(results)} obscure")
            all_obscure.extend(results)
        else:
            print("none")

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for item in all_obscure:
            item["analyzed_at"] = datetime.now().isoformat()
            f.write(json.dumps(item) + "\n")

    print(f"\n{'='*60}")
    print(f"Found {len(all_obscure)} obscure/unusual manuscripts")
    print(f"Saved to {output_path}")

    if all_obscure:
        print(f"\nTop finds:")
        for item in sorted(all_obscure, key=lambda x: x.get("score", 0), reverse=True)[:10]:
            print(f"  [{item.get('score', '?')}] {item['shelfmark']}: {item.get('reason', '')[:60]}")


if __name__ == "__main__":
    main()
