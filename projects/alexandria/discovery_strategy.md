# Discovery Strategy: Finding Unstudied Manuscripts

## The Problem

Our initial approach scored manuscripts based on "sparse metadata = interesting." This led us to the Physiologus (Pal.lat.1074) - which looked mysterious but is actually one of the most copied medieval texts with hundreds of surviving copies.

**The trap:** Sparse metadata on common texts ≠ hidden gem. It just means lazy cataloging.

## The Solution

Hunt like a paleontologist. Ask: **"If we transcribe this, will we recover a vanished human world?"**

That is stricter than "is this obscure?" or "is this interesting?"

The best targets preserve traces of lived human worlds:
- stories and narrative memory
- mysticism, omen reading, divination, and visionary experience
- medicine, pharmacology, recipes, and embodied practice
- cosmology, calendrics, ritual, and the ordering of Heaven and earth
- travel, geography, local records, and remembered events

The wrong targets may still be respectable, but fit the current north star less well:
- dictionaries and glossaries
- ordinary school commentaries
- clean elite theological exposition
- material that is legible but not especially alive

And apply the same rule to sources:

- a famous repository is not automatically a good target
- a source with stable manifests and thin metadata is often better than a
  beautifully curated museum portal
- the best source is one we can crawl unattended and that still contains
  underdescribed lived worlds

### Two Key Insights

1. **Later manuscripts (1400-1700) are better targets:**
   - Cleaner, better preserved, humanist scripts
   - More readable by both humans and AI
   - More likely to be original content (letters, records, scientific notes)
   - Less likely to be "copy #374 of Augustine"

2. **Vanished human worlds > merely respectable scholarship:**
   - A visionary notebook, healing manual, travel account, or farmer's journal
     often beats a polished commentary
   - Personal letters, administrative records, scientific observations,
     divination manuals, medical notebooks, miracle stories = unique
   - Bibles, Psalters, bestiaries = thousands of copies exist

3. **Automation fit matters as much as novelty fit:**
   - fully hands-off discovery needs stable IDs, stable manifests, and public access
   - Vatican-like bulk IIIF sources are often better autonomous hunting grounds
     than highly interpreted object portals
   - thematic sources like IDP are still valuable, but should stay curated and narrow

## Target Collections

Before picking a collection, ask two questions:
- `Does it contain the kind of human traces we care about?`
- `Can the source run unattended without constant repair?`

### Priority 1: Reg.lat (Reginenses Latini)
- **What:** Queen Christina of Sweden's personal collection (1626-1689)
- **Size:** 2,113 manuscripts, **97% digitized**
- **Why:** Intellectual collector interested in philosophy, science, Hebrew mysticism
- **Content:** Personal papers, scholarly notes, esoteric texts - NOT just religious copies
- **Status:** Ready to crawl

### Priority 2: Ott.lat (Ottoboniani Latini)
- **What:** Cardinal Pietro Ottoboni's collection (1667-1740)
- **Size:** 3,400 manuscripts, 54% digitized
- **Why:** Diplomatic correspondence, papal administration records
- **Content:** Letters, administrative documents, political records
- **Status:** Partial digitization

### Priority 3: Chig (Chigiani)
- **What:** Chigi family papers (Roman nobility, 1600s-1900s)
- **Size:** 3,636 manuscripts, **only 18% digitized**
- **Why:** Massively underexplored, family papers and music
- **Content:** Personal correspondence, music manuscripts, family records
- **Status:** Low digitization = opportunity

### Priority 4: Barb.lat 4889-9851 (Barberiniani newer acquisitions)
- **What:** Later acquisitions to Barberini collection
- **Size:** ~5,000 manuscripts in this range, 21% digitized overall
- **Why:** Scientific/medical focus, 17th-19th century
- **Content:** Scientific treatises, medical texts, astronomical observations
- **Status:** Selective sampling needed

### Priority 5: Underdescribed Chinese lanes
- **What:** Borg.cin and selected Vat.estr.or witnesses
- **Why:** Strong chance of preserving cosmology, medicine, ritual, glossarial,
  or transmission-heavy material outside the main Latin scholarly canon
- **Best fit for current north star:** manuscripts where the page world feels
  lived, practical, mystical, or narratively alive
- **Avoid within this lane:** dictionaries, phrasebooks, and lexica unless they
  clearly preserve unusual lived practice

## Collections to AVOID (or deprioritize)

| Collection | Why Skip |
|-----------|----------|
| Pal.lat 1-1000 | Early medieval, mostly religious texts, heavily studied |
| Vat.lat general | Too broad, lots of Bibles/liturgical |
| Any "Psalter", "Bible", "Gradual" | Thousands of copies exist |

## Triage Scoring

Our new triage prompt ("Manuscript Paleontologist") scores on three axes:

1. **interest_score** (0-10): How strongly does this witness preserve a vanished human world?
   - 10 = Stories, ritual, divination, medicine, travel, local records, practical notebooks, visionary or mystical material
   - 6-8 = Strong technical or historical material with some lived texture
   - 1-3 = Standard religious/classical text or clean scholarly exposition

2. **rarity_score** (0-10): How many copies exist worldwide?
   - 10 = Likely unique or one of very few
   - 1-3 = Thousands of copies

3. **unstudied_score** (0-10): Has anyone looked at THIS document?
   - 10 = Zero Google Scholar hits, no transcription
   - 1-3 = Edition exists, multiple papers cite it

**Combined priority = (interest + rarity + unstudied) / 3**

The model also performs live Google searches on each shelfmark to check for existing scholarship.

## Execution Plan

### Phase 1: Inventory Reg.lat
```bash
python -m palimpsest discovery run \
  --collection Reg.lat \
  --range 1-2113 \
  --output discovery/registry/reg_lat_full_inventory.jsonl
```

### Phase 2: Triage with Web Search
```bash
python -m palimpsest discovery triage \
  --input discovery/registry/reg_lat_full_inventory.jsonl \
  --db discovery/manuscripts.db \
  --with-web-search \
  --workers 1
```

### Phase 3: Review Top Candidates
```python
from palimpsest.discovery import DiscoveryDB
db = DiscoveryDB("discovery/manuscripts.db")

# Get highest "unstudied" scores
opps = db.list_opportunities(min_initial_score=7, limit=50)
for opp in opps:
    print(f"{opp.manuscript_id}: {opp.interest_reason}")
```

### Phase 4: Transcribe Winners
Select top 5-10 candidates and run through transcription pipeline.

## External Digitization Projects

Beyond the Vatican, other institutions have digitized manuscript collections that could run through our pipeline:

### Saint Catherine's Monastery, Sinai

One of the world's oldest continuously operating libraries (~4,559 manuscripts, 4th-19th century, 13 languages).

| Source | URL | Notes |
|--------|-----|-------|
| Sinai Manuscripts Digital Library (UCLA) | https://sinaimanuscripts.library.ucla.edu | 10-year comprehensive digitization project |
| Library of Congress | https://www.loc.gov/collections/manuscripts-in-st-catherines-monastery-mount-sinai/ | 1,687 manuscripts, freely available |
| National Library of Israel | https://www.nli.org.il/en/discover/manuscripts/saint-catherine | Rescued from deteriorating microfilm |
| Sinai Palimpsests Project | https://sinaied.library.ucla.edu/ | Spectral imaging of 160+ palimpsests - erased texts recovered |

**Why interesting:**
- Mix of Greek, Syriac, Arabic, Georgian, Slavonic manuscripts
- Palimpsest collection has hidden undertexts from 4th-12th century
- Remote location meant less scholarly attention than European libraries
- Connection to our Reg.lat.931 work (Georgius visited in 1540)

**TODO:** Investigate IIIF availability and manifest structure for these collections.

---

## The Golden Rule

**A visionary notebook, healing manual, travel account, or farmer's journal
scores higher than a clean commentary with sparse metadata.**

We're not looking for "rare text types." We're looking for individual documents
that preserve forgotten tracks of human life, where transcribing them will
recover something genuinely lived, not just something catalogued poorly.
