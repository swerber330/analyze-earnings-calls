# Earnings Call Analyzer for Codex + Claude Code

Analyze a public company's four most recent earnings calls and generate a polished, self-contained HTML report.

The skill extracts key performance indicators and management guidance, compares executive sentiment across four calls, measures hedge-language frequency, and produces a professionally neutral responsive report with compact citations and precise source locators.

## Quick start

Replace `HOOD` with any public company ticker.

### Codex

Paste this into Codex:

```text
$skill-installer Install the skill from https://github.com/swerber330/analyze-earnings-calls/tree/main/skills/analyze-earnings-calls
```

Then run:

```text
$analyze-earnings-calls HOOD
```

### Claude Code

Paste these into Claude Code:

```text
/plugin marketplace add swerber330/analyze-earnings-calls
/plugin install earnings@davewang
```

Then run:

```text
/earnings:analyze-earnings-calls HOOD
```

If Claude Code asks you to reload after installation, run `/reload-plugins` once.

## What the report contains

- Executive summary and current-versus-prior-quarter readout
- Four-call sentiment and hedge-language timeline
- Raw hedge counts and normalized rates per 1,000 executive words
- Key performance indicator history
- Guidance changes and comparability checks
- Management Q&A themes, risks, and watch items
- Compact inline citations with a detailed source-locator ledger
- Responsive desktop, mobile, and print layouts

## Example output

- [Open the self-contained example report](examples/example-earnings-call-report.html)
- Browser QA produces desktop/mobile screenshots from synthetic fixtures as workflow artifacts.

## Repository structure

```text
skills/analyze-earnings-calls/
├── SKILL.md
├── agents/
├── assets/
├── evals/
├── good-vs-bad/
├── instructions/
├── references/
└── scripts/
```

The skill includes deterministic language-analysis scripts, schemas, self-contained report assets, good and bad examples, and an offline evaluation suite.

## Validate a change

Run from `skills/analyze-earnings-calls`:

```bash
python3 scripts/analyze_language.py --self-test
python3 scripts/render_report.py --self-test
python3 evals/run_evals.py
python3 scripts/render_report.py evals/fixtures/report-payload.json /tmp/synthetic-report.html
node scripts/qa_report_layout.mjs /tmp/synthetic-report.html
```

The browser layout check requires Chromium or Google Chrome and Node 22 or later. Set `CHROMIUM_PATH` if needed. Run `python3 evals/qa_synthetic_reports.py /tmp/earnings-report-qa` to check normal, missing-call, and hostile-text synthetic fixtures on desktop (1440 px), mobile (390 px), and print; the output directory receives screenshots and layout logs. The same checks run in GitHub Actions.

## Attribution and notices

Original implementation created by [Dave Wang](https://www.davewang.ai) for investors who want a repeatable way to analyze management language, KPIs, and guidance across earnings calls.

This fork adapts report presentation for institutional research. Generated reports omit promotional messages, portraits, and unrelated website links. The checked-in example and QA screenshots use only the synthetic Example Systems fixture; no licensed transcripts or confidential research are included.

Existing author metadata and bundled font copyright/license notices are retained. The upstream checkout contains no top-level software license file; this fork does not add or imply a new software license. Author attribution is repository metadata, not authorship or endorsement of investment analysis.

The renderer is version 1.2.0. Payload fields and schema version 1.0 remain unchanged; the schema identifiers are now neutral URNs (local schema files remain authoritative). Record the renderer version used in each analysis manifest and regenerate reports after updating the installed fork. Previously generated reports are not changed automatically.
