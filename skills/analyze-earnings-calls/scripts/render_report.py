#!/usr/bin/env python3
"""Render a validated earnings-call analysis payload as self-contained HTML.

The script is intentionally dependency-free and deterministic: it performs no
research or analysis, accepts no executable markup, and emits no runtime asset
requests.  See ``references/report-schema.json`` for the payload contract.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import ipaddress
import json
import math
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
TEMPLATE = ASSETS / "report-template.html"
RENDERER_VERSION = "1.1.0"
SCHEMA = ROOT / "references" / "report-schema.json"
FIXTURE = ROOT / "evals" / "fixtures" / "report-payload.json"

CTA = "Skill by Dave Wang. Find more free AI-for-investing resources at www.davewang.ai."
CTA_URL = "https://www.davewang.ai"
DISCLAIMER = (
    "Not financial advice. AI-generated analysis may contain errors or hallucinations. "
    "Verify all facts and calculations independently and consult a qualified financial "
    "adviser before acting."
)
ASSET_FILES = {
    "portrait": ASSETS / "dave-profile-APPROVED.jpg",
    "body_font": ASSETS / "fonts" / "manrope.ttf",
    "display_font": ASSETS / "fonts" / "league-gothic.ttf",
    "mono_font": ASSETS / "fonts" / "ibm-plex-mono.ttf",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HTTPS_URL_RE = re.compile(
    r"^https://"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?::[0-9]{1,5})?"
    r"(?:[/?#][\x21-\x7e]*)?$"
)
PERCENT_ENCODED_CONTROL_RE = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", re.IGNORECASE)


class PayloadError(ValueError):
    """Raised when input cannot satisfy the auditable report contract."""


def display_date(stamp: str) -> str:
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed.strftime("%b %d, %Y").replace(" 0", " ")


def esc(value: Any) -> str:
    """Escape all payload-derived text for an HTML text or quoted attribute context."""
    return html.escape(str(value), quote=True)


def fail(path: str, message: str) -> None:
    raise PayloadError(f"{path}: {message}")


def obj(value: Any, path: str, required: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(path, "must be an object")
    missing = [key for key in required if key not in value]
    if missing:
        fail(path, f"missing required field(s): {', '.join(missing)}")
    return value


def arr(value: Any, path: str, minimum: int = 0, maximum: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        fail(path, "must be an array")
    if len(value) < minimum:
        fail(path, f"must contain at least {minimum} item(s)")
    if maximum is not None and len(value) > maximum:
        fail(path, f"must contain at most {maximum} item(s)")
    return value


def text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        fail(path, "must be a non-empty string" if not allow_empty else "must be a string")
    return value


def number(value: Any, path: str, low: float | None = None, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(path, "must be a finite number")
    result = float(value)
    if low is not None and result < low:
        fail(path, f"must be >= {low}")
    if high is not None and result > high:
        fail(path, f"must be <= {high}")
    return result


def integer(value: Any, path: str, low: int | None = None, high: int | None = None) -> int:
    """Require a JSON integer; bools and integral-looking floats are invalid."""
    if isinstance(value, bool) or not isinstance(value, int):
        fail(path, "must be an integer")
    if low is not None and value < low:
        fail(path, f"must be >= {low}")
    if high is not None and value > high:
        fail(path, f"must be <= {high}")
    return value


def boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        fail(path, "must be a boolean")
    return value


def validate_url(value: Any, path: str) -> str:
    url = text(value, path)
    if any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        fail(path, "must not contain raw whitespace or control characters")
    if "\\" in url or PERCENT_ENCODED_CONTROL_RE.search(url):
        fail(path, "must not contain ambiguous separators or percent-encoded control characters")
    if re.search(r"%(?![0-9A-Fa-f]{2})", url):
        fail(path, "must not contain malformed percent escapes")
    if not HTTPS_URL_RE.fullmatch(url):
        fail(path, "must be a well-formed absolute https URL with a DNS-style host")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        fail(path, "must contain a valid host and optional port")
    if parsed.scheme != "https" or not parsed.netloc or not hostname or parsed.username or parsed.password:
        fail(path, "must be an absolute https URL without embedded credentials")
    if len(hostname) > 253 or hostname.startswith(".") or hostname.endswith(".") or ".." in hostname:
        fail(path, "must contain an unambiguous DNS-style host")
    if hostname.lower() == "localhost" or "." not in hostname:
        fail(path, "must contain a fully qualified DNS-style host")
    if port is not None and not 1 <= port <= 65535:
        fail(path, "port must be between 1 and 65535")
    if re.fullmatch(r"[0-9.]+", hostname):
        try:
            ipaddress.IPv4Address(hostname)
        except ipaddress.AddressValueError:
            fail(path, "numeric host must be a valid IPv4 address")
    return url


def validate_timestamp(value: Any, path: str) -> str:
    stamp = text(value, path)
    try:
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        fail(path, "must be an ISO-8601 date or timestamp")
    return stamp


def forbid_unknown(value: dict[str, Any], path: str, allowed: set[str]) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        fail(path, f"unknown field(s): {', '.join(extra)}")


def validate_sha256(value: Any, path: str) -> str:
    digest = text(value, path)
    if not SHA256_RE.fullmatch(digest):
        fail(path, "must be a lowercase 64-character SHA-256 digest")
    return digest


def validate_tool(value: Any, path: str) -> dict[str, str]:
    tool = obj(value, path, ("identifier", "version"))
    forbid_unknown(tool, path, {"identifier", "version"})
    text(tool["identifier"], f"{path}.identifier")
    text(tool["version"], f"{path}.version")
    return tool


def validate_sentiment_method(value: Any, path: str) -> dict[str, str]:
    method = obj(value, path, ("kind", "identifier", "version", "asset_sha256"))
    forbid_unknown(method, path, {"kind", "identifier", "version", "asset_sha256"})
    if method["kind"] not in {"finbert_probabilities", "bundled_finance_lexicon"}:
        fail(f"{path}.kind", "must be finbert_probabilities or bundled_finance_lexicon")
    text(method["identifier"], f"{path}.identifier")
    text(method["version"], f"{path}.version")
    validate_sha256(method["asset_sha256"], f"{path}.asset_sha256")
    return method


def validate_manifest(value: Any, path: str = "analysis_manifest") -> dict[str, Any]:
    required = (
        "normalization", "analyzer", "renderer", "normalized_input_sha256",
        "analyzer_config_sha256", "sentiment_method", "hedge_lexicon_sha256",
    )
    manifest = obj(value, path, required)
    forbid_unknown(manifest, path, set(required))
    for key in ("normalization", "analyzer", "renderer"):
        validate_tool(manifest[key], f"{path}.{key}")
    for key in ("normalized_input_sha256", "analyzer_config_sha256", "hedge_lexicon_sha256"):
        validate_sha256(manifest[key], f"{path}.{key}")
    validate_sentiment_method(manifest["sentiment_method"], f"{path}.sentiment_method")
    return manifest


def validate_ref(value: Any, path: str, source_ids: set[str]) -> dict[str, str]:
    ref = obj(value, path, ("source_id", "locator"))
    forbid_unknown(ref, path, {"source_id", "locator"})
    source_id = text(ref["source_id"], f"{path}.source_id")
    locator = text(ref["locator"], f"{path}.locator")
    if source_id not in source_ids:
        fail(f"{path}.source_id", f"unknown source ID {source_id!r}")
    return {"source_id": source_id, "locator": locator}


def validate_refs(value: Any, path: str, source_ids: set[str], *, minimum: int = 1) -> list[dict[str, str]]:
    refs = [validate_ref(item, f"{path}[{index}]", source_ids) for index, item in enumerate(arr(value, path, minimum))]
    if len({(item["source_id"], item["locator"]) for item in refs}) != len(refs):
        fail(path, "must not contain duplicate source references")
    return refs


def validate_claim(value: Any, path: str, source_ids: set[str]) -> dict[str, Any]:
    claim = obj(value, path, ("text", "sources"))
    forbid_unknown(claim, path, {"text", "sources"})
    text(claim["text"], f"{path}.text")
    validate_refs(claim["sources"], f"{path}.sources", source_ids)
    return claim
def validate_executive_claim(value: Any, path: str, source_ids: set[str]) -> dict[str, Any]:
    claim = validate_claim(value, path, source_ids)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9%$'.-]*", claim["text"])
    if len(words) < 8:
        fail(
            f"{path}.text",
            "must contain a specific, decision-useful statement of at least eight words",
        )
    return claim


def validate_sources(payload: dict[str, Any]) -> set[str]:
    sources = arr(payload.get("sources"), "sources", 1)
    ids: set[str] = set()
    allowed = {
        "id", "title", "publisher", "url", "published_at", "retrieved_at",
        "fiscal_period", "coverage", "source_type", "source_grade",
        "rights_access_note", "raw_sha256", "normalized_sha256",
    }
    for index, raw in enumerate(sources):
        path = f"sources[{index}]"
        source = obj(raw, path, (
            "id", "title", "publisher", "url", "retrieved_at", "fiscal_period",
            "coverage", "source_type", "source_grade", "rights_access_note",
        ))
        forbid_unknown(source, path, allowed)
        source_id = text(source["id"], f"{path}.id")
        if source_id in ids:
            fail(f"{path}.id", "must be unique")
        ids.add(source_id)
        for key in ("title", "publisher", "fiscal_period", "coverage", "source_type", "source_grade", "rights_access_note"):
            text(source[key], f"{path}.{key}")
        validate_url(source["url"], f"{path}.url")
        validate_timestamp(source["retrieved_at"], f"{path}.retrieved_at")
        if "published_at" in source and source["published_at"] is not None:
            validate_timestamp(source["published_at"], f"{path}.published_at")
        for key in ("raw_sha256", "normalized_sha256"):
            if key in source and source[key] is not None:
                validate_sha256(source[key], f"{path}.{key}")
    return ids


def validate(payload: Any) -> dict[str, Any]:
    report = obj(payload, "root")
    top = {
        "schema_version", "generated_at", "company", "executive_view",
        "guidance_changes", "kpi_table", "sentiment", "hedge_analysis",
        "qa_themes", "risks", "monitoring_items", "sources", "methodology",
        "limitations", "analysis_manifest", "compliance",
    }
    forbid_unknown(report, "root", top)
    missing = sorted(top - set(report))
    if missing:
        fail("root", f"missing required field(s): {', '.join(missing)}")
    if report["schema_version"] != "1.0":
        fail("schema_version", "must equal '1.0'")
    validate_timestamp(report["generated_at"], "generated_at")
    manifest = validate_manifest(report["analysis_manifest"])
    compliance = obj(
        report["compliance"],
        "compliance",
        ("rights_reviewed", "contains_no_known_confidential_third_party_information", "statement"),
    )
    forbid_unknown(
        compliance,
        "compliance",
        {"rights_reviewed", "contains_no_known_confidential_third_party_information", "statement"},
    )
    if boolean(compliance["rights_reviewed"], "compliance.rights_reviewed") is not True:
        fail("compliance.rights_reviewed", "must be true before rendering")
    if boolean(
        compliance["contains_no_known_confidential_third_party_information"],
        "compliance.contains_no_known_confidential_third_party_information",
    ) is not True:
        fail(
            "compliance.contains_no_known_confidential_third_party_information",
            "must be true before rendering",
        )
    text(compliance["statement"], "compliance.statement")
    source_ids = validate_sources(report)

    company = obj(report["company"], "company", ("name", "ticker", "fiscal_period", "call_date"))
    forbid_unknown(company, "company", {"name", "ticker", "exchange", "fiscal_period", "call_date"})
    for key in ("name", "ticker", "fiscal_period", "call_date"):
        text(company[key], f"company.{key}")
    if "exchange" in company and company["exchange"] is not None:
        text(company["exchange"], "company.exchange")

    view = obj(report["executive_view"], "executive_view", ("bottom_line", "what_changed", "key_debate", "watch_next"))
    forbid_unknown(view, "executive_view", {"bottom_line", "what_changed", "key_debate", "watch_next"})
    for key in ("bottom_line", "what_changed", "key_debate", "watch_next"):
        validate_executive_claim(view[key], f"executive_view.{key}", source_ids)

    for index, raw in enumerate(arr(report["guidance_changes"], "guidance_changes")):
        path = f"guidance_changes[{index}]"
        item = obj(raw, path, ("metric", "target_period", "scope", "basis", "unit", "currency", "classification", "prior", "current", "change", "analysis"))
        forbid_unknown(item, path, {"metric", "target_period", "scope", "basis", "unit", "currency", "classification", "modifiers", "prior", "current", "change", "analysis"})
        for key in ("metric", "target_period", "scope", "basis", "unit", "currency"):
            text(item[key], f"{path}.{key}")
        if item["classification"] not in {"introduced", "raised", "lowered", "narrowed", "widened", "reiterated", "maintained", "withdrawn", "not_updated", "qualitative_only", "not_comparable"}:
            fail(f"{path}.classification", "must use an allowed guidance classification")
        if "modifiers" in item:
            modifiers = arr(item["modifiers"], f"{path}.modifiers", 1)
            allowed_modifiers = {"range_narrowed", "range_widened", "floor_raised", "ceiling_raised", "floor_lowered", "ceiling_lowered"}
            if len(set(modifiers)) != len(modifiers):
                fail(f"{path}.modifiers", "must not contain duplicates")
            for modifier_index, modifier in enumerate(modifiers):
                if modifier not in allowed_modifiers:
                    fail(f"{path}.modifiers[{modifier_index}]", "must use an allowed range modifier")
        for endpoint in ("prior", "current"):
            point = obj(item[endpoint], f"{path}.{endpoint}", ("value", "source_id", "locator"))
            forbid_unknown(point, f"{path}.{endpoint}", {"value", "source_id", "locator"})
            text(point["value"], f"{path}.{endpoint}.value")
            validate_ref({"source_id": point["source_id"], "locator": point["locator"]}, f"{path}.{endpoint}", source_ids)
        validate_claim(item["change"], f"{path}.change", source_ids)
        validate_claim(item["analysis"], f"{path}.analysis", source_ids)

    kpis = obj(report["kpi_table"], "kpi_table", ("periods", "rows"))
    forbid_unknown(kpis, "kpi_table", {"periods", "rows"})
    periods = arr(kpis["periods"], "kpi_table.periods", 4, 4)
    for index, period in enumerate(periods):
        text(period, f"kpi_table.periods[{index}]")
    if len(set(periods)) != 4:
        fail("kpi_table.periods", "must contain four unique newest-first target quarters")
    if company["fiscal_period"] != periods[0]:
        fail("company.fiscal_period", "must equal kpi_table.periods[0]")
    for row_index, raw in enumerate(arr(kpis["rows"], "kpi_table.rows", 1)):
        path = f"kpi_table.rows[{row_index}]"
        row = obj(raw, path, ("metric", "category", "scope", "basis", "definition", "definition_change", "comparability_status", "unit", "values", "takeaway"))
        forbid_unknown(row, path, {"metric", "category", "scope", "basis", "definition", "definition_change", "comparability_status", "unit", "values", "takeaway"})
        for key in ("metric", "category", "scope", "basis", "definition", "unit"):
            text(row[key], f"{path}.{key}")
        text(row["definition_change"], f"{path}.definition_change")
        if row["comparability_status"] not in {"exact", "bridged", "directional", "not_comparable", "insufficient_evidence"}:
            fail(f"{path}.comparability_status", "must use an allowed comparability status")
        values = arr(row["values"], f"{path}.values", 4, 4)
        for value_index, raw_value in enumerate(values):
            vpath = f"{path}.values[{value_index}]"
            cell = obj(raw_value, vpath, ("status",))
            if cell["status"] not in {"reported", "not_discussed", "not_applicable", "source_unavailable", "missing_transcript"}:
                fail(f"{vpath}.status", "must use an allowed KPI disclosure status")
            if cell["status"] == "reported":
                obj(cell, vpath, ("status", "value", "source_id", "locator"))
                forbid_unknown(cell, vpath, {"status", "value", "source_id", "locator"})
                text(cell["value"], f"{vpath}.value")
                validate_ref({"source_id": cell["source_id"], "locator": cell["locator"]}, vpath, source_ids)
            else:
                obj(cell, vpath, ("status", "gap_reason"))
                forbid_unknown(cell, vpath, {"status", "gap_reason"})
                text(cell["gap_reason"], f"{vpath}.gap_reason")
        validate_claim(row["takeaway"], f"{path}.takeaway", source_ids)

    sentiment = obj(report["sentiment"], "sentiment", ("method", "scope", "coverage", "quarters", "current_vs_prior", "latest_lenses"))
    forbid_unknown(sentiment, "sentiment", {"method", "scope", "coverage", "quarters", "current_vs_prior", "latest_lenses"})
    validate_sentiment_method(sentiment["method"], "sentiment.method")
    if sentiment["method"] != manifest["sentiment_method"]:
        fail("sentiment.method", "must exactly match analysis_manifest.sentiment_method")
    text(sentiment["coverage"], "sentiment.coverage")
    if sentiment["scope"] not in {"executive_total", "executive_prepared"}:
        fail("sentiment.scope", "must be executive_total or executive_prepared")
    validate_quarter_series(sentiment["quarters"], "sentiment.quarters", source_ids, "sentiment", periods)
    validate_comparison(sentiment["current_vs_prior"], "sentiment.current_vs_prior", source_ids)
    lenses = arr(sentiment["latest_lenses"], "sentiment.latest_lenses", 0, 4)
    if sentiment["quarters"][0]["status"] == "available" and not lenses:
        fail("sentiment.latest_lenses", "must contain at least one lens when the latest quarter is available")
    for index, raw in enumerate(lenses):
        path = f"sentiment.latest_lenses[{index}]"
        lens = obj(raw, path, ("lens", "score", "label", "sources"))
        forbid_unknown(lens, path, {"lens", "score", "label", "sources"})
        text(lens["lens"], f"{path}.lens")
        number(lens["score"], f"{path}.score", -100, 100)
        text(lens["label"], f"{path}.label")
        validate_refs(lens["sources"], f"{path}.sources", source_ids)

    hedge = obj(report["hedge_analysis"], "hedge_analysis", ("method", "version", "scope", "coverage", "quarters", "current_vs_prior", "top_terms"))
    forbid_unknown(hedge, "hedge_analysis", {"method", "version", "scope", "coverage", "quarters", "current_vs_prior", "top_terms"})
    for key in ("method", "version", "coverage"):
        text(hedge[key], f"hedge_analysis.{key}")
    if hedge["scope"] not in {"executive_total", "executive_prepared"}:
        fail("hedge_analysis.scope", "must be executive_total or executive_prepared")
    validate_quarter_series(hedge["quarters"], "hedge_analysis.quarters", source_ids, "hedge", periods)
    validate_comparison(hedge["current_vs_prior"], "hedge_analysis.current_vs_prior", source_ids)
    if (sentiment["current_vs_prior"]["available"] or hedge["current_vs_prior"]["available"]) and sentiment["scope"] != hedge["scope"]:
        fail("hedge_analysis.scope", "must match sentiment.scope when either comparison is available")
    validate_derived_comparison(sentiment["quarters"], sentiment["current_vs_prior"], "sentiment.current_vs_prior", "score")
    validate_derived_comparison(hedge["quarters"], hedge["current_vs_prior"], "hedge_analysis.current_vs_prior", "rate_per_1000")
    for index, raw in enumerate(arr(hedge["top_terms"], "hedge_analysis.top_terms")):
        path = f"hedge_analysis.top_terms[{index}]"
        term = obj(raw, path, ("term", "count"))
        forbid_unknown(term, path, {"term", "count"})
        text(term["term"], f"{path}.term")
        integer(term["count"], f"{path}.count", 0)

    for section, title_key, detail_key in (
        ("qa_themes", "theme", "analysis"),
        ("risks", "risk", "analysis"),
        ("monitoring_items", "item", "signal"),
    ):
        for index, raw in enumerate(arr(report[section], section)):
            path = f"{section}[{index}]"
            item = obj(raw, path, (title_key, detail_key))
            forbid_unknown(item, path, {title_key, detail_key})
            text(item[title_key], f"{path}.{title_key}")
            validate_claim(item[detail_key], f"{path}.{detail_key}", source_ids)

    for index, raw in enumerate(arr(report["methodology"], "methodology", 1)):
        text(raw, f"methodology[{index}]")
    for index, raw in enumerate(arr(report["limitations"], "limitations", 1)):
        text(raw, f"limitations[{index}]")
    return report


def validate_quarter_series(
    value: Any,
    path: str,
    source_ids: set[str],
    kind: str,
    expected_periods: list[str],
) -> None:
    series = arr(value, path, 4, 4)
    observed_periods: list[str] = []
    for index, raw in enumerate(series):
        qpath = f"{path}[{index}]"
        item = obj(raw, qpath, ("period", "status"))
        period = text(item["period"], f"{qpath}.period")
        observed_periods.append(period)
        if item["status"] not in {"available", "gap"}:
            fail(f"{qpath}.status", "must be available or gap")
        if item["status"] == "gap":
            obj(item, qpath, ("period", "status", "gap_reason"))
            forbid_unknown(item, qpath, {"period", "status", "gap_reason"})
            text(item["gap_reason"], f"{qpath}.gap_reason")
            continue
        if kind == "sentiment":
            obj(item, qpath, ("period", "status", "score", "label", "word_count", "turn_count", "source_id", "locator"))
            forbid_unknown(item, qpath, {"period", "status", "score", "label", "word_count", "turn_count", "source_id", "locator"})
            number(item["score"], f"{qpath}.score", -100, 100)
            text(item["label"], f"{qpath}.label")
            integer(item["word_count"], f"{qpath}.word_count", 1)
            integer(item["turn_count"], f"{qpath}.turn_count", 1)
        else:
            obj(item, qpath, ("period", "status", "raw_count", "word_count", "rate_per_1000", "source_id", "locator"))
            forbid_unknown(item, qpath, {"period", "status", "raw_count", "word_count", "rate_per_1000", "source_id", "locator"})
            raw_count = integer(item["raw_count"], f"{qpath}.raw_count", 0)
            word_count = integer(item["word_count"], f"{qpath}.word_count", 1)
            rate = number(item["rate_per_1000"], f"{qpath}.rate_per_1000", 0)
            expected_rate = 1000.0 * raw_count / word_count
            if abs(rate - expected_rate) > 0.011:
                fail(f"{qpath}.rate_per_1000", f"must equal 1000 × raw_count / word_count within 0.01; expected {expected_rate:.2f}")
        validate_ref({"source_id": item["source_id"], "locator": item["locator"]}, qpath, source_ids)
    if observed_periods != expected_periods:
        fail(path, "periods must exactly match kpi_table.periods in the same order")


def validate_comparison(value: Any, path: str, source_ids: set[str]) -> None:
    item = obj(value, path, ("available",))
    available = boolean(item["available"], f"{path}.available")
    if available:
        obj(item, path, ("available", "direction", "delta", "analysis"))
        forbid_unknown(item, path, {"available", "direction", "delta", "analysis"})
        if item["direction"] not in {"up", "down", "flat"}:
            fail(f"{path}.direction", "must be up, down, or flat")
        number(item["delta"], f"{path}.delta")
        validate_claim(item["analysis"], f"{path}.analysis", source_ids)
    else:
        obj(item, path, ("available", "gap_reason"))
        forbid_unknown(item, path, {"available", "gap_reason"})
        text(item["gap_reason"], f"{path}.gap_reason")


def validate_derived_comparison(quarters: list[dict[str, Any]], comparison: dict[str, Any], path: str, value_key: str) -> None:
    latest, prior = quarters[0], quarters[1]
    comparable = latest["status"] == "available" and prior["status"] == "available"
    if not comparable:
        if comparison["available"]:
            fail(f"{path}.available", "must be false when either of the latest two quarters is a gap")
        return
    if not comparison["available"]:
        fail(f"{path}.available", "must be true when the latest two quarters are available")
    expected = float(latest[value_key]) - float(prior[value_key])
    actual = float(comparison["delta"])
    if abs(actual - expected) > 0.011:
        fail(f"{path}.delta", f"must equal latest minus prior within 0.01; expected {expected:.2f}")
    expected_direction = "up" if expected > 0.005 else "down" if expected < -0.005 else "flat"
    if comparison["direction"] != expected_direction:
        fail(f"{path}.direction", f"must be {expected_direction!r} for delta {expected:.2f}")


def asset_data_uri(path: Path, mime: str) -> str:
    if not path.is_file():
        raise PayloadError(f"required bundled asset missing: {path}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def source_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        source["id"]: {**source, "display_id": f"S{index}"}
        for index, source in enumerate(payload["sources"], start=1)
    }


def source_link(ref: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    source = sources[ref["source_id"]]
    label = f'[{source["display_id"]}]'
    accessible_label = f'{label} {source["title"]}. Locator: {ref["locator"]}'
    return (
        f'<a class="citation" href="{esc(source["url"])}" '
        f'title="{esc(accessible_label)}" aria-label="{esc(accessible_label)}">'
        f'{esc(label)}</a>'
    )


def refs_html(refs: list[dict[str, Any]], sources: dict[str, dict[str, Any]]) -> str:
    grouped: dict[str, list[str]] = {}
    for ref in refs:
        grouped.setdefault(ref["source_id"], []).append(ref["locator"])
    compact_refs = [
        {"source_id": source_id, "locator": " • ".join(locators)}
        for source_id, locators in grouped.items()
    ]
    return '<span class="citations">' + "".join(source_link(ref, sources) for ref in compact_refs) + "</span>"


def source_locators(payload: dict[str, Any]) -> dict[str, list[str]]:
    result = {source["id"]: [] for source in payload["sources"]}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            source_id = value.get("source_id")
            locator = value.get("locator")
            if isinstance(source_id, str) and isinstance(locator, str) and source_id in result:
                locators = result[source_id]
                if locator not in locators:
                    locators.append(locator)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return result


def locator_details(locators: list[str]) -> str:
    if not locators:
        return '<span class="muted">No claim locator used</span>'
    label = f'{len(locators)} cited location{"s" if len(locators) != 1 else ""}'
    items = "".join(f'<li>{esc(locator)}</li>' for locator in locators)
    return (
        f'<details class="locator-list"><summary>{esc(label)}</summary>'
        f'<ol>{items}</ol></details>'
        f'<ol class="locator-print" aria-hidden="true">{items}</ol>'
    )


def claim_html(claim: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    return f'<p>{esc(claim["text"])} {refs_html(claim["sources"], sources)}</p>'


def section_heading(kicker: str, title: str, intro: str = "") -> str:
    intro_html = f'<p class="section-intro">{esc(intro)}</p>' if intro else ""
    return f'<div class="section-head"><span>{esc(kicker)}</span><h2>{esc(title)}</h2>{intro_html}</div>'


def cta_html(portrait_uri: str, position: str) -> str:
    return (
        f'<aside class="cta cta-{esc(position)}"><img src="{portrait_uri}" alt="Dave Wang" '
        'width="72" height="72"><a href="https://www.davewang.ai">'
        f'{esc(CTA)}</a></aside>'
    )

def table_region(table_html: str, label: str) -> str:
    return (
        f'<div class="table-wrap" role="region" aria-label="{esc(label)}" tabindex="0">'
        f'{table_html}</div>'
    )


def report_nav() -> str:
    links = (
        ("executive", "Executive"),
        ("sentiment", "Sentiment"),
        ("hedging", "Hedging"),
        ("guidance", "Guidance"),
        ("kpis", "KPIs"),
        ("qa", "Q&A"),
        ("risks", "Risks"),
        ("sources", "Sources"),
    )
    return (
        '<nav class="report-nav" aria-label="Report sections">'
        + "".join(f'<a href="#{section_id}">{esc(label)}</a>' for section_id, label in links)
        + "</nav>"
    )



def executive_html(payload: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    view = payload["executive_view"]
    cards = []
    for key, label in (("bottom_line", "Bottom line"), ("what_changed", "What changed"), ("key_debate", "Key debate"), ("watch_next", "Watch next")):
        cards.append(f'<article class="insight-card {"primary" if key == "bottom_line" else ""}"><h3>{label}</h3>{claim_html(view[key], sources)}</article>')
    return '<div class="insight-grid">' + "".join(cards) + '</div>'


def guidance_html(items: list[dict[str, Any]], sources: dict[str, dict[str, Any]]) -> str:
    if not items:
        return '<div class="empty-state">No company guidance change was verified.</div>'
    rows = []
    for item in items:
        prior_ref = {"source_id": item["prior"]["source_id"], "locator": item["prior"]["locator"]}
        current_ref = {"source_id": item["current"]["source_id"], "locator": item["current"]["locator"]}
        rows.append(
            '<tr><th scope="row" data-label="Metric">' + esc(item["metric"]) + '</th>'
            f'<td data-label="Prior"><strong>{esc(item["prior"]["value"])}</strong>{refs_html([prior_ref], sources)}</td>'
            f'<td data-label="Current"><strong>{esc(item["current"]["value"])}</strong>{refs_html([current_ref], sources)}</td>'
            f'<td data-label="Comparison basis"><b class="classification">{esc(item["classification"])}</b><small>{esc(" · ".join(item.get("modifiers", []))) + "<br>" if item.get("modifiers") else ""}{esc(item["target_period"])} · {esc(item["scope"])} · {esc(item["basis"])} · {esc(item["unit"])} · {esc(item["currency"])}</small></td>'
            f'<td data-label="Change">{claim_html(item["change"], sources)}</td>'
            f'<td data-label="Read-through">{claim_html(item["analysis"], sources)}</td></tr>'
        )
    table_html = (
        '<table class="guidance-table responsive-table"><caption>Company guidance comparison with independently sourced prior and current endpoints</caption>'
        '<thead><tr><th>Metric</th><th>Prior</th><th>Current</th><th>Comparison basis</th><th>Change</th><th>Read-through</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table>'
    )
    return table_region(table_html, "Guidance comparison table")


def kpi_html(table: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    heads = ''.join(f'<th scope="col">{esc(period)}</th>' for period in table["periods"])
    rows = []
    for row in table["rows"]:
        cells = []
        for period, cell in zip(table["periods"], row["values"]):
            if cell["status"] == "reported":
                ref = {"source_id": cell["source_id"], "locator": cell["locator"]}
                cells.append(
                    f'<td data-label="{esc(period)}"><strong>{esc(cell["value"])}</strong>{refs_html([ref], sources)}</td>'
                )
            else:
                status_labels = {
                    "not_discussed": "Not discussed",
                    "not_applicable": "Not applicable",
                    "source_unavailable": "Source unavailable",
                    "missing_transcript": "Missing transcript",
                }
                cells.append(
                    f'<td class="muted" data-label="{esc(period)}">'
                    f'<span>{esc(status_labels[cell["status"]])}</span><small>{esc(cell["gap_reason"])}</small></td>'
                )
        rows.append(
            f'<tr><th scope="row" data-label="KPI">{esc(row["metric"])}<small>{esc(row["category"])} · {esc(row["scope"])}<br>{esc(row["basis"])} · {esc(row["unit"])}<br>{esc(row["definition"])}<br>Comparability: {esc(row["comparability_status"])} · Definition change: {esc(row["definition_change"])}</small></th>'
            f'{"".join(cells)}<td data-label="Four-quarter read">{claim_html(row["takeaway"], sources)}</td></tr>'
        )
    table_html = (
        f'<table class="kpi-table responsive-table"><caption>Four-quarter KPI table in newest-first target-period order</caption>'
        f'<thead><tr><th>KPI</th>{heads}<th>Four-quarter read</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )
    return table_region(table_html, "Four-quarter KPI table")


def language_timeline_html(sentiment: dict[str, Any], hedge: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    hedge_by_period = {item["period"]: item for item in hedge["quarters"]}
    cards = []
    for index, sentiment_item in enumerate(sentiment["quarters"], start=1):
        hedge_item = hedge_by_period[sentiment_item["period"]]
        refs: list[dict[str, Any]] = []
        if sentiment_item["status"] == "available":
            refs.append({"source_id": sentiment_item["source_id"], "locator": sentiment_item["locator"]})
            sentiment_value = f'{float(sentiment_item["score"]):+.1f}'
            sentiment_detail = f'{sentiment_item["label"]} · {sentiment_item["word_count"]} words · {sentiment_item["turn_count"]} turns'
        else:
            sentiment_value = "Gap"
            sentiment_detail = sentiment_item["gap_reason"]
        if hedge_item["status"] == "available":
            refs.append({"source_id": hedge_item["source_id"], "locator": hedge_item["locator"]})
            hedge_value = f'{float(hedge_item["rate_per_1000"]):.2f}/1k'
            hedge_detail = f'{hedge_item["raw_count"]} raw cues · {hedge_item["word_count"]} words'
        else:
            hedge_value = "Gap"
            hedge_detail = hedge_item["gap_reason"]
        card_state = " gap" if sentiment_item["status"] == "gap" or hedge_item["status"] == "gap" else ""
        citation_html = refs_html(refs, sources) if refs else ""
        cards.append(
            f'<article class="language-quarter{card_state}">'
            f'<span class="quarter-index">0{index}</span><h3>{esc(sentiment_item["period"])}</h3>'
            '<div class="language-metrics">'
            f'<div><span>Sentiment</span><strong>{esc(sentiment_value)}</strong>'
            f'<small>{esc(sentiment_detail)}</small></div>'
            '<div><span>Hedge rate</span>'
            f'<strong>{esc(hedge_value)}</strong>'
            f'<small>{esc(hedge_detail)}</small></div></div>'
            '<p class="language-meta">'
            f'<b>Scope</b> {esc(sentiment["scope"])}<br>'
            f'<b>Coverage</b> {esc(sentiment["coverage"])}'
            '</p>'
            f'{citation_html}'
            '</article>'
        )
    return (
        '<div class="language-timeline" aria-label="Four-call sentiment and hedge-language timeline">'
        + "".join(cards)
        + "</div>"
    )


def sentiment_html(sentiment: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    comparison = comparison_html(sentiment["current_vs_prior"], sources, "Sentiment versus prior quarter")
    lenses = ''.join(f'<article class="metric-card"><span>{esc(item["lens"])}</span><strong>{float(item["score"]):+.1f}</strong><small>{esc(item["label"])}</small>{refs_html(item["sources"], sources)}</article>' for item in sentiment["latest_lenses"])
    lenses_html = f'<div class="metric-grid">{lenses}</div>' if lenses else '<div class="empty-state">No latest-call sentiment lens is available.</div>'
    method = sentiment["method"]
    method_html = f'<p class="method-strip"><b>Method</b> {esc(method["kind"])} · <b>ID</b> {esc(method["identifier"])} · <b>Version</b> {esc(method["version"])} · <b>Scope</b> {esc(sentiment["scope"])} · <b>Coverage</b> {esc(sentiment["coverage"])}</p>'
    return f'{comparison}<div class="sentiment-detail-block">{lenses_html}{method_html}</div>'


def hedge_html(hedge: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    rows = []
    max_rate = max([float(item.get("rate_per_1000", 0)) for item in hedge["quarters"] if item["status"] == "available"] or [1.0])
    for item in hedge["quarters"]:
        if item["status"] == "gap":
            rows.append(f'<div class="hedge-row gap"><strong>{esc(item["period"])}</strong><span>Gap</span><small>{esc(item["gap_reason"])}</small></div>')
        else:
            ref = {"source_id": item["source_id"], "locator": item["locator"]}
            width = float(item["rate_per_1000"]) / max_rate * 100
            rows.append(f'<div class="hedge-row"><strong>{esc(item["period"])}</strong><div class="bar"><i style="width:{width:.1f}%"></i></div><span><b>{esc(item["raw_count"])}</b> raw / <b>{esc(item["word_count"])}</b> words · <b>{float(item["rate_per_1000"]):.2f}</b>/1k</span>{refs_html([ref], sources)}</div>')
    terms = ''.join(f'<li><span>{esc(item["term"])}</span><b>{esc(item["count"])}</b></li>' for item in hedge["top_terms"]) or '<li><span>No repeated hedge phrase verified</span></li>'
    return f'<div class="hedge-layout"><div>{"".join(rows)}</div><aside><h3>Latest top phrases</h3><ul class="term-list">{terms}</ul></aside></div>{comparison_html(hedge["current_vs_prior"], sources, "Hedge-rate versus prior quarter")}<p class="method-strip"><b>Method</b> {esc(hedge["method"])} · <b>Version</b> {esc(hedge["version"])} · <b>Scope</b> {esc(hedge["scope"])} · <b>Coverage</b> {esc(hedge["coverage"])}</p>'


def comparison_html(item: dict[str, Any], sources: dict[str, dict[str, Any]], label: str) -> str:
    if not item["available"]:
        return f'<div class="comparison unavailable"><span>{esc(label)}</span><strong>Not comparable</strong><p>{esc(item["gap_reason"])}</p></div>'
    direction = item["direction"]
    return f'<div class="comparison {esc(direction)}"><span>{esc(label)}</span><strong>{esc(direction.upper())} · {float(item["delta"]):+.2f}</strong>{claim_html(item["analysis"], sources)}</div>'


def evidence_cards(items: list[dict[str, Any]], title_key: str, detail_key: str, sources: dict[str, dict[str, Any]]) -> str:
    if not items:
        return '<div class="empty-state">No material evidence-linked item was identified.</div>'
    return '<div class="evidence-grid">' + ''.join(f'<article><h3>{esc(item[title_key])}</h3>{claim_html(item[detail_key], sources)}</article>' for item in items) + '</div>'


def sources_html(payload: dict[str, Any]) -> str:
    rows = []
    locator_map = source_locators(payload)
    for index, source in enumerate(payload["sources"], start=1):
        display_id = f"S{index}"
        hashes = []
        if source.get("raw_sha256"):
            hashes.append(f'raw {esc(source["raw_sha256"][:12])}…')
        if source.get("normalized_sha256"):
            hashes.append(f'norm {esc(source["normalized_sha256"][:12])}…')
        published = esc(source.get("published_at") or "Not stated")
        rows.append(
            f'<tr id="source-{esc(source["id"])}"><th scope="row" data-label="ID"><a href="{esc(source["url"])}">{esc(display_id)}</a><small class="source-id">{esc(source["id"])}</small></th>'
            f'<td data-label="Source"><a href="{esc(source["url"])}"><strong>{esc(source["title"])}</strong></a><small>{esc(source["publisher"])}</small></td>'
            f'<td data-label="Period / coverage">{esc(source["fiscal_period"])}<small>{esc(source["coverage"])}</small></td>'
            f'<td data-label="Grade / type">{esc(source["source_grade"])}<small>{esc(source["source_type"])}</small></td>'
            f'<td data-label="Dates">{published}<small>Retrieved {esc(source["retrieved_at"])}</small></td>'
            f'<td data-label="Claim locators">{locator_details(locator_map[source["id"]])}</td>'
            f'<td data-label="Rights / integrity">{esc(source["rights_access_note"])}<small>{" · ".join(hashes) if hashes else "Hashes not supplied"}</small></td></tr>'
        )
    table_html = (
        '<table class="source-table responsive-table"><caption>Evidence source ledger, access rights, and integrity metadata</caption>'
        '<thead><tr><th>ID</th><th>Source</th><th>Period / coverage</th><th>Grade / type</th><th>Dates</th><th>Claim locators</th><th>Rights / integrity</th></tr></thead>'
        '<tbody>' + ''.join(rows) + '</tbody></table>'
    )
    return table_region(table_html, "Evidence source ledger")


def manifest_html(manifest: dict[str, Any]) -> str:
    sentiment = manifest["sentiment_method"]
    rows = [
        ("Normalization", f'{manifest["normalization"]["identifier"]} · {manifest["normalization"]["version"]}'),
        ("Analyzer", f'{manifest["analyzer"]["identifier"]} · {manifest["analyzer"]["version"]}'),
        ("Renderer", f'{manifest["renderer"]["identifier"]} · {manifest["renderer"]["version"]}'),
        ("Normalized input SHA-256", manifest["normalized_input_sha256"]),
        ("Analyzer config SHA-256", manifest["analyzer_config_sha256"]),
        ("Sentiment method", f'{sentiment["kind"]} · {sentiment["identifier"]} · {sentiment["version"]}'),
        ("Sentiment asset SHA-256", sentiment["asset_sha256"]),
        ("Hedge lexicon SHA-256", manifest["hedge_lexicon_sha256"]),
    ]
    return '<div class="manifest"><h3>Reproducibility manifest</h3><dl>' + "".join(
        f'<dt>{esc(label)}</dt><dd>{esc(value)}</dd>' for label, value in rows
    ) + '</dl></div>'


def render_html(payload: dict[str, Any]) -> str:
    payload = validate(payload)
    sources = source_map(payload)
    company = payload["company"]
    portrait_uri = asset_data_uri(ASSET_FILES["portrait"], "image/jpeg")
    body_font_uri = asset_data_uri(ASSET_FILES["body_font"], "font/ttf")
    display_font_uri = asset_data_uri(ASSET_FILES["display_font"], "font/ttf")
    mono_font_uri = asset_data_uri(ASSET_FILES["mono_font"], "font/ttf")

    meta = ' · '.join(filter(None, (company.get("exchange"), company["ticker"], company["fiscal_period"], company["call_date"])))
    target_order = " → ".join(payload["kpi_table"]["periods"])
    gap_periods = {
        item["period"]
        for series in (payload["sentiment"]["quarters"], payload["hedge_analysis"]["quarters"])
        for item in series if item["status"] == "gap"
    }
    coverage_badge = f"{len(payload['sources'])} sources · {len(gap_periods)} language-series gap{'s' if len(gap_periods) != 1 else ''}"
    generated_label = display_date(payload["generated_at"])
    sections = [
        f'<header class="hero"><div class="hero-rule"></div><span class="eyebrow">Earnings call intelligence</span><h1>{esc(company["name"])}<br><em>{esc(company["fiscal_period"])}</em></h1><p class="hero-meta">{esc(meta)}</p><div class="hero-badges"><span>Generated {esc(generated_label)}</span><span>{esc(coverage_badge)}</span></div><p class="period-order"><b>Newest-first target periods</b> {esc(target_order)}</p><p class="hero-dek">Four-quarter KPI, guidance, sentiment, and management-language analysis.</p></header>',
        report_nav(),
        f'<section id="executive">{section_heading("01", "Executive view", "What changed, what matters, and what to watch.")}{executive_html(payload, sources)}{cta_html(portrait_uri, "opening")}</section>',
        f'<section id="sentiment">{section_heading("02", "Language trend", "Sentiment and hedge language across four calls, with the latest call compared directly with the prior quarter.")}{language_timeline_html(payload["sentiment"], payload["hedge_analysis"], sources)}{sentiment_html(payload["sentiment"], sources)}</section>',
        f'<section id="hedging">{section_heading("03", "Hedge language", "Raw cue counts and normalized rates per 1,000 included management words.")}{hedge_html(payload["hedge_analysis"], sources)}</section>',
        f'<section id="guidance">{section_heading("04", "Guidance changes", "Prior and current company guidance shown side by side.")}{guidance_html(payload["guidance_changes"], sources)}</section>',
        f'<section id="kpis">{section_heading("05", "KPI four-quarter table", "Four target periods with explicit disclosure gaps.")}{kpi_html(payload["kpi_table"], sources)}</section>',
        f'<section id="qa">{section_heading("06", "Q&A pressure points")}{evidence_cards(payload["qa_themes"], "theme", "analysis", sources)}</section>',
        f'<section id="risks">{section_heading("07", "Risks and monitoring")}<h3 class="subsection-title">Risks</h3>{evidence_cards(payload["risks"], "risk", "analysis", sources)}<h3 class="subsection-title">Monitoring dashboard</h3>{evidence_cards(payload["monitoring_items"], "item", "signal", sources)}</section>',
        f'<section id="sources">{section_heading("08", "Sources, method, and limitations", "Source details, methods, and report limitations.")}{sources_html(payload)}{manifest_html(payload["analysis_manifest"])}<div class="compliance-note"><h3>Compliance</h3><p>{esc(payload["compliance"]["statement"])}</p></div><div class="method-columns"><div><h3>Method</h3><ul>{"".join(f"<li>{esc(item)}</li>" for item in payload["methodology"])}</ul></div><div><h3>Limitations</h3><ul>{"".join(f"<li>{esc(item)}</li>" for item in payload["limitations"])}</ul></div></div>{cta_html(portrait_uri, "closing")}<p class="disclaimer">{esc(DISCLAIMER)}</p></section>',
    ]
    template = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{DOCUMENT_TITLE}}": esc(f'{company["ticker"]} {company["fiscal_period"]} Earnings Call Analysis'),
        "{{FONT_BODY_URI}}": body_font_uri,
        "{{FONT_DISPLAY_URI}}": display_font_uri,
        "{{FONT_MONO_URI}}": mono_font_uri,
        "{{CONTENT}}": ''.join(sections),
        "{{PAYLOAD_SHA256}}": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
    }
    marker_pattern = re.compile(r"\{\{[A-Z0-9_]+\}\}")
    found = marker_pattern.findall(template)
    expected_counts = {marker: 1 for marker in replacements}
    expected_counts["{{PAYLOAD_SHA256}}"] = 2  # metadata plus printable footer
    if set(found) != set(replacements) or any(found.count(marker) != count for marker, count in expected_counts.items()):
        raise PayloadError("trusted template marker set is missing, duplicated, or unknown")
    return marker_pattern.sub(lambda match: replacements[match.group(0)], template)


def write_output(payload: dict[str, Any], output: Path) -> None:
    if output.suffix.lower() not in {".html", ".htm"}:
        raise PayloadError("output must end in .html or .htm")
    rendered = render_html(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def self_test() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["analysis_manifest"]["renderer"] == {
        "identifier": "render_report.py",
        "version": RENDERER_VERSION,
    }, "fixture renderer identity is stale"
    rendered_a = render_html(payload)
    rendered_b = render_html(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert rendered_a == rendered_b, "rendering is not deterministic"
    assert rendered_a.count(CTA) == 2, "CTA must appear exactly twice"
    assert rendered_a.count(f'href="{CTA_URL}"') == 2, "each CTA must be fully linked"
    assert DISCLAIMER in rendered_a, "exact disclaimer missing"
    assert "<script" not in rendered_a.lower(), "runtime scripts are not allowed"
    assert "{{" not in rendered_a, "unresolved template marker"
    assert rendered_a.count("data:image/jpeg;base64,") == 2, "portrait must be embedded in both CTAs"
    assert rendered_a.count("data:font/ttf;base64,") == 3, "fonts must be embedded"
    assert not re.search(r'(?:src|href)=["\'](?:/|file:|assets/)', rendered_a), "local runtime asset reference found"
    malicious = json.loads(json.dumps(payload))
    malicious["executive_view"]["bottom_line"]["text"] = 'Specific operating update keeps this hostile value inert: <img src=x onerror="alert(1)">'
    escaped = render_html(malicious)
    assert '<img src=x onerror="alert(1)">' not in escaped and "&lt;img" in escaped
    def expect_rejected(mutator: Any, reason: str) -> None:
        invalid = json.loads(json.dumps(payload))
        mutator(invalid)
        try:
            validate(invalid)
        except PayloadError:
            return
        raise AssertionError(reason)

    expect_rejected(
        lambda item: item["guidance_changes"][0]["prior"].update(source_id="MISSING"),
        "unknown guidance source was accepted",
    )
    expect_rejected(
        lambda item: item["sentiment"]["current_vs_prior"].update(delta=8.0),
        "incorrect sentiment delta was accepted",
    )
    expect_rejected(
        lambda item: item["hedge_analysis"]["quarters"][0].update(rate_per_1000=99.0),
        "incorrect hedge rate was accepted",
    )
    expect_rejected(
        lambda item: item["company"].update(fiscal_period="Q1 FY2026"),
        "company fiscal period not equal to the newest KPI period was accepted",
    )
    expect_rejected(
        lambda item: item["sentiment"]["quarters"].__setitem__(0, item["sentiment"]["quarters"][1]),
        "misordered sentiment periods were accepted",
    )
    expect_rejected(
        lambda item: item["hedge_analysis"]["quarters"][2].update(period="Q3 FY2025"),
        "misaligned hedge periods were accepted",
    )
    expect_rejected(
        lambda item: item["hedge_analysis"]["quarters"][0].update(raw_count=42.0),
        "floating hedge raw_count was accepted",
    )
    expect_rejected(
        lambda item: item["hedge_analysis"]["quarters"][0].update(word_count=True),
        "boolean hedge word_count was accepted",
    )
    expect_rejected(
        lambda item: item["hedge_analysis"]["top_terms"][0].update(count=11.0),
        "floating top-term count was accepted",
    )
    expect_rejected(
        lambda item: item["sentiment"]["quarters"][0].update(word_count=5480.0),
        "floating sentiment denominator was accepted",
    )
    expect_rejected(
        lambda item: item["sentiment"]["quarters"][2].update(score=0),
        "gap quarter with available-only field was accepted",
    )
    expect_rejected(
        lambda item: item["kpi_table"]["rows"][1]["values"][3].update(value="116%"),
        "KPI disclosure gap with reported-only field was accepted",
    )
    expect_rejected(
        lambda item: item["sentiment"]["current_vs_prior"].update(gap_reason="not allowed"),
        "available comparison with gap-only field was accepted",
    )
    expect_rejected(
        lambda item: item.pop("analysis_manifest"),
        "report without analysis manifest was accepted",
    )
    expect_rejected(
        lambda item: item["compliance"].update(rights_reviewed=False),
        "report without completed rights review was accepted",
    )
    expect_rejected(
        lambda item: item["analysis_manifest"].update(normalized_input_sha256="abc"),
        "invalid manifest hash was accepted",
    )
    expect_rejected(
        lambda item: item["sentiment"]["method"].update(identifier="different-method"),
        "sentiment method inconsistent with manifest was accepted",
    )
    unsafe_urls = [
        "http://example.com/transcript",
        "https://example.com/bad path",
        "https://example.com/%0aheader",
        "https://example.com/%zz",
        "https://example.com\\@evil.com/path",
        "https://user@example.com/path",
        "https://example..com/path",
        "https://example.com:99999/path",
    ]
    for unsafe_url in unsafe_urls:
        expect_rejected(
            lambda item, url=unsafe_url: item["sources"][0].update(url=url),
            f"unsafe source URL was accepted: {unsafe_url!r}",
        )
    assert validate_url("https://example.com/path/to/report?q=a%20b&x=1#section-2", "url")
    expect_rejected(
        lambda item: item["kpi_table"]["rows"][0].pop("comparability_status"),
        "KPI without comparability status was accepted",
    )
    collision = json.loads(json.dumps(payload))
    collision["executive_view"]["bottom_line"]["text"] = "Literal {{PAYLOAD_SHA256}} marker remains visible inside a specific executive summary."
    collision_html = render_html(collision)
    assert "Literal {{PAYLOAD_SHA256}} marker" in collision_html, "payload marker text was reprocessed"
    latest_gap = json.loads(json.dumps(payload))
    latest_gap["sentiment"]["quarters"][0] = {
        "period": latest_gap["kpi_table"]["periods"][0],
        "status": "gap",
        "gap_reason": "Current transcript is not lawfully available.",
    }
    latest_gap["sentiment"]["current_vs_prior"] = {
        "available": False,
        "gap_reason": "Current sentiment is unavailable.",
    }
    latest_gap["sentiment"]["latest_lenses"] = []
    latest_gap_html = render_html(latest_gap)
    assert "No latest-call sentiment lens is available." in latest_gap_html
    assert "5480 words" in rendered_a and "5480</b> words" in rendered_a, "language denominators are not rendered"
    assert payload["analysis_manifest"]["normalized_input_sha256"] in rendered_a, "manifest hash is not rendered"
    assert payload["compliance"]["statement"] in rendered_a, "compliance statement is not rendered"
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["sentiment"]["properties"]["quarters"]["minItems"] == 4
    assert schema["properties"]["hedge_analysis"]["properties"]["quarters"]["maxItems"] == 4
    sentiment_available = schema["$defs"]["sentimentQuarter"]["oneOf"][0]
    assert sentiment_available["properties"]["score"]["minimum"] == -100
    assert {"word_count", "turn_count"} <= set(sentiment_available["required"])
    assert schema["$defs"]["hedgeQuarter"]["oneOf"][0]["properties"]["raw_count"]["type"] == "integer"
    assert schema["properties"]["analysis_manifest"]["$ref"] == "#/$defs/analysisManifest"
    assert "comparability_status" in schema["$defs"]["kpiRow"]["required"]
    assert "target_period" in schema["$defs"]["guidanceChange"]["required"]
    assert schema["$defs"]["source"]["properties"]["url"]["pattern"].startswith("^https://[A-Za-z0-9]")
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "report.html"
        write_output(payload, output)
        assert output.stat().st_size > 50_000
    print("render_report.py self-test passed")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", nargs="?", type=Path, help="UTF-8 report payload JSON")
    parser.add_argument("output", nargs="?", type=Path, help="Destination .html file")
    parser.add_argument("--self-test", action="store_true", help="validate fixture, escaping, assets, and deterministic output")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            self_test()
            return 0
        if args.payload is None or args.output is None:
            parser.error("payload and output are required unless --self-test is used")
        write_output(json.loads(args.payload.read_text(encoding="utf-8")), args.output)
    except (OSError, json.JSONDecodeError, PayloadError, AssertionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
