"""The two storage-cleanup passes in image_service.py: pruning the general
image-proxy cache by age, and sweeping research-image folders whose
conversation_id no longer has any conversation_turns row. Neither is wired to
document deletion — both are keyed to their own real lifecycle, see the
"Storage cleanup" section of image_service.py.
"""

import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.services.image_service import (
    prune_stale_proxy_cache,
    sweep_orphaned_research_images,
)


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    return tmp_path


async def _insert_turn(session, conversation_id):
    await session.execute(
        text(
            "INSERT INTO conversation_turns (conversation_id, role, content) "
            "VALUES (:cid, 'user', 'hi')"
        ),
        {"cid": conversation_id},
    )


async def test_sweep_removes_folders_with_no_matching_turns(storage_root, db_session):
    live_id = uuid4()
    orphan_id = uuid4()
    await _insert_turn(db_session, live_id)
    await db_session.commit()

    research_dir = storage_root / "images" / "research"
    (research_dir / str(live_id)).mkdir(parents=True)
    (research_dir / str(orphan_id)).mkdir(parents=True)
    (research_dir / str(orphan_id) / "photo.jpg").write_bytes(b"x")

    removed = await sweep_orphaned_research_images(db_session)

    assert removed == 1
    assert (research_dir / str(live_id)).exists()
    assert not (research_dir / str(orphan_id)).exists()


async def test_sweep_is_a_noop_when_nothing_is_orphaned(storage_root, db_session):
    live_id = uuid4()
    await _insert_turn(db_session, live_id)
    await db_session.commit()

    research_dir = storage_root / "images" / "research"
    (research_dir / str(live_id)).mkdir(parents=True)

    assert await sweep_orphaned_research_images(db_session) == 0
    assert (research_dir / str(live_id)).exists()


async def test_sweep_handles_missing_research_dir(storage_root, db_session):
    # ensure_storage_dirs() hasn't necessarily run against this tmp_path —
    # the sweep must not blow up just because the directory was never created.
    assert await sweep_orphaned_research_images(db_session) == 0


def test_prune_removes_only_files_older_than_cutoff(storage_root):
    cache_dir = storage_root / "images" / "proxy_cache"
    cache_dir.mkdir(parents=True)
    stale = cache_dir / "aaaa.jpg"
    fresh = cache_dir / "bbbb.jpg"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")

    old_time = time.time() - 40 * 86400
    os.utime(stale, (old_time, old_time))

    removed = prune_stale_proxy_cache(max_age_days=30)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_prune_is_a_noop_on_an_empty_cache(storage_root):
    assert prune_stale_proxy_cache(max_age_days=30) == 0
