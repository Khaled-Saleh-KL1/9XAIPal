"""The evidence check behind an AI answer.

After an answer has been produced (and, on the streaming surfaces, already
delivered), this splits it into claims and asks a model — with the actual
passages in front of it — which claims the cited text supports, which it
only partly supports, which it does not, and which were never cited and are
not in the paper at all. The result is attached to the note/turn and shown
beside the answer. It is the half of trust the prompts alone cannot deliver:
a prompt can tell the model to ground every claim; only a check can tell the
reader whether it did.

⚠ **Flag, never rewrite.** Nothing here touches the answer text. A silently
"corrected" answer would be its own kind of fabrication, and it would hide
from the reader exactly what they need to see — that the model reached past
the paper. Verdicts are shown next to claims; the claims stay as written.

⚠ **The judge is a model too.** Every failure — provider down, unparseable
output, a timeout — degrades to ``status: "unavailable"``, which the UI
renders as "couldn't verify". It must never degrade to a green tick: a false
"verified" is worse than no check at all, because it would be trusted.

⚠ **An uncited claim is checked against the whole evidence pool first.** The
model routinely states something the paper says and forgets the marker. That
is a missing citation, not an invention, and reporting it as "not from the
paper" would train the reader to ignore the flag. So the judge is handed
every block the agent read, not only the cited ones, and an uncited claim it
finds there comes back ``supported`` with the block it was found in. Only a
claim found *nowhere* in the pool is ``uncited``.

One module, three surfaces: the citation markers differ (``[[11]]`` in a
margin note, ``[seq:12]`` in book chat, ``[[P2:41]]`` on the desk), so
``split_claims`` normalises all three to ``(document_id, sequence_id)`` and
everything downstream is shared.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.logging import get_logger
from app.llm import client as llm_client

logger = get_logger(__name__)

VERDICTS = ("supported", "partial", "unsupported", "uncited")

# Evidence handed to the judge is bounded so the prompt is too: the cited
# blocks always fit; blocks the agent merely read fill what is left.
EVIDENCE_CHAR_BUDGET = 6000


@dataclass
class Claim:
    text: str
    # (document_id, sequence_id) pairs the claim cites. document_id is None
    # on the single-paper surfaces (notes, book chat) and filled in by the
    # caller; the desk's [[P2:41]] resolves to a real id per paper.
    refs: list[tuple[Optional[str], int]] = field(default_factory=list)


# ── Claim splitting ───────────────────────────────────────────────────────

# `[[11]]`, `[[30], [31]]`, `[[11, 12]]` — a margin note's markers.
_NOTE_MARKER_RE = re.compile(r"\[\[([0-9,;\s\[\]]+?)\]\]")
# `[seq:12]`, `[seq:12, seq:94]` — book chat's markers.
_SEQ_MARKER_RE = re.compile(r"\[(seq:\d+(?:\s*,\s*seq:\d+)*)\]")
# `[[P2:41]]` — the desk's markers, one paper index + block per marker.
_DESK_MARKER_RE = re.compile(r"\[\[P(\d+):(\d+)\]\]")
# For stripping: the same shapes the three parsers accept — `[[30], [31]]`
# has a `]` INSIDE it, so a naive `[^\]]*` stops early and leaves it in the
# text the reader sees.
_ANY_MARKER_RE = re.compile(r"\[\[[0-9P:,;\s\[\]]+?\]\]|\[seq:[^\]]*\]")
# Emphasis/code markers: harmless to the judge, ugly in the evidence panel.
# Emphasis and code markers only. ⚠ An underscore is emphasis at a word's
# edge ("_this_"), and a subscript inside LaTeX ("d_{model}", "d_k"); the
# judge must see the formula the reader sees, so only the former is stripped.
_INLINE_MD_RE = re.compile(r"\*+|`+|(?<!\w)_+(?=\w)|(?<=\w)_+(?=[\s.,;:!?)\]]|$)")

# A claim boundary: a sentence end, or a new line (a bullet or paragraph).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[*])|\n+")
_MD_NOISE_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+|>\s*)")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


def _refs_in(text: str, paper_index: Optional[dict[int, str]]) -> list[tuple[Optional[str], int]]:
    refs: list[tuple[Optional[str], int]] = []
    if paper_index is not None:
        for p, seq in _DESK_MARKER_RE.findall(text):
            doc = paper_index.get(int(p))
            if doc is not None:
                refs.append((doc, int(seq)))
        return refs
    for blob in _NOTE_MARKER_RE.finditer(text):
        for n in re.findall(r"\d+", blob.group(1)):
            refs.append((None, int(n)))
    for blob in _SEQ_MARKER_RE.finditer(text):
        for n in re.findall(r"\d+", blob.group(1)):
            refs.append((None, int(n)))
    # Preserve order, drop duplicates.
    seen: set = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def split_claims(answer: str, paper_index: Optional[dict[int, str]] = None) -> list[Claim]:
    """Sentence/bullet-level claims, each with the refs cited *in it*.

    ``paper_index`` (``{2: "<document uuid>"}``) switches marker parsing to
    the desk's ``[[P2:41]]`` form; None means the single-paper forms.
    Markdown list/heading prefixes and the markers themselves are stripped
    from the claim text the judge sees — it should judge the sentence, not
    the notation.
    """
    claims: list[Claim] = []
    for raw in _SENTENCE_SPLIT_RE.split(answer or ""):
        # A heading is structure, not a statement: "### Carbon Footprint"
        # asserts nothing, and counting it inflates "9 of 9 claims verified"
        # with rows a reader cannot check.
        if _HEADING_RE.match(raw):
            continue
        piece = _MD_NOISE_RE.sub("", raw).strip()
        if not piece:
            continue
        refs = _refs_in(piece, paper_index)
        clean = _INLINE_MD_RE.sub("", _ANY_MARKER_RE.sub("", piece))
        clean = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", clean)).strip()
        # A fragment that was only a marker, or a lone heading word, is not a
        # claim anyone needs judged. Nor is a lead-in ("The results were as
        # follows:") — the claims are the lines it introduces.
        if len(clean) < 12 or clean.endswith(":"):
            continue
        claims.append(Claim(text=clean, refs=refs))
    return claims


# ── Evidence pool ─────────────────────────────────────────────────────────

def build_evidence(
    blocks_by_doc: dict[str, list[dict]],
    cited: set[tuple[str, int]],
    doc_labels: Optional[dict[str, str]] = None,
) -> list[dict]:
    """The numbered passages the judge sees: ``[{key, document_id,
    sequence_id, page, text}]``. Cited blocks first and always; the blocks the
    agent merely read fill the remaining budget. ``doc_labels`` (desk only)
    names each paper — "P2" — so a verdict can point at the right one."""
    cited_rows, read_rows = [], []
    for doc_id, rows in blocks_by_doc.items():
        for r in rows:
            (cited_rows if (doc_id, int(r["sequence_id"])) in cited else read_rows).append((doc_id, r))

    pool: list[dict] = []
    used = 0
    for doc_id, r in cited_rows + read_rows:
        txt = (r.get("plain_text") or "").strip()
        if not txt:
            continue
        if used + len(txt) > EVIDENCE_CHAR_BUDGET and (doc_id, int(r["sequence_id"])) not in cited:
            continue
        label = (doc_labels or {}).get(doc_id)
        key = f"{label}:{r['sequence_id']}" if label else f"{r['sequence_id']}"
        pool.append({
            "key": key,
            "document_id": doc_id,
            "sequence_id": int(r["sequence_id"]),
            "page": r.get("page_start"),
            "text": txt[:1500],
        })
        used += len(txt)
    return pool


# ── The judge ─────────────────────────────────────────────────────────────

_SYSTEM = """You are a meticulous fact-checker. You are given CLAIMS from an AI's answer about a document, and the PASSAGES from that document the answer was based on. For each claim, decide strictly from the passages:

- "supported": a passage states this (paraphrase is fine; the meaning must be there).
- "partial": a passage supports part of it, but the claim adds, overstates, or generalises beyond what is written.
- "unsupported": the passages the claim cites do not say this, or say otherwise.
- "uncited": the claim cites nothing AND no passage in the pool supports it (the AI's own inference or outside knowledge).

Rules:
- A claim that cites nothing but IS stated in some passage is "supported" — name that passage.
- A sentence that admits a limit ("the paper does not say…", "this is not covered in the retrieved sections") makes no claim about the document's content. Mark it "supported" with the note "admission" — do not mark an honest admission as uncited.
- Judge meaning, not wording. Do not penalise summarising.
- "quote" must be a short verbatim excerpt (under 200 characters) copied from the passage you judged against, or "" if none applies.
- Output ONLY a JSON array, one object per claim, in order, no prose:
[{"claim": 1, "verdict": "supported", "passage": "<passage key or empty>", "quote": "<verbatim excerpt or empty>", "note": "<one short clause, optional>"}]"""


def _judge_prompt(claims: list[Claim], evidence: list[dict]) -> str:
    # A claim's citation is printed with the SAME key its passage is labelled
    # with, so the judge can line them up by eye: "[41]" on the single-paper
    # surfaces, "[P2:41]" on the desk. Never the raw document uuid.
    key_for = {(e["document_id"], e["sequence_id"]): e["key"] for e in evidence}
    lines = ["PASSAGES:"]
    if not evidence:
        lines.append("(no passages were retrieved for this answer)")
    for e in evidence:
        page = f" (p. {e['page']})" if e.get("page") else ""
        lines.append(f"[{e['key']}]{page} {e['text']}")
    lines.append("\nCLAIMS:")
    for i, c in enumerate(claims, 1):
        cites = ", ".join(f"[{key_for.get((d, s), str(s))}]" for d, s in c.refs) if c.refs else "(no citation)"
        lines.append(f"{i}. {c.text}  — cites: {cites}")
    return "\n".join(lines)


def _parse_verdicts(raw: str, n: int) -> Optional[list[dict]]:
    """The judge's JSON array, or None if it is not one we can trust. Tolerates
    a code fence or prose around the array; tolerates nothing inside it."""
    m = re.search(r"\[\s*\{.*\}\s*\]", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list) or len(arr) != n:
        return None
    out = []
    for i, item in enumerate(arr):
        if not isinstance(item, dict):
            return None
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in VERDICTS:
            return None
        out.append({
            "verdict": verdict,
            "passage": str(item.get("passage") or "").strip(),
            "quote": str(item.get("quote") or "").strip()[:240],
            "note": str(item.get("note") or "").strip()[:200],
        })
    return out


def unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason, "claims": [], "summary": {}}


async def verify(claims: list[Claim], evidence: list[dict], *, model: Optional[str] = None) -> dict:
    """One judge call for the whole answer → the report the UI renders.

    ``model`` is the model that wrote the answer, when known, so the check
    runs on something already loaded and known to work here. Never raises.
    """
    if not claims:
        return {"status": "verified", "claims": [], "summary": {v: 0 for v in VERDICTS}}
    # ⚠ An empty pool still goes to the judge. Nothing can be "supported"
    # then, but an answer with no passages is usually an admission ("this is
    # not in the retrieved sections"), and the judge is what tells an
    # admission apart from a claim. Short-circuiting here to "uncited" would
    # flag the model for doing exactly the right thing.
    try:
        result = await llm_client.chat(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _judge_prompt(claims, evidence)}],
            model=model,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning("grounding judge call failed: %s", e)
        return unavailable("judge model unavailable")

    verdicts = _parse_verdicts(result.get("content") or "", len(claims))
    if verdicts is None:
        logger.warning("grounding judge returned unparseable output")
        return unavailable("judge output could not be read")

    by_key = {e["key"]: e for e in evidence}
    rows = []
    for c, v in zip(claims, verdicts):
        ev = _locate(v["passage"], c, evidence, by_key)
        pointer = {"document_id": ev["document_id"], "sequence_id": ev["sequence_id"], "page": ev.get("page")} if ev else None
        rows.append({
            "text": c.text,
            "refs": [list(r) for r in c.refs],
            "verdict": v["verdict"],
            "evidence": pointer,
            "quote": v["quote"],
            "note": v["note"],
        })
    return {"status": "verified", "model": result.get("model"), "claims": rows, "summary": _summary(rows)}


_KEY_RE = re.compile(r"P\d+:\d+|\d+")


def _locate(passage: str, claim: Claim, evidence: list[dict], by_key: dict) -> Optional[dict]:
    """The passage a verdict points at.

    The judge is asked for one key, but a claim resting on two passages comes
    back as "32, 82" often enough that an exact lookup would drop the pointer
    on exactly the well-supported claims. Take the first key it named that is
    in the pool; failing that, fall back to the claim's own first citation, so
    the reader can still jump to what the answer claimed to rely on.
    """
    for key in _KEY_RE.findall(passage or ""):
        if key in by_key:
            return by_key[key]
    for doc, seq in claim.refs:
        for e in evidence:
            if e["sequence_id"] == seq and (doc is None or e["document_id"] == doc):
                return e
    return None


def _summary(rows: list[dict]) -> dict:
    s = {v: 0 for v in VERDICTS}
    for r in rows:
        s[r["verdict"]] = s.get(r["verdict"], 0) + 1
    return s


def enabled() -> bool:
    return bool(settings.grounding_check)


# ── Entry points, one per answer shape ────────────────────────────────────

def _read_seqs(agent_steps: Optional[list[dict]]) -> set[int]:
    """Every block the agent's SEARCH/SECTION/READ calls pulled in — the
    'merely read' half of the evidence pool."""
    seqs: set[int] = set()
    for step in agent_steps or []:
        for s in step.get("seqs") or []:
            try:
                seqs.add(int(s))
            except (TypeError, ValueError):
                continue
    return seqs


async def check_document_answer(
    session,
    *,
    document_id: UUID,
    answer: str,
    agent_steps: Optional[list[dict]] = None,
    model: Optional[str] = None,
) -> dict:
    """A margin note or a book-chat turn: one document, markers ``[[n]]`` or
    ``[seq:n]``. Fetches the pool, judges, returns the report. Never raises."""
    from app.database.repositories import chunks as chunk_repo

    try:
        claims = split_claims(answer)
        doc = str(document_id)
        for c in claims:
            c.refs = [(doc, seq) for _, seq in c.refs]
        cited = {(doc, seq) for c in claims for _, seq in c.refs}
        wanted = {seq for _, seq in cited} | _read_seqs(agent_steps)
        rows = await chunk_repo.get_chunks_by_sequence_ids(session, document_id, sorted(wanted))
        evidence = build_evidence({doc: rows}, cited)
        return await verify(claims, evidence, model=model)
    except Exception as e:
        logger.warning("grounding check failed before judging: %s", e)
        return unavailable("evidence could not be gathered")


async def check_desk_answer(
    session,
    *,
    papers: list[dict],
    answer: str,
    agent_steps: Optional[list[dict]] = None,
    model: Optional[str] = None,
) -> dict:
    """A desk answer: several papers, markers ``[[P2:41]]`` where P2 is the
    1-based position in ``papers`` (the same mapping study_agent.cited_refs
    uses). Blocks are fetched per paper; the pool labels each with its P-number
    so a verdict can say which paper it was checked against."""
    from app.database.repositories import chunks as chunk_repo

    try:
        index = {i + 1: str(p["id"]) for i, p in enumerate(papers)}
        labels = {str(p["id"]): f"P{i + 1}" for i, p in enumerate(papers)}
        claims = split_claims(answer, paper_index=index)
        cited = {(d, seq) for c in claims for d, seq in c.refs if d}
        # Steps on the desk carry seqs without a paper id; they are only
        # useful as evidence when the study holds a single paper.
        read = _read_seqs(agent_steps) if len(papers) == 1 else set()
        by_doc: dict[str, list[dict]] = {}
        for doc_id in {d for d, _ in cited} | ({str(papers[0]["id"])} if read else set()):
            wanted = {seq for d, seq in cited if d == doc_id} | (read if len(papers) == 1 else set())
            by_doc[doc_id] = await chunk_repo.get_chunks_by_sequence_ids(session, UUID(doc_id), sorted(wanted))
        evidence = build_evidence(by_doc, cited, doc_labels=labels)
        return await verify(claims, evidence, model=model)
    except Exception as e:
        logger.warning("desk grounding check failed before judging: %s", e)
        return unavailable("evidence could not be gathered")
