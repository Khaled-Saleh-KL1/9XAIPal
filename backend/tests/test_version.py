"""The version string lives in three places by necessity — app.core.version
(what the running app reports), pyproject.toml (Python tooling) and
frontend/package.json (npm) — and this is what stops them drifting apart."""

import json
import re
from pathlib import Path

from app.core.version import APP_VERSION

_ROOT = Path(__file__).resolve().parents[2]


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)


def test_pyproject_matches_app_version():
    pyproject = (_ROOT / "backend" / "pyproject.toml").read_text()
    assert re.search(rf'^version = "{re.escape(APP_VERSION)}"$', pyproject, re.M)


def test_package_json_matches_app_version():
    package = json.loads((_ROOT / "frontend" / "package.json").read_text())
    assert package["version"] == APP_VERSION


def test_readme_states_the_version():
    readme = (_ROOT / "README.md").read_text()
    assert f"**Version {APP_VERSION}**" in readme
