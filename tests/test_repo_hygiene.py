"""Verify .gitignore keeps credentials and local state out of a commit.

update.py no longer stages anything, so the risk is a person running `git add -A`
in a working checkout, and `git commit -a` on modified tracked files. Both are
ordinary things to do, and `config.env` must not be among what they pick up.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Isolate from ambient user or system git configuration.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=GIT_ENV,
                          capture_output=True, timeout=120)


def is_git_ignored(repo: Path, relative: str) -> bool:
    """Return True when `git check-ignore` reports the path as ignored (exit 0)."""
    result = git(repo, "check-ignore", "-q", "--", relative)
    assert result.returncode in (0, 1), result.stderr.decode("utf-8", "replace")
    return result.returncode == 0


def test_gitignore_keeps_generated_env_files_out_of_a_self_update_commit():
    tmp = Path(tempfile.mkdtemp(prefix="thunder_gitignore_"))
    try:
        shutil.copy(REPO / ".gitignore", tmp / ".gitignore")
        assert git(tmp, "init", "-q").returncode == 0

        for relative in ("config.env", "docker.env", "deploy/prod.env"):
            target = tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("API_HASH=secret\n")
            assert is_git_ignored(tmp, relative), \
                f"a generated {relative} must never be committed"

        (tmp / "config_sample.env").write_text('API_HASH=""\n')
        assert not is_git_ignored(tmp, "config_sample.env"), \
            "the sample template must stay committable"

        (tmp / "VPS.md").write_text("ssh deploy@example.invalid\n")
        assert is_git_ignored(tmp, "VPS.md"), "VPS.md carries an SSH credential"

        (tmp / "venv" / "bin").mkdir(parents=True)
        (tmp / "venv" / "bin" / "python").write_text("")
        assert is_git_ignored(tmp, "venv/bin/python"), \
            "README tells operators to create venv/, which must not be committed"

        (tmp / ".env").write_text("API_HASH=secret\n")
        assert is_git_ignored(tmp, ".env"), "a bare .env must be ignored too"

        # Tool caches and coverage files are created by commands the docs tell
        # developers to run, and none of them may be committable.
        for cache in (".ruff_cache/0.0.1/index", ".pytest_cache/v/cache/lastfailed",
                      ".coverage"):
            target = tmp / cache
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n")
            assert is_git_ignored(tmp, cache), f"{cache} must not be committable"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
