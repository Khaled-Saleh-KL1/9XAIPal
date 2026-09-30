"""Evaluation harness for retrieval relevance sets."""

import argparse
import asyncio
import json
import re
import unicodedata
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.core.config import settings


_ARABIC_MARKS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_EXPECTED_FOLDS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي",
})
_CUTOFFS = (1, 3, 5, 10)


def normalize_expected_text(value: str) -> str:
    """Apply the documented Arabic substring normalization to text and cases."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _ARABIC_MARKS.sub("", normalized.translate(_EXPECTED_FOLDS))
    return " ".join(normalized.split())


def score_question(rows: list[dict], expect: list[str]) -> dict:
    """Score one ranked result list; a result hits when any phrase occurs."""
    expected = [
        (original, normalize_expected_text(original))
        for original in expect
        if normalize_expected_text(original)
    ]
    rank = None
    matched_expect = None
    for position, row in enumerate(rows[:10], start=1):
        body = normalize_expected_text(str(row.get("plain_text") or ""))
        match = next((original for original, phrase in expected if phrase in body), None)
        if match is not None:
            rank = position
            matched_expect = match
            break
    return {
        "rank": rank,
        "matched_expect": matched_expect,
        **{f"hit_at_{cutoff}": rank is not None and rank <= cutoff for cutoff in _CUTOFFS},
        "reciprocal_rank": 1.0 / rank if rank is not None else 0.0,
    }


def aggregate_scores(question_scores: list[dict]) -> dict:
    """Average per-question hit rates and reciprocal rank at ten."""
    count = len(question_scores)
    denominator = count or 1
    return {
        **{
            f"hit_at_{cutoff}": sum(bool(row[f"hit_at_{cutoff}"]) for row in question_scores) / denominator
            for cutoff in _CUTOFFS
        },
        "mrr_at_10": sum(float(row["reciprocal_rank"]) for row in question_scores) / denominator,
    }


def _apply_setting_overrides(overrides: list[str]) -> None:
    fields = settings.__class__.model_fields
    for override in overrides:
        name, separator, raw_value = override.partition("=")
        if not separator or not name:
            raise ValueError(f"invalid --set value {override!r}; expected NAME=VALUE")
        field = fields.get(name)
        if field is None:
            raise ValueError(f"unknown setting {name!r}")
        try:
            value = TypeAdapter(field.annotation).validate_python(raw_value)
        except ValidationError as exc:
            raise ValueError(f"invalid value for setting {name!r}: {exc}") from exc
        setattr(settings, name, value)


def _load_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("case file must contain a JSON array")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        for key in ("id", "question", "document_id", "expect"):
            if key not in case:
                raise ValueError(f"case {index} is missing {key!r}")
        if not isinstance(case["question"], str) or not isinstance(case["expect"], list):
            raise ValueError(f"case {index} must have a string question and an expect array")
        if any(not isinstance(value, str) for value in case["expect"]):
            raise ValueError(f"case {index} expect values must be strings")
        try:
            UUID(str(case["document_id"]))
        except ValueError as exc:
            raise ValueError(f"case {index} document_id must be a UUID") from exc
    return cases


async def _evaluate_cases(cases: list[dict]) -> dict:
    from app.database.connection import async_session_factory
    from app.services.retrieval import search_chunks

    scored = []
    async with async_session_factory() as session:
        for case in cases:
            rows = await search_chunks(
                session,
                case["question"],
                limit=10,
                document_id=UUID(str(case["document_id"])),
            )
            scored.append({
                "id": case["id"],
                "question": case["question"],
                **({"note": case["note"]} if case.get("note") is not None else {}),
                **score_question(rows, case["expect"]),
            })
    return {"questions": scored, "overall": aggregate_scores(scored)}


def _print_scores(report: dict) -> None:
    for row in report["questions"]:
        rank = str(row["rank"]) if row["rank"] is not None else "miss"
        print(
            f"{row['id']}: rank={rank} "
            + " ".join(f"hit@{k}={int(row[f'hit_at_{k}'])}" for k in _CUTOFFS)
            + f" reciprocal_rank={row['reciprocal_rank']:.4f}"
        )
    overall = report["overall"]
    print(
        "overall: "
        + " ".join(f"hit@{k}={overall[f'hit_at_{k}']:.4f}" for k in _CUTOFFS)
        + f" MRR@10={overall['mrr_at_10']:.4f}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", help="JSON file containing retrieval evaluation cases")
    parser.add_argument(
        "--set", action="append", default=[], metavar="NAME=VALUE",
        help="Override an application setting for this evaluation (repeatable)",
    )
    parser.add_argument("--json", dest="json_out", metavar="OUT", help="Write JSON scores to OUT")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _apply_setting_overrides(args.set)
        report = asyncio.run(_evaluate_cases(_load_cases(Path(args.cases))))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    _print_scores(report)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
