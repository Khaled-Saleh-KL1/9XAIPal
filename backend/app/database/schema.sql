-- 9XAIPal Database Schema

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
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

    -- Whether this document is a "book" (chapter-by-chapter reading navigation)
    -- or a "paper" (linear reading). Chosen by the user at upload time.
    doc_kind TEXT NOT NULL DEFAULT 'paper'
);

COMMENT ON COLUMN documents.reading_order IS 'Array of original chunk sequence_ids in LLM-corrected logical reading order. Used to fix two-column and complex layout extraction issues.';

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
    embedding vector(768) NOT NULL,
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

-- Conversation turns
-- Supports the nested sub-thread feature (tangents without polluting the main paper chat).
-- Main linear chat turns have parent_turn_id IS NULL.
-- Sub-thread turns have parent_turn_id pointing to their parent turn in the tree
-- (the branching user turn for the first continuation, or the previous turn for follow-ups).
CREATE TABLE IF NOT EXISTS conversation_turns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    context_type TEXT,
    router_reason TEXT,
    model TEXT,
    citations JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- NULL for all turns that belong to the main linear chat for a conversation_id.
    -- Non-NULL points to the parent turn this message is a reply to (supports
    -- arbitrary-depth nesting of tangents). The root of a sub-thread is the
    -- original user message that started the tangent (even though that user
    -- message itself has parent_turn_id = NULL so it stays visible in main chat).
    parent_turn_id UUID REFERENCES conversation_turns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation
    ON conversation_turns(conversation_id, created_at);

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
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status);

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
