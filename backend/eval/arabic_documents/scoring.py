"""Deterministic, local-only metrics for Arabic OCR evaluation runs."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE_BLOCK = re.compile(r"<img\b[^>]*>.*?</img\s*>", re.IGNORECASE | re.DOTALL)
_PAGE_NUMBER = re.compile(r"<page_number\b[^>]*>.*?</page_number\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,}).*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_LIST_MARKER = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+", re.MULTILINE)
_TABLE_RULE = re.compile(r"(?m)^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
_MARKUP = re.compile(r"(?:\*\*|__|~~|[*_`])")
_WHITESPACE = re.compile(r"\s+")


def normalize_for_score(text: str, options: Mapping[str, Any] | None = None) -> str:
    """Normalize a copy for metrics; the caller's stored OCR string is untouched.

    Alef variants are merged by default. Ya and ta-marbuta/ha equivalence are
    opt-in because those mappings can hide genuine orthographic differences.
    """

    if not isinstance(text, str):
        raise TypeError("OCR text must be a string")
    config = dict(options or {})
    value = unicodedata.normalize("NFKC", text)
    if config.get("strip_markdown", True):
        value = _strip_markdown(value)
    value = value.replace("\u0640", "")
    value = "".join(
        char for char in value
        if not _is_arabic_diacritic(char)
    )
    if config.get("normalize_alef", True):
        value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"}))
    if config.get("normalize_ya", False):
        value = value.replace("ى", "ي")
    if config.get("normalize_ta_marbuta_ha", False):
        value = value.replace("ة", "ه")
    return _WHITESPACE.sub(" ", value).strip()


def _is_arabic_diacritic(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x064B <= codepoint <= 0x065F
        or codepoint == 0x0670
        or 0x06D6 <= codepoint <= 0x06ED
    )


def _strip_markdown(value: str) -> str:
    value = _IMAGE_BLOCK.sub(" ", value)
    value = _PAGE_NUMBER.sub(" ", value)
    value = _HTML_COMMENT.sub(" ", value)
    value = _LINK.sub(r"\1", value)
    value = _FENCE.sub(" ", value)
    value = _TABLE_RULE.sub(" ", value)
    value = _HEADING.sub("", value)
    value = _QUOTE.sub("", value)
    value = _LIST_MARKER.sub("", value)
    value = _HTML_TAG.sub(" ", value)
    value = value.replace("|", " ")
    value = _MARKUP.sub("", value)
    return value


def score_ocr_text(
    prediction: str,
    reference: str,
    normalization: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, float | int]]:
    """Return exact raw and policy-normalized CER/WER/token-F1 metrics."""

    raw = _text_metrics(prediction, reference)
    normalized = _text_metrics(
        normalize_for_score(prediction, normalization),
        normalize_for_score(reference, normalization),
    )
    return {"raw": raw, "normalized": normalized}


def _text_metrics(prediction: str, reference: str) -> dict[str, float | int]:
    reference_chars = list(reference)
    prediction_chars = list(prediction)
    reference_words = reference.split()
    prediction_words = prediction.split()
    char_edits = _edit_distance(reference_chars, prediction_chars)
    word_edits = _edit_distance(reference_words, prediction_words)
    overlap = sum((Counter(reference_words) & Counter(prediction_words)).values())
    precision = overlap / len(prediction_words) if prediction_words else float(not reference_words)
    recall = overlap / len(reference_words) if reference_words else float(not prediction_words)
    token_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "cer": _error_rate(char_edits, len(reference_chars)),
        "char_edits": char_edits,
        "reference_chars": len(reference_chars),
        "wer": _error_rate(word_edits, len(reference_words)),
        "word_edits": word_edits,
        "reference_words": len(reference_words),
        "token_f1": token_f1,
        "token_true_positive": overlap,
        "prediction_words": len(prediction_words),
    }


def aggregate_text_scores(
    pairs: Sequence[Mapping[str, str]],
    normalization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate OCR pairs into micro, macro, and per-document metrics."""

    raw_metrics: list[dict[str, float | int]] = []
    normalized_metrics: list[dict[str, float | int]] = []
    per_document: dict[str, dict[str, dict[str, float | int]]] = {}
    seen: set[str] = set()
    for pair in pairs:
        doc_id = str(pair["doc_id"])
        if doc_id in seen:
            raise ValueError(f"Duplicate OCR scoring document id: {doc_id}")
        seen.add(doc_id)
        scores = score_ocr_text(pair["prediction"], pair["reference"], normalization)
        per_document[doc_id] = scores
        raw_metrics.append(scores["raw"])
        normalized_metrics.append(scores["normalized"])
    return {
        "documents": len(per_document),
        "micro": {
            "raw": _micro_metrics(raw_metrics),
            "normalized": _micro_metrics(normalized_metrics),
        },
        "macro": {
            "raw": _macro_metrics(raw_metrics),
            "normalized": _macro_metrics(normalized_metrics),
        },
        "per_document": per_document,
    }


def _micro_metrics(rows: Sequence[Mapping[str, float | int]]) -> dict[str, float | int | None]:
    if not rows:
        return {
            "cer": None,
            "char_edits": 0,
            "reference_chars": 0,
            "wer": None,
            "word_edits": 0,
            "reference_words": 0,
            "token_f1": None,
            "token_true_positive": 0,
            "prediction_words": 0,
        }
    sums = {
        "char_edits": sum(int(row["char_edits"]) for row in rows),
        "reference_chars": sum(int(row["reference_chars"]) for row in rows),
        "word_edits": sum(int(row["word_edits"]) for row in rows),
        "reference_words": sum(int(row["reference_words"]) for row in rows),
        "token_true_positive": sum(int(row["token_true_positive"]) for row in rows),
        "prediction_words": sum(int(row["prediction_words"]) for row in rows),
    }
    token_precision = (
        sums["token_true_positive"] / sums["prediction_words"]
        if sums["prediction_words"] else float(sums["reference_words"] == 0)
    )
    token_recall = (
        sums["token_true_positive"] / sums["reference_words"]
        if sums["reference_words"] else float(sums["prediction_words"] == 0)
    )
    token_f1 = (
        2 * token_precision * token_recall / (token_precision + token_recall)
        if token_precision + token_recall else 0.0
    )
    return {
        "cer": _error_rate(sums["char_edits"], sums["reference_chars"]),
        "char_edits": sums["char_edits"],
        "reference_chars": sums["reference_chars"],
        "wer": _error_rate(sums["word_edits"], sums["reference_words"]),
        "word_edits": sums["word_edits"],
        "reference_words": sums["reference_words"],
        "token_f1": token_f1,
        "token_true_positive": sums["token_true_positive"],
        "prediction_words": sums["prediction_words"],
    }


def _macro_metrics(rows: Sequence[Mapping[str, float | int]]) -> dict[str, float | None]:
    if not rows:
        return {"cer": None, "wer": None, "token_f1": None}
    return {
        metric: sum(float(row[metric]) for row in rows) / len(rows)
        for metric in ("cer", "wer", "token_f1")
    }


def _edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, left_item in enumerate(left, start=1):
        current = [index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_item != right_item),
            ))
        previous = current
    return previous[-1]


def _error_rate(edits: int, reference_units: int) -> float:
    if reference_units == 0:
        return 0.0 if edits == 0 else 1.0
    return edits / reference_units


def _route_label(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    label = str(value or "uncertain").strip().lower()
    return {
        "arabic_printed": "printed",
        "printed_arabic": "printed",
        "arabic_handwritten": "handwritten",
        "handwritten_arabic": "handwritten",
        "arabic_style_uncertain": "uncertain",
        "unknown": "uncertain",
        "abstain": "uncertain",
        "abstention": "uncertain",
    }.get(label, label)


def score_routes(predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Report confusion, coverage, abstention, and errors among auto-routes."""

    classes = ("english", "printed", "handwritten", "uncertain")
    confusion = {truth: {pred: 0 for pred in classes} for truth in classes[:-1]}
    auto_count = correct_count = abstentions = 0
    printed_as_handwritten = handwritten_as_printed = 0
    for row in predictions:
        truth = _route_label(row["truth"])
        predicted = _route_label(row["prediction"])
        if truth not in confusion:
            raise ValueError(f"Unsupported ground-truth route label: {truth}")
        if predicted not in classes:
            raise ValueError(f"Unsupported predicted route label: {predicted}")
        confusion[truth][predicted] += 1
        if predicted == "uncertain":
            abstentions += 1
            continue
        auto_count += 1
        correct_count += int(predicted == truth)
        printed_as_handwritten += int(truth == "printed" and predicted == "handwritten")
        handwritten_as_printed += int(truth == "handwritten" and predicted == "printed")
    total = len(predictions)
    return {
        "total": total,
        "auto_routed": auto_count,
        "abstentions": abstentions,
        "coverage": auto_count / total if total else None,
        "correct_auto_routes": correct_count,
        "accuracy_on_covered": correct_count / auto_count if auto_count else None,
        "printed_as_handwritten": printed_as_handwritten,
        "handwritten_as_printed": handwritten_as_printed,
        "confusion": confusion,
    }


def usage_cost_usd(
    usage: Mapping[str, Any],
    rates: Mapping[str, Any] | None,
) -> float | None:
    """Price input and output tokens; thought tokens use the output tariff."""

    return _usage_cost_usd(usage, rates, include_thought_tokens=True)


def usage_cost_without_thoughts_usd(
    usage: Mapping[str, Any],
    rates: Mapping[str, Any] | None,
) -> float | None:
    """Price on the legacy candidates-only output basis for ledger comparison."""

    return _usage_cost_usd(usage, rates, include_thought_tokens=False)


def _usage_cost_usd(
    usage: Mapping[str, Any],
    rates: Mapping[str, Any] | None,
    *,
    include_thought_tokens: bool,
) -> float | None:
    if not rates:
        return None
    input_rate = rates.get("input_usd_per_million")
    output_rate = rates.get("output_usd_per_million")
    if input_rate is None or output_rate is None:
        return None
    input_rate = float(input_rate)
    output_rate = float(output_rate)
    if not math.isfinite(input_rate) or not math.isfinite(output_rate) or min(input_rate, output_rate) < 0:
        raise ValueError("Model token rates must be finite and nonnegative")
    prompt = _nonnegative_token_count(usage.get("prompt_tokens", 0))
    output = _nonnegative_token_count(usage.get("output_tokens", 0))
    thoughts = _nonnegative_token_count(usage.get("thought_tokens", 0)) if include_thought_tokens else 0
    return round(
        (prompt * input_rate + (output + thoughts) * output_rate) / 1_000_000,
        12,
    )


def cogs_gap_percent(all_in_cost: float | None, tracker_cost: float | None) -> float | None:
    """Return under-report as a share of all-in cost (thoughts included)."""

    if all_in_cost is None or tracker_cost is None:
        return None
    all_in = float(all_in_cost)
    tracked = float(tracker_cost)
    if not math.isfinite(all_in) or not math.isfinite(tracked) or min(all_in, tracked) < 0:
        raise ValueError("COGS values must be finite and nonnegative")
    if all_in == 0:
        return None
    return round((all_in - tracked) / all_in * 100, 6)


def _nonnegative_token_count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Token counts must be nonnegative integers")
    result = int(value or 0)
    if result < 0 or result != value:
        raise ValueError("Token counts must be nonnegative integers")
    return result


def latency_percentiles(latencies_ms: Sequence[int]) -> dict[str, int | None]:
    """Nearest-rank p50/p95, which is stable for small benchmark samples."""

    values = sorted(int(value) for value in latencies_ms)
    if any(value < 0 for value in values):
        raise ValueError("Latency values must be nonnegative")
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None}
    return {
        "count": len(values),
        "p50_ms": values[max(0, math.ceil(0.50 * len(values)) - 1)],
        "p95_ms": values[max(0, math.ceil(0.95 * len(values)) - 1)],
    }


def page_coverage(expected_pages: Sequence[int], actual_pages: Sequence[int]) -> dict[str, Any]:
    expected = _validated_pages(expected_pages, "expected")
    actual = _validated_pages(actual_pages, "actual", allow_duplicates=True)
    expected_set = set(expected)
    actual_set = set(actual)
    counts = Counter(actual)
    missing = sorted(expected_set - actual_set)
    duplicates = sorted(page for page, count in counts.items() if count > 1)
    unexpected = sorted(actual_set - expected_set)
    return {
        "expected_pages": list(expected),
        "actual_pages": list(actual),
        "missing_pages": missing,
        "duplicate_pages": duplicates,
        "unexpected_pages": unexpected,
        "complete": not missing and not duplicates and not unexpected and len(expected) == len(actual),
    }


def validate_provider_ranges(
    expected_pages: Sequence[int],
    provider_runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    committed: list[int] = []
    providers: dict[str, list[int]] = {}
    for run in provider_runs:
        provider = str(run.get("provider") or "unknown")
        pages = run.get("committed_pages", run.get("pages", []))
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
            raise ValueError("Provider page ranges must be sequences of page numbers")
        clean_pages = _validated_pages(pages, "provider", allow_duplicates=True)
        committed.extend(clean_pages)
        providers.setdefault(provider, []).extend(clean_pages)
    coverage = page_coverage(expected_pages, committed)
    coverage["by_provider"] = {
        provider: sorted(pages) for provider, pages in sorted(providers.items())
    }
    return coverage


def _validated_pages(values: Sequence[int], label: str, *, allow_duplicates: bool = False) -> tuple[int, ...]:
    pages: list[int] = []
    for page in values:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError(f"{label.title()} page numbers must be positive integers")
        pages.append(page)
    if not allow_duplicates and len(set(pages)) != len(pages):
        raise ValueError(f"{label.title()} pages must not contain duplicates")
    if not allow_duplicates and pages != sorted(pages):
        raise ValueError(f"{label.title()} pages must be increasing")
    return tuple(pages)


def compare_paired_runs(
    candidate: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Pair OCR records by document id; positive CER improvement favors candidate."""

    candidate_by_id = _records_by_id(candidate)
    baseline_by_id = _records_by_id(baseline)
    paired: list[dict[str, Any]] = []
    for doc_id in sorted(candidate_by_id.keys() & baseline_by_id.keys()):
        candidate_cer = _record_cer(candidate_by_id[doc_id])
        baseline_cer = _record_cer(baseline_by_id[doc_id])
        if candidate_cer is None or baseline_cer is None:
            continue
        paired.append({
            "doc_id": doc_id,
            "candidate_cer": candidate_cer,
            "baseline_cer": baseline_cer,
            "cer_improvement": baseline_cer - candidate_cer,
        })
    improvements = [row["cer_improvement"] for row in paired]
    return {
        "paired_documents": len(paired),
        "unpaired_candidate_documents": len(candidate_by_id.keys() - baseline_by_id.keys()),
        "unpaired_baseline_documents": len(baseline_by_id.keys() - candidate_by_id.keys()),
        "mean_cer_improvement": (
            sum(improvements) / len(improvements) if improvements else None
        ),
        "per_document": paired,
    }


def _records_by_id(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        doc_id = str(record["doc_id"])
        if doc_id in result:
            raise ValueError(f"Duplicate evaluation record id: {doc_id}")
        result[doc_id] = record
    return result


def _record_cer(record: Mapping[str, Any]) -> float | None:
    scores = record.get("ocr_scores")
    if not isinstance(scores, Mapping):
        return None
    normalized = scores.get("normalized")
    if not isinstance(normalized, Mapping):
        return None
    value = normalized.get("cer")
    return None if value is None else float(value)
