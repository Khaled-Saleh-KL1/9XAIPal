# Runtime Storage

## Purpose

The `storage` directory is the local runtime storage root.

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

