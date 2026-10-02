"""Rollback wiring and queue migration; Docker is faked, Redis is real."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import yaml

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
    cli.write_text(f"#!{sys.executable}\n" + """import sys
import redis
client = redis.Redis(host='host.docker.internal', port=55440, db=0)
print(client.execute_command(*sys.argv[2:]))
""")
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", FAKE_DOCKER_LOG=str(log), CELERY_QUEUE_PREFIX=prefix)
    with celery_app.connection_for_write() as connection:
        client = connection.default_channel.client
        keys = [prefix + key for key in ("ingest", "celery", "ingest\x06\x163", "celery\x06\x163", "unacked", "unacked_index")]
        payloads = [b'{"body":"oldest\\u0000"}', b'\x00binary\xff', b'{"body":"newest"}']
        reserved = {"body": "unchanged", "properties": {"delivery_info": {"routing_key": "ingest"}}, "headers": {"id": str(uuid4())}}
        try:
            for payload in payloads:
                client.lpush(prefix + "ingest", payload)
            client.lpush(prefix + "celery", b"existing")
            client.lpush(prefix + "ingest\x06\x163", b"priority")
            client.hset(prefix + "unacked", "heavy", json.dumps([reserved, "", "ingest"]))
            client.hset(prefix + "unacked", "light", json.dumps([reserved, "", "celery"]))
            client.zadd(prefix + "unacked_index", {"heavy": 1, "light": 2})
            subprocess.run(["bash", str(helper)], env=env, check=True)
            assert client.llen(prefix + "ingest") == 0
            assert [client.rpop(prefix + "celery") for _ in range(4)] == [b"existing", *payloads]
            restored = json.loads(client.rpop(prefix + "celery"))
            assert restored == reserved
            assert client.rpop(prefix + "celery\x06\x163") == b"priority"
            assert not client.hexists(prefix + "unacked", "heavy")
            assert client.hexists(prefix + "unacked", "light")
            assert client.zscore(prefix + "unacked_index", "heavy") is None
            subprocess.run(["bash", str(helper)], env=env, check=True)
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
        env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}"),
        capture_output=True, text=True,
    )
    assert result.returncode != 0
