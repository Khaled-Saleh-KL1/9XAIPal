"""Keep runtime data out of images without hiding Dockerfile inputs."""

import fnmatch
import json
import os
import re
import shlex
from functools import lru_cache
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _normalize_ignore_pattern(pattern):
    parts = []
    for part in pattern.replace("\\", "/").strip("/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _ignore_rules(text=None):
    if text is None:
        ignore_file = BACKEND_ROOT / ".dockerignore"
        text = ignore_file.read_text() if ignore_file.exists() else ""

    rules = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        exception = line.startswith("!")
        pattern = _normalize_ignore_pattern(line[1:] if exception else line)
        # Docker treats a lone '.' as a no-op for historical reasons.
        if not pattern or pattern == ".":
            continue
        rules.append(("!" if exception else "") + pattern)
    return rules


@lru_cache(maxsize=None)
def _dockerignore_pattern_matches(pattern, path):
    pattern_parts = tuple(pattern.split("/"))
    path_parts = tuple(path.split("/"))

    @lru_cache(maxsize=None)
    def match(pattern_index, path_index):
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)

        component = pattern_parts[pattern_index]
        if component == "**":
            return match(pattern_index + 1, path_index) or (
                path_index < len(path_parts) and match(pattern_index, path_index + 1)
            )

        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], component)
            and match(pattern_index + 1, path_index + 1)
        )

    return match(0, 0)


def _is_ignored(path, rules):
    normalized = _normalize_ignore_pattern(str(path))
    if not normalized:
        return False

    parts = normalized.split("/")
    prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    ignored = False

    # Docker uses the last matching rule. Checking each parent makes a matched
    # directory rule apply to all of that directory's descendants.
    for rule in rules:
        exception = rule.startswith("!")
        pattern = rule[1:] if exception else rule
        if any(_dockerignore_pattern_matches(pattern, prefix) for prefix in prefixes):
            ignored = not exception
    return ignored


def _continued(line, escape):
    stripped = line.rstrip()
    count = 0
    for char in reversed(stripped):
        if char != escape:
            break
        count += 1
    return count % 2 == 1


def _heredoc_markers(instruction, operands):
    if instruction not in {"RUN", "COPY", "ADD"}:
        return []

    markers = []
    quote = None
    index = 0
    while index < len(operands):
        char = operands[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if operands.startswith("<<<", index):
            index += 3
            continue
        if not operands.startswith("<<", index):
            index += 1
            continue

        index += 2
        strip_tabs = index < len(operands) and operands[index] == "-"
        if strip_tabs:
            index += 1
        while index < len(operands) and operands[index].isspace():
            index += 1

        if index < len(operands) and operands[index] in {'"', "'"}:
            delimiter_quote = operands[index]
            index += 1
            start = index
            while index < len(operands) and operands[index] != delimiter_quote:
                index += 1
            marker = operands[start:index]
            if index < len(operands):
                index += 1
        else:
            start = index
            while index < len(operands) and operands[index] not in " \t\r\n;|&()<>":
                index += 1
            marker = operands[start:index]

        if marker:
            markers.append((marker, strip_tabs))
    return markers


def _logical_dockerfile_lines(dockerfile):
    lines = dockerfile.read_text().splitlines()
    escape = "\\"
    for line in lines:
        directive = re.match(r"^\s*#\s*escape\s*=\s*([\\`])\s*$", line, re.IGNORECASE)
        if directive:
            escape = directive.group(1)
            break
        if line.strip() and not line.lstrip().startswith("#"):
            break

    logical_lines = []
    pending = []
    start_line = None
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        stripped = line.strip()
        if not pending and (not stripped or line.lstrip().startswith("#")):
            line_index += 1
            continue
        if start_line is None:
            start_line = line_index + 1

        fragment = line.rstrip()
        if _continued(fragment, escape):
            pending.append(fragment[:-1].strip())
            line_index += 1
            continue

        pending.append(fragment.strip())
        logical_line = " ".join(part for part in pending if part)
        logical_lines.append((start_line, line_index + 1, logical_line))
        pending = []
        start_line = None
        instruction_parts = logical_line.split(None, 1)
        if len(instruction_parts) == 2:
            instruction, operands = instruction_parts
            markers = _heredoc_markers(instruction.upper(), operands)
            for marker, strip_tabs in markers:
                line_index += 1
                while line_index < len(lines):
                    body_line = lines[line_index]
                    line_index += 1
                    candidate = body_line.lstrip("\t") if strip_tabs else body_line
                    if candidate == marker:
                        break
            if markers:
                continue
        line_index += 1

    if pending:
        logical_lines.append((start_line, len(lines), " ".join(part for part in pending if part)))
    return logical_lines


def _instruction_tokens(operands):
    if operands.lstrip().startswith("["):
        tokens = json.loads(operands)
        if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
            raise AssertionError(f"expected a JSON string array in Dockerfile instruction: {operands}")
        return tokens
    return shlex.split(operands)


def _shell_word_end(text, start):
    quote = None
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "\\":
            index += 2
            continue
        elif char.isspace():
            break
        index += 1
    return index


def _leading_options(operands):
    """Split leading Dockerfile flags from operands without consuming JSON text."""
    options = []
    value_options = {"--chown", "--chmod", "--exclude", "--from", "--checksum"}
    index = 0
    while index < len(operands):
        while index < len(operands) and operands[index].isspace():
            index += 1
        if index >= len(operands):
            break

        end = _shell_word_end(operands, index)
        word = operands[index:end]
        parsed = shlex.split(word)
        if len(parsed) != 1 or not parsed[0].startswith("--"):
            break

        option = parsed[0]
        options.append(option)
        index = end
        option_name, separator, _ = option.partition("=")
        if not separator and option_name in value_options:
            value_start = index
            while value_start < len(operands) and operands[value_start].isspace():
                value_start += 1
            value_end = _shell_word_end(operands, value_start)
            if value_start < value_end:
                value = shlex.split(operands[value_start:value_end])
                if len(value) == 1:
                    options.append(option_name + "=" + value[0])
                    index = value_end

    return options, operands[index:].lstrip()


def _copy_add_sources(instruction, operands):
    options, operand_text = _leading_options(operands)
    if any(option.partition("=")[0] == "--from" for option in options):
        return []
    # A COPY/ADD heredoc creates its source from the Dockerfile body instead
    # of reading a path from the build context.
    if operand_text.startswith("<<"):
        return []

    tokens = _instruction_tokens(operand_text)
    if not tokens:
        return []
    if len(tokens) < 2:
        return []

    sources = []
    for source in tokens[:-1]:
        if instruction == "ADD" and source.lower().startswith(
            ("http://", "https://", "git://", "ssh://", "git@")
        ):
            continue
        sources.append(source)
    return sources


def _bind_mount_sources(operands):
    tokens = shlex.split(operands)
    sources = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        mount = None
        if token.startswith("--mount="):
            mount = token.partition("=")[2]
            index += 1
        elif token == "--mount" and index + 1 < len(tokens):
            mount = tokens[index + 1]
            index += 2
        elif token.startswith("--"):
            index += 1
            if "=" not in token and index < len(tokens) and not tokens[index].startswith("--"):
                index += 1
            continue
        else:
            break

        options = {}
        for field in mount.split(","):
            key, separator, value = field.partition("=")
            options[key] = value if separator else ""
        if options.get("type", "bind") != "bind" or options.get("from"):
            continue
        sources.append(options.get("source") or options.get("src") or ".")
    return sources


def _copy_sources(dockerfile):
    """Return context sources used by COPY, ADD, and RUN bind mounts."""
    sources = []
    for line_number, _, logical_line in _logical_dockerfile_lines(dockerfile):
        instruction_parts = logical_line.split(None, 1)
        if len(instruction_parts) != 2:
            continue
        instruction, operands = instruction_parts
        instruction = instruction.upper()
        if instruction in {"COPY", "ADD"}:
            found = _copy_add_sources(instruction, operands)
        elif instruction == "RUN":
            found = _bind_mount_sources(operands)
        else:
            continue

        sources.extend((line_number, source) for source in found)
    return sources


def _context_files():
    """List files visible in this build context, excluding generated runtime data."""
    files = []
    for current, directories, filenames in os.walk(BACKEND_ROOT):
        relative_directory = Path(current).relative_to(BACKEND_ROOT)
        directories[:] = [
            name
            for name in directories
            if name not in {".git", ".pytest_cache", ".venv", "__pycache__"}
            and not (relative_directory.as_posix() == "app" and name == "storage")
        ]

        for filename in filenames:
            if filename.endswith((".log", ".pyc")) or filename == ".env" or filename.startswith(".env."):
                continue
            files.append((relative_directory / filename).as_posix())
    return sorted(files)


def _normalize_source(source):
    if "$" in source:
        raise AssertionError(f"Dockerfile context source must be statically resolvable: {source}")

    parts = []
    for part in source.replace("\\", "/").lstrip("/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) or "."


@lru_cache(maxsize=None)
def _source_pattern_matches(pattern, candidate):
    pattern_parts = pattern.split("/") if pattern != "." else []
    candidate_parts = candidate.split("/") if candidate != "." else []
    return len(pattern_parts) == len(candidate_parts) and all(
        fnmatch.fnmatchcase(value, source_part)
        for source_part, value in zip(pattern_parts, candidate_parts)
    )


def _context_files_from_source(source, context_files):
    normalized = _normalize_source(source)
    if normalized == ".":
        return sorted(context_files)

    matched = []
    for path in context_files:
        parts = path.split("/")
        prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
        if any(_source_pattern_matches(normalized, prefix) for prefix in prefixes):
            matched.append(path)
    return matched


def _is_runtime_storage(path):
    return path == "app/storage" or path.startswith("app/storage/")


def _ignored_dockerfile_inputs(dockerfiles, context_files, rules):
    ignored = []
    for dockerfile in dockerfiles:
        for line_number, source in _copy_sources(dockerfile):
            normalized_source = _normalize_source(source)
            if _is_ignored(normalized_source, rules):
                ignored.append(
                    f"{dockerfile.name}:{line_number} source {source} is ignored"
                )

            for path in _context_files_from_source(normalized_source, context_files):
                # Storage is mounted at runtime and is deliberately never baked in.
                if _is_runtime_storage(path):
                    continue
                if _is_ignored(path, rules):
                    ignored.append(
                        f"{dockerfile.name}:{line_number} source {source} needs {path}"
                    )
    return ignored


def _assert_dockerfile_inputs_available(dockerfiles, context_files, rules):
    inputs = [
        (dockerfile.name, line_number, source)
        for dockerfile in dockerfiles
        for line_number, source in _copy_sources(dockerfile)
    ]
    assert inputs, "expected local context inputs in backend Dockerfiles"

    ignored_inputs = _ignored_dockerfile_inputs(dockerfiles, context_files, rules)
    assert not ignored_inputs, "Dockerfile build inputs are ignored: " + "; ".join(ignored_inputs)


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

    _assert_dockerfile_inputs_available(dockerfiles, _context_files(), rules)


def test_dockerignore_supports_recursive_globs_and_last_match_wins():
    assert _is_ignored("app/nested/main.pyc", ["**/*.pyc"])
    assert not _is_ignored("app/main.py", ["app/**", "!app/main.py"])
    assert _is_ignored("app/main.py", ["!app/main.py", "app/**"])
    assert not _is_ignored("nested/main.py", ["*.py"])


def test_dockerfile_inputs_include_add_continuations_and_bind_mounts(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch\n"
        "COPY \\\n"
        "  --chown=1:1 app ./app\n"
        "ADD config/manifest.json /etc/app/manifest.json\n"
        "RUN --mount=type=bind,source=scripts,target=/src \\\n"
        "  python -m compileall /src\n"
        "RUN --mount=type=cache,target=/root/.cache uv sync\n"
        "COPY --from=builder /src/app /app\n"
        "ADD https://example.invalid/config.json /tmp/config.json\n"
    )

    assert _copy_sources(dockerfile) == [
        (2, "app"),
        (4, "config/manifest.json"),
        (5, "scripts"),
    ]


def test_guard_rejects_an_ignored_tracked_file_under_a_directory_source(tmp_path):
    dockerfile = tmp_path / "Dockerfile.lite"
    dockerfile.write_text("FROM scratch\nCOPY app ./app\n")
    original_ignore_file = BACKEND_ROOT / ".dockerignore"
    rules = _ignore_rules(
        original_ignore_file.read_text() + "\napp/main.py\n"
    )

    with pytest.raises(AssertionError, match="app/main.py"):
        _assert_dockerfile_inputs_available([dockerfile], ["app/main.py"], rules)


@pytest.mark.parametrize(
    ("instruction", "options"),
    [
        (
            "COPY",
            "--chown=1000:1000 --chmod=755 --link --parents "
            "--exclude=*.tmp --future-flag=value",
        ),
        (
            "ADD",
            "--chown=1000:1000 --chmod=755 --link "
            "--exclude=*.tmp --future-flag=value",
        ),
    ],
)
def test_json_copy_add_options_still_expose_ignored_context_sources(
    tmp_path, instruction, options
):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch\n"
        f"{instruction} {options} [\\\n"
        '  "app/main.py", \\\n'
        '  "/app/main.py" \\\n'
        "]\n"
    )

    assert _copy_sources(dockerfile) == [(2, "app/main.py")]
    with pytest.raises(AssertionError, match="app/main.py"):
        _assert_dockerfile_inputs_available(
            [dockerfile], ["app/main.py"], _ignore_rules("app/main.py")
        )


def test_json_copy_from_stage_is_not_a_context_input(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch AS builder\n"
        'COPY --from=builder ["/app/main.py", "/app/main.py"]\n'
    )

    assert _copy_sources(dockerfile) == []


def test_dockerfile_heredoc_bodies_are_not_parsed_as_instructions(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch\n"
        "RUN <<-EOF\n"
        "\tCOPY app/fake.py /fake.py\n"
        "\tRUN --mount=type=bind,source=secret,target=/src cat /src/key\n"
        "\tEOF\n"
        "COPY <<'INLINE' /generated.txt\n"
        "ADD scripts/fake.py /fake.py\n"
        "INLINE\n"
        "COPY --chown=1000:1000 app/main.py /app/main.py\n"
    )

    assert _copy_sources(dockerfile) == [(9, "app/main.py")]


def test_multiple_dockerfile_heredocs_do_not_hide_later_context_inputs(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch\n"
        "RUN cat <<FIRST <<SECOND\n"
        "COPY app/first.py /first.py\n"
        "FIRST\n"
        "ADD scripts/second.py /second.py\n"
        "SECOND\n"
        "COPY app/main.py /app/main.py\n"
    )

    assert _copy_sources(dockerfile) == [(7, "app/main.py")]


def test_run_here_string_does_not_hide_later_context_inputs(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch\n"
        'RUN cat <<<"literal"\n'
        "COPY app/main.py /app/main.py\n"
    )

    assert _copy_sources(dockerfile) == [(3, "app/main.py")]
