# Storage & static files

> **What this is:** what lands on disk, where, and which URL serves it.
>
> **Owns:** the storage-root layout and the disk-path to authenticated API route mapping.
> **Does not own:** `STORAGE_ROOT` configuration ([configuration.md](configuration.md)).
>
> **Status:** current · **Source reconciled:** 2026-10-05 (48cb8c6). File-serving ownership and
> cover behavior were checked against source; this update did not contact a running service.
> **Verify with:** `ls -R backend/app/storage`
>
> Nothing under the storage root is served directly. Figures, PDFs and research images each go
> through an authenticated `/api/v1` route that checks ownership first (see
> [api.md](api.md#files)); raw MinerU output (`extracted/`) is never served. The old public
> `/static/*` mounts are gone — [docs/issues/001](../issues/001-public-static-files-bypass-authorization.md).

Everything that isn't in Postgres lives under the **storage root**:
configurable via `settings.storage_root` (default `app/storage`). Paths
are managed in [core/paths.py](../../backend/app/core/paths.py) and all
subdirectories are created at startup by `ensure_storage_dirs()`.

## Layout

```
<storage_root>/
├── documents/         # ingestion-side PDFs (named <uuid>.pdf)
├── extracted/         # raw MinerU output (markdown, JSON, intermediate images)
│   └── <doc_id>/...
├── images/            # curated, served chunk images
│   ├── <doc_id>/<asset_uuid>.png
│   └── research/<conv_id>/...   # research-agent images
├── assets/            # raw PDF copies for download (<doc_id>.pdf)
├── covers/            # PDF-page or generated article covers (<doc_id>.jpg) — a derived cache
└── logs/              # reserved
```

## Why each directory?

### `documents/<storage_uuid>.pdf`
The exact bytes the user uploaded, named with an internal UUID. This is
what `mineru -p` consumes.

### `extracted/<doc_id>/`
MinerU's working directory. The pipeline reads `content_list.json` and
`*.md` from here and copies images out into `images/`. Kept for debugging.

### `images/<doc_id>/<asset_uuid>.<ext>`
The **canonical** location for chunk-linked images. Two important
properties:

1. **The DB stores `file_path` relative to `images_dir()`**, e.g.
   `"<doc_id>/<asset_uuid>.png"`. The served URL is built from it at read time
   (`resolve_asset_url` → `/api/v1/papers/<doc_id>/assets/<file_path>`), so
   nothing in the database depends on where files are mounted.
2. **Filenames are randomized** to avoid collisions.

### `images/research/<conv_id>/...`
Images saved by the research agent during iterative research loops.

### covers/<doc_id>.jpg
For PDFs, this is a roughly 480px-wide raster of page one, created lazily by
PyMuPDF. For imported articles, it may instead be a generated cover created
asynchronously with Cloudflare Workers AI when configured. The task and
backfill procedure are in the [article thumbnail runbook](../runbooks/article-thumbnails.md).

This is derived cache data, not the source document. PDF page covers can be
rendered again from assets; generated article covers need the thumbnail task
and its configured providers to be available again. Removing the cache does
not remove the source document. Deleting a paper removes its cached cover.
### `assets/<doc_id>.pdf`
A second copy of the upload, keyed by document ID so URLs are
predictable. Used by:
- `GET /papers/{id}/raw`: `FileResponse` with `Content-Disposition`, ownership-checked.
  The PDF viewer loads this same URL (with credentials); there is no direct serving.

### `logs/`
Reserved for future structured logs. Not used at the moment.

## Serving files

`main.py` mounts nothing from the storage root. Each kind of file has one
authenticated endpoint, and each resolves the path *from a database row*
rather than from the URL alone:

| File | Route | Ownership check |
| --- | --- | --- |
| extracted figure / table / equation crop | `GET /api/v1/papers/{id}/assets/{file_path}` ([chunks.py](../../backend/app/api/v1/endpoints/chunks.py)) | document belongs to the caller **and** `file_path` names a `chunk_assets` row of that document; then the resolved path must stay below `images_dir()` |
| original PDF | `GET /api/v1/papers/{id}/raw` ([documents.py](../../backend/app/api/v1/endpoints/documents.py)) | document belongs to the caller |
| research-agent image | `GET /api/v1/media/research/{conversation_id}/{filename}` ([media.py](../../backend/app/api/v1/endpoints/media.py)) | a turn of that conversation belongs to the caller; `filename` must be a bare name |

⚠ The ownership check is what makes this safe, not the UUIDs in the paths. A
UUID is an identifier, not a credential: it appears in histories, exported
notes and browser logs. Before 2026-09-10 the whole root was a public
`StaticFiles` mount and a leaked path read another user's PDF with no login
([docs/issues/001](../issues/001-public-static-files-bypass-authorization.md)).

## URL conventions

The API serves private files through authenticated routes that check ownership:

| Resource | Route | Notes |
| --- | --- | --- |
| Chunk-linked figure, table, or equation crop | /api/v1/papers/<doc_id>/assets/<file_path> | The path must match a chunk_assets row for the owned document. |
| Original PDF | /api/v1/papers/<doc_id>/raw | Serves the owned PDF from assets, with a documents fallback. |
| Cover image | /api/v1/papers/<doc_id>/cover | Returns the generated article cache when present; otherwise renders a source PDF page. |
| Research-agent image | /api/v1/media/research/<conversation_id>/<filename> | The conversation must belong to the caller; the filename is a bare name. |

There is no public StaticFiles mount for storage. Vite retains a legacy /static
proxy rule in development, but current stored files use the authenticated API
routes above.

## What gets deleted, and when

| Action                          | Cleans                                                                 |
| ------------------------------- | ---------------------------------------------------------------------- |
| `DELETE /papers/{id}`           | DB cascade (chunks, embeddings, assets, summaries, jobs, descriptions); disk: `documents/<filename>`, `assets/<doc_id>`, `extracted/<doc_id>/`, `images/<doc_id>/`, and `covers/<doc_id>.jpg` (best effort). |
| Restart                         | Nothing; everything is idempotent.                                     |
| Pipeline failure mid-ingestion  | Job + document marked `failed`. Disk artifacts not cleaned automatically. |

## Sizing notes

Per paper, on disk:

- 1 raw PDF in `documents/` (~5–30 MB typical).
- 1 raw PDF in `assets/` (same bytes, duplicate cost).
- MinerU output in `extracted/` (~2–5 MB).
- Per-figure images in `images/` (~50–500 KB each).

For a few hundred papers this is fine. The duplication between
`documents/` and `assets/` can be eliminated by a symlink or small bridge
if it becomes a concern.