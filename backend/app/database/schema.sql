-- 9XAIPal Database Schema

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────────────────────────────────────────
-- Users
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The real invariant is case-insensitive uniqueness, not byte-equality — a
-- functional index on LOWER(email) enforces that at the DB level rather than
-- trusting every future code path (password reset, "change email", …) to
-- normalize case correctly before insert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (LOWER(email));

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Owner. Nullable at the DB level ONLY because schema.sql is applied
    -- idempotently to any deployment of this repo, including ones that may
    -- already hold rows with no safe default owner — a NOT NULL add would
    -- fail outright there. Every INSERT path in app code requires user_id as
    -- a non-Optional argument, so in practice this is never actually NULL on
    -- a row created after multi-user support landed.
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,

    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_size_bytes BIGINT,
    page_count INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- LLM-corrected reading order for complex layouts (two-column papers, etc.)
    -- Stores an array of original sequence_ids in the correct logical reading order.
    reading_order JSONB,
    reading_order_model TEXT,
    reading_order_updated_at TIMESTAMPTZ,

    -- Which extractor produced this document's chunks ("mineru" or "pymupdf_fallback").
    -- Surfaced in the UI so users can see whether they got high-fidelity MinerU
    -- output (typed equations, page_footnotes, table structure) or the degraded
    -- text-only fallback.
    extractor TEXT,

    -- Whether this document is a "book" (chapter-by-chapter reading navigation),
    -- a "paper" (linear reading), or an "article" (an imported web page --
    -- also linear, but with its own conversational prompt voice, see
    -- chat/paper_agent.py's _BY_KIND dicts). Chosen by the user at upload
    -- time for a file, or fixed to 'article' for a URL import.
    doc_kind TEXT NOT NULL DEFAULT 'paper',

    -- The page a doc_kind='article' row was imported from. NULL for anything
    -- uploaded as a file. Lets the reader jump back to the live page — there
    -- is no raw PDF behind an article to fall back to the way /raw does for
    -- everything else.
    source_url TEXT,

    -- A reader-chosen display name, set from the library's rename control.
    -- NULL means no override: the UI falls back to original_filename, which is
    -- often an arXiv id rather than anything readable. Deliberately separate
    -- from original_filename so the uploaded name is never lost and /raw can
    -- still hand back a file named the way it arrived.
    title TEXT,

    -- Paper-only mode: whether this document was embedded at ingestion, or the
    -- embedding pass was skipped because the whole document fits in the chat
    -- model's context (see docs/plans/paper-only-embedding-skip.md).
    --
    -- 'embedded' is the default so that upgrading changes nothing: every
    -- pre-existing row keeps its current behaviour. The value is DECIDED ONCE
    -- at ingestion and never re-derived, so changing PAPER_ONLY_MAX_TOKENS
    -- cannot retroactively reclassify a library.
    embedding_mode TEXT NOT NULL DEFAULT 'embedded',
    embedding_skip_reason TEXT,

    -- Whether a raw HTML snapshot (see raw_snapshot_pages below) has been
    -- saved for this doc_kind='article' row: 'none' (not an article, or not
    -- attempted yet), 'pending' (crawl dispatched, not finished), 'complete',
    -- or 'failed'. A failed/pending crawl never affects `status` above — the
    -- article itself can be fully read and chatted with regardless of
    -- whether its raw copy ever finishes.
    raw_snapshot_status TEXT NOT NULL DEFAULT 'none'
);

COMMENT ON COLUMN documents.reading_order IS 'Array of original chunk sequence_ids in LLM-corrected logical reading order. Used to fix two-column and complex layout extraction issues.';

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);

-- Chunks table with physical ordering
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence_id INTEGER NOT NULL,
    parent_sequence_id INTEGER,
    chunk_type TEXT NOT NULL DEFAULT 'text',
    heading_path TEXT[],
    markdown TEXT NOT NULL,
    plain_text TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    bbox_json JSONB,
    token_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, sequence_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_sequence
    ON chunks(document_id, sequence_id);

-- Chunk embeddings with pgvector
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    embedding vector(1024) NOT NULL,  -- dimension is substituted from VECTOR_DIMENSION at migration time
    embedding_model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Chunk assets (images, figures, tables)
CREATE TABLE IF NOT EXISTS chunk_assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    caption TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunk_assets_chunk_id ON chunk_assets(chunk_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- The desk: studies, their chats, and sticky notes
-- ─────────────────────────────────────────────────────────────────────────────

-- A named group of papers that scopes an answer.
--
-- A study is the unit of "what may this question be answered from". It is
-- deliberately NOT a folder: a paper can sit in several studies at once, and
-- removing it from one takes nothing away from the library.
CREATE TABLE IF NOT EXISTS studies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- See the comment on documents.user_id — same nullable-at-DB,
    -- required-in-app-code invariant.
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_studies_user_id ON studies(user_id);

-- Membership. Ordered by position so the study's paper numbering (P1, P2, …)
-- that the agent cites by is stable across requests -- a citation the reader
-- saw yesterday must still point at the same paper today.
CREATE TABLE IF NOT EXISTS study_papers (
    study_id UUID NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (study_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_study_papers_document ON study_papers (document_id);

-- A note the reader wants to keep in front of them, independent of where in a
-- paper it came from -- or of any paper at all.
--
-- ⚠ Deliberately not personal_notes. A personal note is anchored to a block in
-- one document and lives in that document's margin. A sticky has no anchor, may
-- name several papers or none, and lives on the desk. Sharing a table would
-- give every sticky an anchor_sequence_id that means nothing.
CREATE TABLE IF NOT EXISTS sticky_notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- See the comment on documents.user_id. Required here specifically
    -- because a sticky can exist with study_id NULL and board='universal' —
    -- no parent row to derive ownership from transitively.
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL DEFAULT '',
    -- One of a small named set the UI maps to CSS variables, not a hex value.
    -- A stored hex would not survive the light/dark switch.
    color TEXT NOT NULL DEFAULT 'yellow',
    pinned BOOLEAN NOT NULL DEFAULT FALSE,

    -- Which board this note lives on.
    -- 'chat'      beside one conversation, keyed by study_id below
    -- 'universal' the standalone board, not tied to any conversation
    --
    -- board and study_id together are the scope, and board is NOT redundant:
    -- study_id IS NULL already means the library-wide CHAT, so without this
    -- column a note on the library chat and a note on the universal board are
    -- indistinguishable.
    board TEXT NOT NULL DEFAULT 'universal',
    -- The chat this note sits beside. NULL with board='chat' is the
    -- library-wide chat. Always NULL when board='universal'.
    --
    -- ⚠ SET NULL, not CASCADE. Deleting a study must not delete the reader's
    -- notes -- only the reader deletes a note. They move to the universal
    -- board instead, and the delete confirmation says so.
    study_id UUID REFERENCES studies(id) ON DELETE SET NULL,

    -- Who wrote it: 'user' or 'assistant'. The assistant can create and edit
    -- notes but never delete one, which is enforced structurally -- there is no
    -- delete tool and study_agent does not import the repository's delete.
    origin TEXT NOT NULL DEFAULT 'user',
    -- Which model wrote an assistant note. NULL for the reader's own.
    author_model TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sticky_notes_board ON sticky_notes (board, study_id);
CREATE INDEX IF NOT EXISTS idx_sticky_notes_user_id ON sticky_notes (user_id);

-- Which papers a sticky is about. Zero rows = a note about nothing in
-- particular, which is a first-class case: it shows on every desk.
CREATE TABLE IF NOT EXISTS sticky_note_papers (
    sticky_id UUID NOT NULL REFERENCES sticky_notes(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (sticky_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_sticky_note_papers_document ON sticky_note_papers (document_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Agent memory
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Durable, embedded observations the paper/book/study agents keep about a
-- reader -- a stated preference, their level of expertise, a recurring
-- interest -- so next week's conversation already knows it instead of the
-- reader re-explaining themselves. See chat/memory.py.
--
-- source: 'explicit'  the agent chose to note it mid-conversation (REMEMBER
--                      in a tool block, or a <remember> tag on a turn that
--                      has no tools left -- same reason sticky_notes catches
--                      a stray <note> tag: a model reaches for the marker
--                      whether or not the current turn offers it as a tool)
--         'distilled'  extracted after the fact from a compacted conversation
--                      -- what lets memory grow on its own, not only when the
--                      model happens to say REMEMBER
--
-- document_id NULL means the memory applies for this reader everywhere. Set,
-- it only surfaces while they are in that paper/book. Retrieval checks both.
CREATE TABLE IF NOT EXISTS agent_memories (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'explicit',
    -- Embedded inline at write time (unlike chunk_embeddings, which is
    -- populated by a background worker): a memory is one short sentence
    -- written one at a time, not thousands of blocks from a fresh upload, so
    -- there is no batch worth deferring.
    embedding vector(1024) NOT NULL,  -- dimension is substituted from VECTOR_DIMENSION at migration time
    embedding_model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_memories_user ON agent_memories (user_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_document ON agent_memories (document_id);

-- Conversation turns
-- Supports the nested sub-thread feature (tangents without polluting the main paper chat).
-- Main linear chat turns have parent_turn_id IS NULL.
-- Sub-thread turns have parent_turn_id pointing to their parent turn in the tree
-- (the branching user turn for the first continuation, or the previous turn for follow-ups).
CREATE TABLE IF NOT EXISTS conversation_turns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- See the comment on documents.user_id. Required here specifically
    -- because the pure "ask the whole library" chat has BOTH document_id and
    -- study_id NULL — no parent row exists to derive ownership from.
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    context_type TEXT,
    router_reason TEXT,
    model TEXT,
    citations JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Which study's chat this turn belongs to.
    -- NULL means the library-wide chat -- the scope that sees every paper.
    -- That is a real scope, not a missing value, so it is not an error state.
    study_id UUID REFERENCES studies(id) ON DELETE CASCADE,

    -- The trail of tool calls behind an assistant turn, same shape as
    -- paper_notes.agent_steps. See docs/02-architecture/chat-and-ask.md.
    agent_steps JSONB,

    -- NULL for all turns that belong to the main linear chat for a conversation_id.
    -- Non-NULL points to the parent turn this message is a reply to (supports
    -- arbitrary-depth nesting of tangents). The root of a sub-thread is the
    -- original user message that started the tangent (even though that user
    -- message itself has parent_turn_id = NULL so it stays visible in main chat).
    parent_turn_id UUID REFERENCES conversation_turns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation
    ON conversation_turns(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_user_id
    ON conversation_turns(user_id);

-- Fast lookup for "does this turn have any children in a sub-thread?"
-- and for recursive subtree loading.
CREATE INDEX IF NOT EXISTS idx_conversation_turns_parent
    ON conversation_turns(parent_turn_id);

-- Ask traces for debugging
CREATE TABLE IF NOT EXISTS ask_traces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_turn_id UUID REFERENCES conversation_turns(id) ON DELETE CASCADE,
    context_type TEXT NOT NULL,
    router_reason TEXT,
    retrieved_chunk_ids UUID[],
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ingestion jobs
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    -- Fraction (0-1) of progress *within* the current status, e.g. pages
    -- extracted so far / total pages while status='extracting'. NULL when no
    -- finer-grained signal is available than the status itself (the normal
    -- case for statuses other than 'extracting', and for short documents
    -- extracted in a single pass with nothing to checkpoint mid-way).
    progress_fraction REAL,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status);
-- Backs list_documents' "most recent job per document" LATERAL join (below)
-- — without it, that subquery scans and sorts the whole table once per
-- document row, on a table that only grows (job rows are never deleted).
-- list_documents runs on every library poll.
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_document_created
    ON ingestion_jobs(document_id, created_at DESC);

-- A raw, sanitized HTML snapshot of an imported article (doc_kind='article')
-- — the page actually fetched, exactly as it was (see
-- services/article_crawl.py). The doc_kind='article' equivalent of the
-- original PDF documents_dir() already keeps for a paper/book — the point
-- is letting the reader open the exact HTML the extractor had to work with,
-- to check nothing was missed. One row per article: an earlier version of
-- this also crawled same-site linked pages, which is why `depth` and a
-- one-to-many document_id FK still exist, but every row is depth=0 now —
-- see article_crawl.py's module docstring for why that idea was dropped.
CREATE TABLE IF NOT EXISTS raw_snapshot_pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    -- Always 0 now (the originally-imported URL itself) — kept rather than
    -- dropped so the column doesn't need a migration if per-page crawling
    -- ever comes back.
    depth INT NOT NULL DEFAULT 0,
    -- Filename under core/paths.py's raw_snapshots_dir(document_id) — the
    -- sanitized HTML actually lives on disk, not in this table.
    storage_filename TEXT NOT NULL,
    byte_size INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_snapshot_pages_document
    ON raw_snapshot_pages(document_id, depth, created_at);

-- ============================================================================
-- Section Summaries: Pre-computed hierarchical overviews for high-quality
-- "What is this paper about?" / "Summarize the paper" experiences.
--
-- Design goals (personal use, quality-first):
--   * Separate table (does NOT pollute the source-of-truth `chunks` table)
--   * Stores rich attribution (source_chunk_ids) so answers can cite original
--     sequence_ids / pages even when using the overview path.
--   * Model + prompt_hash for future invalidation / regeneration when you
--     change models or improve prompts.
--   * Supports both per-section (H1/H2) and whole-paper executive summary.
-- ============================================================================

CREATE TABLE IF NOT EXISTS section_summaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Stable identifier for the section within the document (e.g. "h1-03-introduction")
    section_id TEXT NOT NULL,

    -- 0 = whole-paper executive summary, 1 = H1, 2 = H2
    level INTEGER NOT NULL CHECK (level IN (0, 1, 2)),

    -- Full heading path at the time of summarization (e.g. ["Introduction", "Motivation"])
    heading_path TEXT[] NOT NULL,

    -- Inclusive range of source sequence_ids that contributed to this summary
    sequence_start INTEGER,
    sequence_end INTEGER,

    summary_markdown TEXT NOT NULL,
    summary_plain TEXT NOT NULL,

    -- Strong grounding: the exact chunk IDs whose content was fed to the LLM
    source_chunk_ids UUID[] NOT NULL,

    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,           -- hash of the prompt template + version

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (document_id, section_id, model)
);

-- ============================================================================
-- Rich Extraction Enhancements (Quality-First for Tables, Figures & Architectures)
--
-- These additions support much deeper interaction with complex paper elements.
-- The author accepts heavy processing at ingestion time for superior chat quality.
-- ============================================================================

-- Add structured table data to existing chunks (only populated for table-type chunks)
-- This allows the model to query tables intelligently ("what was the F1 score for the 7B variant in Table 4?")
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS table_json JSONB;

-- Figure / Diagram / Architecture Descriptions
-- Generated at ingestion time using VLM (gemma4:26b vision or equivalent).
-- Stored separately so they can be retrieved by GLOBAL search, OVERVIEW, or targeted tools,
-- while the original image remains in chunk_assets.
CREATE TABLE IF NOT EXISTS figure_descriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,

    -- Original image reference
    image_path TEXT NOT NULL,           -- relative path under images/ or original MinerU name

    -- Rich VLM-generated description (technical, precise, good for architectures)
    description_markdown TEXT NOT NULL,
    description_plain TEXT NOT NULL,

    -- Attribution for grounding/citations
    source_sequence_start INTEGER,
    source_sequence_end INTEGER,
    referenced_by_chunk_ids UUID[],     -- text chunks that mention this figure

    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (chunk_id, model)
);

CREATE INDEX IF NOT EXISTS idx_figure_descriptions_document
    ON figure_descriptions(document_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_figure_descriptions_chunk
    ON figure_descriptions(chunk_id);
CREATE INDEX IF NOT EXISTS idx_section_summaries_document
    ON section_summaries(document_id, level, sequence_start);

CREATE INDEX IF NOT EXISTS idx_section_summaries_document_created
    ON section_summaries(document_id, created_at DESC);

-- ============================================================================
-- Paper notes: the margin annotations that replaced the side chat.
--
-- One row is one question the reader asked about a specific place in the paper,
-- plus the answer, rendered as a card in the right margin beside its anchor.
--
-- Deliberately NOT stored in conversation_turns. A note is a different artifact:
-- it is anchored to a location, it is one Q+A pair rather than a rolling
-- transcript, and none of the conversation machinery (routing, compaction,
-- sub-threads) applies to it. Sharing that table would mean every note carried
-- five columns it never uses and appeared in the chat history endpoints.
-- ============================================================================

CREATE TABLE IF NOT EXISTS paper_notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Where the note hangs. anchor_sequence_id is the durable anchor and is
    -- what the reader positions by. anchor_chunk_id is a convenience that goes
    -- NULL if the paper is re-chunked. Keeping both means a re-chunk degrades
    -- the anchor's precision instead of destroying the note.
    anchor_chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
    anchor_sequence_id INTEGER NOT NULL,

    -- 'text'     the reader highlighted a passage, held in anchor_quote
    -- 'figure'   the reader picked a figure, located by anchor_image_path
    -- 'equation' the reader picked a math block: quote is its LaTeX, image its crop
    -- 'table'    the reader picked a whole table: quote is its transcription,
    --            image its crop (a selection inside a table is promoted to this)
    -- 'block'    no selection, so the note hangs off the block in view
    -- (no semicolons in these comments: the migration runner splits on them)
    anchor_kind TEXT NOT NULL DEFAULT 'text',
    anchor_quote TEXT,
    anchor_image_path TEXT,

    question TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',

    -- Which sequence_ids the model actually leaned on, so the card can offer
    -- "jump to §3.2" chips that scroll the article.
    cited_sequence_ids INTEGER[],
    -- 'whole' when the paper fitted in the context window, 'agent' when the
    -- SEARCH/READ loop ran. Surfaced in the card so the reader knows whether
    -- the answer saw everything or went looking.
    retrieval_mode TEXT,

    -- model: what the provider reported answering (shown on the card, so two
    -- notes asking the same question of different models can be compared).
    -- requested_model: what the reader actually picked. Kept separately
    -- because a provider may report a resolved variant, and because it is the
    -- authoritative value for follow-ups, which must stay on the model the
    -- note was started with.
    model TEXT,
    requested_model TEXT,

    -- Which margin the card sits in. Chosen automatically when the note is
    -- created (whichever side is less crowded at that anchor) and overridable
    -- per note, so a card can be moved off a figure it happens to cover.
    -- Ignored on windows too narrow for two gutters.
    margin_side TEXT NOT NULL DEFAULT 'right',

    -- The trail of tool calls that produced this answer: one entry per
    -- SECTION / SEARCH / READ / WEB call, carrying what was asked for, the
    -- model's stated reason, and a one-line summary of what came back.
    -- Persisted rather than only streamed, so a note reopened next week still
    -- shows how it was grounded instead of just what it concluded.
    agent_steps JSONB,

    -- Which surface owns this note.
    -- 'anchor'   a margin card beside the passage it is about (the default)
    -- 'document' asked about the paper as a whole, from the panel
    -- Document-scope notes still carry an anchor_sequence_id (the first block)
    -- because the column is NOT NULL, but nothing positions by it.
    scope TEXT NOT NULL DEFAULT 'anchor',

    -- Follow-ups chain off their parent so a note can become a short thread
    -- without leaving the margin.
    parent_note_id UUID REFERENCES paper_notes(id) ON DELETE CASCADE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_notes_document
    ON paper_notes(document_id, anchor_sequence_id, created_at);

CREATE INDEX IF NOT EXISTS idx_paper_notes_parent
    ON paper_notes(parent_note_id);

-- ============================================================================
-- Personal reading state: bookmarks, the reader's own notes, and decks.
--
-- These were localStorage-only, which made them per-browser: opening the same
-- paper from the LAN server on a tablet showed none of the marks made on the
-- desktop, and clearing site data destroyed them silently. They are small,
-- entirely per-document, and belong next to paper_notes.
--
-- Note the split from paper_notes. A personal note has no question, no model,
-- no citations, and no thread — sharing that table would mean carrying eight
-- unused columns and appearing in every endpoint that lists answers.
-- ============================================================================

-- A place worth coming back to. Several per paper.
CREATE TABLE IF NOT EXISTS reading_bookmarks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- The durable anchor, same convention as paper_notes: a sequence id
    -- survives a re-chunk, a chunk UUID does not.
    sequence_id INTEGER NOT NULL,

    -- Cached at save time so the bookmark list reads without loading the
    -- document. A re-chunk can make this stale, which is the right trade:
    -- a slightly wrong preview beats an empty one.
    snippet TEXT,
    -- 'text' | 'figure' | 'equation' | 'block', for labelling the row.
    kind TEXT NOT NULL DEFAULT 'block',
    page INTEGER,
    -- Scroll fraction when the mark was made, for the progress rail.
    progress REAL NOT NULL DEFAULT 0,
    -- Optional name the reader gave this mark.
    label TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One mark per block. The UI treats a second press on a bookmarked block
    -- as "actually, not here", so a duplicate is always a bug rather than an
    -- intent worth storing.
    UNIQUE (document_id, sequence_id)
);

CREATE INDEX IF NOT EXISTS idx_reading_bookmarks_document
    ON reading_bookmarks(document_id, sequence_id);

-- Something the reader wrote, anchored beside the passage that prompted it.
CREATE TABLE IF NOT EXISTS personal_notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    anchor_chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
    anchor_sequence_id INTEGER NOT NULL,
    anchor_quote TEXT,

    -- Markdown, rendered by the same pipeline as an answer.
    body TEXT NOT NULL,
    margin_side TEXT NOT NULL DEFAULT 'right',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_personal_notes_document
    ON personal_notes(document_id, anchor_sequence_id, created_at);

-- A stack of cards sharing one slot in the margin.
--
-- A deck owns nothing: it is an arrangement over notes that continue to exist
-- independently, so spreading one leaves every note untouched. What it buys is
-- vertical space, which is the gutter's scarce resource.
CREATE TABLE IF NOT EXISTS note_decks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    label TEXT,
    -- Which member is face-up.
    top_index INTEGER NOT NULL DEFAULT 0,
    margin_side TEXT NOT NULL DEFAULT 'right',
    -- Study mode hides each answer until the reader asks for it.
    study BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_note_decks_document
    ON note_decks(document_id, created_at);

-- Deck membership, in stacking order.
--
-- Two nullable foreign keys rather than one polymorphic id column, because
-- this buys real referential integrity: deleting a note removes it from its
-- deck automatically instead of leaving a dangling reference for the client
-- to notice and skip.
CREATE TABLE IF NOT EXISTS note_deck_members (
    deck_id UUID NOT NULL REFERENCES note_decks(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,

    ai_note_id UUID REFERENCES paper_notes(id) ON DELETE CASCADE,
    personal_note_id UUID REFERENCES personal_notes(id) ON DELETE CASCADE,

    PRIMARY KEY (deck_id, ordinal),

    -- Exactly one of the two is set. A member is either an answer or a note,
    -- never both and never neither.
    CHECK ((ai_note_id IS NULL) <> (personal_note_id IS NULL))
);

-- A card belongs to at most one deck, enforced here rather than trusted from
-- the client: the drag gesture that moves a card between decks is a delete
-- plus an insert, and a dropped first half would otherwise duplicate it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_deck_members_ai
    ON note_deck_members(ai_note_id) WHERE ai_note_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_deck_members_personal
    ON note_deck_members(personal_note_id) WHERE personal_note_id IS NOT NULL;
