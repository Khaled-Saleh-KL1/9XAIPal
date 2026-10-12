# Schemas Design

## Purpose

The `schemas` directory contains Pydantic models for API inputs, API outputs, and internal DTO boundaries.

## Files

### `common.py`

Shared response types such as the health response.

### `documents.py`

`DocumentResponse` includes Arabic language/style/direction, classifier provenance,
`ocr_provider_summary`, and typed error/action fields. `ArabicWritingStyleConfirmation`
accepts only `printed` or `handwritten`; the endpoint's queueing semantics are in
the [API reference](../../../docs/03-reference/api.md#post-paperspaper_idarabic-writing-style).
Metadata remains nullable for older/imported documents.

Schemas for document upload responses, document metadata, ingestion state, and document deletion.

### `chat.py`

Schemas for `/ask`. Important response fields include `answer`, `context_type`, `router_reason`, `citations`, and `model`.

## Data Dependencies

`schemas` is imported by `api`, `services`, and `chat`.

`schemas` must not import from `database`, `llm`, or `extraction`.

