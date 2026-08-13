# Report assembly contract

Assemble—not transcribe—the deterministic language output into the validated report payload. `scripts/analyze_language.py` deliberately emits an analysis-stage artifact; KPI, guidance, source-ledger, and narrative evidence still come from the research workflow. Never copy scores or rates by eye.

## Inputs

Require:

1. `language-analysis.json` produced from a normalized input conforming to [the transcript input schema](../references/transcript-input-schema.json);
2. four newest-first fiscal periods and a source ledger from retrieval;
3. source-linked KPI, guidance, Q&A, risk, monitoring, methodology, and limitation records;
4. the report contract in [report-schema.json](../references/report-schema.json).

The four period labels in `company.fiscal_period`, `kpi_table.periods`, `sentiment.quarters`, and `hedge_analysis.quarters` must align exactly. The renderer rejects drift.

## Deterministic mapping

Map analyzer fields as follows:

| Report field | Analyzer source |
|---|---|
| `sentiment.scope` | `sentiment.headline_scope` |
| `sentiment.method.kind` | `sentiment.method.name` |
| method `identifier`, `version`, `asset_sha256` | same-named `sentiment.method` fields |
| sentiment available quarter `period`, `score`, `label`, `word_count`, `turn_count` | `sentiment.quarters[].quarter`, `score`, `label`, `word_count`, `turn_count` |
| hedge available quarter `raw_count`, `word_count`, `rate_per_1000` | `hedge_analysis.quarters[].count`, `word_count`, `per_1000_words` |
| quarter status | `available` → `available`; otherwise `gap` with the exact `gap_reason` |
| latest hedge terms | `hedge_analysis.latest.top_terms` |
| current-minus-prior values | analyzer `current_vs_prior`; do not recalculate by hand |
| manifest normalized input/config/lexicon hashes | `generated_by.hashes` |
| manifest analyzer identifier/version | `generated_by.name` and `generated_by.version` |

For each available language quarter, use its analyzer `source_id`. Build the report locator from the included turn IDs and their stored source locators; do not use a generic URL-only citation. A gap has no invented source reference.

The report sentiment method object must exactly equal `analysis_manifest.sentiment_method`. For the lexical fallback, map the bundled sentiment lexicon name/version/hash. For provided probabilities, map the one model identifier/version/asset hash accepted by the analyzer. If that provenance is absent or mixed, the analyzer selects the bundled fallback for all quarters.

## Narrative and lens assembly

Create current-versus-prior prose only when the analyzer marks the comparison available. State the signed sentiment-point delta or hedge-rate delta and cite both endpoint calls. Do not call a change statistically significant unless an explicitly requested statistical test supports that claim.

Map no more than four latest-call lenses from analyzer section/speaker diagnostics. Keep prepared/Q&A and speaker lenses labeled as diagnostics. If the latest target call is a gap, use an empty `latest_lenses` array; the renderer displays an explicit empty state.

## Integrity gate

Before rendering:

- verify every available quarter has a stable source ID and precise locator;
- keep analyzer `included_turn_ids`, scope, denominators, exclusions, and warnings in the audit bundle;
- use one sentiment method and one headline scope across available observations;
- preserve all four target slots, including gaps;
- copy exact deterministic values without rounding beyond the report's display formatting;
- populate the structured analysis manifest and source hashes when bytes were retained;
- set `compliance.rights_reviewed` and `compliance.contains_no_known_confidential_third_party_information` to `true` only after completing that review, and provide the rendered compliance statement; otherwise stop and resolve the issue;
- preserve source rights notes and analytical limitations;
- validate by running `scripts/render_report.py`; do not bypass its rejection.

The final HTML is derived from the report payload. Retain the normalized input, language analysis, report payload, and HTML together when the user's storage and source rights permit.
