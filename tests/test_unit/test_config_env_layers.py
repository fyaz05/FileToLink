# tests/test_unit/test_config_env_layers.py
"""config.env.local must actually override config.env (documented precedence:
real environment > config.env.local > config.env)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_PROBE = "import Thunder.vars as v; print(int(v.Var.PRIVATE_MODE), v.Var.MAX_BATCH_FILES)"


def _run_in(tmp_path, extra_env=None):
    # These tests exercise the config-file layers themselves, so they must
    # opt back OUT of the conftest's THUNDER_SKIP_CONFIG_FILES hermeticity
    # switch before spawning the probe process.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PRIVATE_", "MAX_BATCH")) and k != "THUNDER_SKIP_CONFIG_FILES"
    }
    env.update(
        {
            "API_ID": "1",
            "API_HASH": "h",
            "BOT_TOKEN": "1:x",
            "BIN_CHANNEL": "-1",
            "DATABASE_URL": "mongodb://localhost/x",
            "OWNER_ID": "42",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=30,
    )


@pytest.mark.unit
def test_local_layer_overrides_base_layer(tmp_path):
    (tmp_path / "config.env").write_text("PRIVATE_MODE=True\nMAX_BATCH_FILES=5\n")
    (tmp_path / "config.env.local").write_text("PRIVATE_MODE=False\nMAX_BATCH_FILES=7\n")
    proc = _run_in(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0 7"  # local wins over base


@pytest.mark.unit
def test_base_layer_applies_without_local(tmp_path):
    (tmp_path / "config.env").write_text("PRIVATE_MODE=True\nMAX_BATCH_FILES=5\n")
    proc = _run_in(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1 5"


@pytest.mark.unit
def test_real_environment_beats_both_files(tmp_path):
    (tmp_path / "config.env").write_text("PRIVATE_MODE=True\n")
    (tmp_path / "config.env.local").write_text("PRIVATE_MODE=True\n")
    proc = _run_in(tmp_path, {"PRIVATE_MODE": "False"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0 50"  # os.environ still wins (default MAX_BATCH=50)
