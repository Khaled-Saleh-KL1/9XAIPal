# Runtime Storage

## Purpose

The `storage` directory is the local runtime storage root.

Arabic OCR extraction publishes document-specific markdown, structured artifacts
and `ocr_manifest.json` beneath `extracted/`. These contain private document text,
page provenance and usage; do not commit them or put them in the public About,
sitemap or AI reference files. See the [storage reference](../../../docs/03-reference/storage.md)
and [Arabic runbook](../../../docs/runbooks/arabic-document-ocr.md).

It should be ignored by git except for this README and empty directory placeholders.

## Subdirectories

### `documents/`

Stores original uploaded PDFs.

### `extracted/`

Stores MinerU output, including markdown and intermediate files.

### `images/`

Stores extracted figures, diagrams, page images, and VLM-enriched assets.

### `logs/`

Stores local application logs.

## Data Dependencies

`core.paths` defines these paths.

`extraction.assets` writes files here.

`database.repositories.assets` stores references to files here.

The database stores paths and metadata; the filesystem stores binary assets.

