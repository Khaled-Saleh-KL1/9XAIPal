# Chat & /ask

> **What this is:** how a question becomes a grounded, cited answer.
>
> **Owns:** the paper agent (`/notes`), the four context routes (`/ask`), the guardrail, the
> research agent, citation hygiene.
> **Does not own:** which model serves each call ([ai-backend.md](ai-backend.md)), where the
> external results come from ([configuration.md § Web search](../03-reference/configuration.md#web-search)).
>
> **Companions:** [overview.md](overview.md): system context ·
> [api.md](../03-reference/api.md): request and response shapes ·
> [database-schema.md](../03-reference/database-schema.md): `paper_notes`, `conversation_turns`.
>
> **Status:** current · **Last verified:** Part 1 on 2026-08-26 against
> [`chat/paper_agent.py`](../../backend/app/chat/paper_agent.py): the tool loop, the `WEB`
> tool, and the step trail were exercised end to end against a live paper; Part 2 on
> 2026-07-25 against [`chat/orchestrator.py`](../../backend/app/chat/orchestrator.py)
> (`main`, 9b75500)
> **Verify with:** the `NOTE[...]` and `ASK[stepN]` log lines emitted on every question, and the
> `step` SSE events on `POST /papers/{id}/notes/stream`

## Three answering paths

Two of them are agents over the same tool layer; the third is the older router.

| | **Paper agent** (`/notes`) | **Study agent** (`/studies/…/chat`) | **Orchestrator** (`/ask`) |
| --- | --- | --- | --- |
| Serves | the article reader's margin notes | the desk: one scope, many papers | books, and any remaining `/ask` caller |
| Source | [`chat/paper_agent.py`](../../backend/app/chat/paper_agent.py) | [`chat/study_agent.py`](../../backend/app/chat/study_agent.py) | [`chat/orchestrator.py`](../../backend/app/chat/orchestrator.py) |
| Scope | one paper | a study, or the whole library | one document |
| Retrieval | the anchor + that paper's contents index | the **study index**: every paper's heading spine | LOCAL / GLOBAL / OVERVIEW / EXTERNAL |
| Cites | `[[42]]` | `[[P2:42]]` | citation objects |
| History | ✗ (one Q+A) | ✅ (a transcript) | ✅ |
| Needs embeddings | ✗ | ✗ | ✅ for GLOBAL |
| Router / guardrail / compaction | ✗ | ✗ | ✅ |
| Persists to | `paper_notes` | `conversation_turns` (`study_id`) | `conversation_turns` + `ask_traces` |

The first two share [`chat/agent_tools.py`](../../backend/app/chat/agent_tools.py): everything
between "the model asked for a section" and "here are the blocks". They differ only in what they
are pointed at, how they name a block, and their prompts.

⚠ **`prefix` is what keeps the two citation schemes apart.** A block number means nothing once
there is more than one paper, so every formatter takes the prefix (`""` or `"P2:"`) rather than
deciding for itself. Without that the two agents would drift into citing the same block two
different ways, and the client could not tell which scheme it was rendering.

⚠ The paper agent deliberately drops routing, the guardrail, and compaction. A note is anchored to
a place the reader is already looking at, so there is nothing to route; paper Q&A is in-scope by
definition, so there is nothing to guard; and a note is one Q+A rather than a rolling transcript,
so there is nothing to compact. Each omission removes a model call from the critical path.

---

# Part 1: The paper agent (`/notes`)

## Two levels of question, one agent

A note's `scope` says which surface owns it. Both go through the same loop; they differ in what
the model is handed and how many rounds it gets.

| | `scope='anchor'` | `scope='document'` |
| --- | --- | --- |
| Asked from | a selection in the article, or `A` | the assistant panel, bottom-left, or `P` |
| `anchor.kind` | `text` / `figure` / `equation` / `table` / `block` | `document` |
| Model is handed | the quote + `LOCAL_CONTEXT_WINDOW` neighbours + contents | the first `PAPER_AGENT_OPENING_BLOCKS` blocks + contents |
| Rounds | `PAPER_AGENT_MAX_STEPS` (4) | `PAPER_AGENT_HOLISTIC_MAX_STEPS` (6) |
| Whole-document stuffing | possible, if `PAPER_WHOLE_DOCUMENT_CONTEXT` | **never** |
| Rendered in | the margin gutter, beside its passage | the assistant panel |

⚠ **`scope` is derived from `anchor.kind`, never accepted as its own field**
([`notes.py::create_note_stream`](../../backend/app/api/v1/endpoints/notes.py)). A request saying
`kind='document', scope='anchor'` has no coherent meaning, and accepting both would create rows no
surface can place. A follow-up inherits its parent's scope.

⚠ **A document-scope note still carries an `anchor_sequence_id`** (the paper's first block)
because the column is `NOT NULL`. Nothing positions by it, and `_choose_margin` explicitly excludes
these rows: they all share one sequence id, so counting them would make every note near the top of
the paper look crowded.

⚠ **Whole-document stuffing is disabled for holistic questions even when the flag is on.** "What
does this paper claim?" is exactly the case the opt-in was written against: handed forty pages,
the model summarises what it was given instead of deciding which sections the question turns on.

## The paper is not in the prompt

A note is a question about one passage. The model gets that passage, its neighbours, and the
paper's **contents**: the heading spine, every entry carrying the block number it starts at.
Everything else it has to go and get.

⚠ **This reverses the old default.** Until 2026-08-18 a paper under `WHOLE_PAPER_MAX_TOKENS` was
stuffed into the prompt whole, and only a paper too large for that got tools. Whole-document
stuffing is now opt-in via `PAPER_WHOLE_DOCUMENT_CONTEXT` (default `False`): a paper that merely
*fits* is not a reason to spend the context window on it.

**What makes the omission safe is the contents index, not the tools.** A model that cannot see the
document but can see its shape knows what exists and can name the section it wants; a model with
neither has only guesses at the paper's vocabulary and no way to tell a gap in its knowledge from a
gap in the paper.

```text
                        note asked
                             │
                  ┌──────────┴──────────┐
                  │ PAPER_WHOLE_        │  default: False
                  │ DOCUMENT_CONTEXT    │
                  │ and paper fits?     │
                  └──────────┬──────────┘
              yes            │            no
          ┌──────────────────┘            └──────────────────┐
          ▼                                                  ▼
    ── whole ──                                       ── agent ──
  every block in the prompt          the anchor + surrounding blocks
  one streamed call                  + PAPER CONTENTS (the index)
          │                                                  │
          │                                    ┌─────────────┴─────────────┐
          │                                    │  up to PAPER_AGENT_       │
          │                                    │  MAX_STEPS rounds         │
          │                                    │                           │
          │                                    │  model emits <tool>       │
          │                                    │   SECTION: <seq>          │
          │                                    │   SEARCH: <terms>         │
          │                                    │   READ: <a>-<b>           │
          │                                    │  backend executes,        │
          │                                    │  feeds results back       │
          │                                    └─────────────┬─────────────┘
          │                                                  ▼
          │                                   rounds spent → forced answer
          └──────────────────┬───────────────────────────────┘
                             ▼
                answer + [[42]] block markers
                             ▼
                cited_sequence_ids → jump chips
```

### (rendered)

```mermaid
%%{init: {'themeVariables': {'fontFamily': 'ui-monospace, SFMono-Regular, Menlo, monospace', 'lineColor': '#8b949e'}}}%%
flowchart TD
    N["note asked<br/>(anchor + question)"] --> SZ{"PAPER_WHOLE_DOCUMENT_CONTEXT<br/>and paper fits?"}
    SZ -->|"yes (opt-in)"| W["whole:<br/>every block in the prompt"]
    SZ -->|"no (default)"| A["agent:<br/>anchor + neighbours<br/>+ PAPER CONTENTS"]
    A --> T{"model emits &lt;tool&gt;?"}
    T -->|"SECTION / SEARCH / READ"| X["execute against chunks<br/>feed observations back"]
    X --> T
    T -->|"no, or rounds spent"| ANS
    W --> ANS["streamed answer<br/>with [[seq]] markers"]
    ANS --> C["cited_sequence_ids<br/>→ jump chips"]

    classDef owned stroke:#3b82f6,stroke-width:2px
    class W,A,X,ANS owned
```

The diagram rules out one thing worth naming: there is no size check on the default path. A
one-page paper and a 90-page one take the same route, and the only thing `WHOLE_PAPER_MAX_TOKENS`
still does is cap the opt-in branch.

⚠ **The answer does not stream on the default path when the model answers without a tool.** The
first call is a non-streamed probe, because the reply may turn out to be a tool block and streaming
`<tool>SEARCH: …` into the margin would be nonsense on screen. Generation time is unchanged; only
the reveal is, and [`lib/pacer.ts`](../../frontend/src/lib/pacer.ts) still paints it at a readable
rate. Streaming returns for the forced final answer after a tool round.

## The tools

| Tool | Syntax | Backed by |
| --- | --- | --- |
| `SECTION` | `SECTION: 31` | the contents entry at that `sequence_id`, expanded to its whole section |
| `SEARCH` | `SEARCH: reference sliding window attention` | Postgres full-text **plus** a literal `ILIKE` substring pass |
| `READ` | `READ: 40-52` | `chunks` in that `sequence_id` range, capped in SQL |
| `WEB` | `WEB: Longformer dilated attention` | the configured provider via [`search/web.py`](../../backend/app/search/web.py): Tavily by default |
| `THINK` | `THINK: checking what τ was set to` | nothing. It executes no call and costs no round: it is the model's own reason for the round, shown to the reader above the fetches it triggered |

⚠ **`WEB` is offered only when a provider is configured** (`web.is_configured()`), and the help
text for it is appended to the system prompt rather than baked in. A model told it can check the
internet, whose every check comes back empty, stops trusting its own observations and starts
guessing, worse than never having the tool.

⚠ **`WEB` is the one tool that leaves the machine.** Only the query string goes out. It is scoped
in the prompt to what the paper cannot answer by construction (what a cited work did, what a term
means in the wider field) and explicitly forbidden for anything the paper states.

⚠ **Web claims are attributed in prose, not with a marker.** `[[n]]` means a block number in *this*
paper; models given `WEB` will otherwise invent `[[WEB]]`, which links to nothing and renders as
literal brackets mid-sentence. The prompt forbids it and
[`NoteCard.tsx::withCitationLinks`](../../frontend/src/views/NoteCard.tsx) strips any marker with no
digits in it as a backstop.

`SECTION` is the tool that makes the index usable: it takes a number the model read off the
contents and returns everything under that heading, down to the next heading of the same or higher
level. Asking for a chapter gets its subsections; asking for a subsection gets only itself.
Resolution is [`paper_agent.py::_section_range`](../../backend/app/chat/paper_agent.py), and it
reads the already-loaded chunk list rather than going back to the database.

⚠ **A block number that is not a heading resolves to the section containing it** rather than
failing. Models routinely pass a `SEARCH` hit to `SECTION` to mean "give me the rest of whatever
this was in", and that is both what they want and the only useful thing to do with the number.

⚠ **The `SECTION` parser is deliberately loose**: `SECTION: [[31]] Method` works. Models echo the
contents line they are following, brackets and title included, and a strict `SECTION:\s*(\d+)$`
throws the call away, which reads to the reader as the index quietly not working.

⚠ **A paper with no detected headings has no index, so one is synthesised**: every Nth block,
sampled by `PAPER_AGENT_MAP_STRIDE`, labelled as a sample rather than a table of contents
([`_format_block_map`](../../backend/app/chat/paper_agent.py)). Without it such a paper loses the
index *and* the document in one move: nothing to browse and nothing in the prompt, leaving a wrong
guess at the vocabulary as a dead end.

⚠ **The tools are a text protocol, not provider tool-calling.** This app fans out to Ollama and
five OpenAI-compatible clouds whose tool-calling support and schemas differ; a fenced block every
model can emit works on all of them, including local models with no tool support at all. The cost
is a parser, and it is a deliberate trade. Tool calls are only recognised inside a `<tool>` block,
so a model that writes "SEARCH:" in prose cannot trigger a round trip.

⚠ **The substring leg is not redundant.** `to_tsvector` discards single Greek letters, equation
numbers, and symbol subscripts entirely: a reader asking "why is τ so small here" gets zero
full-text hits on the one term that matters.

⚠ The two search legs run **sequentially, not gathered**. An `AsyncSession` is a single connection
in a single greenlet context; concurrent statements on it are unsupported and fail under the wrong
interleaving. Both legs are indexed lookups measured in single-digit milliseconds.

## The trail: every fetch is reported, not just logged

The agent can spend six rounds fetching before it writes a word. Without a trail that time is a
spinner, and the answer arrives as an assertion the reader has no way to check. The loop therefore
emits a `step` SSE event per call, the client renders them live, and the whole list is persisted to
`paper_notes.agent_steps` so a note reopened next week still shows how it was grounded.

```text
   model emits <tool>            backend                       reader sees
   ─────────────────             ───────                       ───────────
   THINK: why                                                  “checking what τ was set to”
   SECTION: 31        ──►  _plan() orders the calls
   SEARCH: tau             cheap-first: SECTION, READ,
   WEB: longformer         then SEARCH, then WEB
                                    │
                           for each call, BEFORE running:
                             step{state:"running"}   ──────►   § Reading “4.2 Training mixture”  ⋯
                                    │
                           _run_call() executes it
                                    │
                             step{state:"done"}      ──────►   § Read “4.2 Training mixture”  → 2 blocks · ¶47–¶48
                                    │                          ¶47  ¶48        ← jump chips
                           observation (the raw blocks)
                                    ▼
                           appended to `gathered`, fed
                           back into the next round,
                           NEVER sent to the browser
```

### (rendered)

```mermaid
%%{init: {'themeVariables': {'fontFamily': 'ui-monospace, SFMono-Regular, Menlo, monospace', 'lineColor': '#8b949e'}}}%%
flowchart LR
    T["&lt;tool&gt; block<br/>THINK + calls"] --> P["_plan()<br/>cheap calls first"]
    P --> R["step state=running<br/>(all calls at once)"]
    R --> X["_run_call()"]
    X --> D["step state=done<br/>+ result, seqs, sources"]
    X --> O["observation<br/>(raw blocks)"]
    O --> G[["gathered →<br/>next round's prompt"]]
    D --> UI["AgentTrail.tsx<br/>upsert by step.id"]
    D --> DB[("paper_notes.agent_steps")]

    classDef owned stroke:#3b82f6,stroke-width:2px
    classDef never stroke:#f59e0b,stroke-dasharray:4 3
    class P,X,UI owned
    class O,G never
```

What to notice: the two arrows out of `_run_call` never meet again. The **observation** (thousands
of characters of raw blocks) goes only into the next prompt; the **step** (a label, a one-line
result, the block numbers) goes only to the browser and the database. Streaming the observation
would ship the whole paper to a card that renders one line of it.

| Field | Purpose |
| --- | --- |
| `id` | `s{round}-{index}`. Stable across the `running` → `done` pair |
| `n` | which tool round, 1-based |
| `tool` | `SECTION` \| `SEARCH` \| `READ` \| `WEB` |
| `think` | the model's stated reason, **first call of the round only** |
| `label` | reader-facing phrasing: `Read “4.2 Training mixture”` |
| `result` | one-line summary: `2 blocks · ¶47–¶48`, `no matches`, `4 sources` |
| `seqs` | block numbers pulled in, rendered as jump chips (first 6) |
| `sources` | `{title, url}` for `WEB`, rendered as links |

⚠ **A step arrives twice and the client must upsert by `id`, never append.** Appending shows each
fetch as two rows, the first spinning forever.

⚠ **`think` rides on the first call of a round only.** It explains the *round*; repeated across
three fetches it reads as three separate reasons.

⚠ **`_plan()` deliberately reorders the model's calls**: `SECTION` and `READ` (millisecond index
lookups) before `SEARCH` (two indexed queries) before `WEB` (a network round trip). The reader
watches the trail fill in immediately instead of staring at one pending web row while three instant
fetches queue behind it.

⚠ **The trail renders *outside* the answer's `Collapsible`**
([`NoteCard.tsx`](../../frontend/src/views/NoteCard.tsx)). A long answer is clipped at 300px behind
a "show more"; a trail inside gets clipped with it, so the one control that says how the answer was
grounded would be reachable only by first expanding the thing you were trying to check.

⚠ **Notes written before 2026-08-26 have `agent_steps = []`** and render no trail. That is correct,
not a bug: the trail was never captured for them, and an empty one is honest.

## What the anchor tells the model

`anchor.kind` changes the instruction the model is given, not just the text it sees
([`paper_agent.py::_format_anchor`](../../backend/app/chat/paper_agent.py)):

| Kind | The model is told | Quote carries |
| --- | --- | --- |
| `text` | the reader highlighted this passage; answer about it specifically | the highlighted text |
| `figure` | the image is attached, look at it | the caption |
| `equation` | explain what it says and what each symbol means; **trust the attached crop over the transcription** | its LaTeX |
| `table` | this is the whole table, not one cell; **trust the crop over the transcription**, and read the caption for what the columns mean | its recovered table body |
| `block` | the reader is reading around this point | nothing |

⚠ Both `equation` and `table` say to trust the image over the text, for the same reason: MinerU's
transcription is machine-generated. For a table it specifically loses merged cells, spanning
headers, and footnote markers: the parts that decide what a number means.

## Citations

The prompt asks the model to mark each grounded claim with its block number, `[[42]]`. Those are
parsed into `cited_sequence_ids` and rendered as chips that scroll the article.

⚠ The parser matches a whole bracket blob, not a single number. Models routinely group references
as `[[16], [42]]` or `[[16, 42]]`; a strict `\[\[(\d+)\]\]` silently returns nothing for those,
and the note renders with no chips, making a well-grounded answer look ungrounded.

## Model selection

A note records both `requested_model` (what the reader picked) and `model` (what the provider
reported). The catalog comes from [`llm/catalog.py`](../../backend/app/llm/catalog.py).

⚠ **A follow-up always uses its parent's `requested_model`, and the client cannot override it.**
A thread that switched models halfway would destroy the comparison the picker exists for: you
would no longer know which model said what.

---

# Part 1b: The study agent (the desk)

## What a study is

A named group of papers that scopes an answer. Not a folder: a paper can sit in several studies at
once, and removing it from one takes nothing away from the library or the others.

`study_id IS NULL` on a turn is the **library-wide** scope: every finished paper. That is a real
scope, not a missing value; code that "repairs" it deletes the reader's main conversation. The
route segment for it is the literal string `library`.

## The study index

The whole thing rests on one trade: the model gets **every paper's heading spine and nothing else**.

```text
STUDY INDEX:
P1 (BDH-CQ): In-Context Learning with Recurrent Latent Reasoning (17 pages)
   [[P1:6]] Abstract
   [[P1:28]] 3 Introducing BDH-CQ
     [[P1:32]] 3.2 In-context learning through recurrent memory
P2 (Kimi K3): Open Frontier Intelligence (47 pages)
   [[P2:29]] 2 Model Architecture
     [[P2:33]] 2.1 Hybrid Attention
   [[P2:261]] 5.4 Inference and Online Serving
P3: Unlimited OCR Works (14 pages)
   [[P3:31]] 3. Methodology
     [[P3:39]] 3.4. Reference Sliding Window Attention
```

Ten papers is easily a million tokens of body text; the spine of all ten is a few thousand. That is
the entire reason a cross-paper agent is affordable, and it is the same bet the paper agent makes,
taken at the point where there is no alternative.

⚠ **A paper MinerU found no headings in still gets an entry**, with a note saying `SECTION` will not
work on it. Omitting it would leave the model believing the study is smaller than it is.

## Paper-qualified tools

| Tool | Syntax | Scope |
| --- | --- | --- |
| `SECTION` | `SECTION: P2:31` | one paper's section |
| `READ` | `READ: P1:40-52` | one paper's block range |
| `SEARCH` | `SEARCH: inference cost` | **every paper in the study at once**, hits labelled by paper |
| `WEB` | `WEB: ARC-AGI state of the art` | the public internet |
| `NOTE` | `NOTE: P2 and P3 disagree here` | a write: pins to this chat's board |
| `NOTE ALL` | `NOTE ALL: worth chasing later` | a write: pins to the universal board |
| `THINK` | `THINK: P2 is the one that reports cost directly` | nothing: the reason, shown to the reader |

⚠ `NOTE` is the only tool that changes something, so `_plan` runs it **last**:
the trail then reads as "looked, then wrote" rather than the reverse.

⚠ **The P-numbers come from `study_papers.position`, and they are load-bearing.** They are how an
answer names a paper, so re-ordering a study silently repoints every citation the reader has already
read. That is why membership is written whole-collection (`PUT /studies/{id}/papers`): the list
order *is* the numbering.

⚠ **Out-of-range paper numbers are dropped at plan time**, not at execution. A model that writes
`P7` for a five-paper study is guessing, and fetching the wrong paper is worse than not fetching.

⚠ **`run_search` de-duplicates on `(document_id, sequence_id)`, not `sequence_id`.** Across a study
every paper has a block 12; keying on the number alone silently drops every paper's hit but the
first.

## Notes: the agent reads both boards and writes to either

The desk has two boards (the chat's own, and the universal one), and the agent
sees both and can pin to both.

**Reading.** Every note already pinned rides in the prompt, each labelled with
who wrote it:

```text
NOTES ALREADY ON THE BOARDS (you can add and edit, never remove):
  On this chat:
    - (you wrote) P3 is the only one reporting wall-clock, as tokens/sec
  On the universal board:
    - (the reader wrote) Ask: does anyone report wall-clock rather than FLOPs?
```

⚠ **The authorship label is load-bearing.** Without it the model re-pins its own
notes every few turns, since it has no memory of having written them, and the reader
ends up with the same observation five times in five colours.

**Writing**, two ways, because the model reaches for both:

| Where | Syntax | Board |
| --- | --- | --- |
| In a tool block, during a retrieval round | `NOTE: <text>` | this chat |
| | `NOTE ALL: <text>` | universal |
| In the final answer | `<note>…</note>` | this chat |
| | `<note board="all">…</note>` | universal |

⚠ **The `<note>` tag exists because the model invented it.** Asked to "pin a
note", it wrote `<note>…</note>` into its answer on the first try, since the forced
final turn is told it has no tools, so a `NOTE:` line is genuinely unavailable
there and it improvised a tag. Parsing it is meeting the model where it is;
refusing to would leave raw XML in the reader's answer and nothing on the board.
The tag is stripped from what the reader sees, because the text is going on the
board and saying it twice makes the board a duplicate of the paragraph above it.

⚠ **`NOTE ALL:` must be matched before `NOTE:`**, and the plain pattern must
refuse to match it. Otherwise a universal note parses as a chat note whose body
begins "ALL:" and lands on the wrong board.

⚠ **Both exits from the loop extract notes.** The model can answer on the very
first probe without calling a tool, and that path never touches
`stream_answer`, so extracting only in the streamed branch left the raw tag in
the answer exactly when the reader had asked for a note. That is how it failed
the first time; `_pin_written_notes` is now called from both.

⚠ **Notes are de-duplicated by body against the destination board**, on
collapsed whitespace, because the second copy is usually the first one
re-wrapped. Two per answer, hard.

### The agent cannot delete a note

Not a rule the model is asked to follow, but a structural fact:

- there is no delete tool in the parser or the plan;
- `study_agent` does not import `sticky_repo.delete_sticky`;
- `POST /stickies` forces `origin='user'`, so no client can forge an assistant
  note either, and the assistant's own writes go through the repository.

`DELETE /stickies/{id}` exists for the × in the UI and nothing else calls it.

⚠ `origin` is not patchable. A note the assistant wrote stays badged as the
assistant's however often the reader edits it: the badge records where the claim
came from, not who typed last.

## Repeated calls are short-circuited

⚠ **Observed**: asked a broad question, the model re-requested the same three
`SECTION`s on four consecutive rounds: twelve identical fetches, four of eight
rounds burned making no progress. It can see the results in `WHAT YOU HAVE
GATHERED`, but a fresh copy of the same text reads to it as confirmation rather
than repetition.

A signature set per question (`tool`, `arg`, `board`) now answers a repeat with
"you already fetched this: ask for something else, or answer with what you
have". `NOTE` is exempt: writing the same note twice is caught by the note
de-dup instead, which compares against the board rather than the round.

## The forced final turn can still call a tool

The last round is told it has no tools left. It mostly obeys. When it does not, the tokens are
already streaming to the reader, so stripping afterwards is too late: `tool> THINK: verify P3's
cost claim… SECTION: P3:29` lands in the middle of the answer and stays there until a refetch
quietly replaces it. **Observed, not hypothetical**: it happened on the first live desk question.

[`agent_tools.stream_answer`](../../backend/app/chat/agent_tools.py) therefore filters in the
stream: the moment `<tool` appears the generation has stopped being an answer, and everything from
there is dropped from both the stream and the persisted text.

⚠ It withholds the last few characters until the next token arrives, because the marker can be
split across token boundaries (`"<to"` + `"ol>"`). Without that, a leak that straddles a boundary is
emitted before it can be recognised.

⚠ Both agents route through it. The paper agent had the same latent hole.

## History, not compaction

The desk carries the last `STUDY_HISTORY_TURNS` exchanges so "and the second one?" resolves. Old
answers are trimmed to 700 characters: their job is to make pronouns resolve, not to re-supply
evidence the model can fetch again.

⚠ Deliberately **not** the orchestrator's compaction. That is a whole extra model call per
question, and a desk conversation needing more than eight turns of memory is usually a new question.

---

# Part 2: The orchestrator (`/ask`)

⚠ `[historical]` for papers. The article reader never calls `/ask`; this path now serves books and
any external caller. The routes below are unchanged.

## The four context modes

| Context  | When                                                       | What we retrieve                              | Model receives                                                |
| -------- | ---------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------- |
| LOCAL    | The question is about what's currently on screen           | The current chunk (± neighbors) + its image   | `system + "Context:\n<chunks>"` + image as base64 (multimodal) |
| GLOBAL   | The question requires searching the whole paper            | Top-K similar chunks by pgvector cosine       | `system + "Context:\n<chunks with similarity scores>"`        |
| OVERVIEW | The question is paper-level ("summarize the paper")        | Pre-computed section_summaries (hierarchical) | `system + "Context:\n<structured outline>"`                   |
| EXTERNAL | The question is about something outside the paper          | Top-K web results from the search cascade      | `system + "Context:\n<title/url/snippet rows>"`               |

## The flow ([chat/orchestrator.py](../../backend/app/chat/orchestrator.py))

```python
async def handle_ask(session, *, prompt, document_id, current_chunk_id, conversation_id):
    # Step 0: Topic guardrail (is this about IT/CS?)
    guardrail_result = await check_guardrail(prompt, in_paper_context=...)

    # Step 1: Route the prompt
    decision = await route_prompt(prompt, has_current_chunk=..., has_document=...)
    # decision.context_type ∈ {LOCAL, GLOBAL, OVERVIEW, EXTERNAL}

    # Step 2: Build context
    if LOCAL:
        ctx = build_local_context(session, document_id, current_chunk_id, window_size=1)
        context_text = format_local_context(ctx["chunks"])
        citations = citations_from_chunks(ctx["chunks"])
        image_paths = [asset.file_path for asset in ctx["assets"] if asset_type=="image"]
        system_prompt = LOCAL_SYSTEM_PROMPT

    elif GLOBAL:
        ctx = build_global_context(session, query=prompt, document_id, limit=3)
        context_text = format_global_context(ctx["chunks"])
        citations = citations_from_chunks(ctx["chunks"])
        system_prompt = GLOBAL_SYSTEM_PROMPT

    elif OVERVIEW:
        ctx = build_overview_context(session, document_id)
        context_text = format_overview_context(ctx["summaries"])
        citations = citations_from_summaries(ctx["summaries"])
        system_prompt = OVERVIEW_SYSTEM_PROMPT

    else:  # EXTERNAL
        ctx = build_external_context(prompt, max_results=5)
        context_text = format_external_context(ctx["results"])
        citations = citations_from_web_results(ctx["results"])
        system_prompt = EXTERNAL_SYSTEM_PROMPT

    # Step 3: Build multimodal messages
    messages = build_multimodal_messages(prompt, system=system_prompt,
                                         context_text=context_text,
                                         image_paths=image_paths)

    # Step 4: Call LLM
    result = await ollama_client.chat(messages)

    # Step 5: If the model signals NEEDS_RESEARCH, run the research agent loop
    if result.get("needs_research"):
        result = await research_agent.loop(prompt, document_id)

    # Step 6: Persist turn + trace
    return AskResponse(answer, context_type, router_reason, citations, model, conversation_id)
```

## The router ([chat/router.py](../../backend/app/chat/router.py))

Two-tier:

1. **Cheap heuristic first.** Three keyword lists:
   - `_LOCAL_KEYWORDS`: `"this formula"`, `"this figure"`, `"above"`,
     `"shown here"`, `"bring a picture"`, etc. Only fires if the request
     carries a `current_chunk_id`.
   - `_OVERVIEW_KEYWORDS`: `"summarize the paper"`, `"main contribution"`,
     `"tl;dr"`, `"executive summary"`, etc.
   - `_EXTERNAL_KEYWORDS`: `"latest"`, `"recent"`, `"who is"`,
     `"wikipedia"`, etc.
2. **Fallback to LLM** for ambiguous queries: outputs `LOCAL`, `GLOBAL`,
   `OVERVIEW`, or `EXTERNAL` as JSON.

Special cases:
- No document context → EXTERNAL wins.
- LLM routing failure → default is GLOBAL when document exists.
- Sub-threads default to paper-free (`is_sub_thread=True`).

Each decision carries a `reason` string stored in `ask_traces.router_reason`.

## LOCAL context ([chat/local_context.py](../../backend/app/chat/local_context.py))

1. Fetches a **window** of chunks centered on `current_chunk_id` (default ±1).
2. Fetches every `chunk_assets` row for any chunk in the window.
3. Images are base64-encoded and attached to the Ollama user message.

If the current chunk is a figure, the model literally sees the picture.

## GLOBAL context ([chat/global_context.py](../../backend/app/chat/global_context.py))

1. Calls `get_query_embedding(prompt)`: embeds the query with the active embedding backend (same resolver as ingestion, so query and stored vectors always match).
2. Calls `embeddings.search_embeddings`: pgvector cosine-similarity.
3. Returns top-K (default 3) chunks.
4. Surfaces images attached to retrieved chunks for inline rendering.

## OVERVIEW context ([chat/overview_context.py](../../backend/app/chat/overview_context.py))

1. Fetches all `section_summaries` rows (level 0 + level 1 + level 2).
2. Formats them as a structured document outline.
3. Citations come from each summary's `source_chunk_ids`.

## EXTERNAL context ([chat/external_context.py](../../backend/app/chat/external_context.py))

1. Rewrites the query toward CS/ML (`rewrite_query_for_papers`) so an ambiguous term like
   "transduction" does not return genetics hits.
2. Calls [`search/web.py`](../../backend/app/search/web.py): a cascade of tavily → linkup →
   exa → serpapi → duckduckgo, first configured one to answer wins (tavily itself rotates
   across a comma-separated key list before giving up, and duckduckgo needs no key at all, so
   this never runs fully dry). Never a provider module directly.
3. Ranks results via `search/ranking.py` (dedup + scoring).
4. Returns at most 5 results.

⚠ No `categories`/engine-group filter is applied here. It used to bias SearXNG toward "it" and
"science" engines, but verified (2026-08-31) that restricting SearXNG this way made it return
irrelevant results — Docker Hub and GitHub repos outranking the actual paper for a plain query
like "FlashAttention 2 paper" — because SearXNG's per-result `score` is a per-engine position
weight, not a comparable relevance score across the several engines a category filter turns on.
None of the four current providers have an engine-group concept anyway; the domain bias comes
entirely from step 1.

## Research Agent ([chat/research_agent.py](../../backend/app/chat/research_agent.py))

For complex queries, an iterative research loop:
- Tools: `web_search`, `read_paper_section`, `describe_figure`.
- Maintains a research log and synthesizes a final response.
- Triggered when the LLM emits a `NEEDS_RESEARCH` signal.

## Multimodal request shape ([llm/multimodal.py](../../backend/app/llm/multimodal.py))

```python
messages = [
  {"role": "system", "content": <prompt>},
  {"role": "user",
   "content": "Context:\n<context_text>\n\n<original prompt>",
   "images": ["<base64 PNG/JPEG>", ...]},   # only LOCAL with images
]
```

The client POSTs to `{OLLAMA_BASE_URL}/api/chat` with `stream: false`.

## Citations ([chat/citations.py](../../backend/app/chat/citations.py))

Two builders:
- `citations_from_chunks` → `chunk_id`, `sequence_id`, `page`, `text_snippet`, `source="document"`.
- `citations_from_web_results` → `url`, `text_snippet`, `source=<engine>`.

Persisted as JSON on the assistant's `conversation_turns` row.

## Conversation continuity

`conversation_id` is optional on first turn. If absent, the orchestrator
mints a new UUID. The frontend stores and passes it on every subsequent
`/ask` call.

## Sub-threads

Turns can have a `parent_turn_id` creating a tree of sub-threads.
`get_thread_subtree(root_turn_id)` fetches only the sub-thread's turns
using a recursive CTE. Sub-threads default to paper-free context.

## Compaction

When a conversation grows past a token threshold, it is automatically
compacted: earlier turns are summarized into a compact form and replaced
with a single `role='compaction'` turn. This keeps context from overflowing.

## Tracing

Every `/ask` call inserts an `ask_traces` row with:
`context_type`, `router_reason`, `model`, `prompt_tokens`,
`completion_tokens`, `latency_ms`.