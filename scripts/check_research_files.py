#!/usr/bin/env python3
"""Reject likely research artifacts in the Git index; never print their contents."""
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys

# Exact, reviewed public assets, not directory-wide exemptions.
PUBLIC_ASSETS = {
    "local-research/README.md",
    "skills/analyze-earnings-calls/evals/fixtures/transcript-turns.json",
    "skills/analyze-earnings-calls/evals/fixtures/report-payload.json",
    "skills/analyze-earnings-calls/assets/report-template.html",
    "examples/example-earnings-call-report.html",
}
ARTIFACT_NAMES = {"transcript-turns.json", "language-analysis.json", "report-payload.json"}
DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".html", ".htm", ".json", ".csv"}


def git(*args):
    return subprocess.check_output(["git", *args])


def reason(path, content):
    if path in PUBLIC_ASSETS:
        return None
    p = PurePosixPath(path.lower())
    if "local-research" in p.parts:
        return "private research workspace"
    if p.name in ARTIFACT_NAMES:
        return "workflow data artifact"
    if p.suffix in {".html", ".htm", ".pdf", ".doc", ".docx", ".vtt", ".srt"}:
        return "report/document or transcript attachment; public assets require explicit review"
    if "transcript" in p.name and p.suffix in DOCUMENT_SUFFIXES:
        # Schema definitions are code contracts, not normalized research data.
        if path != "skills/analyze-earnings-calls/references/transcript-input-schema.json":
            return "transcript-like filename"
    # Recognize renamed JSON workflow products by their structure, not the extension.
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(data, dict):
        if "company" in data and ("calls" in data or "executive_view" in data):
            return "research JSON structure"
        if "sentiment" in data and "hedge_analysis" in data and "analysis_scope" in data:
            return "language-analysis JSON structure"
    return None


def main():
    try:
        root = git("rev-parse", "--show-toplevel").decode().strip()
        # Work from the root even when invoked from a subdirectory.
        os.chdir(root)
        paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").split(b"\0")
        rejected = []
        for raw_path in filter(None, paths):
            path = raw_path.decode("utf-8", errors="surrogateescape")
            why = reason(path, git("show", ":" + path))
            if why:
                rejected.append((path, why))
        if rejected:
            print("Research safeguard: commit blocked. Staged paths:", file=sys.stderr)
            for path, why in rejected:
                print(f"  {ascii(path)}: {why}", file=sys.stderr)
            print("Unstage these files and retain permitted research in local-research/.\n"
                  "See docs/research-data-safety.md. No file contents were logged.", file=sys.stderr)
            return 1
        print("Research safeguard: staged files passed.")
        return 0
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"Research safeguard could not inspect the index ({type(exc).__name__}); commit blocked.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
