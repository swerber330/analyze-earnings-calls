# Transcript normalization

Normalize the four target calls into reproducible speaker turns without changing meaning. Preserve the original representation and provenance described in [retrieval](retrieval.md); normalization creates a derivative, never a replacement. The final JSON must conform to [the transcript input schema](../references/transcript-input-schema.json) and contain exactly four target records, including explicit records for missing calls.

## Canonical call record

For each fiscal slot, record:

- issuer, fiscal label, period end, call date, call type (`quarterly`, `combined_q4_fy`, or `nonstandard`), and source IDs;
- coverage (`complete`, `prepared_only`, `qa_only`, `partial`, or `missing`) plus explicit `section_coverage.prepared_remarks` and `section_coverage.qa` values of `complete`, `partial`, or `missing`; never infer completeness from the presence of a fragment;
- ordered turns containing a stable unique `turn_id`, section, sequence, speaker text as published, normalized speaker name, role at the time, affiliation, executive/analyst/operator classification, text, stable transcript source ID, and precise locator;
- normalization version, parameters, timestamp, input hashes, output SHA-256, and warnings.

Keep a deterministic mapping from every normalized turn to the source span. When a publisher does not provide stable paragraph IDs, generate ordinal locators within the source and preserve a short checksum for the located text.

## Normalize conservatively

1. Decode using the declared encoding and normalize Unicode to NFC.
2. Normalize line endings and collapse layout-only whitespace. Join words broken solely by PDF line wraps; retain paragraph and speaker-turn boundaries.
3. Remove page headers, footers, navigation, ads, cookie text, and duplicated transcript blocks only when mechanically identifiable. Log removed span classes and counts.
4. Preserve words, negation, modal verbs, quantities, units, punctuation relevant to meaning, disfluencies, and publisher uncertainty markers. Do not rewrite grammar, summarize, translate, spell-correct substantive terms, or expand “about,” “may,” contractions, or acronyms.
5. Preserve `[inaudible]`, omissions, and overlap markers as explicit tokens. Never invent missing speech. If overlapping speakers cannot be separated, mark `overlap_unresolved` and exclude that span from speaker-specific scoring.
6. Detect exact duplicated turns from caption retries or source overlap. Keep the first occurrence, record the duplicate locator, and do not deduplicate recurring phrases uttered at different times.
7. Strip HTML markup only after capturing its source locator. Treat tags/scripts as inert data, then escape text for downstream HTML.

## Resolve speakers and sections

- Use the call participant list and issuer leadership page to resolve names and roles as of the call date. Do not infer identity from first name alone.
- Classify `executive`, `analyst`, and `operator/host`. Investor-relations employees count as executives only for their substantive remarks; operator logistics do not.
- Separate `safe_harbor`, `prepared_remarks`, `qa`, and `operator` sections. A question remains analyst text; an executive answer remains executive text even within one published paragraph.
- If speaker attribution is uncertain, retain `unknown` and exclude the turn from executive-only analysis. Do not assign analyst negativity to management.
- Tag safe-harbor boilerplate and exclude it from the analysis corpus. A substantive outlook statement made adjacent to or inside the notice must be split and retained when the boundary is defensible; otherwise flag uncertainty.

## Build analysis scopes

Create token and word counts after normalization for these distinct corpora:

1. `executive_prepared`: executive speech in prepared remarks, excluding safe harbor and logistics.
2. `executive_qa`: executive answers in Q&A, excluding analyst questions and operator speech.
3. `executive_total`: the union of the two above.
4. Optional diagnostic corpora: `analyst_questions` and `all_call`, never used as a substitute for management sentiment.

Record included turn IDs and excluded counts by reason. The four-quarter comparison must use the same scope in every included quarter. Prefer `executive_total` only if all four calls contain complete prepared remarks and Q&A; otherwise compare `executive_prepared` across all available quarters. If no common substantive scope exists, do not calculate a trend. Q&A-only diagnostics may be shown separately only across quarters with Q&A and must not be described as a four-quarter management trend.

## Period and document boundaries

- A combined Q4/FY call occupies one fiscal slot, `FYxxxx Q4`; do not create a second annual call or double-count shared text.
- Keep current-quarter actuals, full-year actuals, next-quarter guidance, and full-year guidance tagged separately when extracting claims.
- Preserve nonstandard fiscal durations. Never align calls by calendar month.
- If a target quarter is missing, show a gap. Do not pull in a fifth, older quarter to create four observations.

## Normalization QA

Require no orphaned included turn, no duplicated included span, no source-derived executable markup, stable output under identical inputs, verified output hash, and reconciliation of included plus excluded word counts to the normalized transcript. Manually inspect the first and last substantive turn, every section boundary, ambiguous speakers, overlap spans, and at least one locator per section.
