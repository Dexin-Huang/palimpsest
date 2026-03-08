# Borg.cin.361 research memo

Date: 2026-03-07

## Why this manuscript matters

`Borg.cin.361` looks like a strong Palimpsest target for "underworked knowledge" rather than a generic Chinese manuscript. The source catalog is shelfmark-only, but sampled pages show a multilingual intellectual manuscript with:

- French commentary on Chinese classical material
- dense vertical Chinese prose
- Yijing / trigram diagrams
- named Chinese authorities embedded in the French notes

This is exactly the kind of object that is hard to search, hard to cite, and easy for scholarship to mention without fully reading.

## Sampled pages

Probe outputs live under:

- `experiments/probe_f050r/transcriptions/f050r_pass1.json`
- `experiments/probe_f150r_pass1/transcriptions/f150r_pass1.json`
- `experiments/probe_f300r_pass1/transcriptions/f300r_pass1.json`
- `experiments/probe_f020r_pass1/transcriptions/f020r_pass1.json`
- `experiments/probe_f200r_pass1/transcriptions/f200r_pass1.json`
- `experiments/probe_f250r_pass1/transcriptions/f250r_pass1.json`
- `experiments/probe_f350r_pass1/transcriptions/f350r_pass1.json`
- `experiments/probe_f450r_pass1/transcriptions/f450r_pass1.json`

### f050r

Mixed French + Chinese exegetical page. The page discusses `Kien` / `Kouen` (`乾` / `坤`) and the `易`, with interleaved Chinese citations and French interpretation.

Notable lines:

- `si Kien Kouen sont réunis dans un même tour, ce tour admirable donne la vraye idée du 1er`
- `j'en appelle aux chinois`
- `王 董 溪 云。夫 乾 為 大 矣。夫 坤 為 廣 矣。合 乾 與 坤 以 為 易`

Working interpretation:

- This is not simple copying of a Chinese source.
- It looks like a French-language commentary or study manuscript built around Chinese Yijing material.
- The mixture of transliterated terms, quoted characters, and interpretive prose suggests Jesuit or mission-era scholarly work rather than a standard Chinese printed edition.

### f150r

Pure vertical Chinese prose page. The passage reads like moral-philosophical discourse on ritual as an objective standard:

- `故繩墨誠陳矣則不可欺以曲直`
- `衡誠縣矣則不可欺以輕重`
- `規矩誠施矣則不可欺以方圓`
- `審於禮則不可欺以詐偽`

Working interpretation:

- The wording appears to align with well-known `Xunzi` / `Li lun` style discourse on ritual, standards, and moral order.
- That means the manuscript is not just diagrammatic Yijing material. It likely moves across multiple classical-exegetical registers.

### f300r

Chinese diagram page titled:

- `先天卦變後天卦圖`

Accompanying text includes:

- `此圖先天八四變而為後天也`
- `三乾中畫與三坤交而變為三離`

Working interpretation:

- This is explicit trigram / cosmological transformation material.
- Even if the title phrase exists elsewhere, its presence inside this manuscript confirms that `Borg.cin.361` contains structured diagram pages, not just prose commentary.

### Additional section probes

#### f020r

Very compressed vertical Chinese theological / cosmological prose. The probe only captured the opening header-like phrases:

- `敬畏神`
- `帝`

The raw folio shows much denser content than the probe extracted, but it is clearly in the same broad intellectual-theological register as later pages.

#### f200r

Bilingual French + Chinese analytical page with numbered sections. The French side explicitly discusses:

- `Cinq Elemens c'est yn yang`
- `tai ki`
- motion and rest

This confirms the manuscript is not just quoting Chinese text; it includes sustained French argumentation about Chinese cosmology and metaphysics.

#### f250r

Another bilingual French + Chinese polemical / missionary page. Key lines include:

- `sur les articles de morale`
- `au peuple deux fois chaque mois`
- `n'ayant plus d'esperance en leur secours ils les abandonnent`
- `depuis vingt ans que je suis en Chine`

This is especially important. It suggests firsthand or mission-context religious argument, not only abstract classical commentary.

#### f350r

Vertical Chinese list-like or taxonomic page with lines such as:

- `古史之根本`
- `史本有先後世之二而合為一統`
- `認佛為西方之聖者大非`
- `易理易數`

This looks like a synthetic or classificatory page tying together history, doctrine, and Yijing concepts.

#### f450r

The probe on this page under-read badly, but the raw folio is clearly dense vertical Chinese prose with heavy annotation. The model only returned:

- `非天子則誰乎`

So this page should be treated as a reminder that some sections will require the slower full two-pass path.

## Section map

The sampled pages strongly suggest that the manuscript is not uniform. Current working map:

1. Early Chinese theological / cosmological prose
   - seen in `f020r`
2. French-Chinese analytical commentary on Yijing, `tai ki`, `乾` / `坤`, and related concepts
   - seen in `f050r`, `f200r`
3. Pure Chinese classical / moral-philosophical prose
   - seen in `f150r`
4. Diagrammatic trigram / cosmology section
   - seen in `f300r`
5. Chinese synthetic, index-like, or classificatory section
   - seen in `f350r`
6. Missionary / polemical French-Chinese religious argument
   - seen in `f250r`
7. Later dense Chinese prose sections that may need full restoration to read well
   - seen in `f450r`

## Initial conclusion

This manuscript is already more interesting than the average Vatican shelfmark-only item.

Best current hypothesis:

- a multilingual Jesuit-era working manuscript or compilation
- centered on Yijing / cosmological diagrams and classical Chinese commentary
- with French interpretive notes engaging Chinese authorities directly

That combination is high value because the manuscript may preserve:

- how European readers actually worked through Chinese classics
- how diagrams were interpreted across languages
- hybrid scholarly vocabulary that does not survive well in catalog metadata

## Grade

I agree with the current high-interest grade.

More specifically:

- `interest_score = 9` still looks justified
- `rarity_score = 9` still looks justified
- `unstudied_score = 8` may even be conservative, given how little the source catalog tells us and how much structural variety the sample already shows

Important caveat:

- this is probably not an unknown text in the strong sense
- but it does look underdescribed, structurally rich, and unusually difficult to access without page-by-page work
- that is enough to make it a top-tier Palimpsest target

## Recommended next step

Do not brute-force all 536 pages immediately.

Instead:

1. sample 8-12 pages across the manuscript with `pass1` only
2. cluster them into sections: French commentary, Chinese prose, diagram pages, other
3. once the section map is clear, run full two-pass restoration on the most promising segment first

That first full-restoration segment should probably be:

- `f200r-f260r` if we want missionary / polemical bilingual material
- `f040r-f110r` if we want French-Chinese Yijing commentary
- `f280r-f320r` if we want the diagrammatic cosmology core

## First full restoration experiment

Date: 2026-03-07

Segment:

- `f200r-f202r`

Outputs:

- completed transcriptions:
  - `experiments/segment_f200_f202_full/transcriptions/f201r_final.json`
  - `experiments/segment_f200_f202_full/transcriptions/f202r_final.json`
- canonical pages:
  - `experiments/segment_f200_f202_full/canonical_pages/`
- diplomatic restoration:
  - `experiments/segment_f200_f202_full/restoration/book/book_diplomatic.txt`

What worked:

- The updated `canonical.page` path now preserves mixed Chinese/French text in `source_diplomatic`.
- The restored output for `f201r` and `f202r` is readable and substantively useful.
- The slice confirms this section is about `tai ki`, reason / matter, `Song` scholars, `Xu Shen`, `Shuowen`, and early cosmological interpretation.

What broke:

- `f200r` is the first clear hard page.
- `pass1` attempt 1 and `pass2` attempt 1 both produced truncated JSON and required retries.
- `pass2` attempt 2 for `f200r` stalled long enough that the batch was manually stopped.

Working conclusion:

- The current pipeline is good enough to start restoring small bilingual segments.
- Long, dense bilingual pages still need special handling.
- `f200r` should be treated as a benchmark page for the next refinement round.

## Timing note

Agentic `pass1` is slow enough that discovery should use one-page probes first.

Observed timings:

- `f150r`: about 16 seconds
- `f300r`: about 2 minutes 45 seconds

The variance likely depends on visual complexity and how much zoom/crop work the model performs.
