# Database Design

## Purpose

The `database` directory owns PostgreSQL access, migrations, relational schema, transactions, repositories, and pgvector integration.

PostgreSQL is the canonical local data store. It runs locally through Docker Compose and stores documents, ordered structural chunks, extracted assets, embedding vectors, conversations, and `/ask` traces.

## Files

### `connection.py`

Creates PostgreSQL engine/session factories and verifies connectivity. It should own low-level connection lifecycle and keep driver details away from services and API handlers.

### `migrations.py`

Applies schema migrations idempotently and tracks database version upgrades.

### `schema.sql`

Documents the production schema. Core entities should include `documents`, `chunks`, `chunk_assets`, `chunk_embeddings`, `conversation_turns`, and `ask_traces`.

Recommended chunk identity:

```text
chunks
- id UUID PRIMARY KEY
- document_id UUID NOT NULL
- sequence_id INTEGER NOT NULL
- parent_sequence_id INTEGER NULL
- chunk_type TEXT NOT NULL
- heading_path TEXT[]
- markdown TEXT NOT NULL
- plain_text TEXT NOT NULL
- page_start INTEGER
- page_end INTEGER
- bbox_json JSONB
- token_count INTEGER
- created_at TIMESTAMPTZ NOT NULL

UNIQUE(document_id, sequence_id)
INDEX(document_id, sequence_id)
```

Recommended embedding shape:

```text
chunk_embeddings
- chunk_id UUID PRIMARY KEY REFERENCES chunks(id)
- embedding vector(VECTOR_DIMENSION) NOT NULL
- embedding_model TEXT NOT NULL
- created_at TIMESTAMPTZ NOT NULL
```

### `pgvector.py`

Encapsulates every pgvector operation, including vector index assumptions, embedding insertion, nearest-neighbor search, score normalization, and conversion from vector results back to chunk IDs.

### `transactions.py`

Provides transaction helpers for ingestion. Document metadata, ordered chunks, assets, and embedding job records should either commit together as a coherent state or fail without exposing partial documents.

### `repositories/documents.py`

Owns CRUD operations for document metadata and ingestion status.

### `repositories/chunks.py`

Owns chunk persistence and sequential retrieval. Critical lookups include chunk by ID, chunk by `document_id + sequence_id`, next chunk, previous chunk, and sequence windows.

### `repositories/embeddings.py`

Owns embedding metadata persistence and delegates vector operations to `database.pgvector`.

### `repositories/assets.py`

Stores metadata for extracted images, figures, tables, page snapshots, and layout artifacts.

### `repositories/conversations.py`

Stores chat turns, selected routing context, router reasons, citations, and model metadata.

## Data Model Rules

`chunks.sequence_id` is the source of truth for physical document order.

Vector search may return semantically relevant chunks, but it must not mutate or redefine the stored sequence order.

Sequential reveal should use relational queries such as:

```text
WHERE document_id = :document_id
  AND sequence_id > :current_sequence_id
ORDER BY sequence_id ASC
LIMIT 1
```

Global retrieval should use pgvector similarity against `chunk_embeddings.embedding`, then join back to `chunks` for text, sequence IDs, page spans, and citations.

## Data Dependencies

`database` is used by `extraction` through `services.ingestion` to persist ordered chunks, `embeddings` to store vectors, `services` to retrieve documents and chunks, `chat.global_context` for pgvector retrieval, and `chat.local_context` for current chunk lookup.

`database` should not call MinerU, Ollama, SearXNG, or FastAPI routers.
