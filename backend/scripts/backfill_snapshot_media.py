"""Repair raw-HTML snapshots that were saved before the media fixes shipped.

`sanitize_html` gained three transforms (`_strip_layout_styles`,
`_upgrade_lazy_images`, `_make_videos_playable`) that run at ingestion time.
Snapshots are written ONCE, inline, when an article is imported
(extraction/pipeline_sync.py) — there is no re-crawl path — so every article
imported before those transforms shipped still has its original stored HTML
and still renders with dead videos and thumbnail-sized images.

This re-applies exactly those three transforms to the already-stored files.
It deliberately does NOT re-run the cleaner or re-fetch anything:

  - the stored HTML is already sanitized, and `_CLEANER` is not something to
    run twice for no reason;
  - re-fetching would give a *different* article (these pages change), which
    is not a repair — it silently swaps content the user has already read;
  - the attributes the fix needs are all still present in the stored file.
    That last point is what makes this possible at all: the lazy-loading
    `data-loading`/`data-srcset` payloads (which carry the full-size image
    URLs) survived the original sanitize, so the real 1000px source can be
    recovered from disk without going back to the network.

All three transforms are idempotent, so running this more than once is safe
and a second run reports 0 changes.

Usage (inside the api or worker container):
    python scripts/backfill_snapshot_media.py --dry-run
    python scripts/backfill_snapshot_media.py --apply
"""

import argparse
import shutil
import sys

from lxml import html as lxml_html

from app.core.paths import raw_snapshots_dir
from app.services.article_crawl import (
    _make_videos_playable,
    _strip_layout_styles,
    _upgrade_lazy_images,
)

BACKUP_SUFFIX = ".pre-media-backfill"


def _stats(html: str) -> dict:
    """The things the user actually sees, counted straight off the serialized
    HTML so the before/after numbers mean the same thing in the dry run and
    the applied run."""
    tree = lxml_html.fromstring(html)
    videos = tree.xpath("//video")
    imgs = tree.xpath("//img")
    return {
        "videos": len(videos),
        "videos_with_crossorigin": sum(1 for v in videos if v.get("crossorigin")),
        "videos_with_controls": sum(1 for v in videos if v.get("controls") is not None),
        "imgs": len(imgs),
        "imgs_with_lazy_payload": sum(
            1 for i in imgs if i.get("data-loading") or i.get("data-srcset") or i.get("data-src")
        ),
        "imgs_with_default_size": sum(
            1 for i in imgs if i.get("width") == "300" and i.get("height") == "150"
        ),
    }


def repair(html: str) -> str:
    tree = lxml_html.fromstring(html)
    _strip_layout_styles(tree)
    _upgrade_lazy_images(tree)
    _make_videos_playable(tree)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--document-id", help="repair only this document's snapshots")
    args = ap.parse_args()

    base = raw_snapshots_dir()
    if not base.exists():
        print(f"no snapshot directory at {base}")
        return 0

    dirs = (
        [base / args.document_id]
        if args.document_id
        else sorted(d for d in base.iterdir() if d.is_dir())
    )

    touched = unchanged = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.html")):
            original = f.read_text(encoding="utf-8")
            before = _stats(original)
            repaired = repair(original)
            if repaired == original:
                unchanged += 1
                continue
            after = _stats(repaired)
            touched += 1
            print(f"\n{d.name}/{f.name}")
            print(
                f"  videos {before['videos']}: crossorigin "
                f"{before['videos_with_crossorigin']} -> {after['videos_with_crossorigin']}, "
                f"controls {before['videos_with_controls']} -> {after['videos_with_controls']}"
            )
            print(
                f"  images {before['imgs']}: unresolved lazy payloads "
                f"{before['imgs_with_lazy_payload']} -> {after['imgs_with_lazy_payload']}, "
                f"bogus 300x150 {before['imgs_with_default_size']} -> "
                f"{after['imgs_with_default_size']}"
            )
            if args.apply:
                backup = f.with_suffix(f.suffix + BACKUP_SUFFIX)
                if not backup.exists():
                    shutil.copy2(f, backup)
                f.write_text(repaired, encoding="utf-8")
                print(f"  written (backup: {backup.name})")

    verb = "repaired" if args.apply else "would repair"
    print(f"\n{verb} {touched} file(s); {unchanged} already clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
