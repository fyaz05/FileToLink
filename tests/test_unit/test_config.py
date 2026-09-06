# tests/test_unit/test_config.py
"""M6: config validation surfaces all problems; booleans/sets parse."""

import subprocess
import sys
from pathlib import Path

import pytest

from Thunder.vars import Var, str_to_bool, str_to_int_set

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("Y", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
        ("junk", False),
    ],
)
def test_str_to_bool(raw, expected):
    assert str_to_bool(raw) is expected


@pytest.mark.unit
def test_str_to_int_set():
    import Thunder.vars as vars_mod

    assert str_to_int_set("") == set()
    assert str_to_int_set("-100111 -100222") == {-100111, -100222}
    before = len(vars_mod._config_errors)
    assert str_to_int_set("1 junk 2") == {1, 2}
    # junk is surfaced (M6 collect-all-errors), never silently skipped
    assert len(vars_mod._config_errors) == before + 1
    assert "junk" in vars_mod._config_errors[-1]


@pytest.mark.unit
def test_var_facade_has_new_knobs():
    # every new env knob from the plan exists with a safe default
    assert Var.PRIVATE_MODE is False
    assert Var.ENABLE_LEGACY_LINKS is True
    assert Var.ENABLE_SHELL is False
    assert Var.FILE_TTL_DAYS == 0
    assert Var.EXECUTOR_WORKERS >= 1
    assert Var.BATCH_WORKERS >= 1
    assert Var.BROADCAST_WORKERS >= 1
    assert Var.MAX_CONCURRENT_STREAMS >= 1
    assert Var.TOUCH_FLUSH_SECONDS >= 1
    assert Var.TOUCH_BUFFER_MAX >= 100


@pytest.mark.unit
def test_owner_id_required_boot_fails():
    """H7: missing OWNER_ID must refuse to boot (nobody had owner access)."""
    env = {
        k: v
        for k, v in __import__("os").environ.items()
        if not k.startswith(("API_", "BOT_TOKEN", "BIN_", "OWNER_", "DATABASE_"))
    }
    env.update(
        {
            "API_ID": "1",
            "API_HASH": "h",
            "BOT_TOKEN": "1:x",
            "BIN_CHANNEL": "-1",
            "DATABASE_URL": "mongodb://localhost/x",
            "OWNER_ID": "",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", "import Thunder.vars"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert proc.returncode != 0
    assert "OWNER_ID" in (proc.stderr + proc.stdout)


@pytest.mark.unit
def test_all_problems_reported_together():
    """M6: three bad vars -> all three named before exit (not first-fail)."""
    env = {
        k: v
        for k, v in __import__("os").environ.items()
        if not k.startswith(("API_", "BOT_TOKEN", "BIN_", "OWNER_", "DATABASE_", "MAX_BATCH"))
    }
    env.update(
        {
            "API_ID": "not-a-number",
            "API_HASH": "h",
            "BOT_TOKEN": "1:x",
            "BIN_CHANNEL": "also-bad",
            "DATABASE_URL": "mongodb://localhost/x",
            "OWNER_ID": "42",
            "MAX_BATCH_FILES": "not-int",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", "import Thunder.vars"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    combined = proc.stderr + proc.stdout
    assert proc.returncode != 0
    for var in ("API_ID", "BIN_CHANNEL", "MAX_BATCH_FILES"):
        assert var in combined, f"{var} problem not reported together with the others"
