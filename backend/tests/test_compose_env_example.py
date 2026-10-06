import re
from pathlib import Path

import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")
INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)[^}]*\}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def _strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _load_compose(name):
    document = yaml.safe_load((BACKEND_ROOT / name).read_text())
    assert isinstance(document, dict)
    return document


def _mentioned_environment_names():
    names = set()
    for line in (BACKEND_ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if line.startswith("#"):
            line = line[1:].strip()
        name, separator, _value = line.partition("=")
        if separator and ENV_NAME.fullmatch(name.strip()):
            names.add(name.strip())
    return names


def _active_environment_values():
    values = {}
    for line in (BACKEND_ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if separator and ENV_NAME.fullmatch(name.strip()):
            values[name.strip()] = value.split("#", 1)[0].strip()
    return values


def test_both_compose_files_only_read_variables_mentioned_in_env_example():
    compose_documents = [_load_compose(name) for name in COMPOSE_FILES]
    compose_variables = {
        match.group(1)
        for document in compose_documents
        for value in _strings(document)
        for match in INTERPOLATION.finditer(value)
    }
    missing = sorted(compose_variables - _mentioned_environment_names())

    assert not missing, f"Compose variables missing from backend/.env.example: {missing}"


def test_sample_and_production_compose_use_the_production_ingestion_defaults():
    sample = _active_environment_values()
    assert {
        "GENERATE_FIGURE_DESCRIPTIONS": "false",
        "MINERU_PAGE_BATCH_SIZE": "8",
        "INGEST_PROFILE": "full",
        "ALLOW_PYMUPDF_FALLBACK": "true",
        "DB_POOL_SIZE": "10",
        "DB_MAX_OVERFLOW": "15",
        "MAX_UPLOAD_SIZE_MB": "500",
    }.items() <= sample.items()

    production = _load_compose("docker-compose.prod.yml")["services"]
    assert production["celery_worker"]["environment"]["GENERATE_FIGURE_DESCRIPTIONS"] == "${GENERATE_FIGURE_DESCRIPTIONS:-false}"
    assert production["api"]["environment"]["GENERATE_FIGURE_DESCRIPTIONS"] == "${GENERATE_FIGURE_DESCRIPTIONS:-false}"
    assert production["celery_worker"]["environment"]["MINERU_PAGE_BATCH_SIZE"] == "${MINERU_PAGE_BATCH_SIZE:-8}"

    development = _load_compose("docker-compose.yml")["services"]
    assert development["celery_worker"]["environment"]["MINERU_PAGE_BATCH_SIZE"] == "${MINERU_PAGE_BATCH_SIZE:-100}"


def test_query_and_bulk_embedding_limits_reach_api_and_all_celery_workers():
    expected = {
        "QUERY_EMBEDDING_TIMEOUT_S": "${QUERY_EMBEDDING_TIMEOUT_S:-8}",
        "BULK_EMBEDDING_MAX_INFLIGHT": "${BULK_EMBEDDING_MAX_INFLIGHT:-1}",
        "BULK_EMBEDDING_BATCH_SIZE": "${BULK_EMBEDDING_BATCH_SIZE:-4}",
        "BULK_EMBEDDING_SEMAPHORE_TTL_S": "${BULK_EMBEDDING_SEMAPHORE_TTL_S:-360}",
    }

    for name in COMPOSE_FILES:
        services = _load_compose(name)["services"]
        for service_name in ("api", "celery_worker", "celery_worker_light"):
            environment = services[service_name]["environment"]
            assert {key: environment.get(key) for key in expected} == expected


def test_cloudflare_image_settings_reach_api_and_both_celery_workers():
    expected = {
        "CLOUDFLARE_AI_ACCOUNTS": "${CLOUDFLARE_AI_ACCOUNTS:-}",
        "CLOUDFLARE_IMAGE_MODELS": "${CLOUDFLARE_IMAGE_MODELS:-}",
    }

    assert {key: _active_environment_values().get(key) for key in expected} == {
        key: "" for key in expected
    }
    for name in COMPOSE_FILES:
        services = _load_compose(name)["services"]
        for service_name in ("api", "celery_worker", "celery_worker_light"):
            environment = services[service_name]["environment"]
            assert {key: environment.get(key) for key in expected} == expected


def test_model_availability_refresh_defaults_reach_the_light_worker():
    expected = {
        "ENABLE_MODEL_AVAILABILITY_REFRESH": "${ENABLE_MODEL_AVAILABILITY_REFRESH:-true}",
        "MODEL_AVAILABILITY_REFRESH_HOURS": "${MODEL_AVAILABILITY_REFRESH_HOURS:-3}",
    }

    assert {key: _active_environment_values().get(key) for key in expected} == {
        "ENABLE_MODEL_AVAILABILITY_REFRESH": "true",
        "MODEL_AVAILABILITY_REFRESH_HOURS": "3",
    }
    for name in COMPOSE_FILES:
        light = _load_compose(name)["services"]["celery_worker_light"]
        assert {key: light["environment"].get(key) for key in expected} == expected
