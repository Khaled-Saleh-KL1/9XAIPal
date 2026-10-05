# Services Design

## Purpose

The `services` directory contains application workflows between API handlers and lower-level modules.

## Files

### `documents.py`

Owns document lifecycle behavior: create document records, read metadata, delete documents and local files, and report ingestion status.

### `chunks.py`

Owns sequential chunk behavior: fetch chunk by ID, fetch next chunk, fetch previous chunk, and fetch windows around the current sequence.

The next chunk lookup should use `document_id + current_chunk.sequence_id + 1` or the smallest greater `sequence_id` if sparse recovery is needed.

### `ingestion.py`

Owns transactional ingestion: create ingestion jobs, store document metadata, store ordered chunks, store assets, trigger embeddings, and mark documents complete.

### cloudflare_images.py

Optional Cloudflare Workers AI client for generated article covers. It owns account/model failover
and image-response validation; the worker task calls it only when an account is configured.

### covers.py

Owns the shared cover cache. PDF covers are lazily rasterized from the source PDF; a generated
article cover already in the cache is returned as-is. Both use the document-keyed covers directory.

### `retrieval.py`

Owns global retrieval: embed query, search pgvector, fetch chunk records, preserve similarity scores, and expose physical sequence IDs for citation and ordering.

## Data Dependencies

`services` depends on `database.repositories`, `database.transactions`, `database.pgvector` through repositories, `embeddings.service`, and `core.config`.

`api` depends on `services`.

`chat.global_context` depends on `services.retrieval`.
