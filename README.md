# Analyze Earnings Calls for Codex

Analyze a public company's four most recent earnings calls and generate a polished, self-contained HTML report.

The skill extracts key performance indicators and management guidance, compares executive sentiment across four calls, measures hedge-language frequency, and produces a responsive Dave Wang branded report with compact citations and precise source locators.

![Desktop earnings report](docs/meta-report-desktop.png)

## Install in Codex

Paste this into Codex:

```text
$skill-installer Install the skill from https://github.com/davidwang95/analyze-earnings-calls/tree/main/skills/analyze-earnings-calls
```

The skill becomes available on the next turn. If it does not appear, restart Codex.

## Run it

Invoke the skill with a public company name or ticker:

```text
$analyze-earnings-calls META
```

You can also make the request explicit:

```text
$analyze-earnings-calls Analyze NVIDIA's four most recent completed earnings calls and generate the HTML report.
```

## What the report contains

- Executive summary and current-versus-prior-quarter readout
- Four-call sentiment and hedge-language timeline
- Raw hedge counts and normalized rates per 1,000 executive words
- Key performance indicator history
- Guidance changes and comparability checks
- Management Q&A themes, risks, and watch items
- Compact inline citations with a detailed source-locator ledger
- Responsive desktop, mobile, and print layouts
- DaveWang.ai calls to action

## Example output

- [Open the self-contained example report](examples/example-earnings-call-report.html)
- The screenshot below shows the mobile cover layout.

<img src="docs/meta-report-mobile.png" alt="Mobile earnings report" width="390">

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

The skill includes deterministic language-analysis scripts, schemas, branded report assets, good and bad examples, and an offline evaluation suite.

## Validate a change

Run from `skills/analyze-earnings-calls`:

```bash
python3 scripts/analyze_language.py --self-test
python3 scripts/render_report.py --self-test
python3 evals/run_evals.py
node scripts/qa_report_layout.mjs path/to/report.html
```

The browser layout check requires Chromium or Google Chrome.

## About

Created by [Dave Wang](https://www.davewang.ai) for investors who want a repeatable way to analyze management language, KPIs, and guidance across earnings calls.
