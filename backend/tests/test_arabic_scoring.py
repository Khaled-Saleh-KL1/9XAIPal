"""Deterministic tests for the standalone Arabic OCR evaluation harness."""

from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _scoring():
    module_path = Path(__file__).parents[1] / "eval" / "arabic_documents" / "scoring.py"
    assert module_path.is_file(), "Arabic evaluation scoring is not implemented yet."
    return importlib.import_module("eval.arabic_documents.scoring")


def _runner():
    module_path = Path(__file__).parents[1] / "eval" / "arabic_documents" / "run_eval.py"
    assert module_path.is_file(), "Arabic evaluation runner is not implemented yet."
    return importlib.import_module("eval.arabic_documents.run_eval")


def test_eval_normalization_never_changes_stored_text():
    raw = "إِنَّ الـنص"

    normalized = _scoring().normalize_for_score(raw)

    assert normalized == "ان النص"
    assert raw == "إِنَّ الـنص"


def test_text_score_reports_exact_character_word_and_token_metrics():
    score = _scoring().score_ocr_text("one three", "one two")

    assert score["raw"]["cer"] == 4 / 7
    assert score["raw"]["wer"] == 0.5
    assert score["raw"]["token_f1"] == 0.5


def test_text_aggregation_keeps_micro_and_per_document_cer_distinct():
    aggregate = _scoring().aggregate_text_scores([
        {"doc_id": "short", "reference": "ab", "prediction": "a"},
        {"doc_id": "long", "reference": "uvwxyz", "prediction": "uvwxyz"},
    ])

    assert aggregate["micro"]["normalized"]["cer"] == 1 / 8
    assert aggregate["macro"]["normalized"]["cer"] == 0.25
    assert aggregate["per_document"]["short"]["normalized"]["cer"] == 0.5
    assert aggregate["per_document"]["long"]["normalized"]["cer"] == 0


def test_empty_ocr_aggregate_has_no_accuracy_values():
    aggregate = _scoring().aggregate_text_scores([])

    assert aggregate["documents"] == 0
    assert aggregate["micro"]["normalized"]["cer"] is None
    assert aggregate["micro"]["normalized"]["wer"] is None
    assert aggregate["micro"]["normalized"]["token_f1"] is None


def test_classifier_report_counts_abstention_separately():
    report = _scoring().score_routes([
        {"truth": "printed", "prediction": "printed"},
        {"truth": "handwritten", "prediction": "uncertain"},
    ])

    assert report["coverage"] == 0.5
    assert report["abstentions"] == 1
    assert report["accuracy_on_covered"] == 1.0
    assert report["printed_as_handwritten"] == 0
    assert report["handwritten_as_printed"] == 0


def test_usage_cost_prices_thought_tokens_at_output_rate():
    scoring = _scoring()
    usage = {"prompt_tokens": 100_000, "output_tokens": 1_000, "thought_tokens": 500}
    rates = {"input_usd_per_million": 1.0, "output_usd_per_million": 2.0}
    cost = scoring.usage_cost_usd(usage, rates)
    tracker_cost = scoring.usage_cost_without_thoughts_usd(usage, rates)

    assert cost == 0.103
    assert tracker_cost == 0.102
    assert scoring.cogs_gap_percent(cost, tracker_cost) == pytest.approx((0.001 / 0.103) * 100)


def test_latency_percentiles_use_nearest_rank_for_small_samples():
    assert _scoring().latency_percentiles([10, 20, 30, 40]) == {
        "count": 4,
        "p50_ms": 20,
        "p95_ms": 40,
    }


def test_page_coverage_reports_gaps_duplicates_and_unexpected_pages():
    coverage = _scoring().page_coverage([1, 2, 3], [1, 2, 2, 4])

    assert coverage["missing_pages"] == [3]
    assert coverage["duplicate_pages"] == [2]
    assert coverage["unexpected_pages"] == [4]
    assert coverage["complete"] is False


def test_provider_ranges_detect_page_overlap():
    ranges = _scoring().validate_provider_ranges(
        [1, 2, 3],
        [
            {"provider": "gemini_arabic_flash", "committed_pages": [1, 2]},
            {"provider": "gemma4_arabic_fallback", "committed_pages": [2, 3]},
        ],
    )

    assert ranges["missing_pages"] == []
    assert ranges["duplicate_pages"] == [2]
    assert ranges["complete"] is False


def test_paired_run_comparison_joins_documents_by_id():
    comparison = _scoring().compare_paired_runs(
        candidate=[
            {"doc_id": "a", "ocr_scores": {"normalized": {"cer": 0.2}}},
            {"doc_id": "b", "ocr_scores": {"normalized": {"cer": 0.1}}},
        ],
        baseline=[
            {"doc_id": "a", "ocr_scores": {"normalized": {"cer": 0.4}}},
            {"doc_id": "b", "ocr_scores": {"normalized": {"cer": 0.1}}},
            {"doc_id": "unpaired", "ocr_scores": {"normalized": {"cer": 0.9}}},
        ],
    )

    assert comparison["paired_documents"] == 2
    assert comparison["unpaired_candidate_documents"] == 0
    assert comparison["unpaired_baseline_documents"] == 1
    assert comparison["mean_cer_improvement"] == 0.1


def test_paired_run_comparison_counts_failed_ocr_as_loss():
    comparison = _scoring().compare_paired_runs(
        candidate=[
            {"doc_id": "failed", "ocr_status": "failed", "ocr_scores": None},
            {"doc_id": "good", "ocr_status": "complete", "ocr_scores": {"normalized": {"cer": 0.1}}},
            {"doc_id": "english", "ocr_status": "not_run_for_route", "ocr_scores": None},
        ],
        baseline=[
            {"doc_id": "failed", "ocr_status": "complete", "ocr_scores": {"normalized": {"cer": 0.3}}},
            {"doc_id": "good", "ocr_status": "complete", "ocr_scores": {"normalized": {"cer": 0.2}}},
            {"doc_id": "english", "ocr_status": "not_run_for_route", "ocr_scores": None},
        ],
    )
    assert comparison["paired_documents"] == 2
    assert comparison["scored_documents"] == 1
    assert comparison["candidate_failures"] == 1
    assert comparison["baseline_wins"] == 1
    assert {row["doc_id"] for row in comparison["per_document"]} == {"failed", "good"}


def test_held_out_misroute_fails_mock_run_after_writing_report(tmp_path):
    manifest = tmp_path / "manifest.json"
    run_dir = tmp_path / "held-out-run"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "held_out_splits": ["held_out"],
        "documents": [{
            "doc_id": "printed-misroute", "split": "held_out", "expected_route": "printed",
            "ground_truth_text": "نص عربي", "mock_prediction_route": "handwritten",
        }],
    }), encoding="utf-8")
    assert _runner().main(["--manifest", str(manifest), "--mock", "--run-dir", str(run_dir)]) == 1
    report = json.loads((run_dir / "score.json").read_text(encoding="utf-8"))
    assert report["route_metrics"]["held_out"]["printed_as_handwritten"] == 1


def test_live_handwritten_route_stays_disabled_without_provider_call(tmp_path):
    runner = _runner()
    classified = [{
        "doc_id": "handwritten", "classification_status": "complete",
        "predicted_route": "arabic_handwritten", "ocr_status": "not_run",
    }]
    records, pairs = runner._run_live_ocr(
        classified, [], {}, SimpleNamespace(), "config", "git", tmp_path, "hybrid",
    )
    assert records[0]["ocr_status"] == "disabled_handwritten"
    assert pairs == []


def test_report_aggregates_provider_attempts_retries_and_fallbacks():
    runner = _runner()
    record = {
        "doc_id": "printed-1", "split": "held_out", "expected_route": "printed",
        "predicted_route": "arabic_printed", "classification_status": "complete",
        "ocr_status": "complete", "ocr_attempted": True, "ocr_scores": None,
        "page_count": 1, "page_coverage": {"actual_pages": [1]},
        "provider_range_validation": {"complete": True}, "latency_ms": 0,
        "ocr_latency_ms": 0, "cost_usd": 0, "cost_usd_tracker_basis": 0,
        "provider_runs": [
            {"provider": "gemini_arabic_flash", "attempt_count": 2, "retry_count": 1,
             "attempt_metadata": [{"key_index": 0}, {"key_index": 1}], "usage": {}},
            {"provider": "gemma4_arabic_fallback", "attempt_count": 1, "retry_count": 0, "usage": {}},
        ],
        "token_usage": {}, "request_attempt_count": 3, "provider_page_scores": {},
    }
    report = runner._build_report(
        "live", "hybrid", [record], [], {"normalization": {}, "pricing": {"by_model": {}},
        "held_out_splits": ["held_out"]}, "config", "git", "manifest", [], None,
    )
    assert report["provider_attempts"]["by_provider"]["gemini_arabic_flash"] == 2
    assert report["provider_attempts"]["by_key_index"]["0"] == 1
    assert report["provider_attempts"]["retries"] == 1
    assert report["provider_attempts"]["fallback_documents"] == 1


def test_mock_run_writes_hashes_scores_and_text_free_immutable_records(tmp_path):
    manifest = tmp_path / "manifest.json"
    run_dir = tmp_path / "run-1"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "normalization": {},
        "pricing": {},
        "documents": [{
            "doc_id": "synthetic-printed",
            "split": "mock_printed",
            "expected_route": "arabic_printed",
            "source_pdf": None,
            "ground_truth_text": "ان النص",
            "mock_prediction_text": "ان النص",
        }],
    }), encoding="utf-8")

    result = _runner().main([
        "--manifest", str(manifest), "--mock", "--run-dir", str(run_dir),
    ])

    assert result == 0
    report = json.loads((run_dir / "score.json").read_text(encoding="utf-8"))
    record = json.loads((run_dir / "records.jsonl").read_text(encoding="utf-8"))
    assert report["mode"] == "mock"
    assert report["ocr_metrics"]["all"]["micro"]["normalized"]["cer"] == 0
    assert record["source_sha256"] is None
    assert len(record["ground_truth_sha256"]) == 64
    assert "ان النص" not in (run_dir / "records.jsonl").read_text(encoding="utf-8")

    assert _runner().main([
        "--manifest", str(manifest), "--mock", "--run-dir", str(run_dir),
    ]) == 2


def test_non_mock_manifest_requires_held_out_split(tmp_path):
    runner = _runner()
    source = tmp_path / "source.pdf"
    gold = tmp_path / "gold.txt"
    source.write_bytes(b"placeholder")
    gold.write_text("gold", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "documents": [{
        "doc_id": "doc", "split": "printed", "expected_route": "printed",
        "source_pdf": str(source), "ground_truth_file": str(gold),
    }]}), encoding="utf-8")
    with pytest.raises(runner.EvaluationError, match="held_out_splits"):
        runner._load_manifest(manifest, allow_inline=False)


def test_gemini_preflight_resolves_a_document_without_persisting_paths():
    runner = _runner()
    document = {"doc_id": "printed-1", "source_path": Path("/private/printed-1.pdf")}
    records = [{
        "doc_id": "printed-1",
        "classification_status": "complete",
        "predicted_route": "arabic_printed",
    }]

    assert runner._preflight_sample(records, [document]) is document


def test_classifier_only_run_does_not_score_an_empty_ocr_prediction(tmp_path, monkeypatch):
    runner = _runner()
    source = tmp_path / "printed.pdf"
    source.write_bytes(b"synthetic pdf placeholder")
    classifier_module = importlib.import_module("app.extraction.arabic_classifier")
    monkeypatch.setattr(
        classifier_module,
        "classify_document",
        lambda _path: SimpleNamespace(
            route=SimpleNamespace(value="arabic_printed"),
            language="arabic",
            writing_style="printed",
            text_direction="rtl",
            confidence=0.99,
            classifier_model="test-classifier",
        ),
    )
    monkeypatch.setattr(runner, "_pdf_page_count", lambda _path: 1)
    document = {
        "doc_id": "printed-1",
        "split": "printed",
        "expected_route": "printed",
        "source_path": source,
        "ground_truth_path": None,
        "ground_truth_pages_path": None,
        "ground_truth_text": "Arabic ground truth",
    }

    _records, ocr_pairs = runner._classify_all(
        [document], "classifier-only", None, {}, "config-hash", "git-sha",
    )

    assert ocr_pairs == []


def test_git_sha_is_optional_when_the_git_executable_is_unavailable(monkeypatch):
    runner = _runner()

    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(runner.subprocess, "run", missing_git)

    assert runner._git_sha() is None


def test_page_ground_truth_is_hashed_separately(tmp_path):
    runner = _runner()
    whole_gt = tmp_path / "doc.gt.txt"
    pages_gt = tmp_path / "doc.pages.json"
    whole_gt.write_text("whole document", encoding="utf-8")
    pages_bytes = b'{"1":"first page"}'
    pages_gt.write_bytes(pages_bytes)

    text, whole_hash, page_text, pages_hash = runner._read_ground_truth({
        "doc_id": "doc-1",
        "ground_truth_path": whole_gt,
        "ground_truth_pages_path": pages_gt,
    })

    assert text == "whole document"
    assert whole_hash == hashlib.sha256(b"whole document").hexdigest()
    assert page_text == {1: "first page"}
    assert pages_hash == hashlib.sha256(pages_bytes).hexdigest()


def test_unpriced_provider_call_with_zero_reported_tokens_has_unknown_total():
    runner = _runner()

    costs, total, tracker_total = runner._provider_costs([{
        "provider": "gemini_arabic_flash",
        "model": "gemini-3.7-flash",
        "usage": {"prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0},
    }], {})

    assert costs[0]["cost_usd"] is None
    assert total is None
    assert tracker_total is None


def test_provider_costs_treat_zero_attempts_as_zero_not_unknown():
    runner = _runner()

    costs, total, tracker_total = runner._provider_costs([{
        "provider": "gemini_arabic_flash",
        "model": "gemini-3.7-flash",
        "attempt_count": 0,
        "usage": {"prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0},
    }], {})

    assert costs[0]["cost_usd"] == 0
    assert costs[0]["tracker_cost_usd"] == 0
    assert total == tracker_total == 0


def test_classifier_failures_reduce_coverage_without_counting_as_auto_route():
    runner = _runner()
    report = runner._build_report(
        "classifier-only", None,
        [{
            "doc_id": "failed-doc",
            "split": "printed",
            "expected_route": "printed",
            "predicted_route": None,
            "classification_status": "failed",
            "ocr_status": "not_run",
            "ocr_scores": None,
            "page_count": None,
            "page_coverage": None,
            "provider_range_validation": None,
            "latency_ms": 10,
            "ocr_latency_ms": 0,
            "cost_usd": 0.0,
            "provider_runs": [],
            "token_usage": {"prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "total_tokens": 0},
            "request_attempt_count": 0,
            "provider_page_scores": {},
        }],
        [], {"normalization": {}, "pricing": {"by_model": {}}},
        "config-hash", "git-sha", "manifest-hash", [], None,
    )

    route = report["route_metrics"]["all"]
    assert route["coverage"] == 0
    assert route["documents_requested"] == 1
    assert route["classification_failures"] == 1


def test_report_counts_invalid_provider_page_output_as_a_parse_failure():
    runner = _runner()
    record = {
        "doc_id": "printed-1",
        "split": "printed_digital",
        "expected_route": "printed",
        "predicted_route": "arabic_printed",
        "classification_status": "complete",
        "ocr_status": "incomplete_pages",
        "ocr_attempted": True,
        "ocr_scores": _scoring().score_ocr_text("x", "x"),
        "page_count": 2,
        "page_coverage": {"complete": False, "expected_pages": [1, 2], "actual_pages": [1]},
        "provider_range_validation": {"complete": False},
        "latency_ms": 20,
        "ocr_latency_ms": 10,
        "cost_usd": None,
        "cost_usd_tracker_basis": None,
        "provider_runs": [{
            "provider": "gemini_arabic_flash",
            "model": "gemini-3.7-flash",
            "attempt_count": 1,
            "failure_kind": "invalid_output",
            "usage": {"prompt_tokens": 5, "output_tokens": 0, "thought_tokens": 0},
        }],
        "token_usage": {"prompt_tokens": 5, "output_tokens": 0, "thought_tokens": 0, "total_tokens": 5},
        "request_attempt_count": 1,
        "provider_page_scores": {},
    }

    report = runner._build_report(
        "live", "hybrid", [record], [],
        {"normalization": {}, "pricing": {"by_model": {}}},
        "config-hash", "git-sha", "manifest-hash", [], None,
    )

    assert report["parse_failure_count"] == 1
    assert report["parse_failure_count_by_split"]["printed_digital"] == 1
    assert report["page_integrity"]["total_pages_attempted"] == 2
    assert report["page_integrity"]["total_pages_scored"] == 1


def test_mock_manifest_rejects_real_source_and_gold_files(tmp_path):
    runner = _runner()
    source = tmp_path / "private.pdf"
    gold = tmp_path / "private.gt.txt"
    source.write_bytes(b"synthetic placeholder")
    gold.write_text("private ground truth", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "documents": [{
            "doc_id": "doc-1",
            "split": "printed",
            "expected_route": "printed",
            "source_pdf": str(source),
            "ground_truth_file": str(gold),
        }],
    }), encoding="utf-8")

    with pytest.raises(runner.EvaluationError, match="synthetic"):
        runner._load_manifest(manifest, allow_inline=True)


def test_cost_summary_reports_tracker_gap_and_subset_cost_per_page():
    runner = _runner()

    summary = runner._cost_summary([{
        "doc_id": "printed-1",
        "split": "printed_digital",
        "ocr_attempted": True,
        "page_count": 1,
        "cost_usd": 0.0048,
        "cost_usd_tracker_basis": 0.0015,
    }])

    assert summary["ocr_total_usd"] == 0.0048
    assert summary["ocr_tracker_basis_total_usd"] == 0.0015
    assert summary["cogs_gap_percent"] == 68.75
    assert summary["ocr_cost_usd_per_page"] == 0.0048
    assert summary["by_split"]["printed_digital"]["cogs_gap_percent"] == 68.75


def test_default_runner_mode_stays_classifier_only(tmp_path, monkeypatch):
    runner = _runner()
    source = tmp_path / "printed.pdf"
    gold = tmp_path / "printed.gt.txt"
    source.write_bytes(b"synthetic placeholder")
    gold.write_text("gold text", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "held_out_splits": ["printed_digital"],
        "documents": [{
            "doc_id": "printed-1",
            "split": "printed_digital",
            "expected_route": "printed",
            "source_pdf": str(source),
            "ground_truth_file": str(gold),
        }],
    }), encoding="utf-8")
    runtime_settings = SimpleNamespace(
        arabic_ocr_enabled=False,
        arabic_handwritten_ocr_enabled=False,
        arabic_router_model="local-router",
        arabic_router_base_url="http://localhost:11434",
        arabic_gemini_printed_model="gemini-3.7-flash",
        arabic_gemini_printed_thinking_level="low",
        arabic_gemini_handwritten_model="gemini-3.1-pro-preview",
        arabic_gemma_fallback_model="gemma4:31b-cloud",
        arabic_gemma_base_url="https://ollama.com",
        arabic_ocr_dpi=200,
        arabic_gemini_media_resolution="HIGH",
        arabic_ocr_batch_pages=4,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_max_output_tokens=32768,
        arabic_min_body_char_count=20,
        arabic_classifier_confidence_min=0.80,
        arabic_handwritten_confidence_min=0.97,
        arabic_classifier_batch_pages=8,
    )
    monkeypatch.setattr(runner, "_runtime_settings", lambda: runtime_settings)
    monkeypatch.setattr(runner, "_classify_all", lambda *_args: ([], []))
    monkeypatch.setattr(
        runner, "_run_live_ocr",
        lambda *_args: pytest.fail("default classifier-only mode must not start OCR"),
    )
    run_dir = tmp_path / "classifier-only-run"

    result = runner.main([
        "--manifest", str(manifest), "--run-dir", str(run_dir),
    ])

    assert result == 0
    report = json.loads((run_dir / "score.json").read_text(encoding="utf-8"))
    assert report["mode"] == "classifier-only"
    assert report["ocr_metrics"]["all"]["micro"]["normalized"]["cer"] is None


def test_private_corpus_inside_backend_is_rejected_when_git_cannot_verify_ignore_rules():
    runner = _runner()
    backend_root = Path(runner.__file__).resolve().parents[2]
    private_path = backend_root / "eval" / "arabic_documents" / "corpus" / "private.pdf"

    with pytest.raises(runner.EvaluationError, match="Git|git|ignored"):
        runner._assert_private_file(private_path, None)


def test_custom_run_output_inside_backend_requires_private_ignored_location():
    runner = _runner()
    backend_root = Path(runner.__file__).resolve().parents[2]
    unsafe_path = backend_root / "custom-public-output"
    safe_default = Path(runner.__file__).resolve().parent / "runs" / "run-1"

    with pytest.raises(runner.EvaluationError, match="ignored|outside"):
        runner._assert_private_output_dir(unsafe_path, None)
    runner._assert_private_output_dir(safe_default, None)


def test_live_preflight_probes_each_flash_key_without_serializing_credentials(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    runner = _runner()
    source = tmp_path / "printed.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=72, height=72)
        pdf.save(source)
    observed_keys = []
    observed_sizes = []

    class RuntimeSettings:
        gemini_api_keys = ["secret-key-one", "secret-key-two"]
        arabic_gemini_printed_model = "gemini-3.7-flash"
        arabic_ocr_dpi = 200

        def model_copy(self, *, update):
            return SimpleNamespace(gemini_api_keys=[update["gemini_api_keys_raw"]])

    class FakeGeminiClient:
        def __init__(self, *, settings):
            observed_keys.extend(settings.gemini_api_keys)

        def generate_batch(self, _pages, validator):
            assert callable(validator)
            image_bytes = _pages[0].png
            observed_sizes.append((
                int.from_bytes(image_bytes[16:20], "big"),
                int.from_bytes(image_bytes[20:24], "big"),
            ))
            return SimpleNamespace(
                attempt_count=1,
                latency_ms=7,
                usage=SimpleNamespace(
                    prompt_tokens=3,
                    output_tokens=4,
                    thought_tokens=1,
                    total_tokens=8,
                ),
            )

    gemini_module = importlib.import_module("app.extraction.gemini_ocr_client")
    monkeypatch.setattr(gemini_module, "GeminiOcrClient", FakeGeminiClient)

    results = runner._preflight_gemini(
        {"source_path": source}, RuntimeSettings(),
    )

    assert observed_keys == ["secret-key-one", "secret-key-two"]
    assert all(width >= 199 and height >= 199 for width, height in observed_sizes)
    assert [result["key_index"] for result in results] == [0, 1]
    assert all(result["status"] == "available" for result in results)
    assert all(result["model"] == "gemini-3.7-flash" for result in results)
    serialized = json.dumps(results)
    assert "secret-key-one" not in serialized
    assert "secret-key-two" not in serialized


def test_config_hash_snapshot_includes_classifier_and_decode_settings_but_no_keys():
    runner = _runner()
    settings = SimpleNamespace(
        arabic_router_model="qwen3-vl:4b-instruct",
        arabic_gemini_printed_model="gemini-3.7-flash",
        arabic_gemini_printed_thinking_level="low",
        arabic_gemini_handwritten_model="gemini-3.1-pro-preview",
        arabic_gemma_fallback_model="gemma4:31b-cloud",
        arabic_router_base_url="http://localhost:11434",
        arabic_gemma_base_url="https://ollama.com",
        arabic_ocr_enabled=False,
        arabic_handwritten_ocr_enabled=False,
        arabic_ocr_dpi=200,
        arabic_gemini_media_resolution="HIGH",
        arabic_ocr_batch_pages=4,
        arabic_ocr_single_request_max_pages=4,
        arabic_ocr_max_output_tokens=32768,
        arabic_classifier_confidence_min=0.80,
        arabic_handwritten_confidence_min=0.97,
        arabic_classifier_batch_pages=8,
        arabic_min_body_char_count=20,
        gemini_api_keys_raw="must-not-enter-config-hash",
    )

    snapshot = runner._config_snapshot("live", "hybrid", {}, settings)

    assert snapshot["classifier_confidence_min"] == 0.80
    assert snapshot["handwritten_confidence_min"] == 0.97
    assert snapshot["classifier_batch_pages"] == 8
    assert snapshot["thinking_level"] == "low"
    assert snapshot["max_output_tokens"] == 32768
    assert snapshot["handwritten_model"] == "gemini-3.1-pro-preview"
    assert snapshot["handwritten_ocr_enabled"] is False
    assert "gemini_api_keys_raw" not in snapshot
    assert "must-not-enter-config-hash" not in json.dumps(snapshot)


# --- Structural block retention and reading order (spec §14) ---------------

def _block(page, kind, text):
    return {"page_number": page, "type": kind, "text": text}


_REFERENCE_BLOCKS = [
    _block(1, "heading", "الفصل الأول: المقدمة"),
    _block(1, "text", "هذا هو العمود الأيمن ويقرأ أولا من الأعلى إلى الأسفل"),
    _block(1, "text", "وهذا هو العمود الأيسر ويقرأ بعد انتهاء العمود الأيمن"),
    _block(2, "table", "البند القيمة ألف ١٢"),
    _block(2, "list", "العنصر الأول\nالعنصر الثاني"),
]


def test_structure_scores_a_faithful_extraction_as_complete_and_ordered():
    scores = _scoring().score_structure(_REFERENCE_BLOCKS, _REFERENCE_BLOCKS)

    assert scores["reference_blocks"] == 5
    assert scores["matched_blocks"] == 5
    assert scores["block_retention"] == 1.0
    assert scores["block_precision"] == 1.0
    assert scores["type_mismatches"] == 0
    assert scores["order_pairwise_accuracy"] == 1.0
    assert scores["order_lis_ratio"] == 1.0
    assert scores["by_type"]["heading"] == {"reference": 1, "predicted": 1, "matched": 1, "retention": 1.0}


def test_structure_counts_a_dropped_block_against_retention():
    predicted = [block for block in _REFERENCE_BLOCKS if block["type"] != "table"]

    scores = _scoring().score_structure(predicted, _REFERENCE_BLOCKS)

    assert scores["matched_blocks"] == 4
    assert scores["block_retention"] == pytest.approx(0.8)
    assert scores["block_precision"] == 1.0
    assert scores["by_type"]["table"]["retention"] == 0.0


def test_structure_reports_a_heading_flattened_to_prose_as_a_type_mismatch():
    predicted = [dict(block) for block in _REFERENCE_BLOCKS]
    predicted[0]["type"] = "text"

    scores = _scoring().score_structure(predicted, _REFERENCE_BLOCKS)

    assert scores["by_type"]["heading"]["matched"] == 0
    assert scores["type_mismatches"] == 1
    assert scores["block_retention"] == pytest.approx(0.8)


def test_structure_detects_columns_read_left_to_right():
    reference = _REFERENCE_BLOCKS
    predicted = [reference[0], reference[2], reference[1], reference[3], reference[4]]

    scores = _scoring().score_structure(predicted, reference)

    assert scores["block_retention"] == 1.0
    # One of ten block pairs is out of order; the longest in-order run keeps 4 of 5.
    assert scores["order_pairwise_accuracy"] == pytest.approx(0.9)
    assert scores["order_lis_ratio"] == pytest.approx(0.8)


def test_structure_counts_spurious_blocks_against_precision():
    predicted = [*_REFERENCE_BLOCKS, _block(2, "text", "نص مختلق لا يوجد في الوثيقة الأصلية إطلاقا")]

    scores = _scoring().score_structure(predicted, _REFERENCE_BLOCKS)

    assert scores["block_retention"] == 1.0
    assert scores["block_precision"] == pytest.approx(5 / 6)


def test_structure_matching_tolerates_small_ocr_errors_but_not_other_text():
    predicted = [dict(block) for block in _REFERENCE_BLOCKS]
    predicted[1]["text"] = "هذا هو العمود الايمن ويقرأ اولا من الاعلى الى الاسفل"  # alef variants
    predicted[2]["text"] = "كلام آخر تماما لا علاقة له بالعمود"

    scores = _scoring().score_structure(predicted, _REFERENCE_BLOCKS)

    assert scores["matched_blocks"] == 4
    assert scores["by_type"]["text"]["matched"] == 1


def test_structure_does_not_match_blocks_more_than_one_page_apart():
    reference = [_block(1, "text", "فقرة فريدة في الصفحة الأولى من الوثيقة")]
    predicted = [_block(3, "text", "فقرة فريدة في الصفحة الأولى من الوثيقة")]

    assert _scoring().score_structure(predicted, reference)["matched_blocks"] == 0


def test_structure_rejects_unknown_block_types():
    with pytest.raises(ValueError):
        _scoring().score_structure([_block(1, "sidebar", "x")], _REFERENCE_BLOCKS)


def test_structure_aggregate_is_micro_over_documents():
    scoring = _scoring()
    perfect = scoring.score_structure(_REFERENCE_BLOCKS, _REFERENCE_BLOCKS)
    dropped = scoring.score_structure(_REFERENCE_BLOCKS[:1], _REFERENCE_BLOCKS)

    aggregate = scoring.aggregate_structure_scores([perfect, dropped])

    assert aggregate["documents"] == 2
    assert aggregate["reference_blocks"] == 10
    assert aggregate["matched_blocks"] == 6
    assert aggregate["block_retention"] == pytest.approx(0.6)
    assert aggregate["by_type"]["text"]["retention"] == pytest.approx(0.5)
    assert aggregate["order_pairwise_accuracy"] == 1.0
    assert scoring.aggregate_structure_scores([])["block_retention"] is None


def test_structure_blocks_come_from_the_same_content_list_the_chunker_reads():
    from app.extraction.arabic_adapter import pages_to_content_list
    from app.extraction.arabic_types import ArabicOcrPage

    markdown = (
        "## الفصل الأول: المقدمة\n\n"
        "هذا هو العمود الأيمن ويقرأ أولا من الأعلى إلى الأسفل\n\n"
        "- العنصر الأول\n- العنصر الثاني\n\n"
        "| البند | القيمة |\n| --- | --- |\n| ألف | ١٢ |\n\n"
        "$$x=1$$"
    )
    page = ArabicOcrPage(
        page_number=2, raw_markdown=markdown, markdown=markdown,
        provider="gemini_arabic_flash", model="m",
    )

    blocks = _scoring().blocks_from_content_list(pages_to_content_list([page]))

    assert [(block["page_number"], block["type"]) for block in blocks] == [
        (2, "heading"), (2, "text"), (2, "list"), (2, "table"), (2, "equation"),
    ]
    assert blocks[2]["text"] == "العنصر الأول\nالعنصر الثاني"
    assert "<" not in blocks[3]["text"] and "ألف" in blocks[3]["text"]


def _blocks_file(tmp_path, blocks, name="doc.blocks.json"):
    path = tmp_path / name
    path.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    return path


def test_block_ground_truth_is_read_validated_and_hashed(tmp_path):
    runner = _runner()
    path = _blocks_file(tmp_path, _REFERENCE_BLOCKS)

    blocks, digest = runner._read_block_ground_truth(
        {"doc_id": "doc-1", "ground_truth_blocks_path": path}
    )

    assert blocks == _REFERENCE_BLOCKS
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert runner._read_block_ground_truth({"doc_id": "doc-2"}) == (None, None)

    bad = _blocks_file(tmp_path, [{"page_number": 1, "type": "sidebar", "text": "x"}], "bad.json")
    with pytest.raises(runner.EvaluationError, match="block"):
        runner._read_block_ground_truth({"doc_id": "doc-3", "ground_truth_blocks_path": bad})


def test_real_manifest_resolves_the_block_ground_truth_file(tmp_path):
    runner = _runner()
    source = tmp_path / "source.pdf"
    gold = tmp_path / "gold.txt"
    source.write_bytes(b"placeholder")
    gold.write_text("gold", encoding="utf-8")
    blocks = _blocks_file(tmp_path, _REFERENCE_BLOCKS)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "held_out_splits": ["printed"],
        "documents": [{
            "doc_id": "doc", "split": "printed", "expected_route": "printed",
            "source_pdf": str(source), "ground_truth_file": str(gold),
            "ground_truth_blocks_file": str(blocks),
        }],
    }), encoding="utf-8")

    loaded = runner._load_manifest(manifest, allow_inline=False)

    assert loaded["documents"][0]["ground_truth_blocks_path"] == blocks


def test_mock_manifest_rejects_a_block_ground_truth_file(tmp_path):
    runner = _runner()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "documents": [{
        "doc_id": "doc", "split": "mock", "expected_route": "printed",
        "ground_truth_text": "نص", "ground_truth_blocks_file": str(_blocks_file(tmp_path, [])),
    }]}), encoding="utf-8")

    with pytest.raises(runner.EvaluationError, match="synthetic"):
        runner._load_manifest(manifest, allow_inline=True)


def test_mock_run_reports_structure_metrics_without_storing_text(tmp_path):
    manifest = tmp_path / "manifest.json"
    run_dir = tmp_path / "structure-run"
    predicted = [_REFERENCE_BLOCKS[0], _REFERENCE_BLOCKS[2], _REFERENCE_BLOCKS[1]]
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "structure": {"match_threshold": 0.8},
        "documents": [{
            "doc_id": "structured", "split": "mock", "expected_route": "printed",
            "ground_truth_text": "نص", "mock_prediction_text": "نص",
            "ground_truth_blocks": _REFERENCE_BLOCKS[:3],
            "mock_prediction_blocks": predicted,
        }],
    }, ensure_ascii=False), encoding="utf-8")

    assert _runner().main(["--manifest", str(manifest), "--mock", "--run-dir", str(run_dir)]) == 0

    report = json.loads((run_dir / "score.json").read_text(encoding="utf-8"))
    record = json.loads((run_dir / "records.jsonl").read_text(encoding="utf-8"))
    structure = report["structure_metrics"]["all"]
    assert structure["block_retention"] == 1.0
    assert structure["order_pairwise_accuracy"] == pytest.approx(2 / 3)
    assert report["structure_metrics"]["by_split"]["mock"]["documents"] == 1
    assert record["structure_scores"]["matched_blocks"] == 3
    assert len(record["ground_truth_blocks_sha256"]) == 64
    assert "العمود" not in (run_dir / "records.jsonl").read_text(encoding="utf-8")
    assert "العمود" not in (run_dir / "score.json").read_text(encoding="utf-8")
    markdown = (run_dir / "score.md").read_text(encoding="utf-8")
    assert "block retention 1.0000" in markdown
    assert "Reading order: pairwise" in markdown


def test_live_ocr_scores_structure_from_the_adapter_content_list(tmp_path, monkeypatch):
    from app.extraction import arabic_fallback, arabic_ocr, gemini_ocr_client
    from app.extraction.arabic_types import ArabicOcrPage

    runner = _runner()
    markdown = "## الفصل الأول: المقدمة\n\nهذا هو العمود الأيمن ويقرأ أولا من الأعلى إلى الأسفل"
    page = ArabicOcrPage(
        page_number=1, raw_markdown=markdown, markdown=markdown,
        provider="gemini_arabic_flash", model="m",
    )

    def fake_extract(*, output_dir, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "ocr_manifest.json").write_text(json.dumps({"provider_runs": []}), encoding="utf-8")
        return SimpleNamespace(
            pages=(page,), output_dir=output_dir,
            provider_summary=[{"provider": "gemini_arabic_flash", "pages": [1]}],
        )

    monkeypatch.setattr(arabic_ocr, "extract_arabic_document", fake_extract)
    monkeypatch.setattr(gemini_ocr_client, "GeminiOcrClient", lambda **_kwargs: object())
    monkeypatch.setattr(arabic_fallback, "GemmaArabicFallback", lambda **_kwargs: object())
    monkeypatch.setattr(runner, "_pdf_page_count", lambda _path: 1)
    document = {
        "doc_id": "live", "split": "printed", "expected_route": "printed",
        "source_path": tmp_path / "live.pdf", "ground_truth_path": None,
        "ground_truth_pages_path": None,
        "ground_truth_blocks_path": _blocks_file(tmp_path, _REFERENCE_BLOCKS[:2]),
    }
    classified = [{
        "doc_id": "live", "split": "printed", "classification_status": "complete",
        "predicted_route": "arabic_printed",
    }]

    records, _ = runner._run_live_ocr(
        classified, [document], {}, SimpleNamespace(arabic_router_model="m", arabic_gemini_printed_model="m"),
        "config", "git", tmp_path, "hybrid",
    )

    assert records[0]["ocr_status"] == "complete"
    assert records[0]["structure_scores"]["block_retention"] == 1.0
    assert records[0]["structure_scores"]["by_type"]["heading"]["matched"] == 1
