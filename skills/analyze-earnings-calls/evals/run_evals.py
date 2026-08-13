#!/usr/bin/env python3
"""Run offline regression and safety evaluations for analyze-earnings-calls.

The runner is standard-library only, performs no network access, writes derived
artifacts only under a temporary directory, and verifies that skill files are
unchanged during execution.
"""

from __future__ import annotations

import copy
import hashlib
import html as html_module
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable


sys.dont_write_bytecode = True

EVAL_DIR = Path(__file__).resolve().parent
SKILL_DIR = EVAL_DIR.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
REFERENCES_DIR = SKILL_DIR / "references"
FIXTURES_DIR = EVAL_DIR / "fixtures"

ANALYZER_PATH = SCRIPTS_DIR / "analyze_language.py"
RENDERER_PATH = SCRIPTS_DIR / "render_report.py"
CASES_PATH = EVAL_DIR / "cases.json"
SCHEMA_PATH = REFERENCES_DIR / "report-schema.json"
INPUT_SCHEMA_PATH = REFERENCES_DIR / "transcript-input-schema.json"
HEDGE_LEXICON_PATH = REFERENCES_DIR / "hedge-lexicon.json"
SENTIMENT_LEXICON_PATH = REFERENCES_DIR / "sentiment-lexicon.json"
TRANSCRIPT_FIXTURE_PATH = FIXTURES_DIR / "transcript-turns.json"
REPORT_FIXTURE_PATH = FIXTURES_DIR / "report-payload.json"

EXPECTED_CASE_IDS = {
    "fiscal-calendar-not-publication-month",
    "combined-q4-full-year",
    "kpi-definition-change",
    "missing-qa-common-scope",
    "analyst-negativity-attribution",
    "safe-harbor-boilerplate",
    "may-about-context",
    "longest-match-and-speaker-overlap",
    "missing-target-quarter",
    "official-source-conflict",
    "guidance-range-classification",
    "html-injection-and-url-safety",
    "reproducibility-manifest",
    "fallback-method-consistency",
    "paywall-and-rights",
}


class EvalFailure(AssertionError):
    """Raised for an unmet offline evaluation assertion."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvalFailure(message)


def close(actual: float, expected: float, *, tolerance: float = 1e-9) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    require(isinstance(value, dict), f"{path.name} must contain a JSON object")
    return value


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_skill_files() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(SKILL_DIR.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        snapshot[str(path.relative_to(SKILL_DIR))] = sha256(path)
    return snapshot


def import_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_self_test(script: Path, success_marker: str) -> str:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(script), "--self-test"],
        cwd=str(SKILL_DIR),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    require(
        completed.returncode == 0,
        f"{script.name} self-test failed ({completed.returncode}): {completed.stderr.strip()}",
    )
    require(success_marker in completed.stdout, f"{script.name} self-test did not emit {success_marker!r}")
    return completed.stdout.strip()


def expect_error(call: Callable[[], Any], error_type: type[BaseException], contains: str) -> None:
    try:
        call()
    except error_type as error:
        require(contains in str(error), f"error did not contain {contains!r}: {error}")
    else:
        raise EvalFailure(f"expected {error_type.__name__} containing {contains!r}")


def test_metadata_files() -> None:
    cases = load_json(CASES_PATH)
    schema = load_json(SCHEMA_PATH)
    input_schema = load_json(INPUT_SCHEMA_PATH)
    hedge = load_json(HEDGE_LEXICON_PATH)
    sentiment = load_json(SENTIMENT_LEXICON_PATH)

    rows = cases.get("cases")
    require(isinstance(rows, list) and rows, "cases.json must contain non-empty cases")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    require(len(ids) == len(set(ids)), "case IDs must be unique")
    require(EXPECTED_CASE_IDS <= set(ids), "cases.json is missing one or more required adversarial cases")
    for row in rows:
        require(isinstance(row, dict), "every eval case must be an object")
        require(isinstance(row.get("must"), list) and row["must"], f"{row.get('id')} requires must assertions")
        require(isinstance(row.get("must_not"), list) and row["must_not"], f"{row.get('id')} requires must_not assertions")

    require(schema.get("type") == "object", "report schema root must be an object")
    require(schema.get("additionalProperties") is False, "report schema must reject unknown root fields")
    required = set(schema.get("required", []))
    require({"kpi_table", "sentiment", "hedge_analysis", "sources"} <= required, "schema required fields are incomplete")
    period_schema = schema["properties"]["kpi_table"]["properties"]["periods"]
    require(period_schema.get("minItems") == 4 and period_schema.get("maxItems") == 4, "schema must require four KPI periods")
    require({"analysis_manifest", "compliance"} <= required, "schema must require structured manifest and compliance gates")
    compliance_required = set(schema["$defs"]["compliance"]["required"])
    require(
        {"rights_reviewed", "contains_no_known_confidential_third_party_information", "statement"} <= compliance_required,
        "compliance schema is incomplete",
    )
    manifest_required = set(schema["$defs"]["analysisManifest"]["required"])
    require(
        {"normalized_input_sha256", "analyzer_config_sha256", "sentiment_method", "hedge_lexicon_sha256"} <= manifest_required,
        "analysis manifest is missing reproducibility hashes or method identity",
    )
    sentiment_available = schema["$defs"]["sentimentQuarter"]["oneOf"][0]
    require({"word_count", "turn_count"} <= set(sentiment_available["required"]), "sentiment quarter denominators are not required")
    call_schema = input_schema["properties"]["calls"]
    require(call_schema.get("minItems") == 4 and call_schema.get("maxItems") == 4, "input schema must require exactly four target slots")
    call_required = set(input_schema["$defs"]["call"]["required"])
    require({"call_id", "quarter", "section_coverage", "turns"} <= call_required, "input schema lacks call identity/coverage requirements")
    coverage_required = set(input_schema["$defs"]["sectionCoverage"]["required"])
    require(coverage_required == {"prepared_remarks", "qa"}, "input schema must require explicit prepared and Q&A coverage")
    model_required = set(input_schema["$defs"]["sentimentModel"]["required"])
    require({"identifier", "version", "config_sha256", "asset_sha256"} <= model_required, "input schema lacks model provenance requirements")

    categories = hedge.get("categories")
    require(isinstance(categories, dict), "hedge lexicon categories must be an object")
    phrases = {phrase for values in categories.values() for phrase in values}
    require({"may", "about", "we believe", "believe"} <= phrases, "hedge lexicon lacks context/overlap fixtures")
    contextual = hedge.get("contextual_rules", {})
    require("about" in contextual and "may" in contextual, "hedge lexicon lacks May/about context rules")
    require("longest" in str(hedge.get("matching", "")).lower(), "hedge lexicon must declare longest-phrase matching")

    require(isinstance(sentiment.get("positive"), dict) and sentiment["positive"], "positive sentiment lexicon missing")
    require(isinstance(sentiment.get("negative"), dict) and sentiment["negative"], "negative sentiment lexicon missing")
    require("score is 100" in str(sentiment.get("method", "")).lower(), "fallback scoring method must be documented")


def test_bundled_self_tests() -> None:
    analyzer_output = run_self_test(ANALYZER_PATH, '"status": "ok"')
    analyzer_result = json.loads(analyzer_output)
    require(analyzer_result.get("tests_passed", 0) >= 8, "analyzer did not report all bundled tests")
    renderer_output = run_self_test(RENDERER_PATH, "render_report.py self-test passed")
    require(renderer_output == "render_report.py self-test passed", "unexpected renderer self-test output")


def test_probability_math_and_four_calls(analyzer: ModuleType) -> None:
    def turn(text: str, positive: float, negative: float, neutral: float) -> dict[str, Any]:
        return {
            "speaker": "Elena Torres",
            "role": "CEO",
            "section": "prepared_remarks",
            "text": text,
            "sentiment_probabilities": {
                "positive": positive,
                "negative": negative,
                "neutral": neutral,
            },
        }

    fixture = {
        "calls": [
            {
                "call_id": "CURRENT",
                "quarter": "Q4 FY2026",
                "call_date": "2026-12-01",
                "turns": [
                    turn("growth demand momentum strong improved", 0.9, 0.1, 0.0),
                    turn(
                        "weak decline pressure risk headwind challenging soft uncertain volatile downside missed constraint slowdown weaker disappointing",
                        0.1,
                        0.6,
                        0.3,
                    ),
                ],
            },
            {
                "call_id": "PRIOR",
                "quarter": "Q3 FY2026",
                "call_date": "2026-09-01",
                "turns": [turn("stable outlook remains balanced across markets", 0.3, 0.3, 0.4)],
            },
            {
                "call_id": "OLDER-1",
                "quarter": "Q2 FY2026",
                "call_date": "2026-06-01",
                "turns": [turn("momentum remains healthy across customer markets", 0.4, 0.2, 0.4)],
            },
            {
                "call_id": "OLDER-2",
                "quarter": "Q1 FY2026",
                "call_date": "2026-03-01",
                "turns": [turn("pressure remains visible across customer markets", 0.2, 0.4, 0.4)],
            },
        ]
    }
    model = {
        "identifier": "eval-finance-classifier",
        "version": "1.0.0",
        "config_sha256": "a" * 64,
        "asset_sha256": "b" * 64,
    }
    for call in fixture["calls"]:
        call["source_id"] = f'SRC-{call["call_id"]}'
        call["section_coverage"] = {"prepared_remarks": "complete", "qa": "missing"}
        call["sentiment_model"] = copy.deepcopy(model)
    result = analyzer.analyze(fixture)
    require(result["sentiment"]["method"]["name"] == "finbert_probabilities", "probability method was not selected")
    quarters = result["sentiment"]["quarters"]
    require(len(quarters) == 4, "analyzer must return exactly four supplied target calls")
    require([row["call_id"] for row in quarters] == ["CURRENT", "PRIOR", "OLDER-1", "OLDER-2"], "calls not newest first")
    require(close(quarters[0]["score"], -17.5), "current score must be word-weighted: (80*1 + -50*3) / 4")
    comparison = result["sentiment"]["current_vs_prior"]
    require(close(comparison["delta"], -17.5), "sentiment current-minus-prior delta is incorrect")
    require(comparison["direction"] == "deteriorated", "sentiment delta direction is incorrect")


def test_attribution_invariance(analyzer: ModuleType) -> None:
    fixture = load_json(TRANSCRIPT_FIXTURE_PATH)
    baseline = analyzer.analyze(copy.deepcopy(fixture))
    adversarial = copy.deepcopy(fixture)
    current = next(call for call in adversarial["calls"] if call["call_id"] == "ACME-2027-Q1")
    current["turns"].extend(
        [
            {
                "speaker": "Very Negative Analyst",
                "role": "analyst",
                "section": "qa",
                "text": "Weak decline disappointing missed lowered guidance risk uncertainty may might could about 20 percent.",
            },
            {
                "speaker": "Elena Torres",
                "role": "CEO",
                "section": "safe_harbor",
                "text": "Strong growth record beat raised guidance may might could perhaps approximately 20 percent.",
            },
            {
                "speaker": "Elena Torres / Very Negative Analyst",
                "role": "unknown",
                "section": "qa",
                "text": "We believe weak decline may continue about 20 percent.",
                "locator": {"overlap_unresolved": True},
            },
        ]
    )
    changed = analyzer.analyze(adversarial)
    require(
        stable_json(baseline["sentiment"]["quarters"]) == stable_json(changed["sentiment"]["quarters"]),
        "analyst or safe-harbor text changed management sentiment",
    )
    require(
        stable_json(baseline["hedge_analysis"]["quarters"]) == stable_json(changed["hedge_analysis"]["quarters"]),
        "analyst or safe-harbor text changed management hedge results",
    )
    base_excluded = baseline["analysis_scope"]["excluded_turn_counts"]
    changed_excluded = changed["analysis_scope"]["excluded_turn_counts"]
    require(changed_excluded.get("excluded_role", 0) == base_excluded.get("excluded_role", 0) + 2, "analyst and unresolved-overlap exclusions were not recorded")
    require(
        changed_excluded.get("excluded_section:safe_harbor", 0)
        == base_excluded.get("excluded_section:safe_harbor", 0) + 1,
        "safe-harbor exclusion was not recorded",
    )


def test_may_about_and_overlap(analyzer: ModuleType) -> None:
    result = analyzer.analyze(load_json(TRANSCRIPT_FIXTURE_PATH))
    require(result["analysis_scope"]["headline"]["name"] == "executive_prepared", "missing Q&A must force a prepared-only headline scope")
    latest = result["hedge_analysis"]["latest"]
    terms = [row["term"] for row in latest["evidence"]]
    require(terms.count("may") == 1, "title-cased month May must be excluded while modal may is counted")
    require(terms.count("about") == 1, "about must count only in a numeric context")
    require(terms.count("could") == 1, "prepared-only modal could must be counted")
    require("we believe" not in terms and "believe" not in terms, "Q&A hedges must not leak into the prepared-only headline")
    diagnostic_terms = [row["term"] for row in latest["diagnostics"]["evidence"]]
    require(diagnostic_terms.count("we believe") == 1, "supplementary Q&A must expose the longest phrase we believe")
    require("believe" not in diagnostic_terms, "nested believe must not overlap with we believe")
    require(latest["count"] == len(terms), "latest raw count must reconcile to hedge evidence")
    require(
        close(latest["per_1000_words"], 1000.0 * latest["count"] / latest["word_count"], tolerance=0.0005),
        "hedge rate must equal raw count per 1,000 included words",
    )


def payload_with_gap(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    period = result["kpi_table"]["periods"][2]
    reason = "No lawful complete transcript was available for this target fiscal call."
    result["sentiment"]["quarters"][2] = {"period": period, "status": "gap", "gap_reason": reason}
    result["hedge_analysis"]["quarters"][2] = {"period": period, "status": "gap", "gap_reason": reason}
    for row in result["kpi_table"]["rows"]:
        row["values"][2] = {"status": "missing_transcript", "gap_reason": reason}
    return result


def assert_four_target_contract(payload: dict[str, Any], *, require_gap: bool) -> None:
    periods = payload["kpi_table"]["periods"]
    require(len(periods) == 4 and len(set(periods)) == 4, "KPI table must contain four unique target periods")
    for key in ("sentiment", "hedge_analysis"):
        series = payload[key]["quarters"]
        require(len(series) == 4, f"{key} must contain exactly four target slots")
        require([row["period"] for row in series] == periods, f"{key} target slots must align with the KPI table")
        if require_gap:
            require(any(row["status"] == "gap" for row in series), f"{key} must preserve an explicit target-period gap")


def test_report_validation_and_math(renderer: ModuleType, payload: dict[str, Any]) -> None:
    renderer.validate(copy.deepcopy(payload))
    assert_four_target_contract(payload, require_gap=True)
    gap_payload = payload_with_gap(payload)
    renderer.validate(gap_payload)
    assert_four_target_contract(gap_payload, require_gap=True)

    sentiment = payload["sentiment"]
    expected_sentiment_delta = sentiment["quarters"][0]["score"] - sentiment["quarters"][1]["score"]
    require(close(sentiment["current_vs_prior"]["delta"], expected_sentiment_delta), "report sentiment delta is not current minus prior")
    hedge = payload["hedge_analysis"]
    current = hedge["quarters"][0]
    prior = hedge["quarters"][1]
    require(close(current["rate_per_1000"], 1000 * current["raw_count"] / current["word_count"], tolerance=0.011), "current hedge rate formula is wrong")
    require(close(prior["rate_per_1000"], 1000 * prior["raw_count"] / prior["word_count"], tolerance=0.011), "prior hedge rate formula is wrong")
    expected_hedge_delta = current["rate_per_1000"] - prior["rate_per_1000"]
    require(close(hedge["current_vs_prior"]["delta"], expected_hedge_delta), "report hedge delta is not current minus prior rate")

    unknown = copy.deepcopy(payload)
    unknown["guidance_changes"][0]["prior"]["source_id"] = "UNKNOWN-GUIDANCE-SOURCE"
    expect_error(
        lambda: renderer.validate(unknown),
        renderer.PayloadError,
        "unknown source ID",
    )

    unsafe_url = copy.deepcopy(payload)
    unsafe_url["sources"][0]["url"] = "javascript:alert(1)"
    expect_error(
        lambda: renderer.validate(unsafe_url),
        renderer.PayloadError,
        "absolute https URL",
    )

    def rejected(mutator: Callable[[dict[str, Any]], None], contains: str) -> None:
        invalid = copy.deepcopy(payload)
        mutator(invalid)
        expect_error(lambda: renderer.validate(invalid), renderer.PayloadError, contains)

    rejected(lambda item: item["company"].update(fiscal_period="Q1 FY2026"), "kpi_table.periods[0]")
    rejected(lambda item: item["sentiment"]["quarters"][0].update(period="Q0 FY2026"), "exactly match")
    rejected(lambda item: item["hedge_analysis"]["quarters"][1].update(period="Q0 FY2026"), "exactly match")
    rejected(lambda item: item["hedge_analysis"]["quarters"][0].update(raw_count=42.0), "must be an integer")
    rejected(lambda item: item["hedge_analysis"]["quarters"][0].update(word_count=True), "must be an integer")
    rejected(lambda item: item["hedge_analysis"]["top_terms"][0].update(count=11.0), "must be an integer")
    rejected(lambda item: item["sentiment"]["quarters"][0].update(word_count=5480.0), "must be an integer")
    rejected(lambda item: item["sentiment"]["quarters"][2].update(score=0), "unknown field")
    rejected(lambda item: item["sentiment"]["current_vs_prior"].update(gap_reason="wrong branch"), "unknown field")
    rejected(lambda item: item.pop("analysis_manifest"), "missing required field")
    rejected(lambda item: item["analysis_manifest"].update(normalized_input_sha256="bad"), "SHA-256")
    rejected(lambda item: item["sentiment"]["method"].update(identifier="different"), "exactly match")
    rejected(lambda item: item["executive_view"]["bottom_line"].update(text="Update."), "decision-useful statement")
    for url in (
        "https://example.com/bad path",
        "https://example.com/%0aheader",
        "https://example.com/%zz",
        "https://example..com/path",
        "https://example.com:99999/path",
    ):
        rejected(lambda item, value=url: item["sources"][0].update(url=value), "sources[0].url")
    require(
        renderer.validate_url("https://example.com/path?q=a%20b&x=1#part-2", "url").endswith("#part-2"),
        "normal HTTPS query/fragment URL was rejected",
    )


class RuntimeAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[str] = []
        self.src_values: list[tuple[str, str]] = []
        self.link_rels: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        self.tags.append(lowered)
        mapping = {name.lower(): value or "" for name, value in attrs}
        if "src" in mapping:
            self.src_values.append((lowered, mapping["src"]))
        if lowered == "link":
            self.link_rels.append(mapping.get("rel", ""))


def assert_no_runtime_assets(rendered: str) -> None:
    parser = RuntimeAssetParser()
    parser.feed(rendered)
    forbidden_tags = {"script", "iframe", "object", "embed", "audio", "video", "source"}
    require(not (forbidden_tags & set(parser.tags)), "rendered report contains an active/runtime element")
    require("link" not in parser.tags, "rendered report contains a runtime link asset")
    for tag, source in parser.src_values:
        require(source.startswith("data:"), f"{tag} uses a non-embedded runtime source: {source[:80]}")
    require("@import" not in rendered.lower(), "rendered CSS contains @import")
    css_urls = re.findall(r"url\(\s*(['\"]?)(.*?)\1\s*\)", rendered, flags=re.IGNORECASE)
    require(css_urls, "expected embedded font data URLs were not found")
    for _, value in css_urls:
        require(value.startswith("data:"), f"CSS uses a non-embedded runtime URL: {value[:80]}")


def test_rendered_html(renderer: ModuleType, payload: dict[str, Any]) -> None:
    payload = payload_with_gap(payload)
    rendered = renderer.render_html(copy.deepcopy(payload))
    require(rendered.count(renderer.CTA) == 2, "exact CTA copy must appear twice")
    require(rendered.count(f'href="{renderer.CTA_URL}"') == 2, "exact CTA destination must appear twice")
    require(rendered.count(renderer.DISCLAIMER) == 1, "exact disclaimer must appear once")
    require("<script" not in rendered.lower(), "rendered report contains a script tag")
    assert_no_runtime_assets(rendered)
    for period in payload["kpi_table"]["periods"]:
        require(period in rendered, f"rendered report omitted target period {period}")
    require('class="language-quarter gap"' in rendered, "language-series gap is not visibly rendered")
    require('class="hedge-row gap"' in rendered, "hedge gap is not visibly rendered")
    require("1 language-series gap" in rendered and "2 language-series gaps" not in rendered, "one missing call was double-counted in the cover badge")
    require("UP · +9.00" in rendered, "sentiment current-prior result is not rendered")
    require("UP · +1.16" in rendered, "hedge current-prior result is not rendered")
    require("5480 words" in rendered and "5480</b> words" in rendered, "language denominators are not rendered")
    require(payload["analysis_manifest"]["normalized_input_sha256"] in rendered, "reproducibility manifest is not rendered")

    injection = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    malicious = copy.deepcopy(payload)
    malicious["company"]["name"] = injection
    malicious["executive_view"]["bottom_line"]["text"] = "Specific operating update keeps this hostile value inert: " + injection
    malicious["guidance_changes"][0]["metric"] = injection
    malicious["kpi_table"]["rows"][0]["metric"] = injection
    malicious["sources"][0]["title"] = injection
    escaped = renderer.render_html(malicious)
    require(injection not in escaped, "raw HTML injection payload survived rendering")
    require(escaped.count(html_module.escape(injection, quote=True)) >= 5, "injection fields were not escaped in every context")
    require("<script" not in escaped.lower(), "escaped injection produced a script tag")
    parser = RuntimeAssetParser()
    parser.feed(escaped)
    require(all(tag != "script" for tag in parser.tags), "HTML parser observed an injected script")

    marker_collision = copy.deepcopy(payload)
    marker_collision["executive_view"]["bottom_line"]["text"] = "Literal {{PAYLOAD_SHA256}} marker remains visible inside a specific executive summary."
    collision_html = renderer.render_html(marker_collision)
    require("Literal {{PAYLOAD_SHA256}} marker" in collision_html, "payload marker text was reprocessed as a template marker")


def test_report_readability_contract(renderer: ModuleType, payload: dict[str, Any]) -> None:
    tested = copy.deepcopy(payload)
    long_locator = "Prepared remarks, turn " + "LONG-LOCATOR-" * 80
    tested["executive_view"]["bottom_line"]["sources"][0]["locator"] = long_locator
    rendered = renderer.render_html(tested)

    markers = re.findall(r'<a class="citation"[^>]*>([^<]+)</a>', rendered)
    require(markers, "report rendered no citation markers")
    require(all(re.fullmatch(r"\[S\d+\]", marker) for marker in markers), "citation text is not a compact [S#] marker")
    require(max(map(len, markers)) <= 8, "visible citation marker is too long")
    escaped_locator = html_module.escape(long_locator, quote=True)
    require(rendered.count(escaped_locator) >= 3, "full locator is not preserved in citation metadata, screen details, and print copy")
    require('<details class="locator-list">' in rendered, "source ledger does not expose tappable locator details")
    require('<ol class="locator-print" aria-hidden="true">' in rendered, "source ledger omits print-only precise locators")
    require("Claim locators" in rendered, "source ledger omits the precise-locator column")

    responsive_tables = re.findall(r'<table class="[^"]*\bresponsive-table\b[^"]*"', rendered)
    require(len(responsive_tables) == 3, "all three dense tables must use responsive card behavior")
    for label in ("Metric", "Prior", "Current", "KPI", "Four-quarter read", "Source", "Claim locators", "Rights / integrity"):
        require(f'data-label="{label}"' in rendered, f"responsive cell label missing: {label}")

    ids = re.findall(r'\bid="([^"]+)"', rendered)
    require(len(ids) == len(set(ids)), "rendered report contains duplicate DOM IDs")
    internal_source_id = payload["sources"][0]["id"]
    require(f'<small class="source-id">{html_module.escape(internal_source_id)}</small>' in rendered, "source ledger omits internal source ID")
    require(rendered.count('class="language-quarter') == 4, "unified four-call timeline does not contain four slots")
    require(rendered.count("Sentiment</span>") == 4, "unified timeline omits a sentiment metric")
    require(rendered.count("Hedge rate</span>") == 4, "unified timeline omits a hedge-rate metric")
    require("raw cues" in rendered and "<b>Coverage</b>" in rendered, "unified timeline omits raw counts or coverage")
    require(
        re.search(r'<div class="sentiment-detail-block">.*?<p class="method-strip">.*?</p></div>', rendered, flags=re.DOTALL) is not None,
        "sentiment lenses and methodology must remain one print-pagination unit",
    )
    require(".sentiment-detail-block,.insight-card" in rendered, "sentiment print-pagination rule is missing")
    for target in ("executive", "sentiment", "hedging", "guidance", "kpis", "qa", "risks", "sources"):
        require(f'href="#{target}"' in rendered and f'id="{target}"' in rendered, f"broken report navigation target: {target}")

    hero_end = rendered.index("</header>")
    executive_start = rendered.index('<section id="executive">')
    opening_cta = rendered.index('class="cta cta-opening"')
    require('class="cta cta-opening"' not in rendered[:hero_end], "opening CTA interrupts the cover")
    require(opening_cta > executive_start, "opening CTA must follow the executive analysis")
    require(rendered.count('class="cta cta-opening"') == 1, "opening CTA count changed")

    ordered_ids = ("executive", "sentiment", "hedging", "guidance", "kpis", "qa", "risks", "sources")
    positions = [rendered.index(f'<section id="{section_id}">') for section_id in ordered_ids]
    require(positions == sorted(positions), "report sections are not in the required insight-first order")


def test_deterministic_rerun(analyzer: ModuleType, renderer: ModuleType) -> None:
    transcript = load_json(TRANSCRIPT_FIXTURE_PATH)
    first_analysis = analyzer.analyze(copy.deepcopy(transcript))
    second_analysis = analyzer.analyze(copy.deepcopy(transcript))
    require(stable_json(first_analysis) == stable_json(second_analysis), "analyzer rerun is not byte-stable")

    payload = load_json(REPORT_FIXTURE_PATH)
    first_html = renderer.render_html(copy.deepcopy(payload))
    second_html = renderer.render_html(copy.deepcopy(payload))
    require(first_html == second_html, "renderer rerun is not byte-stable")
    with tempfile.TemporaryDirectory(prefix="earnings-call-evals-") as temp_dir:
        destination = Path(temp_dir) / "report.html"
        renderer.write_output(copy.deepcopy(payload), destination)
        require(destination.read_text(encoding="utf-8") == first_html, "temporary CLI-equivalent render differs from in-memory render")


def main() -> int:
    before = snapshot_skill_files()
    analyzer = import_path("earnings_call_analyzer_eval", ANALYZER_PATH)
    renderer = import_path("earnings_call_renderer_eval", RENDERER_PATH)
    report_payload = load_json(REPORT_FIXTURE_PATH)

    tests: list[tuple[str, Callable[[], None]]] = [
        ("parse cases, schema, and lexicons", test_metadata_files),
        ("run analyzer and renderer self-tests", test_bundled_self_tests),
        ("FinBERT-style word weighting and four-call ordering", lambda: test_probability_math_and_four_calls(analyzer)),
        ("analyst and safe-harbor invariance", lambda: test_attribution_invariance(analyzer)),
        ("May/about context and longest-match overlap", lambda: test_may_about_and_overlap(analyzer)),
        ("report four-slot, gap, source, and calculation validation", lambda: test_report_validation_and_math(renderer, report_payload)),
        ("CTA, disclaimer, self-contained HTML, and injection safety", lambda: test_rendered_html(renderer, report_payload)),
        ("compact citations, responsive tables, and insight-first hierarchy", lambda: test_report_readability_contract(renderer, report_payload)),
        ("deterministic analyzer and renderer rerun", lambda: test_deterministic_rerun(analyzer, renderer)),
    ]

    passed: list[str] = []
    failures: list[dict[str, str]] = []
    for name, test in tests:
        try:
            test()
        except Exception as error:  # report all offline failures in one run
            failures.append({"test": name, "error": f"{type(error).__name__}: {error}"})
        else:
            passed.append(name)

    after = snapshot_skill_files()
    if before != after:
        changed = sorted(set(before) | set(after))
        changed = [path for path in changed if before.get(path) != after.get(path)]
        failures.append({"test": "read-only execution", "error": f"skill files changed during evaluation: {changed}"})
    else:
        passed.append("read-only execution")

    result = {
        "status": "ok" if not failures else "failed",
        "tests_passed": len(passed),
        "tests_failed": len(failures),
        "passed": passed,
        "failures": failures,
        "offline": True,
        "stdlib_only": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
