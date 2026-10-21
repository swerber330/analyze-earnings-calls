# Research data safeguards

This public repository contains skill source, reviewed fixtures, examples, and
validation tools. Real company research belongs in `local-research/` at the
repository root (or a private directory outside the checkout). Provider access
does not grant redistribution rights. Retain only what the license permits;
otherwise retain permitted excerpts, metadata, or links as retrieval.md requires.

## Set up once per clone

From the repository root, enable the bundled lightweight pre-commit hook:

```sh
git config --local core.hooksPath .githooks
python3 scripts/check_research_files.py
```

Requires only Git and Python 3. If you already use a custom hooksPath or a hook
manager, call `python3 scripts/check_research_files.py` from your existing
pre-commit hook instead of replacing it. Hooks are not activated by cloning;
each contributor must enable one. The check can also be run manually before
committing, from any directory in this checkout.

## Run research locally

Create a run directory and use absolute paths for research artifacts. For example,
from the repository root in a POSIX shell:

```sh
RESEARCH_RUN="$PWD/local-research/ISSUER/2026-Q3"
mkdir -p "$RESEARCH_RUN"/raw "$RESEARCH_RUN"/normalized "$RESEARCH_RUN"/intermediate "$RESEARCH_RUN"/reports
python3 skills/analyze-earnings-calls/scripts/analyze_language.py \
  "$RESEARCH_RUN/normalized/transcript-turns.json" "$RESEARCH_RUN/intermediate/language-analysis.json"
# Assemble report-payload.json in intermediate/ using the documented workflow.
python3 skills/analyze-earnings-calls/scripts/render_report.py \
  "$RESEARCH_RUN/intermediate/report-payload.json" "$RESEARCH_RUN/reports/company-earnings-analysis.html"
node skills/analyze-earnings-calls/scripts/qa_report_layout.mjs \
  "$RESEARCH_RUN/reports/company-earnings-analysis.html"
```

The filenames above are placeholders, not supplied data. Store the source ledger,
raw retained files, normalized text, calculations, final payload, reports, and QA
screenshots/output within the run directory. Deliver through an appropriately
private channel only when permitted; do not attach research to public GitHub
issues, pull requests, releases, or Actions artifacts/logs.

## What the safeguards cover

`.gitignore` excludes the workspace and common transcript/output filenames even
when saved elsewhere. It preserves the two existing JSON fixtures by exact path.
It deliberately does not ignore all JSON, HTML, or documentation: source schemas,
templates, scripts, and reviewed public examples must remain trackable.

The hook inspects staged paths and staged JSON contents, including files added
with `git add -f`. It blocks the workspace, canonical intermediate filenames,
transcript-like filenames, report/document attachments (HTML, PDF, Word), captions,
and recognizable renamed research JSON. It checks the index rather than the
working copy, handles spaces/newlines in filenames, allows deletions, and logs
paths/reasons only. Reviewed public assets are exempted by exact path in the
checker; adding another public example requires deliberate review of both its
rights/content and the checker exception. Existing examples are preserved, not
certified as licensed by this change. Never replace fixtures with actual research.

Before committing, review `git diff --cached --stat` and inspect the staged diff
locally. To unstage an accidental file without deleting the local copy:

```sh
git restore --staged -- path/to/file
```

## Limits and response

Ignore rules do not remove already tracked files or erase history. Hooks are
local, opt-in, and bypassable (`--no-verify`, GitHub uploads, API commits, or an
unconfigured clone). This is a heuristic guard, not content classification,
credential scanning, encryption, or a license review. Arbitrarily named plain
text/Markdown, archives, images, other formats, and changes to exempt assets can
still carry sensitive material. Keep real research in the workspace and review
all publication destinations. No history rewrite or deletion is warranted by
this prospective risk assessment alone.

If actual disclosure is found, stop publication, identify affected paths and
commits without reposting the contents, assess applicable provider obligations,
and arrange appropriate removal/history remediation. Rotate any exposed
credentials. Removing a file from the latest commit alone cannot recall clones.

## Validate changes

From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check_research_files.py
```

Run the skill's existing self-tests and offline evals as described in README.md.
The safeguard tests use only synthetic markers in disposable Git repositories.
