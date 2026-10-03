import subprocess
import sys
import importlib

from _queue_test_helpers import python_subprocess_options


def _eval_api():
    module = importlib.import_module("scripts.eval_retrieval")
    for name in ("normalize_expected_text", "score_question", "aggregate_scores"):
        assert callable(getattr(module, name, None)), f"missing evaluation function: {name}"
    return module


def test_eval_cli_advertises_cases_settings_and_json_output():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.eval_retrieval", "--help"],
        **python_subprocess_options(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--set" in result.stdout
    assert "--json" in result.stdout


def test_expected_text_normalization_folds_arabic_and_collapses_space():
    normalize = getattr(_eval_api(), "normalize_expected_text")

    assert normalize("ﻻ أ إ آ ٱ ى ة َـ\tX  \n Y") == "لا ا ا ا ا ي ه X Y"


def test_question_score_uses_first_matching_rank_and_any_expectation():
    score_question = getattr(_eval_api(), "score_question")
    rows = [
        {"plain_text": "nothing here"},
        {"plain_text": "A paragraph with the needle phrase."},
        {"plain_text": "also has the other phrase"},
    ]

    score = score_question(rows, ["needle phrase", "other phrase"])

    assert score == {
        "rank": 2,
        "matched_expect": "needle phrase",
        "hit_at_1": False,
        "hit_at_3": True,
        "hit_at_5": True,
        "hit_at_10": True,
        "reciprocal_rank": 0.5,
    }
    assert score_question(rows[:1], ["needle phrase"])["reciprocal_rank"] == 0.0


def test_aggregate_score_averages_hits_and_reciprocal_rank():
    aggregate_scores = getattr(_eval_api(), "aggregate_scores")

    assert aggregate_scores([
        {"hit_at_1": True, "hit_at_3": True, "hit_at_5": True, "hit_at_10": True, "reciprocal_rank": 1.0},
        {"hit_at_1": False, "hit_at_3": True, "hit_at_5": True, "hit_at_10": True, "reciprocal_rank": 0.5},
        {"hit_at_1": False, "hit_at_3": False, "hit_at_5": False, "hit_at_10": False, "reciprocal_rank": 0.0},
    ]) == {
        "hit_at_1": 1 / 3,
        "hit_at_3": 2 / 3,
        "hit_at_5": 2 / 3,
        "hit_at_10": 2 / 3,
        "mrr_at_10": 0.5,
    }


def test_setting_overrides_parse_values_and_reject_unknown_names(monkeypatch):
    from app.core.config import settings
    from scripts import eval_retrieval

    monkeypatch.setattr(settings, "debug", True)
    eval_retrieval._apply_setting_overrides(["debug=false"])

    assert settings.debug is False
    try:
        eval_retrieval._apply_setting_overrides(["not_a_setting=true"])
    except ValueError as exc:
        assert "unknown setting" in str(exc)
    else:
        raise AssertionError("unknown settings must be rejected")
