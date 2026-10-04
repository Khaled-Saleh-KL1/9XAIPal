"""Keep runtime data out of images without hiding Dockerfile inputs."""

import fnmatch
import json
import shlex
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _ignore_rules():
    ignore_file = BACKEND_ROOT / ".dockerignore"
    if not ignore_file.exists():
        return []
    return [
        line.strip()
        for line in ignore_file.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _is_ignored(path, rules):
    parts = Path(path).as_posix().strip("/").split("/")
    prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    ignored = False

    for rule in rules:
        exception = rule.startswith("!")
        pattern = rule[1:] if exception else rule
        pattern = pattern.strip("/")
        if pattern == ".":
            continue

        matches = any(
            fnmatch.fnmatchcase(prefix, pattern)
            or ("/" not in pattern and fnmatch.fnmatchcase(prefix.rsplit("/", 1)[-1], pattern))
            for prefix in prefixes
        )
        if matches:
            ignored = not exception

    return ignored


def _copy_sources(dockerfile):
    sources = []
    for line_number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
        instruction = line.strip()
        if not instruction.startswith("COPY "):
            continue

        operands = instruction[5:].strip()
        tokens = json.loads(operands) if operands.startswith("[") else shlex.split(operands)
        if not tokens:
            continue

        if any(token == "--from" or token.startswith("--from=") for token in tokens):
            continue

        while tokens and tokens[0].startswith("--"):
            option = tokens.pop(0)
            if option in {"--chown", "--chmod", "--exclude"} and tokens:
                tokens.pop(0)

        for source in tokens[:-1]:
            sources.append((line_number, source))

    return sources


def test_runtime_data_is_excluded_and_dockerfile_inputs_remain_available():
    rules = _ignore_rules()

    for runtime_path in (
        "app/storage/raw_snapshots/sample.html",
        "app/storage/extracted/.mineru-api-example/output.md",
        "app/storage/images/proxy_cache/example.jpg",
        "app/__pycache__/module.cpython-311.pyc",
        "app/module.pyc",
        ".pytest_cache/v/cache/nodeids",
        ".venv/bin/python",
        ".env",
        ".env.production",
        ".env.example",
        "tests/test_example.py",
        "logs/api.log",
    ):
        assert _is_ignored(runtime_path, rules), f"{runtime_path} must be excluded"

    dockerfiles = sorted(BACKEND_ROOT.glob("Dockerfile*"))
    assert dockerfiles, "expected backend Dockerfiles to be checked"

    copied_sources = [
        (dockerfile.name, line_number, source)
        for dockerfile in dockerfiles
        for line_number, source in _copy_sources(dockerfile)
    ]
    assert copied_sources, "expected local COPY sources in backend Dockerfiles"

    excluded_sources = [
        f"{name}:{line_number} COPY {source}"
        for name, line_number, source in copied_sources
        if _is_ignored(source, rules)
    ]
    assert not excluded_sources, "Dockerfile COPY inputs are ignored: " + ", ".join(excluded_sources)
