# The evidence behind every AI answer

> **What this is:** the change that made every AI answer — margin notes, book
> chat, the desk — show which of its sentences the paper actually supports,
> quote the passage beside each one, and mark the sentences the paper does
> *not* support instead of letting them pass as grounded.
>
> **How to read it:** §1 what was already there and the three gaps → §2 the
> judge: how claims are split, what it sees, what it may say → §3 the three
> integrations and the UI → §4 what was verified, including the test that
> proves it catches a fabrication → §5 what this deliberately does not do.
>
> **Companions (detail):**
> [chat-and-ask.md](../02-architecture/chat-and-ask.md) (`## The evidence check`) ·
> [frontend.md](../02-architecture/frontend.md) (`### The evidence panel`) ·
> [api.md](../03-reference/api.md) (`GroundingReport`, the `grounding` event) ·
> [database-schema.md](../03-reference/database-schema.md) (`paper_notes.grounding`,
> `conversation_turns.grounding`).
>
> **Status:** shipped · **Reflects code as of:** 2026-09-10

---

## 1. Checking, and showing — the half that was missing

The app already did more here than it first appears. Every answer surface tells
the model to ground each claim and to say "not present in the retrieved
sections" rather than invent; answers carry citation markers that render as
chips with page numbers; and the agent persists a trail of every fetch it made
("an agent that silently disappears… is indistinguishable from one that
hallucinated" — `chat/paper_agent.py`). All of that stays. What was missing:

1. **Nothing verified a citation.** The model self-reports `[[11]]`; a
   plausible wrong citation passed untouched. That is the fabrication that
   matters most, because it *looks* grounded.
2. **Uncited sentences were invisible.** A sentence with no marker rendered
   identically to a cited one — yet that is exactly the model's own
   inference, the thing a researcher most needs to see flagged.
3. **Verifying meant jumping away.** Chips navigate; the evidence was never
   shown beside the claim (the desk's `CitationRef` expands a block inline;
   notes and book chat did not).

Decided with Khaled: **automatic**, after every answer, on **all three
surfaces** now. One extra model call per answer; `GROUNDING_CHECK=false` turns
it off.

**Flag, never rewrite.** The judge annotates; it never edits the answer or
hides anything. "Admit instead of make up" is delivered by *showing* which
claims rest on the paper and which don't — a silently corrected answer would be
its own kind of fabrication. And the judge is itself a model: the panel says so
in its footer, and a judge failure degrades to "couldn't verify", never to a
false green tick.

## 2. One judge, three surfaces (`backend/app/chat/grounding.py`)

```text
   answer ──split_claims──▶ claims, each with the refs cited *in it*
   cited blocks + blocks the agent READ ──build_evidence──▶ pool (cited first, 6k-char budget)
   claims + pool ──one llm.client.chat, temperature 0──▶ JSON array, one verdict per claim
                                                          ──▶ GroundingReport
```

**Claims.** Sentence- and bullet-level split of the Markdown answer. Each claim
carries the refs found *in that sentence*, parsed from whichever marker the
surface uses and normalised to `(document_id | None, seq)`: `[[11]]` and
`[[30], [31]]` (notes), `[seq:12, seq:94]` (book chat), `[[P2:41]]` (desk —
`P<n>` resolved through the study's paper list, the same mapping
`study_agent.cited_refs` does). Markers, bold and code are stripped from the
text the judge sees — it judges the sentence, not the notation. ⚠ An
underscore is emphasis at a word's edge and a subscript inside LaTeX
(`d_{model}`); only the former is stripped, or the judge would be shown a
formula the reader never saw. Headings and lead-ins ("…as follows:") are not
claims; the first live run counted them and reported "9 of 9 verified" with
four rows nobody could check.

**Evidence.** The cited blocks first, then everything the agent's SECTION/READ
steps fetched (`agent_steps[].seqs`, already persisted on every surface),
under a budget so the judge prompt is bounded. Each block is keyed the way the
claims cite it — `41`, or `P2:41` on the desk — so the judge can line them up
by eye. ⚠ Never the raw document uuid: the first prompt printed
`[<uuid>:41]` on the claim and `[41]` on the passage and the judge could not
match them.

**Verdicts.** `supported` · `partial` (an over-reach: the passage says X, the
claim says X and Y) · `unsupported` (the cited passage does not say this, or
says otherwise) · `uncited` (no marker, and nothing in the pool supports it —
the model's own inference or outside knowledge). Two rules make this fair
rather than pedantic:

- An uncited claim is checked against the **whole pool first**. A sentence the
  model forgot to mark but that *is* in the paper comes back `supported` with
  the block it was found in. The point is to flag inference, not punctuation.
- An **admission** ("this is not present in the retrieved sections") makes no
  claim about the document; it is `supported` with note `admission`. The model
  is never penalised for doing the right thing. This is why an empty evidence
  pool still goes to the judge instead of short-circuiting to `uncited` — the
  first cut did short-circuit, and flagged a pure refusal as two unsupported
  claims.

**Strictness.** `_parse_verdicts` tolerates a code fence or prose *around* the
array and nothing inside it: the array must have exactly one object per claim
with a verdict from the fixed vocabulary, or the whole report is
`status: unavailable`. A partial array applied to the first *n* claims would be
a report that looks complete and is not. `verify()` never raises.

**Pointers.** Each verdict names the passage it judged against and quotes a
verbatim excerpt (≤ 240 chars), which is what the UI shows beside the claim. ⚠
The judge is asked for one key and returns "32, 82" for a claim resting on two
passages often enough that an exact lookup dropped the pointer on exactly the
well-supported claims; `_locate` takes the first key it named that is in the
pool, and falls back to the claim's own first citation.

## 3. After `done`, in its own session; one panel, three call sites

Each surface yields its existing `done`, then runs the check in a fresh
`async_session_factory()` session, persists (`paper_notes.grounding` for
notes, `conversation_turns.grounding` for book chat and the desk — both are
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `critical_alters`), and yields
`{"type": "grounding", …}`. The answer is complete and committed before the
judge starts, so a judge failure can never lose one, and the SSE clients
already read until the stream closes. `/ask` (JSON) runs it inline and returns
`turn_id` + `grounding`; the orchestrator's `done` gained `turn_id` so the
stream endpoint can persist against the right row.

Client side (`views/EvidencePanel.tsx`): a pure `EvidenceList` plus a thin
stateful wrapper. Closed, one line — "6 of 7 claims verified · 1 not from the
paper" — warmed to `--accent` when anything is flagged. Open, the claim list
with ✓ / ◐ / ⚠ / ○ marks, the quoted passage inline, and a jump (`onJump(seq)`
in notes; `onOpenPaper(doc, seq)` labelled `P2 · p. 8` on the desk; label only
in the book chat, which has no reader beside it). "Verifying evidence…" between
`done` and `grounding` (`onVerifying` on all three stream readers); "Couldn't
verify this answer" on `unavailable`; nothing at all for a row from before the
feature. Wired under the trail in `NoteCard`, `ChatPane`, `StudyChat`, outside
the `Collapsible` for the same reason the trail is.

## 4. Verification

**The judge against a real model, with a real fabrication.** On the throwaway
stack with the live *Attention Is All You Need* rows copied in, a real note
answer had one fabricated sentence spliced in with a real-looking citation and
one true-but-uncited sentence added:

| Claim | Expected | gemma4:31b said |
| --- | --- | --- |
| "Training took **2 million GPUs for three years** [[94]]" (fabricated) | `unsupported` | `unsupported`, quote: *"Training took 3.5 days on 8 P100 GPUs."* |
| A true sentence with no marker | `supported` | `supported`, with the block it was found in |
| "This design inspired GPT-4 and Gemini" | `uncited` | `uncited` |
| Two genuinely cited sentences | `supported` | `supported` |

5 of 5. That is the test that proves the feature does what it claims.

**All three endpoints over real HTTP** (throwaway API, `app/` mounted):
`grounding` arrives after `done` on `/notes/stream`, `/ask/stream` and
`/studies/{id}/chat/stream`; `/ask` returns `turn_id` + `grounding` inline;
the rows are updated and `GET …/notes`, `GET …/chat`, `GET /studies/{id}/chat`
return them; with `GROUNDING_CHECK=false` the stream ends at `done`, no judge
call is made and the row stays `NULL`. A book-chat answer with an admission
section ("Carbon footprint: not present in the retrieved sections") came back
`supported · admission`; a desk answer's `[[P1:94]]` resolved to the real
document id and page 8.

**Pure functions under pytest** (`tests/test_grounding.py`, 18 tests): the
split on a real note shape (bullets, bold, grouped markers, a marker-less
sentence), all three marker forms, the desk's P-index mapping and out-of-study
drop, heading/lead-in skipping, LaTeX underscore survival, evidence keys ==
prompt cites, budget order, strict verdict parsing (wrong count / bad word /
prose / empty → `None`), `unavailable` never carrying a summary, `_locate`'s
fallbacks. Full suite: 574 passed.

**The panel, every state, `renderToString`:** summary counts, closed/open,
flagged colouring, verifying, no-report, all four verdict marks with quotes,
jump-as-button vs label, unavailable; then the real `NoteCardView`,
`PendingNoteCard` and `StudyChat` rendered with the real report from the HTTP
run. `tsc` and `vite build` clean. The live library was never written to.

## 5. Not done, on purpose

- **Rewriting or hiding unsupported claims** (§1 — flag only).
- **Verifying the pre-computed section summaries** (`section_summaries`): built
  at ingestion, not per answer; a separate pass if wanted.
- **Personal notes**: the reader's own text has nothing to verify.
- **A second opinion from a different model**: the check runs on the model that
  answered because it is loaded and known to work; a stronger judge is a
  one-line change (`model=` in `verify`) once one is worth its latency.
