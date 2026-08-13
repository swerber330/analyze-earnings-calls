# KPI and guidance analysis

Extract what management reported or discussed; do not estimate undisclosed values. Validate numerical facts against issuer releases, filings, presentations, and supplements when available. Preserve transcript locators for spoken claims and primary-document locators for verified values.

## KPI extraction record

For each observation, capture:

- stable metric ID, issuer label, normalized label, category, definition as disclosed, value/range, unit, scale, currency, sign, period, periodicity, reported/adjusted basis, consolidated/segment/product/geography scope, and status;
- claim type (`actual`, `guidance`, `target`, `qualitative_commentary`, or `derived`), speaker, transcript source ID and locator, verifying source ID and locator, and confidence;
- comparison language and stated comparator (year over year, sequential, constant currency, etc.);
- definition/scope change, restatement, acquisition/divestiture effect, 52/53-week effect, or absent disclosure;
- derivation formula and cited inputs only for transparent arithmetic requested by the report. Never label a derived value as reported.

Distinguish a numeric zero from unavailable, not applicable, not discussed, and missing transcript coverage.

## Exact comparability rule

Classify two KPI observations as `exact` only when all of the following match or are explicitly recast by the issuer onto one basis:

1. metric definition/formula and numerator/denominator;
2. entity, segment/product/geography scope and consolidation status;
3. reported versus adjusted basis and adjustment policy;
4. currency and foreign-exchange basis;
5. unit, scale, sign, and whether a rate is points or percent change;
6. fiscal periodicity and duration (quarter, YTD, year; including 52/53-week or stub effects);
7. measurement timing (period-end, average, bookings during period, trailing period);
8. cohort population and inclusion/exclusion rules.

Use these comparability statuses:

- `exact`: every condition matches or official recast comparatives exist.
- `bridged`: a complete issuer-provided reconciliation/recast converts both observations to one basis. Show the bridge and source.
- `directional`: a known difference remains but a limited directional comparison is still useful. Name the difference; do not calculate precise growth or imply like-for-like continuity.
- `not_comparable`: definition, scope, basis, period, or population differs materially without a complete bridge.
- `insufficient_evidence`: definition or source detail is too sparse to decide.

A same label is not evidence of comparability. A renamed metric is not automatically different. Never splice `directional`, `not_comparable`, or `insufficient_evidence` observations into a continuous numeric series. Display discontinuities and definition changes in the report.

## KPI selection and trend statements

- Include financial, operating, customer, product, pipeline, unit-economics, capital, and workforce KPIs that management quantitatively reports or makes decision-relevant.
- Prioritize recurring KPIs and newly introduced/retired metrics. Do not count ordinary GAAP line items as “special KPIs” unless central to management’s narrative; they may still appear in financial highlights.
- Keep actuals separate from guidance and targets. Keep consolidated metrics separate from segments.
- Calculate quarter-over-quarter or year-over-year changes only for `exact` or `bridged` pairs. Cite both endpoint observations and show formula/units.
- A missing mention is `not_discussed`, not zero and not evidence of deterioration. Distinguish source absence from transcript-section absence.
- When an official numerical source conflicts with the transcript, use the corrected/latest official value, retain the spoken value in the conflict log, and explain the resolution.

## Guidance classification

Classify each forward-looking statement independently by horizon and metric:

- `introduced`: first quantified guidance for that metric/horizon in the four-call window or after an explicit reset.
- `raised`: comparable lower bound, upper bound, midpoint, or single-point expectation moved upward, with no material scope/basis change that invalidates the comparison.
- `lowered`: comparable expectation moved downward under the same rule.
- `narrowed`: range width decreased while the midpoint is unchanged within disclosed precision. If the midpoint also changes, classify direction as raised/lowered and add `range_narrowed` as a modifier.
- `widened`: range width increased while midpoint is unchanged within disclosed precision; otherwise add it as a modifier to raised/lowered.
- `reiterated`: identical range/point or management explicitly reaffirmed it with no contradictory comparable numbers.
- `maintained`: economically unchanged within disclosed rounding but not literally identical; show both values and why treated as unchanged.
- `withdrawn`: management explicitly removes prior guidance or says it can no longer provide it.
- `not_updated`: prior guidance exists but the call supplies no valid update. Silence is never reiteration.
- `qualitative_only`: forward outlook is directional but not quantified.
- `not_comparable`: definition, scope, horizon, currency/basis, or fiscal duration changed without a full bridge.

For ranges, compute midpoint only as a labeled derivation `(low + high) / 2`; do not infer a missing bound. “At least,” “up to,” “approximately,” and “about” are qualifiers, not closed ranges. Preserve them. Compare like horizons only: next quarter to next quarter for the same fiscal slot, full-year to the same full year, and long-term targets to the same target date.

## Evidence and narrative gate

Every KPI/guidance statement in the HTML must resolve to a source locator. Use precise language: “management said,” “the release reports,” or “derived from disclosed bounds.” Do not call absence a cut, analyst speculation management guidance, or a safe-harbor list an outlook. Add material definition changes, unavailable periods, conflicts, and coverage limits to data-quality notes.
