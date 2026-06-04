# 9XAIPal: Technical Architecture & Deep Dive

This document provides a comprehensive, exhaustive technical reference for the 9XAIPal system. It covers the data models, pipeline orchestration, chat logic, and frontend implementation details.

---

## 1. System Topology

9XAIPal is a **single-tenant, local-first** application. All core components run on the user's local machine to ensure privacy, low latency, and zero per-token costs.

### Component Stack
*   **Frontend:** Vite + React (TypeScript), Tailwind CSS, KaTeX.
*   **Backend:** FastAPI (Python 3.11+), Pydantic v2, SQLAlchemy (Async).
*   **Task Queue:** Celery + Redis (for long-running ingestion and embedding tasks).
*   **Database:** PostgreSQL 16 with `pgvector` extension for semantic search.
*   **Inference Engines:**
    *   **Ollama:** Powers chat + VLM (`qwen3.5:cloud` or configurable) and Embeddings (`nomic-embed-text`).
    *   **MinerU 3.x:** High-fidelity PDF-to-Markdown extraction including LaTeX equations, table structures, and figures.
    *   **SearXNG:** Local metasearch engine for external web context.

---

## 2. Data Architecture

The system uses a relational schema in PostgreSQL, extended by `pgvector` for RAG capabilities.

### Core Schema (PostgreSQL)

#### `documents`
The root entity for every PDF.
*   `id` (UUID): Primary Key.
*   `status`: `queued`, `extracting`, `chunking`, `embedding`, `complete`, `failed`.
*   `reading_order` (JSONB): An LLM-corrected sequence of chunk IDs for complex multi-column layouts.
*   `extractor`: Stores which engine produced the data (`mineru` vs `pymupdf_fallback`).

#### `chunks`
The atomic unit of information.
*   `sequence_id` (INT): The physical order in the document.
*   `chunk_type`: `text`, `heading`, `table`, `figure`, `math`, `footnote`.
*   `heading_path` (TEXT[]): Hierarchical path.
*   `markdown` / `plain_text`: The extracted content.
*   `table_json` (JSONB): Structured data for tables.
*   `image_refs` (TEXT[]): Links to images stored on disk.

#### `chunk_embeddings`
*   `embedding` (vector(768)): 768-dimensional vector produced by `nomic-embed-text`.
*   `embedding_model`: The model used (for future migration support).

#### `chunk_assets`
*   Images extracted from MinerU output, linked back to chunks.

#### `section_summaries`
Pre-computed hierarchical overviews.
*   `level`: 0 (Executive Summary), 1 (H1), 2 (H2).
*   `summary_markdown`: LLM-generated summary of the section.
*   `source_chunk_ids`: UUIDs of chunks used to generate the summary (for citations).

#### `figure_descriptions`
VLM-generated technical descriptions of diagrams/architectures.
*   `description_markdown`: Detailed technical analysis of the image.
*   `model`: The VLM used (e.g., `qwen3.5:cloud`).

#### `conversation_turns`
*   `parent_turn_id` (UUID, nullable): FK to self for nested sub-thread support.

#### `ingestion_jobs`
*   `status`: `queued / extracting / chunking / embedding / summarizing / complete / failed`.

---

## 3. The Ingestion Pipeline

### Step-by-Step Orchestration (`app/extraction/pipeline_sync.py`)

1.  **Extraction (MinerU):** Invokes `mineru` via subprocess. Performs layout analysis, OCR (if needed), and formula recognition (LaTeX).
2.  **Structural Chunking:**
    *   Parses `content_list.json` from MinerU.
    *   Falls back to regex-based markdown chunking if MinerU's structured output is unavailable.
    *   Chunks are split by headings (H1, H2, H3).
3.  **Asset Management:**
    *   Extracted images are moved to `app/storage/images/`.
    *   Filenames are randomized and linked back to `chunks` via `chunk_assets`.
4.  **Embedding (Celery):**
    *   Dispatches `embed_document.delay()` to Celery.
    *   Batches of 20 chunks → Ollama `/api/embeddings`.
    *   Results stored in `chunk_embeddings`.
5.  **Summarization & VLM Analysis (Celery, background):**
    *   After embeddings complete, `generate_section_summaries.delay()` fires.
    *   Hierarchical section summaries → `section_summaries` table.
    *   VLM figure descriptions → `figure_descriptions` table.

---

## 4. Chat & Research Orchestration

The `/ask` endpoint (`app/chat/orchestrator.py`) is the most complex part of the system.

### Intent-Based Routing (`router.py`)

Every query is classified into one of five routes:
1.  **`LOCAL`:** Specific to the chunk on the user's screen. Includes multimodal support.
2.  **`GLOBAL`:** pgvector cosine similarity search across the entire document.
3.  **`OVERVIEW`:** Bypasses vector search; uses pre-computed `section_summaries`.
4.  **`EXTERNAL`:** Triggers SearXNG for web results.
5.  **`OUT_OF_SCOPE`:** Guardrail rejects non-IT/CS topics.

### Research Agent (`research_agent.py`)

For complex queries, an iterative research loop:
*   **Tools:** `web_search`, `read_paper_section`, `describe_figure`.
*   **State:** Maintains a temporary research log and synthesizes a final response.
*   **Triggered** when the model emits a `NEEDS_RESEARCH` signal.

### Sub-Threads

Turns can have `parent_turn_id` creating a tree of sub-threads for tangents.
Sub-threads default to paper-free context and use `get_thread_subtree()` for
history (recursive CTE, not full conversation).

### Compaction

Long conversations are automatically compacted: early turns summarized into
a single `role='compaction'` turn to prevent context overflow.

---

## 5. Frontend Architecture

### Key Views
*   **`LibraryView`:** Grid/List view with real-time polling of ingestion status.
*   **`ReadingView`:**
    *   Granular chunk-by-chunk reveal (headings, paragraphs, tables, figures).
    *   KaTeX integration for LaTeX math.
    *   Tracks which chunk is currently visible for LOCAL context.
*   **`ChatPane`:**
    *   Supports sub-threaded conversations (indented, tree-based).
    *   Inline paper figures via `SafeWebImage` component.
    *   Citation badges that jump to source chunks.

### State Management
*   Standard React `useState` and `useCallback` for local view state.
*   URL Hash-based routing (`#/paper/{id}`) for deep linking.

---

## 6. Infrastructure & Deployment

*   **Docker Compose:** Orchestrates `postgres` (pgvector), `redis`, `celery_worker`
    (built from `Dockerfile.mineru` with PyTorch CPU), `searxng`, and the `api` server.
*   **Storage Layout:**
    *   `backend/app/storage/documents/`: Original PDFs.
    *   `backend/app/storage/extracted/`: MinerU intermediate output.
    *   `backend/app/storage/images/`: Final optimized assets.
    *   `backend/app/storage/assets/`: Static PDF copies.

---

## 7. Future Extensibility Points

*   **Cross-Paper Search:** Extending GLOBAL search across the entire library.
*   **External Knowledge Graphs:** Integration with Zotero or Semantic Scholar APIs.
*   **Alternative Extractors:** Adding Docling or Marker as extraction backends.
*   **Multiple Model Backends:** Support for OpenAI-compatible APIs alongside Ollama.