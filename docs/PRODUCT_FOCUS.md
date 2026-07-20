# Product Focus

## The product

Palimpsest turns image-bound manuscripts into trustworthy, readable books.

The input is an archival IIIF manifest. The output is an EPUB and a static
reader backed by the same structured book model. Between them, Palimpsest
preserves the evidence needed to inspect every transformation: page images,
diplomatic transcription, image alignment, translation, reconstruction,
reference evidence, emendation apparatus, model and prompt identity, token use,
cost, and implementation fingerprints.

## User promise

A reader receives a coherent book rather than a directory of OCR fragments. A
scholar can move from a published sentence back to the diplomatic reading and
source page. An operator can resume, refresh, or audit production without
repeating unchanged paid work.

## Product priorities

### 1. Faithful reading

Transcription captures what the page says before editorial correction.
Alignment anchors character positions to image regions. Translation uses a
manuscript-wide glossary and outline so repeated terms and structures remain
consistent.

### 2. Explicit editorial intervention

Reconstruction joins page fragments without erasing page identity. Reference
work produces a bounded evidence dossier. Emendation creates a separate reading
and apparatus; it never overwrites diplomatic evidence.

### 3. Readable publication

The terminal object is a book model with chapters, translation, original text,
emended reading, apparatus, catalog identity, and production colophon. EPUB and
static HTML are renderings of that model, not independent editorial products.

### 4. Operational trust

Recipes validate before execution. Stations declare exact inputs and one
output. Artifacts write atomically. Production history is append-only.
Implementation identity derives from executable source. Model use and cost are
recorded. Configuration drift requires explicit refresh.

## Success criteria

Palimpsest succeeds when:

- a new IIIF manuscript can enter through one intake command;
- the recipe can run or resume page-by-page without hidden state;
- failures identify the exact station and page without corrupting prior work;
- every derived artifact has stable location, schema, and provenance;
- the reconstructed manuscript preserves source-page boundaries;
- editorial changes are anchored and explained;
- EPUB and static reader agree because both use one book model;
- a published passage can be audited back to its image and production record.

## Scope discipline

Palimpsest is not a generic knowledge platform, open-ended archive discovery
service, or unconstrained research agent. It is a manuscript recovery factory.
New work belongs when it improves source intake, page understanding, editorial
reconstruction, provenance, publication, or the reliable operation of that
line.
