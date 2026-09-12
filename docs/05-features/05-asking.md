# Area 5 — Asking: the AI answering paths (features 66–75, plus 108)

> Part of the [feature catalogue](README.md). Companion architecture doc:
> [chat-and-ask.md](../02-architecture/chat-and-ask.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

Three answering paths share one tool layer ([`chat/agent_tools.py`](../../backend/app/chat/agent_tools.py)):

| | Paper agent | Study agent | Orchestrator |
| --- | --- | --- | --- |
| Serves | margin notes; **book chat streaming** | the desk | blocking `/ask`, sub-threads |
| Source | `chat/paper_agent.py` | `chat/study_agent.py` | `chat/orchestrator.py` |
| Retrieval | anchor + the paper's contents index | the study index (every paper's heading spine) | LOCAL / GLOBAL / OVERVIEW / EXTERNAL |
| Cites | `[[42]]` | `[[P2:42]]` | citation objects (`[seq:12]` in text) |
| Needs embeddings | no | no | yes, for GLOBAL |
| Persists to | `paper_notes` | `conversation_turns` (`study_id`) | `conversation_turns` + `ask_traces` |

---

## 66. The paper agent

**What it does.** Answers a question about one passage (or, from the book chat, about the book)
by *navigating* the document with tools rather than being handed the whole thing.

**Where.** [`chat/paper_agent.py`](../../backend/app/chat/paper_agent.py)
(`answer_paper_question`), `agent_tools.py`, `prompts.py`, settings `PAPER_AGENT_MAX_STEPS` (4),
`PAPER_AGENT_HOLISTIC_MAX_STEPS` (6), `PAPER_AGENT_READ_MAX_CHUNKS` (40),
`PAPER_AGENT_SEARCH_LIMIT` (8), `PAPER_AGENT_WEB_LIMIT` (4), `LOCAL_CONTEXT_WINDOW`,
`PAPER_WHOLE_DOCUMENT_CONTEXT` (off).

**How it works.** The model is handed exactly three things: what the reader pointed at, the blocks
around it, and the paper's **CONTENTS** — the heading spine, each entry carrying the block number
it starts at. Everything else it must fetch, in up to four rounds, with a text protocol inside a
`<tool>` block:

| Tool | Backed by |
| --- | --- |
| `SECTION: 31` | the contents entry at that block, expanded to its whole section (down to the next heading of the same or higher level); a non-heading block resolves to the section *containing* it, because models pass a SEARCH hit to SECTION to mean "the rest of whatever this was in" |
| `SEARCH: sliding window` | **hybrid**: pgvector similarity (when embeddings exist) fused with Postgres full-text, **plus a literal `ILIKE` substring leg** — `to_tsvector` drops single Greek letters, equation numbers and subscripts, so "why is τ so small" gets zero full-text hits on the one term that matters, and embeddings blur exactly those tokens too. Legs run sequentially (an `AsyncSession` is one connection; concurrent statements on it corrupt) |
| `READ: 40-52` | the markdown of that range, capped in SQL, clamped to the progress ceiling |
| `WEB: …` | the search cascade (feature 71); offered only when a provider is configured — a model told it can check the internet whose every check comes back empty stops trusting its own observations |
| `IMAGE: …` | a picture from the public web, when neither the document nor a drawing can show what something looks like |
| `THINK: …` | nothing — the model's reason for the round, shown to the reader |
| `REMEMBER: …` | a durable note about the *reader* (feature 108) |

`_plan()` orders calls cheap-first (SECTION, READ, then SEARCH, then WEB); each emits a `step`
event before (`running`) and after (`done`). `anchor.kind` changes the instruction, not just the
text: a figure says "the image is attached, look at it"; an equation and a table say **trust the
attached crop over the transcription** (MinerU's transcription loses merged cells, spanning headers
and footnote markers — the parts that decide what a number means). The prompt asks for `[[42]]`
markers on grounded claims; the parser matches a whole bracket blob (`[[16], [42]]`, `[[16, 42]]`)
because a strict single-number regex silently returned nothing for grouped citations and a
well-grounded note rendered with no chips. Web claims are attributed in prose, never with a
marker (`[[WEB]]` links to nothing). Two scopes: `anchor` (a passage, 4 rounds) and `document`
(a holistic question, the first `PAPER_AGENT_OPENING_BLOCKS` + contents, 6 rounds, never
whole-document stuffing even when the flag is on — handed forty pages, the model summarises what
it was given instead of deciding which sections the question turns on).

**Why the paper is not in the prompt.** Until 2026-08-18 a paper under `WHOLE_PAPER_MAX_TOKENS`
was stuffed whole. Stuffing forty pages behind a question about one sentence costs the entire
context window and dilutes the answer — the model drifts into summarising. What makes the omission
safe is the contents index, not the tools: a model that can see the *shape* of the document knows
what exists and can name the section it wants; one with neither has only guesses at the paper's
vocabulary. And the tools are a **text protocol, not provider tool-calling**: the LLM client fans
out to Ollama and five OpenAI-compatible clouds whose tool support differs; a fenced block every
model can emit works on all of them, including local models with no tool support. The cost is a
parser — a deliberate trade. The agent drops the router, guardrail and compaction: a note is
anchored, in-scope by definition, and one Q+A, so each omission removes a model call from the
critical path. `stream_answer` filters a `<tool` that appears mid-answer on the forced final turn
(observed on the first live desk question), withholding the last few characters until the next
token because the marker can straddle a token boundary.

---

## 67. Synthesised index for heading-less papers

**What it does.** A paper MinerU found no headings in still gets an index: every Nth block
(`PAPER_AGENT_MAP_STRIDE`, 12), labelled as a sample rather than a table of contents.

**Where.** `agent_tools.py::format_block_map`, `paper_agent.py::_format_block_map`.

**Why.** Without it such a paper loses the index *and* the document in one move — nothing to
browse and nothing in the prompt, leaving a wrong guess at the vocabulary as a dead end. The desk
does the same for a paper in a study, with a note that `SECTION` will not work on it — omitting it
would leave the model believing the study is smaller than it is.

---

## 68. The routed orchestrator: four context modes, router, guardrail, multimodal

**What it does.** The older answering path: a **router** picks one of four retrieval modes, the
context is built, a **guardrail** keeps `/ask` on topic, the page image rides along to a vision
model when relevant.

**Where.** [`chat/orchestrator.py`](../../backend/app/chat/orchestrator.py) (`handle_ask`,
`stream_ask`), [`router.py`](../../backend/app/chat/router.py),
[`guardrail.py`](../../backend/app/chat/guardrail.py), `local_context.py`, `global_context.py`,
`overview_context.py`, `external_context.py`, [`llm/multimodal.py`](../../backend/app/llm/multimodal.py).
Serves the blocking `POST /ask`, sub-threads, and any non-book caller; book **streaming** goes
to the paper agent (feature 63).

| Mode | When | Retrieves | The model sees |
| --- | --- | --- | --- |
| LOCAL | about what is on screen ("this figure", "above") | the current chunk ± 1 and its assets | the chunks + the image as base64 |
| GLOBAL | needs the whole paper | top-K by pgvector cosine (query embedded with the same resolver as ingestion, so vectors always match) | chunks with similarity scores |
| OVERVIEW | paper-level ("summarise", "tl;dr") | the hierarchical section summaries | a structured outline |
| EXTERNAL | outside the paper ("latest", "who is") | the web cascade, query first rewritten toward CS/ML so "transduction" does not return genetics | title/url/snippet rows |

**How it works.** The router is two-tier: cheap keyword lists first (`_LOCAL_KEYWORDS` fire only
with a `current_chunk_id`), then an LLM classifier for ambiguous queries returning one of the four
as JSON; no document → EXTERNAL; classifier failure → GLOBAL when a document exists; every decision
carries a `reason` stored in `ask_traces`. The guardrail (`is_topic_allowed`) restricts `/ask` to
IT-related questions with a classifier — but when reading a paper, paper-grounded prompts
("describe this figure") skip the classifier: the document is itself IT. Messages are shaped as
`system` + `user: "Context:\n<context>\n\n<prompt>"` with `images: [base64…]` only for LOCAL. If
the model signals `NEEDS_RESEARCH`, the research agent (feature 70) takes over. When the reader
asks for a figure, the "AVAILABLE PAPER FIGURES" block lists the paper's figure URLs so the model
can embed one inline (feature 72).

**Why it is still here.** It is the only path with a rolling transcript, sub-threads and
compaction; the agent paths deliberately have none of that.

---

## 69. Conversation continuity, sub-threads, and compaction

**What it does.** Chats persist and resume; any turn can spawn a sub-thread ("Thread →") up to
depth 3; long conversations are compacted so context never overflows.

**Where.** `conversation_turns` (`conversation_id`, `parent_turn_id`, `thread_root_turn_id`,
`role='compaction'`), [`repositories/conversations.py`](../../backend/app/database/repositories/conversations.py)
(`get_thread_subtree` — a recursive CTE, `compute_turn_depth`), `endpoints/ask.py`
(`MAX_SUB_THREAD_DEPTH = 3`), `orchestrator.py::maybe_compact_conversation`, `ChatPane.tsx`
(`threadStack`, Esc pops one level).

**How it works.** `conversation_id` is optional on the first turn (the server mints one); the
client passes it back. A sub-thread is a tree under a root user turn; the main view excludes
sub-thread turns (`parent_turn_id IS NULL`) so replies inside a tangent never leak into the linear
chat. Sub-threads default to paper-free context. Past a token threshold, earlier turns are
summarised into one `compaction` turn. Every scoped query is bound to `(user_id, document_id)` —
a conversation id reused from another user or paper 404s
([docs/issues/005](../issues/005-conversation-ids-are-not-bound-to-the-paper.md); ⚠ the first fix
used `(:document_id IS NULL OR …)`, which asyncpg cannot type — `CAST(:document_id AS uuid)` is
the pattern).

**The desk is different on purpose**: it carries the last `STUDY_HISTORY_TURNS` exchanges with old
answers trimmed to 700 characters — their job is to make pronouns resolve, not to re-supply
evidence the model can fetch again — and no compaction, which is a whole extra model call per
question.

---

## 70. The research agent

**What it does.** For a knowledge-gap question (a brand-new paper, a technology the model has never
seen), an iterative loop: search the web, read paper sections, describe figures, keep a research
log, synthesise — and persist any useful images it found as local assets.

**Where.** [`chat/research_agent.py`](../../backend/app/chat/research_agent.py) (`MAX_ITERATIONS`
3, 6 results per search, ≤ 8 sources in the findings),
[`services/image_service.py`](../../backend/app/services/image_service.py)
(`download_and_store_research_image`, content-addressed under `images/research/<conv_id>/`),
`GET /media/research/{conversation_id}/{filename}`.

**Why.** Triggered only when the main model explicitly signals `NEEDS_RESEARCH`; it reuses the
existing search infrastructure and never touches LOCAL/GLOBAL/OVERVIEW. Images are downloaded
once server-side and stored permanently so they become durable, offline parts of the conversation
— a hotlinked web image rots; a paper figure does not. Failures never break the answer.

---

## 71. The web search cascade

**What it does.** One door to the web for every caller: `tavily → linkup → exa → serpapi →
duckduckgo`, first configured provider to answer wins.

**Where.** [`search/web.py`](../../backend/app/search/web.py), one client per provider,
[`search/ranking.py`](../../backend/app/search/ranking.py) (dedupe by URL, score),
[`core/circuit_breaker.py`](../../backend/app/core/circuit_breaker.py), `GET /search/web`.

**How it works.** `auto` falls through to the next provider the moment one errors *or returns zero
results*. Tavily accepts a **comma-separated key list** and rotates through it — each key carries
its own free monthly allowance, so an exhausted key falls to the next before the cascade moves on
(same for `OLLAMA_API_KEY`). DuckDuckGo needs no key and is always eligible, so the box always
has web search — and it is last by design because it scrapes an undocumented endpoint. A provider
that fails `FAILURE_THRESHOLD` (3) times in a row is skipped for a five-minute cooldown, then given
one trial call; its *priority* never changes, so a recovered provider is picked up automatically.
Only the query string leaves the machine — never paper text, chunks or chat history.

**Why no Google.** Tried twice (Gemini grounding: 100 % 429 from the EEA; Custom Search: 100 free
queries/day then it bills). The rule: no provider here may cost money; Tavily's allowance scales by
adding keys. **No per-user rate limit and no result ceiling** on `/search/web`, at Khaled's
request: the cascade ends at a free provider, so an exhausted quota degrades rather than errors,
and a cap only throttled the few readers this box serves. It requires a session, so anonymous
callers cannot spend the keys.

---

## 72. Inline paper figures in answers

**What it does.** When the reader asks for a figure, the model embeds it — `![caption](url)` — and
the chat renders the real image; web images the research agent found render the same way.

**Where.** `orchestrator.py` (the "RELEVANT PAPER FIGURES" block, `prompts.py::FIGURE_INSTRUCTIONS`),
`repositories/assets.py::resolve_asset_url`, [`components/AnswerImage.tsx`](../../frontend/src/components/AnswerImage.tsx).

**How it works.** The prompt lists the paper's figure URLs (authenticated `/papers/{id}/assets/…`
links) and tells the model to copy one exactly. `AnswerImage` resolves `/api/…` paths against the
configured API origin, lazy-loads, sends no referrer, and on error shows a labelled
"Image blocked by source" fallback with the original link — hotlink protection is so common on the
web that a broken red X would be the normal case.

**Why the instructions are only appended on request.** An earlier version force-embedded images
into every answer, and the model started prepending an image to every reply.

---

## 73. The grounding check on all three surfaces

**What it does.** After every answer on every surface — notes, book chat, desk — one judge call
checks each sentence against the passages the answer cited and the agent read, and the client
shows the verdicts (feature 51). `GROUNDING_CHECK=false` turns it off.

**Where.** [`chat/grounding.py`](../../backend/app/chat/grounding.py) (`check_document_answer`,
`check_desk_answer`, `verify`, `unavailable`), the trailing `grounding` SSE event in
`notes.py`, `ask.py`, `studies.py`; `/ask` (JSON) returns it inline with `turn_id`.

**How it works.** Each surface yields its existing `done`, then runs the check in a fresh session,
persists (`paper_notes.grounding` / `conversation_turns.grounding`), and yields the report — the
answer is complete and committed before the judge starts, so a judge failure can never lose one.
The judge runs on the same model that answered (loaded and known to work); a stronger judge is a
one-line `model=` change.

---

## 74. Ask traces

**What it does.** Every `/ask` records a row in `ask_traces`: `context_type`, `router_reason`,
`model`, `prompt_tokens`, `completion_tokens`, `latency_ms`.

**Why.** Debugging a wrong answer starts with "which route, why, how long" — without the trace it
is guesswork against a non-deterministic model.

---

## 75. Direct retrieval endpoints

**What it does.** `GET /search/vector?q=&document_id=&limit=` (embed the query, top-K by cosine,
across the library when `document_id` is omitted) and `GET /search/web?q=&limit=` (the cascade
with the same ranking EXTERNAL applies). Not called by the standard UI; for testing retrieval.

**Where.** [`endpoints/search.py`](../../backend/app/api/v1/endpoints/search.py).

---

## 108. Cross-session memory about the reader

> Found while writing this catalogue — it was not in the 107-row table.

**What it does.** Durable observations about the *reader* — a stated preference ("keep answers
short"), their level, a recurring interest — remembered across conversations and documents and
recalled when relevant to a new question.

**Where.** [`chat/memory.py`](../../backend/app/chat/memory.py) (`recall_memories`,
`format_memories`, `write_memory`, `write_remembered`, `distill_memories`), `agent_memories`
table, `repositories/memories.py`; used by `paper_agent.py`, `study_agent.py`, and the
orchestrator's compaction checkpoint.

**How it works.** Two ways a memory is written, both through `write_memory` so dedup lives in one
place: **explicit** — the agent chose to note it mid-conversation (`REMEMBER:` in a tool block, or a
`<remember>…</remember>` tag on the answer), always global; **distilled** — at the orchestrator's
compaction checkpoint, which is already paying for one extra model call to re-read the transcript,
so memory grows on its own rather than only when the agent happens to say REMEMBER. Memories are
embedded; recall is by cosine similarity to the current question (≥ 0.45 to spend prompt space on),
and a candidate above 0.92 to an existing memory is treated as already known — a reader who
mentions the same preference three conversations running produces one memory, not three. Recall is
scoped by `user_id`.

**Why this is the one place the chat stack leans on pgvector.** `paper_agent` stays off similarity
search for *paper content* because it returns passages about a topic rather than the one that
states it. That objection does not apply here: "roughly the same meaning" is exactly what a memory
lookup wants, since a reader phrases the same preference differently every time.
