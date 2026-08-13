# Language, sentiment, and hedging analysis

Analyze management language as descriptive evidence, not a prediction or investment recommendation. Use normalized, speaker-resolved corpora from [normalization](normalization.md) and the methods in [methodology](../references/methodology.md). The analyzer input must follow [the transcript input schema](../references/transcript-input-schema.json): exactly four target fiscal-slot records, stable call/turn/source IDs, and explicit `complete`, `partial`, or `missing` prepared/Q&A coverage. Unknown coverage is not complete. An unavailable target remains a record with empty turns and explicit missing coverage.

## Freeze one comparable corpus

Select one management scope that exists on the same basis in every non-gap observation. Prefer `executive_total` only when every target call with usable prepared remarks also has explicitly complete, substantive Q&A; otherwise use `executive_prepared`. A call without explicit complete prepared coverage or a stable transcript `source_id` is a visible gap, not a reason to erase valid observations. Exclude analyst questions, operator speech, safe-harbor boilerplate, duplicated captions, normalization-excluded turns, unresolved overlap, uncertain attribution, zero-word turns, and unidentified speakers. Named Presidents, COOs, and other company C-suite/EVP/SVP roles normalize to `executive`; IR-only speakers do not.

Apply the identical inclusion filters, normalization version, model/lexicon, tokenization, aggregation, and hedge rules across all available quarters. Report word counts and coverage next to scores. Preserve exactly four target slots. If one slot is missing, score the others on the frozen scope and calculate current-versus-prior only when both adjacent endpoints are available; do not imply a complete four-observation trend.

## Sentiment score

Use exactly one of these methods for the entire comparison:

1. **Provided per-turn FinBERT-style probabilities.** For every eligible executive turn, accept `p_positive`, `p_neutral`, and `p_negative` only when they sum to 1 within tolerance and share one explicit model `identifier`, `version`, `asset_sha256`, and `config_sha256`. Missing, invalid, or mixed provenance forces the fallback for the entire run. Compute turn score as `100 × (p_positive − p_negative)`. Compute each call/scope score as the word-weighted mean of turn scores, using the normalized analysis-word count for each turn. Do not average labels or use neutral probability as positive evidence.
2. **Bundled deterministic finance lexicon fallback.** If consistent per-turn model probabilities cannot be produced for all compared calls, score every call with the bundled finance lexicon algorithm in `scripts/analyze_language.py`. Do not mix model and lexicon scores in one trend.

Record method name, analyzer version, model or lexicon version, model identifier when applicable, parameter/config hash, lexicon hash, normalized input hash, included turn IDs, word counts, and run timestamp. Scores range from -100 to +100. Label them as model- or lexicon-derived sentiment, not objective truth. Avoid unsupported “positive/negative” thresholds; emphasize changes and source examples.

Calculate current-versus-prior delta only when both use identical scope and method: `current score − immediately prior fiscal call score`. Report the sign and points, not percent. Show all four target fiscal slots and make gaps explicit.

## Hedge-word analysis

Use the internal, versioned hedge lexicon bundled with `scripts/analyze_language.py`. Match case-insensitively after the documented normalization, prefer the longest valid phrase at a position, and count non-overlapping matches. For example, a multiword entry consumes its component words so they are not counted again.

Apply contextual filters before counting:

- exclude safe-harbor boilerplate, analyst/operator text, quotations attributed to others, and non-substantive event logistics;
- exclude a token when it is part of a proper name, document title, fixed product name, URL/email, or another documented false-positive context;
- retain genuine uncertainty/modality in negated phrases and substantive management answers; do not remove “may” merely because nearby language is positive;
- preserve qualifier distinctions such as `about`/`approximately` and modal `may`/`might`; do not stem unrelated words into hedge hits;
- for overlap or ambiguous speaker attribution, exclude the affected span and log why.

Report per call and scope:

- raw hedge count;
- hedge rate `1,000 × raw count / analysis word count`;
- denominator word count;
- lexicon/analyzer versions and hashes;
- optional category counts and a few short, escaped context snippets with locators.

Use the normalized rate—not raw count—for cross-quarter direction. Compute current-versus-prior rate delta only on the same scope. Raw counts remain visible for auditability. Never interpret a higher hedge rate alone as deception; discuss call length, subject mix, missing Q&A, and small-denominator sensitivity.

## Attribution and robustness checks

- Keep analyst sentiment separate as an optional diagnostic. Analyst negativity must never lower the management score.
- Provide prepared-only and Q&A-only supplementary results only when their coverage is comparable; label them diagnostics and do not substitute them for the selected headline scope.
- Inspect turns with the largest weighted contribution, unusual hedge clusters, and abrupt score changes against source locators.
- Flag scores based on sparse text, machine transcription, incomplete sections, unresolved conflicts, or changed executives/topic mix.
- Use short evidence excerpts only as rights permit; otherwise paraphrase with a locator.

## Reproducibility gate

Run `scripts/analyze_language.py` twice on identical normalized inputs and configuration. Scores, counts, included turn IDs, gap decisions, and hashes must match. Verify `generated_by.hashes.normalized_input_sha256` against the canonical normalized JSON. Reject mixed methods, mixed model provenance, mixed scopes, inferred completeness, fewer than four target records, missing denominators, invalid probability sums, double-counted hedge phrases, or untraceable snippets. Assemble the report only through [the report assembly contract](report-assembly.md).
