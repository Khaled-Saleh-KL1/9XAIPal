"""Rollback wiring and queue migration; Docker is faked, Redis is real."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import yaml

from _queue_test_helpers import python_subprocess_options, redis_test_url
from app.core.config import settings
from app.core.celery_app import celery_app

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_migrates_before_restoring_old_tree():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    rollback = next(s["run"] for s in steps if s.get("name") == "Automatic rollback to the last known-good commit")
    assert rollback.index('bash "$DEPLOY_DIR/scripts/rollback-celery-queues.sh"') < rollback.index("rsync -a --delete")
    assert rollback.index("rsync -a --delete") < rollback.index("DEPLOY_SCOPE=full")
    script = (ROOT / "scripts/deploy-once.sh").read_text()
    assert "up -d --build --remove-orphans)" in script


def test_shell_queue_move_preserves_order_payloads_and_reserved_messages(tmp_path):
    helper = ROOT / "scripts/rollback-celery-queues.sh"
    assert helper.exists()
    prefix = f"test-M2-rollback-{uuid4()}:"
    log = tmp_path / "docker.log"
    # The helper's actual Redis Lua executes against test Redis through redis-cli.
    docker = tmp_path / "docker"
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  inspect) exit 0 ;;
  stop|rm) exit 0 ;;
  exec) shift 2; exec "$@" ;;
  *) exit 1 ;;
esac
''')
    docker.chmod(0o755)
    # The test image has redis-py rather than redis-cli. Keep the helper's
    # shell/command path real, substituting only the local client executable.
    cli = tmp_path / "redis-cli"
    cli.write_text(f"#!{sys.executable}\n" + """import os, sys
import redis
client = redis.Redis.from_url(os.environ['M6_TEST_REDIS_URL'])
print(client.execute_command(*sys.argv[2:]))
""")
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", FAKE_DOCKER_LOG=str(log), CELERY_QUEUE_PREFIX=prefix, M6_TEST_REDIS_URL=settings.redis_url)
    with celery_app.connection_for_write() as connection:
        client = connection.default_channel.client
        keys = [prefix + key for key in ("ingest", "celery", "ingest\x06\x163", "celery\x06\x163", "unacked", "unacked_index")]
        payloads = [json.dumps({'body': body, 'properties': {'delivery_info': {'exchange': 'celery', 'routing_key': 'ingest'}, 'priority': 0}, 'headers': {'id': str(uuid4())}}).encode() for body in ('oldest\x00', 'middle', 'newest')]
        reserved = {"body": "unchanged", "properties": {"delivery_info": {"routing_key": "ingest"}}, "headers": {"id": str(uuid4())}}
        try:
            for payload in payloads:
                client.lpush(prefix + "ingest", payload)
            client.lpush(prefix + "celery", b"existing")
            client.lpush(prefix + "ingest\x06\x163", payloads[0])
            client.hset(prefix + "unacked", "heavy", json.dumps([reserved, "", "ingest"]))
            client.hset(prefix + "unacked", "light", json.dumps([reserved, "", "celery"]))
            client.zadd(prefix + "unacked_index", {"heavy": 1, "light": 2})
            subprocess.run(["bash", str(helper)], **python_subprocess_options(env=env), check=True)
            assert client.llen(prefix + "ingest") == 0
            assert client.rpop(prefix + "celery") == b"existing"
            for payload in payloads:
                expected = json.loads(payload)
                expected["properties"]["delivery_info"] = {"exchange": "celery", "routing_key": "celery"}
                assert json.loads(client.rpop(prefix + "celery")) == expected
            restored = json.loads(client.rpop(prefix + "celery"))
            expected = json.loads(json.dumps(reserved))
            expected["properties"]["delivery_info"] = {"exchange": "celery", "routing_key": "celery"}
            assert restored == expected
            assert json.loads(client.rpop(prefix + "celery\x06\x163"))["properties"]["delivery_info"]["routing_key"] == "celery"
            assert not client.hexists(prefix + "unacked", "heavy")
            assert client.hexists(prefix + "unacked", "light")
            assert client.zscore(prefix + "unacked_index", "heavy") is None
            subprocess.run(["bash", str(helper)], **python_subprocess_options(env=env), check=True)
            assert client.llen(prefix + "celery") == 0
            commands = log.read_text()
            assert commands.index("stop 9xaipal-api 9xaipal-celery-worker 9xaipal-celery-worker-light") < commands.index("rm -f 9xaipal-celery-worker-light") < commands.index("exec 9xaipal-redis")
        finally:
            client.delete(*keys)


def test_redis_error_reply_aborts_rollback_even_if_cli_exits_zero(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text('''#!/bin/sh
case "$1" in
  inspect) exit 1 ;;
  exec) echo 'ERR queue recovery failed'; exit 0 ;;
  *) exit 99 ;;
esac
''')
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/rollback-celery-queues.sh")],
        **python_subprocess_options(env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")),
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_migrated_delivery_restores_and_retries_on_pre_split_queue():
    from kombu import Connection, Exchange, Queue
    from celery import Celery
    import redis
    from celery.exceptions import Retry
    import pytest
    prefix = f"m3-restore-{uuid4()}:"
    broker = redis_test_url()
    lua = (ROOT / "scripts/rollback-celery-queues.sh").read_text().split("<<'LUA'\n")[1].split("\nLUA")[0]
    old = Celery("pre-split", broker=broker)
    old.conf.update(broker_transport_options={"global_keyprefix": prefix}, task_default_queue="celery", task_queues=(Queue("celery", Exchange("celery"), routing_key="celery"),))
    @old.task(bind=True, name="m3.retry", max_retries=1)
    def retried(self, value):
        raise self.retry(countdown=0)
    with Connection(broker, transport_options={"global_keyprefix": prefix}) as connection:
        channel = connection.default_channel
        client = redis.Redis.from_url(broker)
        for name in ("celery", "ingest"):
            Queue(name, Exchange("celery"), routing_key=name).bind(channel).declare()
        try:
            # Both queued and reserved ingress must have their routes repaired.
            for reserved in (False, True):
                old.send_task("m3.retry", args=["payload"], queue="ingest", connection=connection)
                if reserved:
                    assert channel.basic_get("ingest", no_ack=False) is not None
                client.eval(lua, 0, prefix)
                assert not any(binding.endswith(b"\x06\x16ingest") for binding in client.smembers(prefix + "_kombu.binding.celery"))
                message = channel.basic_get("celery", no_ack=False)
                assert message.delivery_info["routing_key"] == "celery"
                channel.qos.restore_by_tag(message.delivery_tag)
                assert client.llen(prefix + "ingest") == 0
                assert client.llen(prefix + "celery") == 1
                restored = channel.basic_get("celery", no_ack=False)
                retried.push_request(id=restored.headers["id"], args=["payload"], kwargs={}, retries=0, delivery_info=restored.delivery_info, called_directly=False)
                try:
                    with pytest.raises(Retry):
                        retried.run("payload")
                finally:
                    retried.pop_request()
                restored.ack()
                assert client.llen(prefix + "ingest") == 0
                retry = channel.basic_get("celery", no_ack=False)
                assert retry.headers["retries"] == 1
                assert retry.delivery_info["routing_key"] == "celery"
                retry.ack()
        finally:
            keys = list(client.scan_iter(prefix + "*"))
            if keys:
                client.delete(*keys)
