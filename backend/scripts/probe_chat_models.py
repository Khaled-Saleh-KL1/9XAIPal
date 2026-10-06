#!/usr/bin/env python3
"""Probe reader models through the paper reader's direct and agent paths.

Postgres access, memory writes, and paper tools are replaced with in-memory
stubs. Tracing and model-availability writes are disabled. Provider requests
still pass through the application's shared rate limiter, and the default
catalog lookup reads the configured Ollama tags endpoint. No reader data is
written; the rate limiter may update its short-lived Redis counter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from typing import Any, AsyncIterator
from uuid import uuid4


_MODEL_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SECRET_PREFIX_RE = re.compile(r"^(?:sk-|nvapi-|hf[_-]|ollama[_-]|bearer\s)", re.IGNORECASE)
_SECRET_SHAPE_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "error: invalid arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="Send short synthetic reader questions to configured models.",
        epilog="Without --models, every model in the live catalog is probed.",
    )
    parser.add_argument(
        "--models",
        metavar="MODELS",
        help="comma-separated model names; defaults to every model in the catalog",
    )
    parser.add_argument(
        "--mode",
        metavar="{simple,agent,both}",
        default="both",
        help="reader path to probe (default: both)",
    )
    return parser


def _safe_model_label(value: str | None) -> str:
    """Keep model attribution printable without exposing token-shaped input."""
    value = value or "unknown"
    secret_shaped = (
        _SECRET_PREFIX_RE.match(value)
        or (_SECRET_SHAPE_RE.fullmatch(value) and not any(ch in value for ch in "/:."))
    )
    if secret_shaped or not _MODEL_LABEL_RE.fullmatch(value):
        return "[redacted]"
    return value


def _emit(
    requested_model: str,
    mode: str,
    *,
    error_class: str | None = None,
    answered_model: str | None = None,
) -> None:
    status = "ERROR" if error_class else "OK"
    row = {
        "model": _safe_model_label(requested_model),
        "mode": mode,
        "status": status,
        "error_class": error_class,
        "answered_model": _safe_model_label(answered_model) if answered_model else None,
    }
    print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))


async def _synthetic_chunks(*_args: Any, **_kwargs: Any) -> list[dict]:
    return [
        {
            "id": uuid4(),
            "sequence_id": 1,
            "chunk_type": "heading",
            "plain_text": "Synthetic paper",
            "markdown": "Synthetic paper",
            "heading_path": ["1"],
            "token_count": 2,
            "page_start": 1,
            "page_end": 1,
        },
        {
            "id": uuid4(),
            "sequence_id": 2,
            "chunk_type": "text",
            "plain_text": "The synthetic method improves retrieval accuracy on a fixed benchmark.",
            "markdown": "The synthetic method improves retrieval accuracy on a fixed benchmark.",
            "heading_path": ["1"],
            "token_count": 12,
            "page_start": 1,
            "page_end": 1,
        },
    ]


async def _no_memories(*_args: Any, **_kwargs: Any) -> list[dict]:
    return []


async def _no_memory(*_args: Any, **_kwargs: Any) -> None:
    return None


async def _no_remembered(*_args: Any, **_kwargs: Any) -> AsyncIterator[dict]:
    if False:
        yield {}


async def _synthetic_tool_call(
    _session: Any,
    _document_id: Any,
    _chunks: list[dict],
    call: dict,
    **_kwargs: Any,
) -> dict:
    return {
        **call,
        "label": f"Synthetic {call['tool'].lower()} lookup",
        "result": "synthetic data",
        "observation": (
            "Synthetic paper block 2: the method improves retrieval accuracy "
            "on a fixed benchmark."
        ),
        "seqs": [2],
        "sources": [],
    }


async def _ignore_model_result(*_args: Any, **_kwargs: Any) -> None:
    return None


async def _probe_one(paper_agent: Any, settings: Any, model: str, mode: str) -> None:
    old_whole_context = settings.paper_whole_document_context
    settings.paper_whole_document_context = mode == "simple"
    document_id, user_id = uuid4(), uuid4()
    answer = ""
    answered_model = None
    try:
        async for event in paper_agent.answer_paper_question(
            None,
            document_id=document_id,
            user_id=user_id,
            question="In one sentence, what does this passage claim?",
            anchor={
                "kind": "block",
                "sequence_id": 2,
                "quote": "The synthetic method improves retrieval accuracy on a fixed benchmark.",
            },
            model=model,
            max_steps=1,
            allow_web=False,
        ):
            if event.get("type") == "token":
                answer += event.get("text") or ""
            elif event.get("type") == "done":
                answer = event.get("answer") or answer
                answered_model = event.get("model") or None

        if not answer.strip():
            _emit(model, mode, error_class="EmptyAnswer", answered_model=answered_model)
        else:
            _emit(model, mode, answered_model=answered_model)
    except Exception as error:
        # Provider exception messages may include response bodies. Report only
        # the class and never print exception text, headers, or settings.
        _emit(model, mode, error_class=type(error).__name__, answered_model=answered_model)
    finally:
        settings.paper_whole_document_context = old_whole_context


async def _run(args: argparse.Namespace) -> int:
    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        # Delay application imports until after argparse handles --help. This
        # keeps help usable without loading settings or providers.
        from app.chat import paper_agent
        from app.core.config import settings
        from app.llm import client as llm_client

        settings.trace_enabled = False
        paper_agent.chunk_repo.get_all_document_chunks = _synthetic_chunks
        paper_agent.recall_memories = _no_memories
        paper_agent.write_memory = _no_memory
        paper_agent.write_remembered = _no_remembered
        paper_agent._run_call = _synthetic_tool_call
        paper_agent.web_search.is_configured = lambda: False
        llm_client.availability.record_model_result = _ignore_model_result

        if args.models is not None:
            models = list(dict.fromkeys(part.strip() for part in args.models.split(",") if part.strip()))
        else:
            from app.llm.catalog import list_chat_models

            catalog = await list_chat_models()
            models = list(dict.fromkeys(item["name"] for item in catalog.get("models", [])))

        if not models:
            _emit("catalog", "catalog", error_class="NoModelsInCatalog")
            return 1

        modes = ("simple", "agent") if args.mode == "both" else (args.mode,)
        for model in models:
            for mode in modes:
                await _probe_one(paper_agent, settings, model, mode)
    finally:
        logging.disable(previous_log_disable)
    return 0


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.mode not in {"simple", "agent", "both"}:
        parser.error("mode must be simple, agent, or both")
    try:
        return asyncio.run(_run(args))
    except Exception as error:
        # Catalog or initialization errors stay safe to show in server logs.
        _emit("catalog", "catalog", error_class=type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
