"""Queue isolation, legacy backlog consumption, and deployment coverage."""

import re
from pathlib import Path

import pytest
import yaml

import app.core.celery_app as celery_module
from app.core.config import Settings, settings
from app.core.celery_app import celery_app
from app.workers import reliability


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name,queue", [
    ("9xaipal.process_ingestion", "ingest"),
    ("9xaipal.reconstruct_reading_order", "ingest"),
    ("9xaipal.process_article_ingestion", "celery"),
    ("9xaipal.generate_article_thumbnail", "celery"),
    ("9xaipal.embed_document", "celery"),
    ("9xaipal.generate_section_summaries", "celery"),
    ("9xaipal.generate_figure_descriptions", "celery"),
])
def test_tasks_route_without_call_site_options(name, queue):
    route = celery_app.amqp.router.route({}, name, args=(), kwargs={})
    assert route["queue"].name == queue


def test_queues_declared_and_unknown_tasks_use_legacy_default():
    assert {queue.name for queue in celery_app.conf.task_queues} == {"ingest", "celery"}
    assert celery_app.amqp.router.route({}, "tests.future_task")["queue"].name == "celery"


def test_model_availability_refresh_settings_default_enabled_every_three_hours(
    monkeypatch,
):
    monkeypatch.delenv("ENABLE_MODEL_AVAILABILITY_REFRESH", raising=False)
    monkeypatch.delenv("MODEL_AVAILABILITY_REFRESH_HOURS", raising=False)

    configured = Settings(_env_file=None)

    assert configured.enable_model_availability_refresh is True
    assert configured.model_availability_refresh_hours == 3


def test_model_availability_refresh_beat_entry_uses_configured_interval(monkeypatch):
    monkeypatch.setattr(settings, "enable_model_availability_refresh", True)
    monkeypatch.setattr(settings, "model_availability_refresh_hours", 7)

    entry = celery_module._build_beat_schedule()["model-availability-refresh"]

    assert entry == {
        "task": "9xaipal.refresh_model_availability",
        "schedule": 7 * 60 * 60,
        "options": {"queue": "celery"},
    }


def test_disabled_model_availability_refresh_has_no_beat_entry(monkeypatch):
    monkeypatch.setattr(settings, "enable_model_availability_refresh", False)

    schedule = celery_module._build_beat_schedule()

    assert "model-availability-refresh" not in schedule
    assert "q1-stalled-jobs" in schedule
    assert "q1-daily-summary" in schedule


@pytest.mark.parametrize(
    ("worker_role", "enabled", "expected"),
    [("light", True, True), ("ingest", True, False), ("light", False, False)],
)
def test_model_availability_refresh_startup_dispatch_is_light_only_and_optional(
    monkeypatch, worker_role, enabled, expected
):
    from unittest.mock import Mock

    monkeypatch.setenv("WORKER_ROLE", worker_role)
    monkeypatch.setattr(settings, "enable_model_availability_refresh", enabled)
    send_task = Mock()
    monkeypatch.setattr(celery_app, "send_task", send_task)

    reliability.schedule_model_availability_refresh_on_worker_ready()

    if expected:
        send_task.assert_called_once_with(
            "9xaipal.refresh_model_availability", countdown=60, queue="celery"
        )
    else:
        send_task.assert_not_called()


def test_article_thumbnail_task_has_an_explicit_light_queue_route():
    assert celery_app.conf.task_routes["9xaipal.generate_article_thumbnail"] == {
        "queue": "celery"
    }


@pytest.mark.parametrize("compose_file,heavy_memory,heavy_concurrency", [
    ("docker-compose.prod.yml", "${WORKER_MEM_LIMIT:-7G}", "--concurrency=2"),
    ("docker-compose.yml", "${WORKER_MEM_LIMIT:-12G}", None),
])
def test_workers_consume_separate_queues(compose_file, heavy_memory, heavy_concurrency):
    services = yaml.safe_load((BACKEND_ROOT / compose_file).read_text())["services"]
    heavy = services["celery_worker"]
    light = services["celery_worker_light"]
    for worker, queue in ((heavy, "ingest"), (light, "celery")):
        assert re.search(rf"(?:^|\s)-Q\s+{queue}(?:\s|'|$)", worker["command"])
        assert "--hostname=celery@%h" in worker["command"]
        assert worker["healthcheck"]["test"][0] == "CMD-SHELL"
        assert "inspect ping" in worker["healthcheck"]["test"][1]
        assert "--destination celery@$$HOSTNAME" in worker["healthcheck"]["test"][1]
        assert worker["restart"] == "unless-stopped"
    assert heavy["environment"]["WORKER_ROLE"] == "ingest"
    assert light["environment"]["WORKER_ROLE"] == "light"
    light_only = {
        "WORKER_ROLE",
        "ENABLE_MODEL_AVAILABILITY_REFRESH",
        "MODEL_AVAILABILITY_REFRESH_HOURS",
    }
    assert {k: v for k, v in heavy["environment"].items() if k != "WORKER_ROLE"} == {
        k: v for k, v in light["environment"].items() if k not in light_only
    }
    assert "python -m app.extraction.arabic_capability &&" in heavy["command"]
    assert "arabic_capability" not in light["command"]
    assert light["container_name"] == "9xaipal-celery-worker-light"
    assert light["container_name"] != heavy["container_name"]
    assert "--concurrency=${LIGHT_WORKER_CONCURRENCY:-2}" in light["command"]
    if heavy_concurrency:
        assert heavy_concurrency in heavy["command"]
    else:
        assert "--concurrency" not in heavy["command"]
    assert heavy["deploy"]["resources"]["limits"]["memory"] == heavy_memory
    assert light["deploy"]["resources"]["limits"]["memory"] == "${LIGHT_WORKER_MEM_LIMIT:-2G}"
    for key in ("build", "volumes", "depends_on", "extra_hosts", "networks"):
        assert light.get(key) == heavy.get(key)


def test_backend_and_both_deploy_build_api_and_both_workers():
    script = (BACKEND_ROOT.parent / "scripts/deploy-once.sh").read_text()
    block = re.search(r"^  both\|backend\)\n(.*?)^    ;;", script, re.M | re.S)
    assert block is not None
    command = re.search(r"docker compose[^\n]*up -d --build ([^)\n]+)", block.group(1))
    assert command is not None
    assert command.group(1).split() == ["api", "celery_worker", "celery_worker_light"]


def test_queues_have_distinct_broker_routing_keys():
    # Sharing a direct exchange must not bind both queues to the same key.
    queues = celery_app.amqp.queues
    assert queues["ingest"].routing_key == "ingest"
    assert queues["celery"].routing_key == "celery"


def test_published_tasks_reach_only_the_selected_broker_queue():
    # Declare both queues first to catch accidental shared direct-exchange bindings.
    with celery_app.connection_for_write() as connection:
        channel = connection.default_channel
        client = channel.client
        for queue in celery_app.amqp.queues.values():
            queue.bind(channel).declare()
        client.delete("ingest", "celery")
        try:
            celery_app.send_task("9xaipal.process_ingestion", args=(), connection=connection)
            assert client.llen("ingest") == 1
            assert client.llen("celery") == 0
            celery_app.send_task("9xaipal.embed_document", args=(), connection=connection)
            assert client.llen("ingest") == 1
            assert client.llen("celery") == 1
        finally:
            client.delete("ingest", "celery")
