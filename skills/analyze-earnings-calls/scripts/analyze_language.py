#!/usr/bin/env python3
"""Deterministic executive-language analysis for earnings-call transcripts.

The script accepts a normalized JSON object containing ``calls`` and emits
deterministic language-analysis JSON. This is an analysis-stage artifact, not a
complete renderer payload; report assembly must map it into the report schema
and add source-ledger, KPI, guidance, and narrative fields. Transcript text is
always handled as untrusted data: it is tokenized and scored, never interpreted
as an instruction or executed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
HEDGE_LEXICON_PATH = SKILL_DIR / "references" / "hedge-lexicon.json"
SENTIMENT_LEXICON_PATH = SKILL_DIR / "references" / "sentiment-lexicon.json"
FIXTURE_PATH = SKILL_DIR / "evals" / "fixtures" / "transcript-turns.json"

SCHEMA_VERSION = "1.0.0"
ANALYZER_VERSION = "1.1.0"
MAX_CALLS = 4
MIN_SUBSTANTIVE_WORDS = 5
PROBABILITY_SUM_TOLERANCE = 1e-6
ALLOWED_ROLES = {"ceo", "cfo", "executive"}
EXCLUDED_ROLES = {"analyst", "operator", "ir", "unknown"}
GENERIC_SPEAKERS = {
    "",
    "unknown",
    "operator",
    "conference call operator",
    "analyst",
    "management",
    "executive",
    "speaker",
    "unidentified speaker",
    "investor relations",
}

TOKEN_RE = re.compile(
    r"[A-Za-z]+(?:['’][A-Za-z]+)?|(?:[$€£¥])?\d+(?:,\d{3})*(?:\.\d+)?%?"
)
WORD_RE = re.compile(r"\b(?:[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?:\.\d+)?)\b")
SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.MULTILINE)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _round(value: float, digits: int = 3) -> float:
    result = round(float(value), digits)
    return 0.0 if result == -0.0 else result


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "call"


def _normalize_role(value: Any) -> str:
    role = _text(value).lower().replace("&", "and")
    role = re.sub(r"[^a-z]+", " ", role).strip()
    if any(term in role for term in ("investor relations", "equity analyst", "research analyst")):
        return "ir" if "investor relations" in role else "analyst"
    if "operator" in role or "moderator" in role or "conference host" in role:
        return "operator"
    if role in {"ceo", "chief executive officer", "chief executive"}:
        return "ceo"
    if role in {"cfo", "chief financial officer", "chief financial"}:
        return "cfo"
    if role in {"executive", "company executive", "other executive", "management executive"}:
        return "executive"
    if "analyst" in role:
        return "analyst"
    if role in {"ir", "investor relations", "head of investor relations", "vp investor relations"}:
        return "ir"
    executive_abbreviations = {
        "coo", "cto", "cio", "cmo", "cro", "cpo", "cao", "clo",
        "chief operating officer", "chief technology officer",
        "chief information officer", "chief marketing officer",
        "chief revenue officer", "chief product officer",
        "chief accounting officer", "chief legal officer",
    }
    if (
        role in executive_abbreviations
        or re.search(r"\bchief\b.+\bofficer\b", role)
        or re.search(r"\b(?:coo|cto|cio|cmo|cro|cpo|cao|clo)\b", role)
        or re.search(r"\b(?:executive|senior) vice president\b", role)
        or re.search(r"\b(?:evp|svp)\b", role)
        or re.search(r"\bpresident\b", role)
        or role in {"general counsel", "treasurer"}
    ):
        return "executive"
    return role or "unknown"


def _normalize_section(value: Any) -> str:
    section = _text(value, "unknown").lower().replace("&", "and")
    section = re.sub(r"[^a-z0-9]+", "_", section).strip("_")
    aliases = {
        "prepared": "prepared_remarks",
        "prepared_remark": "prepared_remarks",
        "remarks": "prepared_remarks",
        "questions_and_answers": "qa",
        "question_and_answer": "qa",
        "q_and_a": "qa",
        "q_a": "qa",
        "safeharbor": "safe_harbor",
        "safe_harbour": "safe_harbor",
        "forward_looking_statements": "safe_harbor",
        "operator_instructions": "operator",
    }
    return aliases.get(section, section or "unknown")


def _is_named_executive(turn: Mapping[str, Any]) -> Tuple[bool, str]:
    role = _normalize_role(turn.get("role"))
    speaker = _text(turn.get("speaker") or turn.get("speaker_display"))
    if role not in ALLOWED_ROLES or role in EXCLUDED_ROLES:
        return False, "excluded_role"
    if speaker.lower() in GENERIC_SPEAKERS or not re.search(r"[A-Za-z]", speaker):
        return False, "unnamed_speaker"
    section = _normalize_section(turn.get("section"))
    if section in {"operator", "safe_harbor"}:
        return False, f"excluded_section:{section}"
    text = _text(turn.get("text"))
    if not text:
        return False, "empty_text"
    if _word_count(text) <= 0:
        return False, "zero_word_text"
    return True, "included"


def _normalization_exclusion_reason(turn: Mapping[str, Any]) -> Optional[str]:
    """Return a conservative exclusion reason for normalized uncertainty flags."""

    direct_flags = {
        "excluded": "normalization_excluded",
        "exclude_from_analysis": "normalization_excluded",
        "overlap_unresolved": "unresolved_overlap",
        "speaker_uncertain": "uncertain_attribution",
        "attribution_uncertain": "uncertain_attribution",
        "unresolved": "normalization_unresolved",
        "is_duplicate": "duplicate_turn",
        "duplicate": "duplicate_turn",
    }
    if turn.get("include") is False or turn.get("included") is False:
        return "normalization_excluded"
    if _text(turn.get("duplicate_of")):
        return "duplicate_turn"
    for key, reason in direct_flags.items():
        if turn.get(key) is True:
            return reason

    raw_flags = turn.get("normalization_flags", turn.get("flags"))
    if isinstance(raw_flags, dict):
        for key, reason in direct_flags.items():
            if raw_flags.get(key) is True:
                return reason
        if raw_flags.get("include") is False or raw_flags.get("included") is False:
            return "normalization_excluded"
        if _text(raw_flags.get("duplicate_of")):
            return "duplicate_turn"
    elif isinstance(raw_flags, (list, tuple, set)):
        normalized = {str(item).strip().lower().replace("-", "_") for item in raw_flags}
        flag_reasons = {
            "excluded": "normalization_excluded",
            "exclude_from_analysis": "normalization_excluded",
            "overlap_unresolved": "unresolved_overlap",
            "speaker_uncertain": "uncertain_attribution",
            "attribution_uncertain": "uncertain_attribution",
            "unresolved": "normalization_unresolved",
            "duplicate": "duplicate_turn",
            "caption_retry": "duplicate_turn",
        }
        for flag, reason in flag_reasons.items():
            if flag in normalized:
                return reason
    return None


def _sentiment_model_provenance(
    turn: Mapping[str, Any], call: Mapping[str, Any]
) -> Optional[Dict[str, str]]:
    """Canonicalize explicit, hash-backed model provenance from a turn or call."""

    candidate = turn.get("sentiment_model")
    if not isinstance(candidate, dict):
        candidate = call.get("sentiment_model")
    if not isinstance(candidate, dict):
        return None
    identifier = _text(candidate.get("identifier") or candidate.get("model_id") or candidate.get("name"))
    version = _text(candidate.get("version") or candidate.get("model_version"))
    config_sha256 = _text(
        candidate.get("config_sha256")
        or candidate.get("config_hash")
        or candidate.get("model_config_sha256")
    ).lower()
    asset_sha256 = _text(
        candidate.get("asset_sha256")
        or candidate.get("model_sha256")
        or candidate.get("weights_sha256")
    ).lower()
    if (
        not identifier
        or not version
        or not re.fullmatch(r"[0-9a-f]{64}", config_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", asset_sha256)
    ):
        return None
    return {
        "identifier": identifier,
        "version": version,
        "config_sha256": config_sha256,
        "asset_sha256": asset_sha256,
    }


def _parse_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    for candidate in (raw, raw[:10]):
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            pass
    return None


def _quarter_rank(call: Mapping[str, Any]) -> Tuple[int, int, int, int, str]:
    """Sort primarily by issuer fiscal sequence so an undated missing slot stays in place."""

    parsed = _parse_date(call.get("call_date") or call.get("date"))
    date_rank = int(parsed.strftime("%Y%m%d")) if parsed else 0
    label = _text(call.get("quarter") or call.get("fiscal_period"))
    year_value = call.get("fiscal_year") or call.get("year")
    quarter_value = call.get("fiscal_quarter")
    try:
        year = int(year_value)
    except (TypeError, ValueError):
        year_match = re.search(r"\bFY\s*(\d{4})\b", label, re.I)
        year = int(year_match.group(1)) if year_match else 0
    try:
        quarter = int(quarter_value)
    except (TypeError, ValueError):
        quarter_match = re.search(r"\bQ([1-4])\b", label, re.I)
        quarter = int(quarter_match.group(1)) if quarter_match else 0
    has_fiscal_sequence = int(year > 0 and 1 <= quarter <= 4)
    return has_fiscal_sequence, year, quarter, date_rank, label


def _call_meta(call: Mapping[str, Any], index: int) -> Dict[str, Any]:
    quarter = _text(call.get("quarter") or call.get("fiscal_period"))
    if not quarter:
        fiscal_q = call.get("fiscal_quarter")
        fiscal_y = call.get("fiscal_year")
        quarter = f"Q{fiscal_q} FY{fiscal_y}" if fiscal_q and fiscal_y else f"Call {index + 1}"
    call_date = _text(call.get("call_date") or call.get("date"))
    call_id = _text(call.get("call_id") or call.get("id"))
    if not call_id:
        call_id = _slug(f"{quarter}-{call_date or index + 1}")
    source = call.get("source") if isinstance(call.get("source"), dict) else {}
    source_url = _text(source.get("url") or call.get("source_url"))
    source_title = _text(source.get("title") or call.get("source_title"))
    source_id = _text(call.get("source_id") or source.get("id"))
    return {
        "call_id": call_id,
        "quarter": quarter,
        "call_date": call_date or None,
        "source_id": source_id or None,
        "source": {
            "title": source_title or None,
            "url": source_url or None,
        },
    }


def _tokenize(text: str) -> List[Dict[str, Any]]:
    return [
        {
            "raw": match.group(0),
            "norm": match.group(0).lower().replace("’", "'"),
            "start": match.start(),
            "end": match.end(),
        }
        for match in TOKEN_RE.finditer(text)
    ]


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _excerpt(text: str, start: int = 0, end: Optional[int] = None, limit: int = 300) -> str:
    if not text:
        return ""
    end = start if end is None else end
    left = max(text.rfind(".", 0, start), text.rfind("?", 0, start), text.rfind("!", 0, start), text.rfind("\n", 0, start))
    left = 0 if left < 0 else left + 1
    candidates = [position for mark in ".!?\n" if (position := text.find(mark, end)) >= 0]
    right = min(candidates) + 1 if candidates else len(text)
    value = re.sub(r"\s+", " ", text[left:right]).strip()
    if len(value) <= limit:
        return value
    center = max(0, start - left)
    window_start = max(0, center - limit // 2)
    window_end = min(len(value), window_start + limit)
    clipped = value[window_start:window_end].strip()
    return ("…" if window_start else "") + clipped + ("…" if window_end < len(value) else "")


def _locator(turn: Mapping[str, Any], turn_index: int, meta: Mapping[str, Any]) -> Dict[str, Any]:
    raw = turn.get("locator")
    locator: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    locator["turn_index"] = turn_index
    if meta["source"].get("url") and not locator.get("source_url"):
        locator["source_url"] = meta["source"]["url"]
    return locator


def _probability_map(turn: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    candidates = [
        turn.get("sentiment_probabilities"),
        turn.get("finbert"),
        turn.get("sentiment"),
        {
            "positive": turn.get("p_positive"),
            "negative": turn.get("p_negative"),
            "neutral": turn.get("p_neutral"),
        },
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            positive = float(candidate.get("positive", candidate.get("pos")))
            negative = float(candidate.get("negative", candidate.get("neg")))
            neutral = float(candidate.get("neutral", candidate.get("neu", 0.0)))
        except (TypeError, ValueError):
            continue
        values = [positive, negative, neutral]
        if any(not math.isfinite(item) or item < 0 or item > 1 for item in values):
            continue
        total = sum(values)
        if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
            continue
        return {
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
        }
    return None


def _compile_phrases(entries: Iterable[Tuple[str, str, float]]) -> Dict[str, List[Dict[str, Any]]]:
    by_first: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for phrase, category, weight in entries:
        norms = [token["norm"] for token in _tokenize(phrase)]
        if not norms:
            continue
        by_first[norms[0]].append(
            {"phrase": phrase.lower(), "tokens": norms, "category": category, "weight": float(weight)}
        )
    for values in by_first.values():
        values.sort(key=lambda item: (-len(item["tokens"]), item["phrase"], item["category"]))
    return by_first


def _numeric_about_context(tokens: Sequence[Mapping[str, Any]], end_index: int) -> bool:
    units = {"percent", "percentage", "basis", "points", "million", "billion", "thousand", "x"}
    for token in tokens[end_index : end_index + 3]:
        raw = str(token["raw"])
        norm = str(token["norm"])
        if re.fullmatch(r"[$€£¥]?\d+(?:,\d{3})*(?:\.\d+)?%?", raw) or norm in units:
            return True
    return False


def _find_hedges(text: str, compiled: Mapping[str, Sequence[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
    tokens = _tokenize(text)
    matches: List[Dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        found: Optional[Mapping[str, Any]] = None
        for candidate in compiled.get(tokens[index]["norm"], []):
            phrase_tokens = candidate["tokens"]
            end_index = index + len(phrase_tokens)
            if end_index > len(tokens):
                continue
            if [token["norm"] for token in tokens[index:end_index]] != phrase_tokens:
                continue
            if candidate["phrase"] == "may" and tokens[index]["raw"] == "May":
                continue
            if candidate["phrase"] == "about" and not _numeric_about_context(tokens, end_index):
                continue
            found = candidate
            break
        if found is None:
            index += 1
            continue
        end_index = index + len(found["tokens"])
        start_char = tokens[index]["start"]
        end_char = tokens[end_index - 1]["end"]
        matches.append(
            {
                "term": found["phrase"],
                "category": found["category"],
                "start": start_char,
                "end": end_char,
                "excerpt": _excerpt(text, start_char, end_char),
            }
        )
        index = end_index
    return matches


def _find_sentiment(
    text: str,
    compiled: Mapping[str, Sequence[Mapping[str, Any]]],
    negators: Sequence[str],
    intensifiers: Mapping[str, float],
    diminishers: Mapping[str, float],
) -> Dict[str, Any]:
    tokens = _tokenize(text)
    matches: List[Dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        found: Optional[Mapping[str, Any]] = None
        for candidate in compiled.get(tokens[index]["norm"], []):
            phrase_tokens = candidate["tokens"]
            end_index = index + len(phrase_tokens)
            if end_index <= len(tokens) and [item["norm"] for item in tokens[index:end_index]] == phrase_tokens:
                found = candidate
                break
        if found is None:
            index += 1
            continue
        start_window = max(0, index - 3)
        context = [item["norm"] for item in tokens[start_window:index]]
        polarity = 1.0 if found["category"] == "positive" else -1.0
        negated = any(term in negators for term in context)
        if negated:
            polarity *= -1.0
        multiplier = 1.0
        for term in reversed(context):
            if term in intensifiers:
                multiplier *= float(intensifiers[term])
                break
            if term in diminishers:
                multiplier *= float(diminishers[term])
                break
        signed_weight = polarity * float(found["weight"]) * multiplier
        end_index = index + len(found["tokens"])
        start_char = tokens[index]["start"]
        end_char = tokens[end_index - 1]["end"]
        matches.append(
            {
                "term": found["phrase"],
                "base_polarity": found["category"],
                "negated": negated,
                "weight": _round(signed_weight, 4),
                "token_count": len(found["tokens"]),
                "start": start_char,
                "end": end_char,
                "excerpt": _excerpt(text, start_char, end_char),
            }
        )
        index = end_index
    numerator = sum(float(item["weight"]) for item in matches)
    denominator = sum(abs(float(item["weight"])) for item in matches)
    score = 100.0 * numerator / denominator if denominator else 0.0
    return {
        "score": _round(_clamp(score, -100.0, 100.0), 2),
        "numerator": numerator,
        "denominator": denominator,
        "matches": matches,
        "matched_token_count": sum(int(item["token_count"]) for item in matches),
    }


def _sentiment_label(score: float) -> str:
    if score >= 20:
        return "positive"
    if score <= -20:
        return "negative"
    return "neutral"


def _direction(delta: Optional[float], threshold: float = 1.0) -> str:
    if delta is None:
        return "not_available"
    if delta > threshold:
        return "improved"
    if delta < -threshold:
        return "deteriorated"
    return "stable"


def _lexical_confidence(matched_terms: int, token_coverage: float) -> Dict[str, Any]:
    if matched_terms <= 0:
        value = 0.0
    else:
        term_component = min(0.45, 0.12 * math.sqrt(matched_terms))
        coverage_component = min(0.35, token_coverage * 5.0)
        value = min(0.8, 0.2 + term_component + coverage_component)
    return {
        "score": _round(value, 3),
        "basis": "Bundled lexical confidence rises with matched-term count and token coverage; capped at 0.8 because no model probabilities are available.",
    }


def _finbert_confidence(probabilities: Sequence[Mapping[str, float]], weights: Sequence[int]) -> Dict[str, Any]:
    total_weight = sum(weights)
    if not probabilities or total_weight <= 0:
        value = 0.0
    else:
        value = sum(max(item.values()) * weight for item, weight in zip(probabilities, weights)) / total_weight
    return {
        "score": _round(value, 3),
        "basis": "Token-weighted mean of each included turn's maximum normalized class probability.",
    }


def _aggregate_sentiment(turns: Sequence[Mapping[str, Any]], method: str) -> Dict[str, Any]:
    word_count = sum(int(turn["word_count"]) for turn in turns)
    if method == "finbert_probabilities":
        weighted_numerator = sum(float(turn["score"]) * int(turn["word_count"]) for turn in turns)
        score = weighted_numerator / word_count if word_count else 0.0
        probabilities = [turn["probabilities"] for turn in turns]
        weights = [int(turn["word_count"]) for turn in turns]
        confidence = _finbert_confidence(probabilities, weights)
        coverage = {
            "eligible_turns": len(turns),
            "scored_turns": len(turns),
            "turn_coverage_ratio": 1.0 if turns else 0.0,
            "eligible_words": word_count,
            "matched_terms": None,
            "matched_token_count": None,
            "token_coverage_ratio": None,
        }
    else:
        numerator = sum(float(turn["sentiment_numerator"]) for turn in turns)
        denominator = sum(float(turn["sentiment_denominator"]) for turn in turns)
        score = 100.0 * numerator / denominator if denominator else 0.0
        matched_terms = sum(len(turn["sentiment_matches"]) for turn in turns)
        matched_token_count = sum(int(turn["matched_token_count"]) for turn in turns)
        token_coverage = matched_token_count / word_count if word_count else 0.0
        confidence = _lexical_confidence(matched_terms, token_coverage)
        coverage = {
            "eligible_turns": len(turns),
            "scored_turns": sum(1 for turn in turns if turn["sentiment_matches"]),
            "turn_coverage_ratio": _round(
                sum(1 for turn in turns if turn["sentiment_matches"]) / len(turns), 3
            ) if turns else 0.0,
            "eligible_words": word_count,
            "matched_terms": matched_terms,
            "matched_token_count": matched_token_count,
            "token_coverage_ratio": _round(token_coverage, 4),
        }
    score = _round(_clamp(score, -100.0, 100.0), 2)
    return {
        "score": score,
        "label": _sentiment_label(score),
        "word_count": word_count,
        "turn_count": len(turns),
        "coverage": coverage,
        "confidence": confidence,
    }


def _sentiment_lens(turns: Sequence[Mapping[str, Any]], method: str, field: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for turn in turns:
        groups[str(turn[field])].append(turn)
    output: List[Dict[str, Any]] = []
    for name in sorted(groups):
        aggregate = _aggregate_sentiment(groups[name], method)
        aggregate[field] = name
        output.append(aggregate)
    return output


def _sentiment_evidence(turns: Sequence[Mapping[str, Any]], limit: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    base: List[Dict[str, Any]] = []
    for turn in turns:
        if abs(float(turn["score"])) < 0.0001:
            continue
        evidence = {
            "call_id": turn["call_id"],
            "quarter": turn["quarter"],
            "turn_id": turn["turn_id"],
            "speaker": turn["speaker"],
            "role": turn["role"],
            "section": turn["section"],
            "score": _round(float(turn["score"]), 2),
            "excerpt": _excerpt(turn["text"], 0, len(turn["text"])),
            "locator": turn["locator"],
        }
        if turn.get("sentiment_matches"):
            evidence["matched_terms"] = [
                {
                    "term": item["term"],
                    "weight": item["weight"],
                    "negated": item["negated"],
                }
                for item in turn["sentiment_matches"][:8]
            ]
        base.append(evidence)
    positive = sorted(
        (item for item in base if item["score"] > 0),
        key=lambda item: (-item["score"], item["locator"].get("turn_index", 0), item["speaker"]),
    )[:limit]
    negative = sorted(
        (item for item in base if item["score"] < 0),
        key=lambda item: (item["score"], item["locator"].get("turn_index", 0), item["speaker"]),
    )[:limit]
    return {"positive": positive, "negative": negative}


def _rate(count: int, words: int) -> float:
    return _round(1000.0 * count / words, 3) if words else 0.0


def _category_rows(matches: Sequence[Mapping[str, Any]], words: int, categories: Sequence[str]) -> List[Dict[str, Any]]:
    counts = Counter(str(item["category"]) for item in matches)
    return [
        {
            "category": category,
            "count": int(counts.get(category, 0)),
            "per_1000_words": _rate(int(counts.get(category, 0)), words),
        }
        for category in categories
    ]


def _hedge_group_rows(
    turns: Sequence[Mapping[str, Any]], field: str, categories: Sequence[str]
) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for turn in turns:
        groups[str(turn[field])].append(turn)
    rows: List[Dict[str, Any]] = []
    for name in sorted(groups):
        group_turns = groups[name]
        words = sum(int(turn["word_count"]) for turn in group_turns)
        matches = [match for turn in group_turns for match in turn["hedge_matches"]]
        rows.append(
            {
                field: name,
                "count": len(matches),
                "per_1000_words": _rate(len(matches), words),
                "word_count": words,
                "by_category": _category_rows(matches, words, categories),
            }
        )
    return rows


def _hedge_evidence(turns: Sequence[Mapping[str, Any]], limit: int = 16) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    for turn in turns:
        for match in turn["hedge_matches"]:
            evidence.append(
                {
                    "call_id": turn["call_id"],
                    "quarter": turn["quarter"],
                    "turn_id": turn["turn_id"],
                    "speaker": turn["speaker"],
                    "role": turn["role"],
                    "section": turn["section"],
                    "term": match["term"],
                    "category": match["category"],
                    "excerpt": match["excerpt"],
                    "locator": turn["locator"],
                }
            )
    evidence.sort(
        key=lambda item: (
            item["locator"].get("turn_index", 0),
            item["category"],
            item["term"],
            item["speaker"],
        )
    )
    return evidence[:limit]


def _prepare_calls(payload: Mapping[str, Any]) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], Dict[str, Any]]:
    raw_calls = payload.get("calls")
    if not isinstance(raw_calls, list):
        raise ValueError("Input must contain a 'calls' array")
    valid_calls = [(dict(item), index) for index, item in enumerate(raw_calls) if isinstance(item, dict)]
    if len(valid_calls) < MAX_CALLS:
        raise ValueError(
            "Exactly four target fiscal slots are required. Supply at least four call records; "
            "materialize an unavailable slot as a record with status='missing' or coverage='missing'."
        )
    ordered = sorted(valid_calls, key=lambda pair: _quarter_rank(pair[0]), reverse=True)
    selected = ordered[:MAX_CALLS]
    prepared: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for output_index, (call, original_index) in enumerate(selected):
        prepared.append((call, _call_meta(call, output_index)))
    call_ids = [meta["call_id"] for _, meta in prepared]
    quarters = [meta["quarter"] for _, meta in prepared]
    if len(set(call_ids)) != MAX_CALLS or len(set(quarters)) != MAX_CALLS:
        raise ValueError("The four selected target records must have unique call_id and fiscal-quarter labels.")
    input_summary = {
        "calls_received": len(raw_calls),
        "valid_calls_received": len(valid_calls),
        "calls_analyzed": len(prepared),
        "calls_omitted_by_four_call_limit": max(0, len(valid_calls) - MAX_CALLS),
        "ordering": "newest_first_by_fiscal_sequence_then_call_date",
        "max_calls": MAX_CALLS,
        "normalization": payload.get("normalization") if isinstance(payload.get("normalization"), dict) else None,
    }
    return prepared, input_summary



def _coverage_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"complete", "full", "available", "yes", "true"}:
            return True
        if normalized in {"partial", "incomplete", "missing", "unavailable", "no", "false"}:
            return False
    if isinstance(value, dict):
        if isinstance(value.get("complete"), bool):
            return bool(value["complete"])
        for key in ("status", "coverage_status", "completeness"):
            parsed = _coverage_value(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _section_coverage(call: Mapping[str, Any], section: str) -> Optional[bool]:
    aliases = {
        "prepared_remarks": ("prepared_remarks", "prepared", "remarks"),
        "qa": ("qa", "q_and_a", "questions_and_answers"),
    }[section]
    for key in (f"{section}_complete", *(f"{alias}_complete" for alias in aliases)):
        if key in call:
            parsed = _coverage_value(call.get(key))
            if parsed is not None:
                return parsed
    for container_key in ("section_coverage", "coverage"):
        container = call.get(container_key)
        if isinstance(container, dict):
            nested = container.get("sections")
            mappings = [container, nested] if isinstance(nested, dict) else [container]
            for mapping in mappings:
                for alias in aliases:
                    if alias in mapping:
                        parsed = _coverage_value(mapping.get(alias))
                        if parsed is not None:
                            return parsed
        else:
            parsed = _coverage_value(container)
            if parsed is not None:
                return parsed
    return None


def _section_gap_reason(section: str, row: Mapping[str, Any]) -> str:
    label = "prepared remarks" if section == "prepared_remarks" else "Q&A"
    if not row.get("source_ready", False):
        return "A stable transcript source_id was not supplied for this target call."
    if row["coverage_complete"] is None:
        return f"Explicit complete {label} coverage was not supplied."
    if row["coverage_complete"] is False:
        return f"{label} is missing or incomplete."
    return f"{label} contains fewer than {MIN_SUBSTANTIVE_WORDS} included executive words."


def _select_headline_scope(
    prepared_calls: Sequence[Tuple[Mapping[str, Any], Mapping[str, Any]]],
    eligible_turns: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Freeze one scope while retaining incomplete target calls as visible gaps."""

    per_call: List[Dict[str, Any]] = []
    for call, meta in prepared_calls:
        call_turns = [turn for turn in eligible_turns if turn["call_id"] == meta["call_id"]]
        section_rows: Dict[str, Dict[str, Any]] = {}
        for section in ("prepared_remarks", "qa"):
            section_turns = [turn for turn in call_turns if turn["section"] == section]
            words = sum(int(turn["word_count"]) for turn in section_turns)
            coverage_complete = _section_coverage(call, section)
            substantive = words >= MIN_SUBSTANTIVE_WORDS
            source_ready = bool(meta.get("source_id"))
            available = substantive and coverage_complete is True and source_ready
            section_rows[section] = {
                "turn_count": len(section_turns),
                "word_count": words,
                "substantive": substantive,
                "coverage_complete": coverage_complete,
                "source_ready": source_ready,
                "available_for_headline": available,
                "gap_reason": None if available else _section_gap_reason(
                    section,
                    {"coverage_complete": coverage_complete, "source_ready": source_ready},
                ),
            }
        per_call.append({**meta, "sections": section_rows})

    prepared_rows = [
        row for row in per_call
        if row["sections"]["prepared_remarks"]["available_for_headline"]
    ]
    if prepared_rows and all(
        row["sections"]["qa"]["available_for_headline"] for row in prepared_rows
    ):
        name = "executive_total"
        sections = ["prepared_remarks", "qa"]
        reason = (
            "Every target call with usable prepared remarks also has explicitly complete, "
            "substantive executive Q&A; unavailable target calls remain gaps."
        )
    elif prepared_rows:
        name = "executive_prepared"
        sections = ["prepared_remarks"]
        reason = (
            "Prepared remarks are the broadest explicitly complete common management scope; "
            "calls without complete prepared coverage remain gaps."
        )
    else:
        name = "unavailable"
        sections = []
        reason = (
            "No target call has explicitly complete, substantive executive prepared remarks; "
            "no headline language series is calculated."
        )

    available_count = 0
    for row in per_call:
        if name == "unavailable":
            available = False
            gap_reason = reason
        else:
            missing_sections = [
                section for section in sections
                if not row["sections"][section]["available_for_headline"]
            ]
            available = not missing_sections
            gap_reason = None if available else " ".join(
                row["sections"][section]["gap_reason"] for section in missing_sections
            )
        row["available_for_selected_scope"] = available
        row["gap_reason"] = gap_reason
        available_count += int(available)

    return {
        "name": name,
        "available": available_count > 0,
        "sections": sections,
        "selection_reason": reason,
        "minimum_substantive_words_per_section": MIN_SUBSTANTIVE_WORDS,
        "explicit_complete_coverage_required": True,
        "available_call_count": available_count,
        "gap_call_count": len(per_call) - available_count,
        "per_call": per_call,
    }


def analyze(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Analyze a transcript payload and return deterministic JSON-serializable data."""

    hedge_lexicon = _read_json(HEDGE_LEXICON_PATH)
    sentiment_lexicon = _read_json(SENTIMENT_LEXICON_PATH)
    prepared_calls, input_summary = _prepare_calls(payload)
    analyzer_config = {
        "schema_version": SCHEMA_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "max_calls": MAX_CALLS,
        "minimum_substantive_words_per_section": MIN_SUBSTANTIVE_WORDS,
        "probability_sum_tolerance": PROBABILITY_SUM_TOLERANCE,
        "exact_target_slot_count": MAX_CALLS,
        "explicit_complete_section_coverage_required": True,
        "stable_source_id_required_for_scoring": True,
        "allowed_roles": sorted(ALLOWED_ROLES),
        "excluded_roles": sorted(EXCLUDED_ROLES),
        "excluded_sections": ["operator", "safe_harbor"],
        "sentiment_neutral_band": [-20, 20],
        "hedge_normalization": "per_1000_words",
    }
    hashes = {
        "normalized_input_sha256": _sha256_json(payload),
        "hedge_lexicon_sha256": _sha256_file(HEDGE_LEXICON_PATH),
        "sentiment_lexicon_sha256": _sha256_file(SENTIMENT_LEXICON_PATH),
        "analyzer_config_sha256": _sha256_json(analyzer_config),
    }

    hedge_entries: List[Tuple[str, str, float]] = []
    categories = sorted(hedge_lexicon["categories"])
    for category in categories:
        for phrase in hedge_lexicon["categories"][category]:
            hedge_entries.append((phrase, category, 1.0))
    compiled_hedges = _compile_phrases(hedge_entries)

    sentiment_entries: List[Tuple[str, str, float]] = []
    for polarity in ("positive", "negative"):
        for phrase, weight in sentiment_lexicon[polarity].items():
            sentiment_entries.append((phrase, polarity, float(weight)))
    compiled_sentiment = _compile_phrases(sentiment_entries)

    eligible_raw: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], int]] = []
    excluded_counts: Counter[str] = Counter()
    excluded_examples: List[Dict[str, Any]] = []
    total_turns = 0
    seen_turn_keys: set[Tuple[str, str]] = set()
    for call, meta in prepared_calls:
        turns = call.get("turns")
        if not isinstance(turns, list):
            turns = []
        for turn_index, raw_turn in enumerate(turns):
            total_turns += 1
            if not isinstance(raw_turn, dict):
                excluded_counts["invalid_turn"] += 1
                continue
            turn = dict(raw_turn)
            reason = _normalization_exclusion_reason(turn)
            explicit_turn_id = _text(turn.get("turn_id") or turn.get("id"))
            if reason is None and explicit_turn_id:
                key = (meta["call_id"], explicit_turn_id)
                if key in seen_turn_keys:
                    reason = "duplicate_turn_id"
                else:
                    seen_turn_keys.add(key)
            if reason is None:
                included, reason = _is_named_executive(turn)
            else:
                included = False
            if not included:
                excluded_counts[reason] += 1
                if len(excluded_examples) < 12:
                    excluded_examples.append(
                        {
                            "call_id": meta["call_id"],
                            "turn_index": turn_index,
                            "turn_id": explicit_turn_id or None,
                            "speaker": _text(turn.get("speaker") or turn.get("speaker_display")) or None,
                            "role": _normalize_role(turn.get("role")),
                            "section": _normalize_section(turn.get("section")),
                            "reason": reason,
                        }
                    )
                continue
            eligible_raw.append((turn, call, meta, turn_index))

    probability_rows = [_probability_map(turn) for turn, _, _, _ in eligible_raw]
    provenance_rows = [
        _sentiment_model_provenance(turn, call) for turn, call, _, _ in eligible_raw
    ]
    unique_provenance = {
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in provenance_rows
        if item is not None
    }
    all_have_probabilities = (
        bool(eligible_raw)
        and all(item is not None for item in probability_rows)
        and all(item is not None for item in provenance_rows)
        and len(unique_provenance) == 1
    )
    sentiment_method = "finbert_probabilities" if all_have_probabilities else "bundled_finance_lexicon"
    if all_have_probabilities:
        model_provenance = next(item for item in provenance_rows if item is not None)
        method_detail = {
            "name": sentiment_method,
            "identifier": model_provenance["identifier"],
            "version": model_provenance["version"],
            "asset_sha256": model_provenance["asset_sha256"],
            "model_config_sha256": model_provenance["config_sha256"],
            "formula": "100 * word-weighted mean(p_positive - p_negative); every included turn must use one explicit model identifier, version, asset hash, and config hash.",
            "selection_reason": "Every eligible executive turn supplied valid class probabilities with identical explicit model provenance.",
        }
    else:
        method_detail = {
            "name": sentiment_method,
            "identifier": sentiment_lexicon["name"],
            "version": sentiment_lexicon["version"],
            "asset_sha256": hashes["sentiment_lexicon_sha256"],
            "model_config_sha256": None,
            "formula": sentiment_lexicon["method"],
            "selection_reason": "Valid probabilities and identical explicit model provenance were not available for every eligible executive turn, so one bundled lexical method was applied to every turn and quarter.",
        }

    method_detail["analyzer_config_sha256"] = hashes["analyzer_config_sha256"]
    method_detail["sentiment_lexicon_sha256"] = hashes["sentiment_lexicon_sha256"]
    analyzed_turns: List[Dict[str, Any]] = []
    negators = [str(item).lower() for item in sentiment_lexicon["negators"]]
    for turn, call, meta, turn_index in eligible_raw:
        turn_text = _text(turn.get("text"))
        words = _word_count(turn_text)
        probability = _probability_map(turn)
        hedge_matches = _find_hedges(turn_text, compiled_hedges)
        lexical = _find_sentiment(
            turn_text,
            compiled_sentiment,
            negators,
            sentiment_lexicon["intensifiers"],
            sentiment_lexicon["diminishers"],
        )
        if sentiment_method == "finbert_probabilities":
            assert probability is not None
            turn_score = 100.0 * (probability["positive"] - probability["negative"])
        else:
            turn_score = lexical["score"]
        analyzed_turns.append(
            {
                **meta,
                "turn_id": _text(turn.get("turn_id") or turn.get("id")) or f'{meta["call_id"]}:turn-{turn_index}',
                "speaker": _text(turn.get("speaker") or turn.get("speaker_display")),
                "role": _normalize_role(turn.get("role")),
                "section": _normalize_section(turn.get("section")),
                "text": turn_text,
                "word_count": words,
                "locator": _locator(turn, turn_index, meta),
                "probabilities": probability,
                "score": _round(turn_score, 2),
                "sentiment_numerator": lexical["numerator"],
                "sentiment_denominator": lexical["denominator"],
                "sentiment_matches": lexical["matches"],
                "matched_token_count": lexical["matched_token_count"],
                "hedge_matches": hedge_matches,
            }
        )

    headline_scope = _select_headline_scope(prepared_calls, analyzed_turns)
    scope_rows = {row["call_id"]: row for row in headline_scope["per_call"]}
    available_call_ids = {
        call_id for call_id, row in scope_rows.items()
        if row["available_for_selected_scope"]
    }
    headline_turns = [
        turn for turn in analyzed_turns
        if turn["call_id"] in available_call_ids
        and turn["section"] in headline_scope["sections"]
    ]
    headline_words = sum(int(turn["word_count"]) for turn in headline_turns)
    sentiment_quarters: List[Dict[str, Any]] = []
    hedge_quarters: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if prepared_calls and not analyzed_turns:
        warnings.append("No turns met the named-executive role, normalization, and section filters.")
    if input_summary["calls_omitted_by_four_call_limit"]:
        warnings.append(
            f"Omitted {input_summary['calls_omitted_by_four_call_limit']} older call(s); analysis is capped at the four newest fiscal calls."
        )
    if not headline_scope["available"]:
        warnings.append(headline_scope["selection_reason"])
    elif headline_scope["gap_call_count"]:
        warnings.append(
            f"{headline_scope['gap_call_count']} of four target call(s) are explicit gaps under {headline_scope['name']}; available calls remain scored."
        )

    for _, meta in prepared_calls:
        scope_row = scope_rows[meta["call_id"]]
        quarter_available = bool(scope_row["available_for_selected_scope"])
        gap_reason = scope_row["gap_reason"]
        diagnostic_turns = [turn for turn in analyzed_turns if turn["call_id"] == meta["call_id"]]
        quarter_turns = [turn for turn in headline_turns if turn["call_id"] == meta["call_id"]]
        sentiment_aggregate = _aggregate_sentiment(quarter_turns, sentiment_method)
        sentiment_aggregate["available"] = quarter_available
        sentiment_aggregate["headline_scope"] = headline_scope["name"]
        sentiment_aggregate["gap_reason"] = None if quarter_available else gap_reason
        if not quarter_available:
            sentiment_aggregate["score"] = None
            sentiment_aggregate["label"] = "not_available"
        sentiment_quarters.append(
            {
                **meta,
                **sentiment_aggregate,
                "diagnostic_lenses_scope": "all_eligible_executive_turns_supplementary",
                "by_section": _sentiment_lens(diagnostic_turns, sentiment_method, "section"),
                "by_speaker": _sentiment_lens(diagnostic_turns, sentiment_method, "speaker"),
                "top_evidence": _sentiment_evidence(quarter_turns),
                "included_turn_ids": [turn["turn_id"] for turn in quarter_turns],
            }
        )

        words = sum(int(turn["word_count"]) for turn in quarter_turns)
        hedge_matches = [match for turn in quarter_turns for match in turn["hedge_matches"]]
        term_counts = Counter(str(item["term"]) for item in hedge_matches)
        hedge_quarters.append(
            {
                **meta,
                "available": quarter_available,
                "headline_scope": headline_scope["name"],
                "gap_reason": None if quarter_available else gap_reason,
                "count": len(hedge_matches) if quarter_available else None,
                "per_1000_words": _rate(len(hedge_matches), words) if quarter_available else None,
                "word_count": words,
                "included_turns": len(quarter_turns),
                "included_turn_ids": [turn["turn_id"] for turn in quarter_turns],
                "by_category": _category_rows(hedge_matches, words, categories),
                "by_section": _hedge_group_rows(diagnostic_turns, "section", categories),
                "by_speaker": _hedge_group_rows(diagnostic_turns, "speaker", categories),
                "top_terms": [
                    {"term": term, "count": count}
                    for term, count in sorted(term_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
                ],
                "diagnostics": {
                    "scope": "all_eligible_executive_turns_supplementary",
                    "by_section": _hedge_group_rows(diagnostic_turns, "section", categories),
                    "by_speaker": _hedge_group_rows(diagnostic_turns, "speaker", categories),
                    "evidence": _hedge_evidence(diagnostic_turns),
                },
                "evidence": _hedge_evidence(quarter_turns),
            }
        )
    sentiment_trend = [
        {
            "call_id": item["call_id"],
            "quarter": item["quarter"],
            "call_date": item["call_date"],
            "available": item["available"],
            "headline_scope": item["headline_scope"],
            "score": item["score"],
            "label": item["label"],
            "confidence": item["confidence"]["score"],
        }
        for item in sentiment_quarters
    ]
    hedge_trend = [
        {
            "call_id": item["call_id"],
            "quarter": item["quarter"],
            "call_date": item["call_date"],
            "available": item["available"],
            "headline_scope": item["headline_scope"],
            "count": item["count"],
            "per_1000_words": item["per_1000_words"],
        }
        for item in hedge_quarters
    ]

    sentiment_comparison: Dict[str, Any]
    hedge_comparison: Dict[str, Any]
    if len(sentiment_quarters) >= 2 and all(item["available"] for item in sentiment_quarters[:2]):
        current = sentiment_quarters[0]
        prior = sentiment_quarters[1]
        delta = _round(current["score"] - prior["score"], 2)
        sentiment_comparison = {
            "available": True,
            "current_call_id": current["call_id"],
            "current_quarter": current["quarter"],
            "current_score": current["score"],
            "prior_call_id": prior["call_id"],
            "prior_quarter": prior["quarter"],
            "prior_score": prior["score"],
            "delta": delta,
            "direction": _direction(delta),
        }
    else:
        sentiment_comparison = {
            "available": False,
            "current_call_id": sentiment_quarters[0]["call_id"] if sentiment_quarters else None,
            "current_quarter": sentiment_quarters[0]["quarter"] if sentiment_quarters else None,
            "current_score": sentiment_quarters[0]["score"] if sentiment_quarters else None,
            "prior_call_id": None,
            "prior_quarter": None,
            "prior_score": None,
            "delta": None,
            "direction": "not_available",
        }

    if len(hedge_quarters) >= 2 and all(item["available"] for item in hedge_quarters[:2]):
        current_h = hedge_quarters[0]
        prior_h = hedge_quarters[1]
        count_delta = current_h["count"] - prior_h["count"]
        rate_delta = _round(current_h["per_1000_words"] - prior_h["per_1000_words"], 3)
        hedge_comparison = {
            "available": True,
            "current_call_id": current_h["call_id"],
            "current_quarter": current_h["quarter"],
            "current_count": current_h["count"],
            "current_per_1000_words": current_h["per_1000_words"],
            "prior_call_id": prior_h["call_id"],
            "prior_quarter": prior_h["quarter"],
            "prior_count": prior_h["count"],
            "prior_per_1000_words": prior_h["per_1000_words"],
            "count_delta": count_delta,
            "per_1000_words_delta": rate_delta,
            "direction": "more_hedged" if rate_delta > 0.05 else "less_hedged" if rate_delta < -0.05 else "stable",
        }
    else:
        hedge_comparison = {
            "available": False,
            "current_call_id": hedge_quarters[0]["call_id"] if hedge_quarters else None,
            "current_quarter": hedge_quarters[0]["quarter"] if hedge_quarters else None,
            "current_count": hedge_quarters[0]["count"] if hedge_quarters else None,
            "current_per_1000_words": hedge_quarters[0]["per_1000_words"] if hedge_quarters else None,
            "prior_call_id": None,
            "prior_quarter": None,
            "prior_count": None,
            "prior_per_1000_words": None,
            "count_delta": None,
            "per_1000_words_delta": None,
            "direction": "not_available",
        }

    latest_sentiment = None
    if sentiment_quarters:
        latest_q = sentiment_quarters[0]
        latest_sentiment = {
            "call_id": latest_q["call_id"],
            "quarter": latest_q["quarter"],
            "available": latest_q["available"],
            "headline_scope": latest_q["headline_scope"],
            "gap_reason": latest_q["gap_reason"],
            "score": latest_q["score"],
            "label": latest_q["label"],
            "coverage": latest_q["coverage"],
            "confidence": latest_q["confidence"],
            "lenses_scope": latest_q["diagnostic_lenses_scope"],
            "lenses": {
                "by_section": latest_q["by_section"],
                "by_speaker": latest_q["by_speaker"],
            },
            "top_evidence": latest_q["top_evidence"],
        }

    latest_hedges = None
    if hedge_quarters:
        latest_h = hedge_quarters[0]
        latest_hedges = {
            "call_id": latest_h["call_id"],
            "quarter": latest_h["quarter"],
            "available": latest_h["available"],
            "headline_scope": latest_h["headline_scope"],
            "gap_reason": latest_h["gap_reason"],
            "count": latest_h["count"],
            "per_1000_words": latest_h["per_1000_words"],
            "word_count": latest_h["word_count"],
            "by_category": latest_h["by_category"],
            "by_section": latest_h["by_section"],
            "by_speaker": latest_h["by_speaker"],
            "top_terms": latest_h["top_terms"],
            "evidence": latest_h["evidence"],
            "diagnostics": latest_h["diagnostics"],
        }

    diagnostic_words = sum(int(turn["word_count"]) for turn in analyzed_turns)
    included_words = headline_words
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": {
            "name": "analyze_language.py",
            "version": ANALYZER_VERSION,
            "deterministic": True,
            "hashes": hashes,
            "config": analyzer_config,
        },
        "input": input_summary,
        "analysis_scope": {
            "transcript_handling": "untrusted_data_no_instruction_execution",
            "headline": headline_scope,
            "included_roles": sorted(ALLOWED_ROLES),
            "excluded_roles": sorted(EXCLUDED_ROLES),
            "excluded_sections": ["operator", "safe_harbor"],
            "named_speakers_required": True,
            "total_turns_in_selected_calls": total_turns,
            "included_turns": len(headline_turns),
            "included_turn_ids": [turn["turn_id"] for turn in headline_turns],
            "diagnostic_eligible_turns": len(analyzed_turns),
            "diagnostic_eligible_turn_ids": [turn["turn_id"] for turn in analyzed_turns],
            "diagnostic_eligible_words": diagnostic_words,
            "included_words": included_words,
            "excluded_turn_counts": dict(sorted(excluded_counts.items())),
            "excluded_turn_examples": excluded_examples,
        },
        "sentiment": {
            "headline_scope": headline_scope["name"],
            "method": method_detail,
            "scale": {"minimum": -100, "maximum": 100, "neutral_band": [-20, 20]},
            "quarters": sentiment_quarters,
            "trend": sentiment_trend,
            "latest": latest_sentiment,
            "current_vs_prior": sentiment_comparison,
            "coverage": {
                "included_turns": len(headline_turns),
                "included_words": included_words,
                "calls_with_included_turns": sum(1 for item in sentiment_quarters if item["turn_count"] > 0),
                "calls_analyzed": len(sentiment_quarters),
            },
        },
        "hedge_analysis": {
            "headline_scope": headline_scope["name"],
            "method": {
                "analyzer_config_sha256": hashes["analyzer_config_sha256"],
                "hedge_lexicon_sha256": hashes["hedge_lexicon_sha256"],
                "name": "bundled_hedge_lexicon",
                "version": hedge_lexicon["version"],
                "matching": hedge_lexicon["matching"],
                "normalization": "raw count and count per 1,000 included executive words",
                "contextual_filters": hedge_lexicon["contextual_rules"],
            },
            "quarters": hedge_quarters,
            "trend": hedge_trend,
            "latest": latest_hedges,
            "current_vs_prior": hedge_comparison,
            "coverage": {
                "included_turns": len(headline_turns),
                "included_words": included_words,
                "calls_with_included_turns": sum(1 for item in hedge_quarters if item["included_turns"] > 0),
                "calls_analyzed": len(hedge_quarters),
            },
        },
        "warnings": warnings,
    }
    return result


def _stable_json(value: Any, pretty: bool = True) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    ) + "\n"


def _self_test() -> Dict[str, Any]:
    fixture = _read_json(FIXTURE_PATH)
    first = analyze(fixture)
    second = analyze(copy.deepcopy(fixture))
    tests: List[str] = []

    assert _stable_json(first) == _stable_json(second)
    tests.append("byte-stable deterministic JSON")
    assert first["input"]["calls_received"] == 5
    assert first["input"]["calls_analyzed"] == 4
    assert first["input"]["calls_omitted_by_four_call_limit"] == 1
    assert [item["call_id"] for item in first["sentiment"]["quarters"]] == [
        "ACME-2027-Q1",
        "ACME-2026-Q4",
        "ACME-2026-Q3",
        "ACME-2026-Q2",
    ]
    tests.append("four-call cap and newest-first ordering")
    assert first["sentiment"]["method"]["name"] == "bundled_finance_lexicon"
    assert -100 <= first["sentiment"]["latest"]["score"] <= 100
    assert first["sentiment"]["current_vs_prior"]["available"] is True
    tests.append("lexical sentiment scale, trend, and current-v-prior delta")
    assert first["analysis_scope"]["headline"]["name"] == "executive_prepared"
    assert first["sentiment"]["latest"]["coverage"]["eligible_words"] == 29
    assert first["analysis_scope"]["diagnostic_eligible_words"] > first["analysis_scope"]["included_words"]
    latest_hedges = first["hedge_analysis"]["quarters"][0]
    evidence_terms = [item["term"] for item in latest_hedges["evidence"]]
    diagnostic_terms = [item["term"] for item in latest_hedges["diagnostics"]["evidence"]]
    assert latest_hedges["word_count"] == 29
    assert latest_hedges["count"] == 3
    assert evidence_terms.count("about") == 1
    assert evidence_terms.count("may") == 1
    assert evidence_terms.count("could") == 1
    assert "we believe" not in evidence_terms
    assert "we believe" in diagnostic_terms
    assert "believe" not in diagnostic_terms
    tests.append("frozen prepared-only headline plus supplementary Q&A diagnostics")
    tests.append("numeric about filter, month-name May filter, and longest-overlap matching")

    excluded = first["analysis_scope"]["excluded_turn_counts"]
    assert excluded.get("excluded_role", 0) >= 3
    assert excluded.get("excluded_section:operator", 0) >= 1
    assert excluded.get("excluded_section:safe_harbor", 0) >= 1
    assert first["analysis_scope"]["transcript_handling"] == "untrusted_data_no_instruction_execution"
    tests.append("named-executive filtering, section filtering, and prompt-injection-as-data policy")

    probability_fixture = copy.deepcopy(fixture)
    model = {
        "identifier": "synthetic-fixture-finance-classifier",
        "version": "1.0.0",
        "config_sha256": "a" * 64,
        "asset_sha256": "b" * 64,
    }
    for call in probability_fixture["calls"]:
        call["sentiment_model"] = copy.deepcopy(model)
        for turn in call.get("turns", []):
            turn["sentiment_probabilities"] = {"positive": 0.62, "negative": 0.18, "neutral": 0.20}
    probability_result = analyze(probability_fixture)
    assert probability_result["sentiment"]["method"]["name"] == "finbert_probabilities"
    assert probability_result["sentiment"]["method"]["identifier"] == model["identifier"]
    assert probability_result["sentiment"]["method"]["asset_sha256"] == model["asset_sha256"]
    assert probability_result["sentiment"]["latest"]["score"] == 44.0
    assert probability_result["sentiment"]["latest"]["coverage"]["turn_coverage_ratio"] == 1.0
    tests.append("consistent explicit model provenance and probability path")

    invalid_probability_fixture = copy.deepcopy(probability_fixture)
    invalid_probability_fixture["calls"][0]["turns"][0]["sentiment_probabilities"] = {
        "positive": 0.62,
        "negative": 0.18,
        "neutral": 0.30,
    }
    invalid_probability_result = analyze(invalid_probability_fixture)
    assert invalid_probability_result["sentiment"]["method"]["name"] == "bundled_finance_lexicon"
    mixed_model_fixture = copy.deepcopy(probability_fixture)
    mixed_model_fixture["calls"][0]["sentiment_model"]["version"] = "2.0.0"
    assert analyze(mixed_model_fixture)["sentiment"]["method"]["name"] == "bundled_finance_lexicon"
    hashes = invalid_probability_result["generated_by"]["hashes"]
    assert hashes["normalized_input_sha256"] == _sha256_json(invalid_probability_fixture)
    assert all(len(hashes[key]) == 64 for key in (
        "normalized_input_sha256", "hedge_lexicon_sha256",
        "sentiment_lexicon_sha256", "analyzer_config_sha256",
    ))
    tests.append("strict probability/provenance fallback and SHA-256 run manifest")

    gap_fixture = copy.deepcopy(fixture)
    target_gap = next(call for call in gap_fixture["calls"] if call["call_id"] == "ACME-2026-Q3")
    target_gap["section_coverage"]["prepared_remarks"] = "partial"
    gap_result = analyze(gap_fixture)
    assert gap_result["analysis_scope"]["headline"]["name"] == "executive_prepared"
    gap_rows = {item["call_id"]: item for item in gap_result["sentiment"]["quarters"]}
    assert gap_rows["ACME-2026-Q3"]["available"] is False
    assert gap_rows["ACME-2026-Q3"]["score"] is None
    assert gap_rows["ACME-2027-Q1"]["available"] is True
    assert gap_rows["ACME-2026-Q4"]["available"] is True
    assert gap_result["sentiment"]["current_vs_prior"]["available"] is True
    assert gap_result["hedge_analysis"]["current_vs_prior"]["available"] is True
    tests.append("one incomplete target remains a gap without erasing valid adjacent calls")

    unknown_coverage_fixture = copy.deepcopy(fixture)
    current_call = next(call for call in unknown_coverage_fixture["calls"] if call["call_id"] == "ACME-2027-Q1")
    current_call.pop("section_coverage")
    unknown_result = analyze(unknown_coverage_fixture)
    assert unknown_result["sentiment"]["quarters"][0]["available"] is False
    assert "Explicit complete prepared remarks coverage" in unknown_result["sentiment"]["quarters"][0]["gap_reason"]
    assert unknown_result["sentiment"]["current_vs_prior"]["available"] is False
    tests.append("unknown coverage is never inferred complete")

    no_scope_fixture = copy.deepcopy(fixture)
    for call in no_scope_fixture["calls"]:
        call["section_coverage"] = {"prepared_remarks": "partial", "qa": "missing"}
    no_scope_result = analyze(no_scope_fixture)
    assert no_scope_result["analysis_scope"]["headline"]["name"] == "unavailable"
    assert all(item["score"] is None for item in no_scope_result["sentiment"]["quarters"])
    tests.append("no headline is calculated without any explicit complete prepared scope")

    broad_role_fixture = copy.deepcopy(fixture)
    current_call = next(call for call in broad_role_fixture["calls"] if call["call_id"] == "ACME-2027-Q1")
    current_call["turns"].append({
        "turn_id": "ACME-2027-Q1:president",
        "speaker": "Jordan Kim",
        "role": "President and Chief Operating Officer",
        "section": "prepared_remarks",
        "text": "Execution improved and customer growth remained resilient.",
        "locator": {"paragraph": 99},
    })
    broad_result = analyze(broad_role_fixture)
    assert "ACME-2027-Q1:president" in broad_result["analysis_scope"]["included_turn_ids"]
    tests.append("named president and broader C-suite roles normalize to executive")

    excluded_fixture = copy.deepcopy(fixture)
    current_call = next(call for call in excluded_fixture["calls"] if call["call_id"] == "ACME-2027-Q1")
    current_call["turns"].extend([
        {
            "turn_id": "ACME-2027-Q1:turn-1",
            "speaker": "Elena Torres", "role": "CEO", "section": "prepared_remarks",
            "text": "This duplicate retry may not be counted.", "locator": {"paragraph": 100},
        },
        {
            "turn_id": "ACME-2027-Q1:overlap",
            "speaker": "Elena Torres", "role": "CEO", "section": "prepared_remarks",
            "text": "We may perhaps decline.", "overlap_unresolved": True,
            "locator": {"paragraph": 101},
        },
    ])
    excluded_result = analyze(excluded_fixture)
    excluded_counts = excluded_result["analysis_scope"]["excluded_turn_counts"]
    assert excluded_counts.get("duplicate_turn_id") == 1
    assert excluded_counts.get("unresolved_overlap") == 1
    tests.append("duplicate IDs and unresolved speaker overlap are excluded")

    too_short = {"calls": copy.deepcopy(fixture["calls"][:3])}
    try:
        analyze(too_short)
    except ValueError as error:
        assert "Exactly four target fiscal slots" in str(error)
    else:
        raise AssertionError("fewer than four target slot records were accepted")
    tests.append("exact four-target-slot input contract")

    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "fixture": str(FIXTURE_PATH),
        "tests_passed": len(tests),
        "tests": tests,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze sentiment and executive hedging across exactly four target fiscal-call slots."
    )
    parser.add_argument("input", nargs="?", help="Input JSON path; use '-' for stdin")
    parser.add_argument("output_positional", nargs="?", help="Optional output JSON path")
    parser.add_argument("-o", "--output", help="Output JSON path; stdout when omitted")
    parser.add_argument("--compact", action="store_true", help="Emit compact deterministic JSON")
    parser.add_argument("--self-test", action="store_true", help="Run bundled deterministic regression tests")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.self_test:
            result = _self_test()
        else:
            if not args.input:
                raise ValueError("Provide an input JSON path, '-' for stdin, or --self-test")
            if args.input == "-":
                payload = json.load(sys.stdin)
                if not isinstance(payload, dict):
                    raise ValueError("stdin JSON must be an object")
            else:
                payload = _read_json(Path(args.input))
            result = analyze(payload)
        rendered = _stable_json(result, pretty=not args.compact)
        output_path = args.output or args.output_positional
        if output_path:
            Path(output_path).write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (AssertionError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        error = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc) or "self-test assertion failed",
        }
        sys.stderr.write(_stable_json(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
