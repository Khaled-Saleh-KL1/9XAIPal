"""Offline-first Arabic document OCR evaluation CLI.

The default mode calls only the configured local classifier. Provider OCR is
available only with --live; --mock exercises report generation without PDFs,
local models, credentials, or network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from eval.arabic_documents.scoring import (
    aggregate_text_scores,
    cogs_gap_percent,
    compare_paired_runs,
    latency_percentiles,
    page_coverage,
    score_ocr_text,
    score_routes,
    usage_cost_usd,
    validate_provider_ranges,
)

_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_EXPECTED_ROUTES = {"english", "printed", "handwritten", "arabic_printed", "arabic_handwritten"}
_NORMALIZATION_OPTIONS = {
    "strip_markdown",
    "normalize_alef",
    "normalize_ya",
    "normalize_ta_marbuta_ha",
}


class EvaluationError(ValueError):
    """A safe, actionable evaluation-input or configuration error."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Call real OCR providers; never calls Gemini Pro.")
    mode.add_argument("--mock", action="store_true", help="Use only synthetic manifest data; no model/network calls.")
    parser.add_argument(
        "--ocr-arm",
        choices=("hybrid", "gemma-only"),
        default="hybrid",
        help="Live printed OCR arm; ignored outside --live.",
    )
    parser.add_argument("--run-dir", type=Path, help="New, empty run directory; existing paths are never overwritten.")
    parser.add_argument("--compare-run-dir", type=Path, help="Pair this run with another run's records.jsonl by document id.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = "mock" if args.mock else "live" if args.live else "classifier-only"
    arm = args.ocr_arm if args.live else None

    try:
        manifest_path = args.manifest.expanduser().resolve(strict=True)
        manifest = _load_manifest(manifest_path, allow_inline=bool(args.mock))
        documents = manifest["documents"]
        if not documents:
            raise EvaluationError("The manifest has no documents; provide the frozen corpus or use --mock with synthetic entries.")
        runtime_settings = None if args.mock else _runtime_settings()
        config = _config_snapshot(mode, arm, manifest, runtime_settings)
        config_sha256 = _sha256(_canonical_json(config).encode("utf-8"))
        git_sha = _git_sha()
        run_dir = _new_run_dir(args.run_dir)
        _assert_private_output_dir(run_dir, _git_repo_root())
        run_dir.mkdir(parents=True, exist_ok=False)
        manifest_sha256 = _sha256(manifest_path.read_bytes())

        if args.mock:
            records, pairs = _mock_run(documents, manifest, config_sha256, git_sha)
            preflight: list[dict[str, Any]] = []
        else:
            classified, classification_pairs = _classify_all(documents, mode, arm, manifest, config_sha256, git_sha)
            preflight = []
            if args.live and arm == "hybrid":
                sample = _preflight_sample(classified, documents)
                if sample is None:
                    raise EvaluationError(
                        "Live hybrid OCR requires at least one locally classified printed Arabic PDF for the one-page Gemini Flash preflight."
                    )
                preflight = _preflight_gemini(sample, runtime_settings)
                _write_json_exclusive(run_dir / "preflight.json", {
                    "model": runtime_settings.arabic_gemini_printed_model,
                    "key_results": preflight,
                })
                if not any(item["status"] == "available" for item in preflight):
                    _write_run_outputs(
                        run_dir,
                        classified,
                        _build_report(
                            mode, arm, classified, [], manifest, config_sha256,
                            git_sha, manifest_sha256, preflight, None,
                        ),
                    )
                    print(
                        "No configured Gemini API key produced a valid one-page "
                        "gemini-3.7-flash response; no corpus OCR was run.",
                        file=sys.stderr,
                    )
                    return 2
            records = classified
            pairs = classification_pairs
            if args.live:
                ocr_records, ocr_pairs = _run_live_ocr(
                    classified, documents, manifest, runtime_settings,
                    config_sha256, git_sha, run_dir, arm,
                )
                records = ocr_records
                pairs = ocr_pairs

        paired = None
        if args.compare_run_dir:
            baseline_records = _read_records(args.compare_run_dir.expanduser().resolve() / "records.jsonl")
            paired = compare_paired_runs(records, baseline_records)
        report = _build_report(
            mode, arm, records, pairs, manifest, config_sha256,
            git_sha, manifest_sha256, preflight, paired,
        )
        _write_run_outputs(run_dir, records, report)
        print(f"Arabic OCR evaluation ({mode}) wrote a text-free report to {run_dir}")
        return 0
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Arabic OCR evaluation failed: {error}", file=sys.stderr)
        return 2


def _load_manifest(path: Path, *, allow_inline: bool) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvaluationError("The evaluation manifest must be valid UTF-8 JSON.") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise EvaluationError("The evaluation manifest must declare schema_version 1.")
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise EvaluationError("The evaluation manifest must contain a documents array.")
    normalization = manifest.get("normalization", {})
    if not isinstance(normalization, dict) or set(normalization) - _NORMALIZATION_OPTIONS:
        raise EvaluationError("The manifest contains unsupported normalization options.")
    for key, value in normalization.items():
        if not isinstance(value, bool):
            raise EvaluationError(f"Normalization option {key!r} must be boolean.")
    pricing = manifest.get("pricing", {})
    if not isinstance(pricing, dict):
        raise EvaluationError("The manifest pricing section must be an object.")
    by_model = pricing.get("by_model", {})
    if not isinstance(by_model, dict):
        raise EvaluationError("pricing.by_model must be an object.")

    repo_root = _git_repo_root()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in documents:
        if not isinstance(raw, dict):
            raise EvaluationError("Every manifest document entry must be an object.")
        if allow_inline and any(
            raw.get(field) is not None
            for field in ("source_pdf", "ground_truth_file", "ground_truth_pages_file")
        ):
            raise EvaluationError("Mock mode accepts synthetic inline text only; remove PDF and ground-truth file paths.")
        doc_id = raw.get("doc_id")
        split = raw.get("split")
        expected_route = _truth_label(raw.get("expected_route"))
        if not isinstance(doc_id, str) or not _DOC_ID.fullmatch(doc_id) or doc_id in {".", ".."}:
            raise EvaluationError("Each document needs a safe, unique doc_id (letters, digits, dot, underscore, hyphen).")
        if doc_id in seen:
            raise EvaluationError(f"Duplicate manifest doc_id: {doc_id}")
        seen.add(doc_id)
        if not isinstance(split, str) or not split.strip():
            raise EvaluationError(f"Document {doc_id!r} needs a non-empty split label.")
        if expected_route not in _EXPECTED_ROUTES:
            raise EvaluationError(f"Document {doc_id!r} has an unsupported expected_route.")

        inline_gt = raw.get("ground_truth_text")
        source_pdf = _manifest_file(
            raw.get("source_pdf"), path.parent, "source_pdf", doc_id,
            required=not allow_inline,
        )
        ground_truth_file = _manifest_file(
            raw.get("ground_truth_file"), path.parent, "ground_truth_file", doc_id,
            required=not (allow_inline and isinstance(inline_gt, str)),
        )
        pages_file = _manifest_file(raw.get("ground_truth_pages_file"), path.parent, "ground_truth_pages_file", doc_id, required=False)
        if allow_inline:
            if inline_gt is not None and not isinstance(inline_gt, str):
                raise EvaluationError(f"Mock ground_truth_text for {doc_id!r} must be a string.")
        else:
            if source_pdf is None or source_pdf.suffix.lower() != ".pdf":
                raise EvaluationError(f"Document {doc_id!r} must reference a local PDF source.")
            if ground_truth_file is None:
                raise EvaluationError(f"Document {doc_id!r} must reference a human-verified ground_truth_file.")
            _assert_private_file(source_pdf, repo_root)
            _assert_private_file(ground_truth_file, repo_root)
            if pages_file is not None:
                _assert_private_file(pages_file, repo_root)

        result.append({
            **raw,
            "doc_id": doc_id,
            "split": split.strip(),
            "expected_route": expected_route,
            "source_path": source_pdf,
            "ground_truth_path": ground_truth_file,
            "ground_truth_pages_path": pages_file,
        })
    return {**manifest, "documents": result}


def _truth_label(value: Any) -> str:
    return {
        "arabic_printed": "printed",
        "printed_arabic": "printed",
        "arabic_handwritten": "handwritten",
        "handwritten_arabic": "handwritten",
    }.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def _manifest_file(
    value: Any,
    base: Path,
    field: str,
    doc_id: str,
    *,
    required: bool = True,
) -> Path | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        if not required and value is None:
            return None
        raise EvaluationError(f"Document {doc_id!r} needs a {field} path.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise EvaluationError(f"The {field} file for {doc_id!r} does not exist.") from error
    if not resolved.is_file():
        raise EvaluationError(f"The {field} path for {doc_id!r} is not a file.")
    return resolved


def _assert_private_file(path: Path, repo_root: Path | None) -> None:
    if repo_root is None:
        backend_root = Path(__file__).resolve().parents[2]
        try:
            path.resolve().relative_to(backend_root)
        except ValueError:
            return
        raise EvaluationError(
            "Git is unavailable to verify ignore rules for a corpus file inside backend; "
            "place it outside backend or run with Git available."
        )
    try:
        relative = path.resolve().relative_to(repo_root)
    except ValueError:
        return
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise EvaluationError(
            "Git is unavailable to verify corpus ignore rules; place private files outside the repository."
        ) from error
    if result.returncode != 0:
        raise EvaluationError(
            "Private PDF and ground-truth files inside the repository must be git-ignored; "
            "place the corpus outside git or under backend/eval/arabic_documents/corpus/."
        )


def _assert_private_output_dir(path: Path, repo_root: Path | None) -> None:
    if repo_root is not None:
        try:
            relative = path.resolve().relative_to(repo_root)
        except ValueError:
            return
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
                cwd=repo_root,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise EvaluationError(
                "Git is unavailable to verify run-output ignore rules; choose a directory outside the repository."
            ) from error
        if result.returncode != 0:
            raise EvaluationError(
                "Run output inside the repository must be git-ignored; use backend/eval/arabic_documents/runs/ or an external directory."
            )
        return

    backend_root = Path(__file__).resolve().parents[2]
    try:
        path.resolve().relative_to(backend_root)
    except ValueError:
        return
    safe_runs_root = Path(__file__).resolve().parent / "runs"
    if path.resolve() == safe_runs_root or safe_runs_root in path.resolve().parents:
        return
    raise EvaluationError(
        "Git is unavailable to verify run-output ignore rules; use the default ignored runs/ directory or an external directory."
    )


def _read_ground_truth(
    document: dict[str, Any],
) -> tuple[str | None, str | None, dict[int, str], str | None]:
    path = document.get("ground_truth_path")
    if path is not None:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        digest = _sha256(raw)
    else:
        inline = document.get("ground_truth_text")
        text = inline if isinstance(inline, str) else None
        digest = _sha256(text.encode("utf-8")) if text is not None else None
    page_text: dict[int, str] = {}
    page_digest: str | None = None
    page_path = document.get("ground_truth_pages_path")
    if page_path is not None:
        pages_bytes = page_path.read_bytes()
        page_digest = _sha256(pages_bytes)
        raw_pages = json.loads(pages_bytes.decode("utf-8"))
        if isinstance(raw_pages, dict):
            pairs = raw_pages.items()
        elif isinstance(raw_pages, list):
            pairs = ((item.get("page_number"), item.get("text")) for item in raw_pages if isinstance(item, dict))
        else:
            raise EvaluationError(f"Page ground truth for {document['doc_id']!r} must be an object or array.")
        for page, value in pairs:
            try:
                page_number = int(page)
            except (TypeError, ValueError):
                raise EvaluationError(f"Page ground truth for {document['doc_id']!r} has a non-integer page number.") from None
            if page_number < 1 or not isinstance(value, str) or page_number in page_text:
                raise EvaluationError(f"Page ground truth for {document['doc_id']!r} has an invalid page entry.")
            page_text[page_number] = value
    return text, digest, page_text, page_digest


def _runtime_settings():
    from app.core.config import settings

    return settings


def _config_snapshot(mode: str, arm: str | None, manifest: dict[str, Any], runtime_settings: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "mode": mode,
        "ocr_arm": arm,
        "normalization": manifest.get("normalization", {}),
        "pricing": manifest.get("pricing", {}),
    }
    if runtime_settings is not None:
        config.update({
            "arabic_ocr_enabled": runtime_settings.arabic_ocr_enabled,
            "handwritten_ocr_enabled": runtime_settings.arabic_handwritten_ocr_enabled,
            "classifier_model": runtime_settings.arabic_router_model,
            "classifier_base_url": runtime_settings.arabic_router_base_url,
            "printed_model": runtime_settings.arabic_gemini_printed_model,
            "thinking_level": runtime_settings.arabic_gemini_printed_thinking_level,
            "handwritten_model": runtime_settings.arabic_gemini_handwritten_model,
            "gemma_model": runtime_settings.arabic_gemma_fallback_model,
            "gemma_base_url": runtime_settings.arabic_gemma_base_url,
            "dpi": runtime_settings.arabic_ocr_dpi,
            "media_resolution": runtime_settings.arabic_gemini_media_resolution,
            "batch_pages": runtime_settings.arabic_ocr_batch_pages,
            "single_request_max_pages": runtime_settings.arabic_ocr_single_request_max_pages,
            "max_output_tokens": runtime_settings.arabic_ocr_max_output_tokens,
            "minimum_body_char_count": runtime_settings.arabic_min_body_char_count,
            "classifier_confidence_min": runtime_settings.arabic_classifier_confidence_min,
            "handwritten_confidence_min": runtime_settings.arabic_handwritten_confidence_min,
            "classifier_batch_pages": runtime_settings.arabic_classifier_batch_pages,
        })
    return config


def _git_sha() -> str | None:
    repo_root = _git_repo_root()
    if repo_root is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=False,
            capture_output=True, text=True,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git_repo_root() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).resolve().parent,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _new_run_dir(requested: Path | None) -> Path:
    if requested is not None:
        return requested.expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parent / "runs" / f"{stamp}-{uuid4().hex[:8]}"


def _classify_all(
    documents: list[dict[str, Any]],
    mode: str,
    arm: str | None,
    manifest: dict[str, Any],
    config_sha256: str,
    git_sha: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    from app.extraction.arabic_classifier import classify_document

    records: list[dict[str, Any]] = []
    pairs: list[dict[str, str]] = []
    normalization = manifest.get("normalization", {})
    for document in documents:
        reference, reference_hash, page_gt, page_hash = _read_ground_truth(document)
        record = _base_record(document, mode, arm, config_sha256, git_sha, reference_hash, page_hash)
        started = time.monotonic()
        try:
            decision = classify_document(document["source_path"])
        except Exception as error:
            record["classification_status"] = "failed"
            record["classification_error_type"] = type(error).__name__
            record["classification_latency_ms"] = _elapsed_ms(started)
            record["latency_ms"] = record["classification_latency_ms"]
            records.append(record)
            continue
        elapsed = _elapsed_ms(started)
        record.update({
            "classification_status": "complete",
            "predicted_route": _route_value(decision.route),
            "detected_language": decision.language,
            "detected_writing_style": decision.writing_style,
            "text_direction": decision.text_direction,
            "classification_confidence": decision.confidence,
            "classifier_model": decision.classifier_model,
            "classification_latency_ms": elapsed,
            "latency_ms": elapsed,
            "page_count": _pdf_page_count(document["source_path"]),
        })
        records.append(record)
    return records, pairs


def _base_record(
    document: dict[str, Any],
    mode: str,
    arm: str | None,
    config_sha256: str,
    git_sha: str | None,
    reference_hash: str | None,
    page_reference_hash: str | None,
) -> dict[str, Any]:
    source_path = document.get("source_path")
    return {
        "doc_id": document["doc_id"],
        "split": document["split"],
        "expected_route": document["expected_route"],
        "predicted_route": None,
        "classification_status": "not_run",
        "classification_latency_ms": 0,
        "ocr_status": "not_run",
        "ocr_attempted": False,
        "ocr_scores": None,
        "ocr_provider_summary": [],
        "provider_runs": [],
        "provider_page_scores": {},
        "token_usage": _zero_usage(),
        "request_attempt_count": 0,
        "page_count": None,
        "page_coverage": None,
        "provider_range_validation": None,
        "latency_ms": 0,
        "ocr_latency_ms": 0,
        "cost_usd": 0.0,
        "cost_usd_per_page": None,
        "source_sha256": _sha256(source_path.read_bytes()) if source_path is not None else None,
        "ground_truth_sha256": reference_hash,
        "ground_truth_pages_sha256": page_reference_hash,
        "config_sha256": config_sha256,
        "git_sha": git_sha,
        "mode": mode,
        "ocr_arm": arm,
    }


def _mock_run(
    documents: list[dict[str, Any]],
    manifest: dict[str, Any],
    config_sha256: str,
    git_sha: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    pairs: list[dict[str, str]] = []
    normalization = manifest.get("normalization", {})
    for document in documents:
        reference, reference_hash, _, page_hash = _read_ground_truth(document)
        if reference is None:
            raise EvaluationError(f"Mock document {document['doc_id']!r} needs ground_truth_text.")
        record = _base_record(document, "mock", "mock", config_sha256, git_sha, reference_hash, page_hash)
        record.update({
            "classification_status": "complete",
            "predicted_route": _truth_label(document.get("mock_prediction_route", document["expected_route"])),
            "detected_language": document.get(
                "mock_detected_language",
                "english" if document["expected_route"] == "english" else "arabic",
            ),
            "detected_writing_style": "printed" if document["expected_route"] in {"printed", "arabic_printed"} else "handwritten" if document["expected_route"] in {"handwritten", "arabic_handwritten"} else "unknown",
            "text_direction": "ltr" if document["expected_route"] == "english" else "rtl",
            "classification_confidence": 1.0,
            "classifier_model": "mock",
            "classification_latency_ms": 0,
            "page_count": int(document.get("mock_page_count", 1)),
            "latency_ms": 0,
            "mock": True,
        })
        prediction = ""
        if record["predicted_route"] == "printed" and document["expected_route"] in {"printed", "arabic_printed"}:
            prediction = document.get("mock_prediction_text", reference)
            if not isinstance(prediction, str):
                raise EvaluationError(f"mock_prediction_text for {document['doc_id']!r} must be a string.")
            page_count = record["page_count"]
            pages = list(range(1, page_count + 1))
            record.update({
                "ocr_attempted": True,
                "ocr_status": "complete",
                "ocr_scores": score_ocr_text(prediction, reference, normalization),
                "ocr_provider_summary": [{"provider": "mock", "pages": pages}],
                "provider_runs": [{
                    "provider": "mock", "model": "mock", "requested_pages": pages,
                    "committed_pages": pages, "attempt_count": 0, "retry_count": 0,
                    "latency_ms": 0, "usage": _zero_usage(),
                }],
                "page_coverage": page_coverage(pages, pages),
                "provider_range_validation": validate_provider_ranges(pages, [{"provider": "mock", "committed_pages": pages}]),
                "cost_usd": 0.0,
                "cost_usd_tracker_basis": 0.0,
                "cogs_gap_percent": None,
                "cost_usd_per_page": 0.0 if page_count else None,
            })
            pairs.append({"doc_id": document["doc_id"], "split": document["split"], "reference": reference, "prediction": prediction})
        elif record["predicted_route"] == "handwritten":
            record["ocr_status"] = "disabled_handwritten"
        else:
            record["ocr_status"] = "not_run_for_route"
        records.append(record)
    return records, pairs


def _classify_all_unused_placeholder():
    """Kept out of command flow; declared nowhere in runtime behavior."""


def _preflight_sample(
    records: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, Any] | None:
    documents_by_id = {document["doc_id"]: document for document in documents}
    for record in records:
        if record.get("classification_status") != "complete" or record.get("predicted_route") != "arabic_printed":
            continue
        document = documents_by_id.get(record["doc_id"])
        if document is not None and document.get("source_path") is not None:
            return document
    return None


def _preflight_gemini(document: dict[str, Any], runtime_settings: Any) -> list[dict[str, Any]]:
    from app.extraction.arabic_adapter import parse_complete_page_prefix
    from app.extraction.arabic_types import GeminiKeysExhausted, GeminiOutputInvalid, GeminiRequestInvalid
    from app.extraction.gemini_ocr_client import GeminiOcrClient
    from app.extraction.arabic_types import RenderedPage
    import fitz

    source = document["source_path"]
    with fitz.open(source) as pdf:
        if pdf.page_count < 1:
            raise EvaluationError("The Gemini preflight PDF has no pages.")
        page = pdf.load_page(0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csRGB, alpha=False)
        rendered = RenderedPage(page_number=1, png=pixmap.tobytes("png"))
    keys = runtime_settings.gemini_api_keys
    results: list[dict[str, Any]] = []
    for key_index, key in enumerate(keys):
        isolated_settings = runtime_settings.model_copy(update={"gemini_api_keys_raw": key})
        client = GeminiOcrClient(settings=isolated_settings)
        started = time.monotonic()

        def validator(text: str, expected: list[int]) -> int:
            parsed = parse_complete_page_prefix(
                text,
                expected,
                provider="gemini_arabic_flash",
                model=runtime_settings.arabic_gemini_printed_model,
            )
            return len(parsed.pages)

        try:
            result = client.generate_batch([rendered], validator=validator)
            results.append({
                "key_index": key_index,
                "status": "available",
                "status_category": "valid_response",
                "model": runtime_settings.arabic_gemini_printed_model,
                "attempt_count": result.attempt_count,
                "latency_ms": result.latency_ms,
                "usage": _usage_from_object(result.usage),
            })
        except GeminiKeysExhausted as error:
            results.append({
                "key_index": key_index,
                "status": "unavailable",
                "status_category": _safe_failure_kind(error.final_kind),
                "model": runtime_settings.arabic_gemini_printed_model,
                "attempt_count": error.attempt_count,
                "latency_ms": error.latency_ms or _elapsed_ms(started),
                "usage": _sum_usage_dicts([_usage_from_object(item) for item in error.attempt_usage]),
            })
        except (GeminiRequestInvalid, GeminiOutputInvalid) as error:
            results.append({
                "key_index": key_index,
                "status": "unavailable",
                "status_category": "invalid_request" if isinstance(error, GeminiRequestInvalid) else "invalid_output",
                "model": runtime_settings.arabic_gemini_printed_model,
                "attempt_count": 1,
                "latency_ms": _elapsed_ms(started),
                "usage": _zero_usage(),
            })
        except Exception:
            results.append({
                "key_index": key_index,
                "status": "unavailable",
                "status_category": "client_error",
                "model": runtime_settings.arabic_gemini_printed_model,
                "attempt_count": 1,
                "latency_ms": _elapsed_ms(started),
                "usage": _zero_usage(),
            })
    return results


def _run_live_ocr(
    classified: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    manifest: dict[str, Any],
    runtime_settings: Any,
    config_sha256: str,
    git_sha: str | None,
    run_dir: Path,
    arm: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    from app.extraction.arabic_fallback import GemmaArabicFallback
    from app.extraction.arabic_ocr import extract_arabic_document
    from app.extraction.arabic_types import ClassificationDecision, DocumentRoute, GeminiKeysExhausted
    from app.extraction.gemini_ocr_client import GeminiOcrClient

    docs_by_id = {document["doc_id"]: document for document in documents}
    records = [dict(record) for record in classified]
    pairs: list[dict[str, str]] = []
    for record in records:
        if record.get("classification_status") != "complete" or record.get("predicted_route") != "arabic_printed":
            if record.get("predicted_route") == "handwritten":
                record["ocr_status"] = "disabled_handwritten"
            else:
                record["ocr_status"] = "not_run_for_route"
            continue
        document = docs_by_id[record["doc_id"]]
        reference, _, page_gt, _ = _read_ground_truth(document)
        route = DocumentRoute.ARABIC_PRINTED
        classification = ClassificationDecision(
            route=route,
            language=record.get("detected_language") or "arabic",
            writing_style="printed",
            text_direction="rtl",
            confidence=float(record.get("classification_confidence") or 0),
            classifier_model=str(record.get("classifier_model") or runtime_settings.arabic_router_model),
        )
        gemini_client: Any
        if arm == "gemma-only":
            gemini_client = _GemmaOnlyTrigger(runtime_settings.arabic_gemini_printed_model, GeminiKeysExhausted)
        else:
            gemini_client = GeminiOcrClient(settings=runtime_settings)
        output_dir = run_dir / "artifacts" / record["doc_id"]
        started = time.monotonic()
        record["ocr_attempted"] = True
        try:
            result = extract_arabic_document(
                pdf_path=document["source_path"],
                output_dir=output_dir,
                classification=classification,
                gemini_client=gemini_client,
                gemma_client=GemmaArabicFallback(settings=runtime_settings),
                settings=runtime_settings,
            )
            ocr_latency = _elapsed_ms(started)
            predicted_text = "\n\n".join(page.markdown for page in result.pages)
            ocr_manifest = json.loads((result.output_dir / "ocr_manifest.json").read_text(encoding="utf-8"))
            provider_runs = ocr_manifest.get("provider_runs", [])
            pages = [page.page_number for page in result.pages]
            expected_pages = list(range(1, _pdf_page_count(document["source_path"]) + 1))
            coverage = page_coverage(expected_pages, pages)
            range_validation = validate_provider_ranges(expected_pages, provider_runs)
            usage = _sum_usage_dicts([run.get("usage", {}) for run in provider_runs])
            costs, cost_total, tracker_cost_total = _provider_costs(
                provider_runs, manifest.get("pricing", {}).get("by_model", {})
            )
            record.update({
                "ocr_status": "complete" if coverage["complete"] else "incomplete_pages",
                "ocr_scores": score_ocr_text(predicted_text, reference, manifest.get("normalization", {})) if reference is not None else None,
                "ocr_provider_summary": result.provider_summary,
                "provider_runs": provider_runs,
                "provider_costs": costs,
                "token_usage": usage,
                "request_attempt_count": sum(int(run.get("attempt_count", 0)) for run in provider_runs),
                "page_count": len(expected_pages),
                "page_coverage": coverage,
                "provider_range_validation": range_validation,
                "ocr_latency_ms": ocr_latency,
                "latency_ms": int(record.get("classification_latency_ms", 0)) + ocr_latency,
                "cost_usd": cost_total,
                "cost_usd_tracker_basis": tracker_cost_total,
                "cogs_gap_percent": cogs_gap_percent(cost_total, tracker_cost_total),
                "cost_usd_per_page": cost_total / len(expected_pages) if cost_total is not None and expected_pages else None,
            })
            if reference is not None:
                pairs.append({"doc_id": record["doc_id"], "split": record["split"], "reference": reference, "prediction": predicted_text})
            if page_gt:
                record["provider_page_scores"] = _provider_page_scores(result.pages, page_gt, manifest.get("normalization", {}))
        except Exception as error:
            record["ocr_status"] = "failed"
            record["ocr_error_type"] = type(error).__name__
            record["ocr_latency_ms"] = _elapsed_ms(started)
            record["latency_ms"] = int(record.get("classification_latency_ms", 0)) + record["ocr_latency_ms"]
            record["cost_usd"] = None
            record["cost_usd_tracker_basis"] = None
            record["cogs_gap_percent"] = None
            record["cost_usd_per_page"] = None
    return records, pairs


class _GemmaOnlyTrigger:
    def __init__(self, model: str, exhausted_error: type[Exception]) -> None:
        self.model = model
        self.exhausted_error = exhausted_error

    def generate_batch(self, _pages: Any, validator: Any) -> Any:
        raise self.exhausted_error(
            final_kind="quota",
            best_partial=None,
            attempt_usage=(),
            attempt_count=0,
            latency_ms=0,
        )


def _provider_page_scores(pages: Any, reference_pages: dict[int, str], normalization: dict[str, Any]) -> dict[str, Any]:
    from collections import defaultdict as _defaultdict

    pairs_by_provider: dict[str, list[dict[str, str]]] = _defaultdict(list)
    for page in pages:
        reference = reference_pages.get(page.page_number)
        if reference is not None:
            pairs_by_provider[page.provider].append({
                "doc_id": f"page-{page.page_number}",
                "reference": reference,
                "prediction": page.markdown,
            })
    return {
        provider: aggregate_text_scores(pairs, normalization)
        for provider, pairs in sorted(pairs_by_provider.items())
    }


def _build_report(
    mode: str,
    arm: str | None,
    records: list[dict[str, Any]],
    pairs: list[dict[str, str]],
    manifest: dict[str, Any],
    config_sha256: str,
    git_sha: str | None,
    manifest_sha256: str,
    preflight: list[dict[str, Any]],
    paired: dict[str, Any] | None,
) -> dict[str, Any]:
    route_rows = [
        {"truth": record["expected_route"], "prediction": record["predicted_route"]}
        for record in records
        if record.get("classification_status") == "complete" and record.get("predicted_route") is not None
    ]
    route_by_split: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        if record.get("classification_status") == "complete" and record.get("predicted_route") is not None:
            route_by_split[record["split"]].append({"truth": record["expected_route"], "prediction": record["predicted_route"]})
    pair_by_split: dict[str, list[dict[str, str]]] = defaultdict(list)
    for pair in pairs:
        pair_by_split[pair["split"]].append(pair)
    expected_splits = sorted({record["split"] for record in records})
    ocr_records = [record for record in records if record.get("ocr_scores") is not None]
    ocr_attempt_records = [record for record in records if record.get("ocr_attempted") is True]
    cost_summary = _cost_summary(records)
    preflight_costs = [
        usage_cost_usd(item.get("usage", {}), manifest.get("pricing", {}).get("by_model", {}).get(item.get("model")))
        for item in preflight
    ]
    from eval.arabic_documents.scoring import usage_cost_without_thoughts_usd

    preflight_tracker_costs = [
        usage_cost_without_thoughts_usd(
            item.get("usage", {}),
            manifest.get("pricing", {}).get("by_model", {}).get(item.get("model")),
        )
        for item in preflight
    ]
    preflight_total = _sum_optional_costs(preflight_costs)
    preflight_tracker_total = _sum_optional_costs(preflight_tracker_costs)
    all_in_total = _add_optional_costs(cost_summary["ocr_total_usd"], preflight_total)
    all_in_tracker_total = _add_optional_costs(
        cost_summary["ocr_tracker_basis_total_usd"], preflight_tracker_total
    )
    page_count = sum(
        len((record.get("page_coverage") or {}).get("actual_pages", []))
        for record in ocr_records
    )
    route_all = score_routes(route_rows)
    route_all["classification_failures"] = sum(record.get("classification_status") == "failed" for record in records)
    route_all["documents_requested"] = len(records)
    route_all["coverage"] = route_all["auto_routed"] / len(records) if records else None
    parse_failures_by_split = {
        split: sum(
            run.get("failure_kind") == "invalid_output"
            for record in records if record.get("split") == split
            for run in record.get("provider_runs", [])
        )
        for split in expected_splits
    }
    route_metrics_by_split: dict[str, Any] = {}
    for split in expected_splits:
        split_records = [record for record in records if record["split"] == split]
        split_metrics = score_routes(route_by_split[split])
        split_metrics["classification_failures"] = sum(
            record.get("classification_status") == "failed" for record in split_records
        )
        split_metrics["documents_requested"] = len(split_records)
        split_metrics["coverage"] = (
            split_metrics["auto_routed"] / len(split_records) if split_records else None
        )
        route_metrics_by_split[split] = split_metrics
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": None,
        "mode": mode,
        "ocr_arm": arm,
        "mock_only": mode == "mock",
        "config_sha256": config_sha256,
        "git_sha": git_sha,
        "manifest_sha256": manifest_sha256,
        "document_count": len(records),
        "route_metrics": {
            "all": route_all,
            "by_split": route_metrics_by_split,
        },
        "ocr_metrics": {
            "all": aggregate_text_scores(pairs, manifest.get("normalization", {})),
            "by_split": {
                split: aggregate_text_scores(pair_by_split[split], manifest.get("normalization", {}))
                for split in expected_splits
            },
        },
        "page_integrity": {
            "documents_attempted": len(ocr_attempt_records),
            "documents_scored": len(ocr_records),
            "complete_documents": sum(
                record.get("page_coverage", {}).get("complete") is True
                and record.get("provider_range_validation", {}).get("complete") is True
                for record in ocr_records
            ),
            "total_pages_scored": page_count,
            "total_pages_attempted": sum(int(record.get("page_count") or 0) for record in ocr_attempt_records),
        },
        "latency_ms": {
            "end_to_end": latency_percentiles([int(record.get("latency_ms", 0)) for record in records]),
            "end_to_end_by_split": {
                split: latency_percentiles([
                    int(record.get("latency_ms", 0))
                    for record in records if record.get("split") == split
                ])
                for split in expected_splits
            },
            "ocr": latency_percentiles([int(record.get("ocr_latency_ms", 0)) for record in ocr_attempt_records]),
            "ocr_by_split": {
                split: latency_percentiles([
                    int(record.get("ocr_latency_ms", 0))
                    for record in ocr_attempt_records
                    if record.get("split") == split
                ])
                for split in expected_splits
            },
        },
        "cost": {
            **cost_summary,
            "preflight_total_usd": preflight_total,
            "preflight_tracker_basis_total_usd": preflight_tracker_total,
            "preflight_cogs_gap_percent": cogs_gap_percent(preflight_total, preflight_tracker_total),
            "total_usd_including_preflight": all_in_total,
            "tracker_basis_total_usd_including_preflight": all_in_tracker_total,
            "cogs_gap_percent_including_preflight": cogs_gap_percent(all_in_total, all_in_tracker_total),
            "all_in_cost_usd_per_page": (
                all_in_total / cost_summary["pages_attempted"]
                if all_in_total is not None and cost_summary["pages_attempted"] else None
            ),
            "unpriced_models": sorted({
                str(run.get("model"))
                for record in records
                for run in record.get("provider_runs", [])
                if usage_cost_usd(run.get("usage", {}), manifest.get("pricing", {}).get("by_model", {}).get(run.get("model"))) is None
                and int(run.get("attempt_count", 1) or 0) > 0
            } | {
                str(item.get("model"))
                for item in preflight
                if usage_cost_usd(
                    item.get("usage", {}),
                    manifest.get("pricing", {}).get("by_model", {}).get(item.get("model")),
                ) is None and int(item.get("attempt_count", 1) or 0) > 0
            }),
        },
        "tokens": _sum_usage_dicts([record.get("token_usage", {}) for record in records]),
        "request_attempt_count": sum(int(record.get("request_attempt_count", 0)) for record in records),
        "parse_failure_count": sum(parse_failures_by_split.values()),
        "parse_failure_count_by_split": parse_failures_by_split,
        "ocr_failure_count": sum(record.get("ocr_status") == "failed" for record in records),
        "provider_page_scores": _aggregate_provider_page_scores(records),
        "preflight": [{key: value for key, value in item.items() if key != "usage"} | {"usage": item.get("usage", _zero_usage())} for item in preflight],
        "paired_comparison": paired,
    }
    return report


def _aggregate_provider_page_scores(records: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[str, list[dict[str, str]]] = defaultdict(list)
    # Page-level gold is scored during each document run. This report section
    # intentionally records those aggregates only when such gold was provided.
    for record in records:
        for provider, aggregate in record.get("provider_page_scores", {}).items():
            pairs[provider].append({"aggregate": aggregate, "split": record["split"], "doc_id": record["doc_id"]})
    return {
        provider: {
            "documents_with_page_gold": len(rows),
            "per_document": rows,
        }
        for provider, rows in sorted(pairs.items())
    }


def _cost_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        attempted = [record for record in selected if record.get("ocr_attempted") is True]
        pages = sum(int(record.get("page_count") or 0) for record in attempted)

        def total(field: str) -> float | None:
            if not attempted:
                return 0.0
            values = [record.get(field) for record in attempted]
            if any(value is None for value in values):
                return None
            return round(sum(float(value) for value in values), 12)

        actual = total("cost_usd")
        tracker = total("cost_usd_tracker_basis")
        return {
            "documents_attempted": len(attempted),
            "pages_attempted": pages,
            "ocr_total_usd": actual,
            "ocr_tracker_basis_total_usd": tracker,
            "cogs_gap_percent": cogs_gap_percent(actual, tracker),
            "ocr_cost_usd_per_page": actual / pages if actual is not None and pages else None,
        }

    result = summarize(records)
    result["by_split"] = {
        split: summarize([record for record in records if record.get("split") == split])
        for split in sorted({str(record.get("split")) for record in records})
    }
    return result


def _sum_optional_costs(values: list[float | None]) -> float | None:
    if not values:
        return 0.0
    if any(value is None for value in values):
        return None
    return round(sum(float(value) for value in values), 12)


def _add_optional_costs(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(float(left) + float(right), 12)


def _write_run_outputs(run_dir: Path, records: list[dict[str, Any]], report: dict[str, Any]) -> None:
    report["run_id"] = run_dir.name
    lines = "".join(_canonical_json(record) + "\n" for record in records)
    _write_exclusive(run_dir / "records.jsonl", lines)
    _write_json_exclusive(run_dir / "score.json", report)
    _write_exclusive(run_dir / "score.md", _markdown_report(report))


def _markdown_report(report: dict[str, Any]) -> str:
    route = report["route_metrics"]["all"]
    ocr = report["ocr_metrics"]["all"]["micro"]["normalized"]
    cost = report["cost"]
    lines = [
        "# Arabic document OCR evaluation",
        "",
        f"- Mode: `{report['mode']}`" + (" (synthetic only; not an accuracy result)" if report["mock_only"] else ""),
        f"- Documents: {report['document_count']}",
        f"- Git SHA: `{report['git_sha'] or 'unavailable'}`",
        f"- Config SHA-256: `{report['config_sha256']}`",
        f"- Route coverage: {_format_metric(route['coverage'])}; abstentions: {route['abstentions']}; classifier failures: {route['classification_failures']}",
        f"- Auto-route accuracy: {_format_metric(route['accuracy_on_covered'])}",
        f"- Printed → handwritten errors: {route['printed_as_handwritten']}; handwritten → printed errors: {route['handwritten_as_printed']}",
        f"- Normalized micro CER / WER / token-F1: {_format_metric(ocr['cer'])} / {_format_metric(ocr['wer'])} / {_format_metric(ocr['token_f1'])}",
        f"- OCR cost including thoughts: {_format_metric(cost['ocr_total_usd'])} USD; {_format_metric(cost['ocr_cost_usd_per_page'])} USD/page",
        f"- Candidates-only tracker estimate: {_format_metric(cost['ocr_tracker_basis_total_usd'])} USD; thought-token under-report: {_format_percent(cost['cogs_gap_percent'])}",
        f"- Total including preflight: {_format_metric(cost['total_usd_including_preflight'])} USD; all-in/page: {_format_metric(cost['all_in_cost_usd_per_page'])} USD",
        f"- Parse failures: {report['parse_failure_count']}; OCR failures: {report['ocr_failure_count']}",
        f"- OCR p50/p95 latency: {report['latency_ms']['ocr']['p50_ms']} / {report['latency_ms']['ocr']['p95_ms']} ms",
        "",
        "No OCR text or prompts are included in this report. Run artifacts may contain OCR text and must remain private.",
        "",
    ]
    if report["mock_only"]:
        lines.append("This mock result validates harness behavior only; it is not a model benchmark.")
    if cost["ocr_total_usd"] is None:
        lines.append("Cost is unknown because one or more model rates are not configured in the manifest.")
    lines.append("")
    return "\n".join(lines)


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise EvaluationError(f"Comparison run does not contain records.jsonl: {path.parent}")
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise EvaluationError("Comparison records.jsonl contains a non-object row.")
            records.append(value)
    return records


def _provider_costs(
    provider_runs: list[dict[str, Any]],
    rates_by_model: dict[str, Any],
) -> tuple[list[dict[str, Any]], float | None, float | None]:
    from eval.arabic_documents.scoring import usage_cost_without_thoughts_usd

    costs: list[dict[str, Any]] = []
    total = 0.0
    tracker_total = 0.0
    unknown = False
    tracker_unknown = False
    for run in provider_runs:
        model = run.get("model")
        usage = run.get("usage", {})
        cost = usage_cost_usd(usage, rates_by_model.get(model))
        tracker_cost = usage_cost_without_thoughts_usd(usage, rates_by_model.get(model))
        token_count = sum(int(usage.get(key, 0) or 0) for key in ("prompt_tokens", "output_tokens", "thought_tokens"))
        if int(run.get("attempt_count", 1) or 0) == 0 and token_count == 0:
            cost = tracker_cost = 0.0
        costs.append({
            "provider": run.get("provider"),
            "model": model,
            "cost_usd": cost,
            "tracker_cost_usd": tracker_cost,
        })
        if cost is None:
            unknown = True
        elif cost is not None:
            total += cost
        if tracker_cost is None:
            tracker_unknown = True
        else:
            tracker_total += tracker_cost
    return (
        costs,
        None if unknown else round(total, 12),
        None if tracker_unknown else round(tracker_total, 12),
    )


def _usage_from_object(usage: Any) -> dict[str, int]:
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "thought_tokens": int(getattr(usage, "thought_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _sum_usage_dicts(usages: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("prompt_tokens", "output_tokens", "thought_tokens", "total_tokens")
    return {key: sum(int(item.get(key, 0) or 0) for item in usages) for key in keys}


def _zero_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "total_tokens": 0}


def _safe_failure_kind(value: Any) -> str:
    safe = str(value or "provider_error").strip().lower()
    return safe if re.fullmatch(r"[a-z0-9_-]{1,40}", safe) else "provider_error"


def _route_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _pdf_page_count(path: Path) -> int:
    import fitz

    with fitz.open(path) as document:
        return document.page_count


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _write_json_exclusive(path: Path, value: Any) -> None:
    _write_exclusive(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _write_exclusive(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as output:
        output.write(content)


def _format_metric(value: Any) -> str:
    return "unknown" if value is None else f"{float(value):.4f}"


def _format_percent(value: Any) -> str:
    return "unknown" if value is None else f"{float(value):.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
