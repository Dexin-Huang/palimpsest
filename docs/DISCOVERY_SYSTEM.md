# Vatican Manuscript Discovery System

A cost-effective system for finding interesting/obscure manuscripts in the Vatican's digital collections.

## Overview

The Vatican Library has **millions** of pages across **tens of thousands** of digitized manuscripts. This system helps identify the most interesting ones without burning through API credits.

### Two-Stage Approach

1. **Metadata Scan** (FREE) - Crawl IIIF manifests for keywords, authors, subjects
2. **WTF Analysis** (API credits) - Use Gemini only on the top candidates

## Tools

### 1. Metadata Scanner (`scripts/scan_vatican_metadata.py`)

Scans Vatican IIIF manifests for interesting content based on:
- Keywords (alchemy, magic, medicine, secrets, etc.)
- Authors (Albertus Magnus, Avicenna, Arnaldus de Villanova, etc.)
- Content type scoring (scientific > literary > liturgical)

```bash
# List available collections
python scripts/scan_vatican_metadata.py --list-collections

# Scan a range of manuscripts
python scripts/scan_vatican_metadata.py --collection "Pal.lat" --range 1000-1300 --min-score 6

# Search for specific keywords
python scripts/scan_vatican_metadata.py --collection "Vat.lat" --range 1-500 --keywords "alchemia,secretis"
```

**Cost**: FREE (HTTP requests only)

### 2. WTF Analyzer (`scripts/wtf_analyzer.py`)

Downloads first page and uses Gemini to identify:
- Language and script type
- Content type and date estimate
- Specific work/author identification
- "WTF Score" (1-10 unusualness rating)

```bash
# Analyze a single manuscript
python scripts/wtf_analyzer.py --shelfmark "Pal.lat.1177"

# Batch analyze top candidates from discovery file
python scripts/wtf_analyzer.py --batch discovery/registry/interesting.jsonl --top 5
```

**Cost**: ~$0.01 per manuscript (one Gemini call)

### 3. Collection Explorer (`scripts/explore_vatican.py`)

Quick checks for digitization status and basic manifest info.

```bash
python scripts/explore_vatican.py --check "Pal.lat.1267"
```

## Vatican Collections

| Prefix | Name | Est. Range | Notes |
|--------|------|-----------|-------|
| Pal.lat | Palatini latini | 1-2000 | German Palatinate library - MANY scientific texts |
| Vat.lat | Vaticani latini | 1-15000 | Main Vatican Latin - largest collection |
| Reg.lat | Reginenses latini | 1-2100 | Queen Christina of Sweden's collection |
| Barb.lat | Barberiniani latini | 1-4000 | Barberini family |
| Ott.lat | Ottoboniani latini | 1-3500 | Ottoboni family |
| Urb.lat | Urbinates latini | 1-1800 | Urbino ducal library |
| Vat.gr | Vaticani graeci | 1-2400 | Greek manuscripts |
| Vat.ebr | Vaticani ebraici | 1-700 | Hebrew manuscripts |
| Vat.ar | Vaticani arabici | 1-1700 | Arabic manuscripts |

## Keyword Scoring

Interesting keywords (positive scores):
- `alchim*`, `lapis philosophorum`, `transmutatio` (+8-10)
- `magi*`, `necromant*`, `daemon*`, `cabala*` (+7-9)
- `secret*`, `arcana*`, `occult*`, `mysterium` (+6-7)
- `medicin*`, `chirurg*`, `anatom*` (+4-5)

Boring keywords (negative scores):
- `biblia`, `missale`, `breviarium` (-3)
- `homilia`, `sermones` (-2)

## Discovery Registry

Discoveries are saved to `discovery/registry/` as JSONL files with this schema:

```json
{
  "manuscript_id": "vat_pal_lat_1267",
  "shelfmark": "Pal.lat.1267",
  "repository": "BAV",
  "collection": "Palatini latini",
  "iiif": {
    "manifest_url": "https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json",
    "canvas_count": 62
  },
  "scholarship": {
    "obscurity_score": 7,
    "score_reasons": ["+8: alchim"]
  },
  "discovery": {
    "discovered_date": "2026-01-28",
    "method": "iiif_crawl",
    "wtf_factor": 7
  },
  "status": "discovered"
}
```

## Workflow

1. **Survey**: Run metadata scanner on collection ranges
   ```bash
   python scripts/scan_vatican_metadata.py --collection "Pal.lat" --range 1-500 --output discovery/registry/pal_lat_survey.jsonl
   ```

2. **Filter**: Review top-scoring manuscripts in registry

3. **Analyze**: Run WTF analyzer on top candidates
   ```bash
   python scripts/wtf_analyzer.py --batch discovery/registry/pal_lat_survey.jsonl --top 10
   ```

4. **Transcribe**: Use two-pass pipeline on selected manuscripts
   ```bash
   python scripts/transcribe_manuscript.py --images projects/new_manuscript/images --prompt lumen_luminum
   ```

## Current Findings (Pal.lat 1000-1300)

55 manuscripts with score >= 7, including:
- **Pal.lat.1099**: Galen + Avicenna + Albertus Magnus medical miscellany
- **Pal.lat.1199**: "Magister" in title - possible magic content
- **Pal.lat.1242**: Pseudo-Albertus Magnus medical texts
- **Pal.lat.1177**: "medicina alchemica" - explicitly alchemical medicine

## Rate Limits

The Vatican API rate-limits aggressive crawling. Keep `--parallel` at 2-3 and add delays between large scans.

## Files

```
discovery/
├── schema/
│   └── manuscript.schema.json    # JSON schema for records
├── registry/
│   └── *.jsonl                   # Discovery registries
├── audit/
│   └── wtf_analysis_*.jsonl      # Gemini analysis results
└── collections/
    └── (collection metadata)
```
