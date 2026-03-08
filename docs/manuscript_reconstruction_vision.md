# Manuscript Reconstruction Vision

## The Problem

Historical manuscripts like Reg.lat.931 (Georgius Huszthius's Ottoman travelogue, 1538-1566) exist in three states:
1. **Physical artifact** - locked in Vatican, requires travel
2. **Digital facsimile** - viewable online, but unreadable to non-Latinists
3. **Scholarly edition** - text-only, loses the material experience

None of these make the manuscript truly *accessible* while preserving what makes it special.

## The Vision

Create **visual English reconstructions** of manuscript pages - preserving the aesthetic, layout, and feel of the original, but readable by anyone.

A reader should be able to "turn pages" of Georgius's travelogue and experience:
- His handwriting style (rendered in English)
- His marginalia and corrections
- His pyramid drawing
- The feel of 16th-century parchment
- The story, in their own language

## Two-Layer Architecture

### Layer 1: TEI-XML Scholarly Edition (Foundation)

```xml
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <!-- Metadata: author, date, provenance, bibliography -->
  </teiHeader>
  <facsimile>
    <!-- Links to Vatican IIIF images -->
  </facsimile>
  <text>
    <body>
      <pb n="f011r" facs="vatican_iiif_url"/>
      <ab type="diplomatic">atq̄ ibi ego tuba cecini sodalibus</ab>
      <ab type="normalized">atque ibi ego tuba cecini sodalibus</ab>
      <ab type="translation">and there I played the trumpet for my companions</ab>
      <note type="historical">The Great Pyramid, January 1538. Georgius climbed
        to the summit and played a fanfare - one of the earliest European
        accounts of ascending the pyramid.</note>
      <name type="place" ref="#giza">pyramids</name>
    </body>
  </text>
</TEI>
```

**Purpose:** Machine-readable, citable, searchable, scholarly foundation.

### Layer 2: Visual Reconstruction (Presentation)

Image generation pipeline that produces English manuscript pages:

**Inputs:**
- Original folio image (style reference)
- TEI-XML content (English translation + layout info)
- Page structure (line breaks, marginalia positions, illustrations)

**Outputs:**
- High-resolution "manuscript page" image
- English text rendered in period-appropriate hand style
- Original illustrations preserved/enhanced
- Marginalia translated and positioned

**Technical approaches:**
- Style transfer from original folios
- Fine-tuned image generation for manuscript aesthetics
- Controlled text rendering (ControlNet or similar)
- Compositing for illustrations/drawings

## Design Principles

1. **Fidelity to structure** - Same line breaks, same page divisions, same marginalia positions
2. **Honest translation** - Clear this is a reconstruction, not a forgery
3. **Preserve the human** - His corrections, his drawing, his personality
4. **Dual access** - Always link visual pages to TEI-XML source and Vatican original

## Example: f011r (The Pyramid Page)

**Original:** Latin text describing entering the Great Pyramid, finding the sarcophagus, climbing to the summit, playing trumpet.

**Reconstruction would show:**
- English text in humanist hand style
- Same ~34 lines per page
- Marginal note about Turkish vocabulary preserved (translated)
- Same page number "22" in corner
- Watermark: "Reconstruction from Reg.lat.931 | Original: digi.vatlib.it"

## Audiences

| Audience | Uses Layer 1 (TEI-XML) | Uses Layer 2 (Visual) |
|----------|------------------------|------------------------|
| Scholars | Primary research, citation | Quick reference |
| Students | Text analysis | Reading, engagement |
| General public | Rarely | Primary experience |
| Museums/exhibitions | Metadata | Display, printing |

## Open Questions

1. **Legal:** Can we generate derivative visual works from Vatican digitizations?
2. **Font/hand style:** Train custom model or use existing humanist scripts?
3. **Illustrations:** Preserve original drawings or redraw in style?
4. **Format:** Web viewer? PDF? Print-on-demand?
5. **Scale:** Start with one manuscript or design for many?

## MVP Scope

**Reg.lat.931 - Georgius Huszthius Travelogue**
- 79 folios
- Mix of text and illustration
- Compelling narrative (Ottoman captivity, pyramids, Indian campaign)
- Already transcribed

Deliverables:
1. Complete TEI-XML edition with English translation
2. Visual reconstruction of select "highlight" pages (10-15)
3. Web viewer prototype

## Why This Manuscript First

- **Narrative appeal:** Adventure story, pyramids, sea battles
- **Historical value:** Rare eyewitness account of Ottoman India campaign
- **Visual interest:** Includes drawings
- **Completeness:** Already fully transcribed
- **Accessibility gap:** No English translation exists

---

*Draft: January 2026*
