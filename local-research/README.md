# Local research workspace

Only this guide is versioned. All other contents are ignored by Git.
Create one subdirectory per issuer/run, for example `ISSUER/2026-Q3/`, with:

- `raw/`: transcripts, permitted source downloads, captions, and source ledger.
- `normalized/`: `transcript-turns.json` and provenance/hash records.
- `intermediate/`: `language-analysis.json`, calculations, and `report-payload.json`.
- `reports/`: HTML reports, PDFs, screenshots, and layout QA output.

Use only material you are entitled to retain. This directory is not encrypted
or access-controlled by Git; keep backups and cloud synchronization consistent
with provider terms and your research confidentiality requirements.
Never put real research in this README, source directories, examples, or fixtures.
See [research data safeguards](../docs/research-data-safety.md).
