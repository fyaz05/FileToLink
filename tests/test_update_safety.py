"""update.py: preconditions are verified before anything destructive runs,
and a failed update restores the previous tree.

Each test works in a throwaway copy, so the repository is never touched.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# update.py reads configuration at import, so give it placeholders.
BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith("UPSTREAM")}
BASE_ENV.update({
    "API_ID": "123456",
    "API_HASH": "0" * 32,
    "BOT_TOKEN": "123456:AA" + "x" * 30,
    "BIN_CHANNEL": "-1001234567890",
    "OWNER_ID": "1",
    "DATABASE_URL": "mongodb://localhost:27017/t",
})

SKIP = shutil.ignore_patterns("__pycache__", "logs", "*.session*", "config.env", ".git")


def make_sandbox():
    """A copy of the project with a canary .git and a config.env to protect."""
    target = Path(tempfile.mkdtemp(prefix="thunder_upd_"))
    shutil.copytree(REPO, target, dirs_exist_ok=True, ignore=SKIP)
    shutil.rmtree(target / ".git", ignore_errors=True)
    (target / ".git").mkdir()
    (target / ".git" / "CANARY").write_text("history that must survive\n")
    (target / "config.env").write_text("KEEP_ME=1\n")
    return target


def run_update(cwd, upstream="", branch="main", timeout=300):
    env = dict(BASE_ENV)
    env["UPSTREAM_REPO"] = upstream
    env["UPSTREAM_BRANCH"] = branch
    return subprocess.run([sys.executable, "update.py"], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout)


def make_upstream(broken=False):
    """A real bare repository to update from."""
    root = Path(tempfile.mkdtemp(prefix="thunder_src_"))
    src, bare = root / "src", root / "up.git"
    shutil.copytree(REPO, src, ignore=SKIP)
    (src / "UPSTREAM_MARKER.txt").write_text("from upstream\n")
    if broken:
        (src / "Thunder" / "broken_syntax.py").write_text("def oops(:\n    pass\n")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "."],
        ["git", "commit", "-sm", "upstream", "-q"],
        ["git", "branch", "-M", "main"],
    ):
        subprocess.run(command, cwd=src, capture_output=True, timeout=120)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                   cwd=root, capture_output=True, timeout=120)
    return root, bare


def test_refuses_when_upstream_unset():
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream="")
        assert result.returncode == 0
        assert (sandbox / ".git" / "CANARY").exists()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_refuses_when_git_is_missing():
    sandbox = make_sandbox()
    try:
        env = dict(BASE_ENV)
        env["UPSTREAM_REPO"] = "https://example.invalid/x.git"
        env["PATH"] = "/nonexistent"
        result = subprocess.run([sys.executable, "update.py"], cwd=sandbox,
                                env=env, capture_output=True, text=True,
                                timeout=180)
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists(), "history must survive"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_refuses_unreachable_remote():
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream="https://example.invalid/nope.git")
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists(), "history must survive"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_refuses_missing_branch():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare), branch="does-not-exist")
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


def test_successful_update_applies_upstream_and_keeps_config():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare))
        assert result.returncode == 0, result.stderr[-500:]
        assert (sandbox / "UPSTREAM_MARKER.txt").exists()
        assert not (sandbox / ".git" / "CANARY").exists(), "history replaced"
        assert (sandbox / "config.env").read_text().startswith("KEEP_ME")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


def test_update_is_idempotent():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        assert run_update(sandbox, upstream=str(bare)).returncode == 0
        assert run_update(sandbox, upstream=str(bare)).returncode == 0
        assert (sandbox / "config.env").exists()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


def test_broken_upstream_rolls_back():
    """A tree that will not compile must not be left in place."""
    root, bare = make_upstream(broken=True)
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare))
        assert result.returncode == 1
        assert not (sandbox / "Thunder" / "broken_syntax.py").exists(), \
            "the file the failed update added must be removed"
        assert (sandbox / "config.env").exists()
        assert (sandbox / "update.py").exists(), "original code restored"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
