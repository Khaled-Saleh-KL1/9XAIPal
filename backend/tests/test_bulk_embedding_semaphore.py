"""Redis-backed tests for bulk embedding permits and worker integration."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import redis
from pydantic import ValidationError

from _queue_test_helpers import redis_test_url
from app.core.config import Settings, settings
from app.embeddings import model, service_sync


@pytest.fixture
def redis_client():
    client = redis.Redis.from_url(redis_test_url(), decode_responses=True)
    client.ping()
    yield client
    client.close()


def _permit_factory():
    permit = getattr(service_sync, "bulk_embedding_permit", None)
    assert callable(permit), "service_sync must use the shared bulk embedding permit"
    return permit


def test_bulk_embedding_settings_defaults_and_validate_positive_values():
    config = Settings(_env_file=None)

    assert getattr(config, "bulk_embedding_max_inflight", None) == 1
    assert getattr(config, "bulk_embedding_batch_size", None) == 4
    assert getattr(config, "bulk_embedding_semaphore_ttl_s", None) == 360

    with pytest.raises(ValidationError):
        Settings(_env_file=None, bulk_embedding_max_inflight=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bulk_embedding_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bulk_embedding_semaphore_ttl_s=0)


def test_two_bulk_workers_never_exceed_the_redis_permit_limit(redis_client):
    permit = _permit_factory()
    prefix = f"bulk-embedding-test:{uuid4()}"
    start = threading.Barrier(3)
    lock = threading.Lock()
    active = 0
    peak = 0
    failures = []

    def worker():
        nonlocal active, peak
        try:
            start.wait(timeout=2)
            with permit(
                redis_client=redis_client,
                key_prefix=prefix,
                max_inflight=1,
                ttl_s=0.15,
                poll_interval_s=0.005,
            ):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.4)  # longer than TTL: the live lease must renew
                with lock:
                    active -= 1
        except Exception as exc:  # surfaced in the parent test thread below
            failures.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert peak == 1
    assert active == 0


def test_unrenewed_lease_expires_and_another_worker_can_acquire(redis_client):
    permit = _permit_factory()
    prefix = f"bulk-embedding-test:{uuid4()}"
    first_slot = f"{prefix}:slot:0"
    assert redis_client.set(first_slot, "crashed-worker", nx=True, px=80)

    started = time.monotonic()
    with permit(
        redis_client=redis_client,
        key_prefix=prefix,
        max_inflight=1,
        ttl_s=0.3,
        poll_interval_s=0.005,
    ):
        acquired_after = time.monotonic() - started
        assert redis_client.get(first_slot) != "crashed-worker"

    assert acquired_after >= 0.06


def test_query_embedding_does_not_wait_for_an_occupied_bulk_permit(
    redis_client, monkeypatch,
):
    permit = _permit_factory()
    monkeypatch.setattr(settings, "redis_url", redis_test_url())
    embed = AsyncMock(return_value=[0.1, 0.2])
    monkeypatch.setattr(model, "get_embedding", embed)

    pool = ThreadPoolExecutor(max_workers=1)
    blocked = False
    with permit(
        redis_client=redis_client,
        key_prefix="9xaipal:bulk-embedding",
        max_inflight=1,
        ttl_s=1,
        poll_interval_s=0.005,
    ):
        future = pool.submit(
            lambda: asyncio.run(model.get_query_embedding("query stays interactive"))
        )
        try:
            result = future.result(timeout=0.2)
        except TimeoutError:
            blocked = True

    if blocked:
        result = future.result(timeout=1)
    pool.shutdown(wait=True)

    assert not blocked
    assert result == [0.1, 0.2]
    assert embed.await_count == 1


def test_redis_failure_fails_bulk_embedding_closed():
    permit = _permit_factory()

    class UnavailableRedis:
        def set(self, *_args, **_kwargs):
            raise redis.ConnectionError("redis unavailable")

    with pytest.raises(RuntimeError, match="Redis"):
        with permit(
            redis_client=UnavailableRedis(),
            key_prefix=f"bulk-embedding-test:{uuid4()}",
            max_inflight=1,
            ttl_s=1,
            poll_interval_s=0.005,
        ):
            pytest.fail("bulk embedding must not run when Redis is unavailable")


def test_chunk_batch_request_runs_inside_the_bulk_permit(monkeypatch):
    events = []

    @contextmanager
    def recording_permit():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    chunks = [
        {"id": str(index), "plain_text": f"chunk {index}", "chunk_type": "text"}
        for index in range(5)
    ]
    cursor = 0

    def get_chunks(_session, _document_id, limit, **_kwargs):
        nonlocal cursor
        batch = chunks[cursor:cursor + limit]
        cursor += len(batch)
        return batch

    def embed(texts):
        events.append(f"request:{len(texts)}")
        return [[0.1] for _ in texts]

    monkeypatch.setattr(service_sync, "bulk_embedding_permit", recording_permit, raising=False)
    monkeypatch.setattr(service_sync, "get_chunks_without_embeddings_sync", get_chunks)
    monkeypatch.setattr(service_sync, "get_embeddings_batch_sync", embed)
    monkeypatch.setattr(service_sync, "active_embedding_model_sync", lambda: "test-model")
    monkeypatch.setattr(service_sync, "_persist_batch", lambda *_args: None)
    monkeypatch.setattr(
        service_sync, "settings",
        SimpleNamespace(
            bulk_embedding_batch_size=4,
            embedding_max_concurrency=1,
            contextual_embeddings_arabic_enabled=False,
            embed_max_chars=2000,
        ),
    )

    embedded = service_sync.embed_document_chunks_sync(object(), uuid4())

    assert embedded == 5
    assert events == ["enter", "request:4", "exit", "enter", "request:1", "exit"]
