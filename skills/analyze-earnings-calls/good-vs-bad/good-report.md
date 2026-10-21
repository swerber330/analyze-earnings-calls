# Good report example

This synthetic excerpt demonstrates the expected evidence and restraint. Names and values are fictional; locators illustrate the required format.

## Northstar Systems — FY2026 Q2 earnings-call analysis

**Coverage:** Four target fiscal calls: FY2026 Q2, FY2026 Q1, FY2025 Q4/FY, and FY2025 Q3. All include complete prepared remarks and Q&A, so headline language metrics use `executive_total` throughout.

### What changed

Northstar reported FY2026 Q2 annual recurring revenue of $842 million, up 18% year over year, while net retention was 112%. ARR is exactly comparable across the window; net retention becomes non-comparable before FY2026 Q1 because the company changed the included customer cohort and did not recast prior periods. [Release, ARR table row 4](source:release-q2#arr-row-4) [Call, prepared turn 18](source:call-q2#prepared-turn-18)

Full-year revenue guidance moved from $1.94–$1.98 billion to $1.96–$1.99 billion. The derived midpoint increased from $1.960 billion to $1.975 billion, so the report classifies it as **raised**; the range also narrowed from $40 million to $30 million. Both ranges use the same consolidated, reported-currency FY2026 basis. [Q1 release, outlook table](source:release-q1#outlook) [Q2 release, outlook table](source:release-q2#outlook)

### Management language

| Fiscal call | Coverage | Scope | Sentiment | Hedge count | Hedges / 1k words | Words |
|---|---|---|---:|---:|---:|---:|
| FY2026 Q2 | Complete | Executive total | 14.2 | 47 | 8.6 | 5,465 |
| FY2026 Q1 | Complete | Executive total | 9.8 | 51 | 9.7 | 5,258 |
| FY2025 Q4/FY | Complete | Executive total | 7.1 | 50 | 9.5 | 5,263 |
| FY2025 Q3 | Complete | Executive total | 11.3 | 42 | 8.4 | 4,998 |

Current-versus-prior sentiment increased **4.4 points** (`14.2 − 9.8`). The normalized hedge rate decreased **1.1 per 1,000 words** (`8.6 − 9.7`); raw counts are shown but are not used for the directional conclusion. Management repeatedly linked improved tone to enterprise renewals, while still qualifying the pace of public-sector bookings. [Q2 prepared turn 31](source:call-q2#prepared-turn-31) [Q2 Q&A turn 87](source:call-q2#qa-turn-87)

Scores use provided per-turn finance-model probabilities and the word-weighted formula `100 × (p_positive − p_negative)`. All calls use model `finance-model-v3`, analyzer `2.1.0`, normalization `1.2.0`, and identical executive-total filters. Hedge results use internal lexicon `hedges-2026-08`, longest-match rules, and contextual exclusions. Model/config, lexicon, source, and normalized-input SHA-256 values appear in Methodology.

### KPI continuity

| KPI | Current | Prior | Comparability | Interpretation |
|---|---:|---:|---|---|
| Annual recurring revenue | $842m | $808m | Exact | Same definition, scope, currency, period, and basis |
| Net retention | 112% | 111% | Exact for Q2 vs Q1 | Issuer changed cohort in Q1; earlier history is visibly broken |
| Customers >$100k ARR | 1,248 | Not discussed | Insufficient evidence | Silence is not zero and no change is calculated |

### Data quality

The FY2025 Q4 event is one combined Q4/full-year call and appears once. All four transcripts are issuer-hosted. The Q2 transcript states “approximately $840 million” while the release reports $842 million; the table uses the precise release value and retains the spoken approximation in the conflict note. No analyst text, operator logistics, or safe-harbor boilerplate enters headline scoring.

SHA-256 and locator details are available in the source ledger. No paywall-restricted text is reproduced. Rights and access were reviewed for every source; this report uses public or authorized material and contains no information known to be confidential to a third party.

> Not financial advice. AI-generated analysis may contain errors or hallucinations. Verify all facts and calculations independently and consult a qualified financial adviser before acting.

## Why this is good

- It uses current plus three prior completed fiscal calls and treats a combined Q4/FY event once.
- It compares language on one management-only scope and exposes method, counts, denominators, versions, and hashes.
- It separates exact KPI continuity from a definition break and treats an absent mention correctly.
- It classifies guidance from comparable endpoints and labels midpoint/width arithmetic as derived.
- It resolves a source conflict without erasing provenance and keeps source attribution attached to evidence.

## Presentation contract

- The report opens with the company and fiscal period, then the executive readout. Promotional messages, author portraits, and unrelated links are absent.
- One four-call language timeline shows sentiment, hedge rate, raw cue count, denominator, scope, coverage, gaps, and compact `[S#]` citations for every fiscal slot.
- At 390 px, guidance, KPI, and source tables become labeled full-width cards. Every report-navigation link remains visible, source links retain contrast, and citation targets remain usable.
