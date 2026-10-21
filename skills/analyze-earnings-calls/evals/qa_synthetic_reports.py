#!/usr/bin/env python3
"""Render synthetic normal, missing-call, and hostile-text reports; run browser QA.

Requires Chromium and Node with built-in WebSocket support (Node 22+).
All outputs go to the supplied directory, never to transcript/research storage.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
from run_evals import REPORT_FIXTURE_PATH, SCRIPTS_DIR, import_path, payload_with_gap


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 evals/qa_synthetic_reports.py OUTPUT_DIRECTORY")
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    renderer = import_path("synthetic_report_renderer", SCRIPTS_DIR / "render_report.py")
    payload = json.loads(REPORT_FIXTURE_PATH.read_text(encoding="utf-8"))
    hostile = copy.deepcopy(payload)
    injection = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    hostile["executive_view"]["bottom_line"]["text"] = "Synthetic hostile text must remain inert: " + injection
    hostile["sources"][0]["title"] = "Synthetic source " + injection
    variants = {"normal": payload, "missing-call": payload_with_gap(payload), "hostile-text": hostile}
    for name, fixture in variants.items():
        output = destination / f"synthetic-{name}.html"
        renderer.write_output(fixture, output)
        env = {**os.environ, "REPORT_QA_ARTIFACT_DIR": str(destination)}
        result = subprocess.run(
            ["node", str(SCRIPTS_DIR / "qa_report_layout.mjs"), str(output)],
            env=env, capture_output=True, text=True, check=False,
        )
        (destination / f"synthetic-{name}-layout.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise SystemExit(f"{name}: browser QA failed\n{result.stdout}{result.stderr}")
        print(f"{name}: desktop, mobile, and print QA passed")


if __name__ == "__main__":
    main()
