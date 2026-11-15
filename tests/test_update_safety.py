"""Verify update.py preconditions and rollback in isolated project copies."""
import subprocess
import sys

from update_helpers import (BASE_ENV, force_rmtree, make_sandbox, make_upstream,
                            run_update)


def test_refuses_when_upstream_unset():
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream="")
        assert result.returncode == 0
        assert (sandbox / ".git" / "CANARY").exists()
    finally:
        force_rmtree(sandbox)


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
        force_rmtree(sandbox)


def test_no_git_and_no_checkout_is_not_an_error():
    """In container environments without git, update is skipped without error."""
    sandbox = make_sandbox()
    try:
        force_rmtree(sandbox / ".git")
        assert not (sandbox / ".git").exists()

        env = dict(BASE_ENV)
        env["UPSTREAM_REPO"] = "https://example.invalid/x.git"
        env["PATH"] = "/nonexistent"
        result = subprocess.run([sys.executable, "update.py"], cwd=sandbox,
                                env=env, capture_output=True, text=True,
                                timeout=180)
        assert result.returncode == 0, result.stderr[-500:]
        assert (sandbox / "config.env").exists(), "nothing may be touched"
        assert (sandbox / "update.py").exists(), "code must be left intact"
    finally:
        force_rmtree(sandbox)


def test_refuses_unreachable_remote():
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream="https://example.invalid/nope.git")
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists(), "history must survive"
    finally:
        force_rmtree(sandbox)


def test_refuses_missing_branch():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare), branch="does-not-exist")
        assert result.returncode == 1
        assert (sandbox / ".git" / "CANARY").exists()
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)


def test_successful_update_applies_upstream_and_keeps_config_and_history():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        result = run_update(sandbox, upstream=str(bare))
        assert result.returncode == 0, result.stderr[-500:]
        assert (sandbox / "UPSTREAM_MARKER.txt").exists()
        assert (sandbox / "config.env").read_text().startswith("KEEP_ME")

        # The update applies new code without replacing the repository, so the
        # user's own remote and branches are still there afterwards.
        assert (sandbox / ".git" / "CANARY").exists(), \
            "a successful update must not replace the user's repository"
        remotes = subprocess.run(["git", "remote", "-v"], cwd=sandbox,
                                 capture_output=True, text=True, timeout=120)
        assert "example.invalid" in remotes.stdout or "origin" in remotes.stdout
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)


def test_update_is_idempotent():
    root, bare = make_upstream()
    sandbox = make_sandbox()
    try:
        assert run_update(sandbox, upstream=str(bare)).returncode == 0
        assert run_update(sandbox, upstream=str(bare)).returncode == 0
        assert (sandbox / "config.env").exists()
    finally:
        force_rmtree(sandbox)
        force_rmtree(root)


def test_broken_upstream_rolls_back():
    """A broken upstream that fails bytecode compilation is rolled back."""
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
        force_rmtree(sandbox)
        force_rmtree(root)
