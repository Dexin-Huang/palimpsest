# Vatican Palatine Latin - Alchemical Manuscripts

This project processes medieval alchemical manuscripts from the Vatican Library's Bibliotheca Palatina collection.

## Current Focus: Pal. lat. 1267

**Date**: 14th century (French origin)
**Content**: Collection of alchemical treatises including:
- Pseudo-Aristotelian "Lumen Luminis" (Light of Lights)
- RÄzÄ«'s alchemical works (KitÄb al-AsrÄr)
- Technical illustrations of alchemical apparatus (furnaces, alembics)

**Source**:
- Vatican Digital Library: https://digi.vatlib.it/view/MSS_Pal.lat.1267
- Heidelberg (Palatina digitization): https://digi.ub.uni-heidelberg.de/diglit/bav_pal_lat_1267

## Text Layers

For medieval Latin manuscripts, text layers are:
- `la_diplomatic` - exact transcription preserving abbreviations and original spelling
- `la_normalized` - expanded abbreviations, classical Latin spelling
- `en_literal` - direct English translation
- `en_interpreted` - contextual interpretation with modern chemical equivalents

## Challenges

1. **Abbreviations**: Medieval Latin uses extensive abbreviation marks (up to 50% of words abbreviated)
2. **Technical vocabulary**: Alchemical terminology requires specialized knowledge
3. **Symbol systems**: Some manuscripts use alchemical symbols for elements/substances

## IIIF Access

Images available via Vatican IIIF:
```
https://digi.vatlib.it/iiifimage/MSS_Pal.lat.1267/Pal.lat.1267_{SEQUENCE}_fa_{FOLIO}.jp2/full/1000,/0/default.jpg
```

Example folio 1r (sequence 0005):
```
https://digi.vatlib.it/iiifimage/MSS_Pal.lat.1267/Pal.lat.1267_0005_fa_0001r.jp2/full/1000,/0/default.jpg
```
