import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "probe_chat_models.py"


def test_probe_help_lists_models_and_modes_without_connecting_to_providers():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert "--models MODELS" in result.stdout
    assert "--mode {simple,agent,both}" in result.stdout
    assert result.stderr == ""
