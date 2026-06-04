# Storage & Static Files

Everything that isn't in Postgres lives under the **storage root** —
configurable via `settings.storage_root` (default `app/storage`). Paths
are managed in [core/paths.py](../backend/app/core/paths.py) and all
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
   `"<doc_id>/<asset_uuid>.png"`. This makes `/static/images/...` URLs
   stable across deployments.
2. **Filenames are randomized** to avoid collisions.

### `images/research/<conv_id>/...`
Images saved by the research agent during iterative research loops.

### `assets/<doc_id>.pdf`
A second copy of the upload, keyed by document ID so URLs are
predictable. Used by:
- `GET /papers/{id}/raw` — `FileResponse` with `Content-Disposition`.
- `GET /static/assets/{id}.pdf` — direct static serving.

### `logs/`
Reserved for future structured logs. Not used at the moment.

## Static mounts ([main.py](../backend/app/main.py))

```python
app.mount("/static/images",    StaticFiles(directory=images_dir(),    check_dir=False))
app.mount("/static/extracted", StaticFiles(directory=extracted_dir(), check_dir=False))
app.mount("/static/assets",    StaticFiles(directory=assets_dir(),    check_dir=False))
app.mount("/static/images/research", StaticFiles(directory=research_images_dir(), check_dir=False))
```

`check_dir=False` lets the mount succeed even before the directory exists.

## URL conventions

| URL                                       | Maps to                                         |
| ----------------------------------------- | ----------------------------------------------- |
| `/static/images/<doc_id>/<asset>.png`     | `<storage_root>/images/<doc_id>/<asset>.png`    |
| `/static/extracted/<doc_id>/...`          | `<storage_root>/extracted/<doc_id>/...`         |
| `/static/assets/<doc_id>.pdf`             | `<storage_root>/assets/<doc_id>.pdf`            |
| `/static/images/research/<conv_id>/<f>`   | `<storage_root>/images/research/<conv_id>/<f>`  |
| `/api/v1/papers/<doc_id>/raw`             | streams `assets/<doc_id>.pdf` or `documents/<filename>` (fallback) |

In dev, Vite proxies `/api` and `/static` to `:8000`.

## What gets deleted, and when

| Action                          | Cleans                                                                 |
| ------------------------------- | ---------------------------------------------------------------------- |
| `DELETE /papers/{id}`           | DB cascade (chunks, embeddings, assets, summaries, jobs, descriptions); disk: `documents/<filename>`, `assets/<doc_id>`, `extracted/<doc_id>/`, `images/<doc_id>/` (best effort). |
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