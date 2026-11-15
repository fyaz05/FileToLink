"""Regression tests for the update.py defects found in review.

Two of the defects covered here destroy data; the test that guards new
behaviour rather than a defect says so in its own docstring.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

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


def make_deployment(history: bool = True):
    """A checkout-shaped copy with private history and a config.env to protect.

    The .git directory is a realistic stub: a rollback test needs to prove the
    real thing came back, so it has the files a real repository would have.
    """
    root = Path(tempfile.mkdtemp(prefix="thunder_deploy_"))
    app = root / "app"
    shutil.copytree(REPO, app, ignore=SKIP)
    shutil.rmtree(app / ".git", ignore_errors=True)
    if history:
        git = app / ".git"
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "config").write_text(
            '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n'
            '\turl = https://example.invalid/private.git\n')
        (git / "refs" / "heads" / "main").write_text("0" * 40 + "\n")
        (git / "CANARY").write_text("private history that must survive\n")
    (app / "config.env").write_text("KEEP_ME=1\n")
    (app / "Thunder" / "logs").mkdir(parents=True, exist_ok=True)
    (app / "Thunder" / "logs" / "bot.txt").write_text("live log\n")
    return root, app


def make_upstream(broken=False):
    root = Path(tempfile.mkdtemp(prefix="thunder_ups_"))
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


def run_update(app, upstream, branch="main", timeout=400):
    env = dict(BASE_ENV)
    env["UPSTREAM_REPO"] = str(upstream)
    env["UPSTREAM_BRANCH"] = branch
    return subprocess.run([sys.executable, "update.py"], cwd=app, env=env,
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def test_a_failed_update_restores_the_original_git_history():
    """The tree must never come back without the history it had before.

    _apply_upstream re-initialises the repository to fetch upstream, so a failure
    afterwards used to leave the user with a throwaway one-commit repo and their
    branches, remotes and stashes gone.
    """
    upstream_root, bare = make_upstream(broken=True)
    deploy_root, app = make_deployment()
    try:
        result = run_update(app, bare)
        assert result.returncode == 1, "a broken upstream must fail"
        assert (app / ".git" / "CANARY").exists(), \
            "the original .git must be back after a rollback"
        assert (app / ".git" / "refs" / "heads" / "main").exists(), \
            "branches must survive too, not just a placeholder repository"
        config = (app / ".git" / "config").read_text(encoding="utf-8", errors="replace")
        assert "example.invalid/private.git" in config, \
            "the original remotes must be intact"
        assert "thunder@update.local" not in config, \
            "a rollback must not leave the throwaway repository in place"
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)


def test_a_successful_update_leaves_a_workable_checkout():
    """After a successful update the deployment must be a usable git checkout.

    This one is not a regression test for a pre-existing defect: the pre-fix code
    never preserved history, so it had no backup to leak. It guards the new
    behaviour -- that the preserved history is cleaned up and the repository left
    behind still works -- which nothing else covers.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        result = run_update(app, bare)
        assert result.returncode == 0, result.stderr[-400:]
        assert (app / "UPSTREAM_MARKER.txt").exists()
        assert (app / "config.env").read_text().startswith("KEEP_ME")
        assert not (deploy_root / "thunder_git_backup").exists(), \
            "the preserved history is obsolete once the update succeeded"

        status = subprocess.run(["git", "status", "--porcelain"], cwd=app,
                                capture_output=True, timeout=120)
        assert status.returncode == 0, "the remaining .git must be a valid repository"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=app,
                              capture_output=True, text=True, timeout=120)
        assert head.returncode == 0 and len(head.stdout.strip()) == 40
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)


def test_config_recovered_when_a_previous_update_was_killed():
    """config.env must not be left stranded by a run that died mid-update.

    The backup name is fixed rather than per-process for this reason: a later run
    has to be able to find the file the dead run left behind.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        (app / "config.env").unlink()
        (deploy_root / ".config.env.thunder.tmp").write_text("KEEP_ME=1\n")

        result = run_update(app, bare)

        assert result.returncode == 0, result.stderr[-400:]
        assert (app / "config.env").read_text().startswith("KEEP_ME"), \
            "the stranded config must be recovered, or the bot cannot start"
        assert not (deploy_root / ".config.env.thunder.tmp").exists()
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)


def test_no_git_checkout_is_not_updated_even_when_git_is_installed():
    """A tarball deployment on a host that has git must be left alone.

    Previously the missing-.git check only ran when git itself was unusable, so
    an unpacked archive was re-initialised as a git repository.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        shutil.rmtree(app / ".git")
        assert not (app / ".git").exists()

        result = run_update(app, bare)

        assert result.returncode == 0, "not applicable is not an error"
        assert not (app / ".git").exists(), "no repository may be created here"
        assert (app / "config.env").read_text().startswith("KEEP_ME")
        assert not (app / "UPSTREAM_MARKER.txt").exists(), "nothing may be applied"
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)


def test_rollback_keeps_the_open_log_file():
    """Thunder/logs is written to while the update runs.

    The rollback used to exempt a top-level "logs" directory, which never exists
    (the real path is Thunder/logs), so the whole Thunder/ tree was deleted. On
    Windows the deletion silently failed with WinError 32 and only the log file's
    in-use status saved it; on Linux it was removed. The assertion therefore has
    to be that the rollback never tries to remove it, which holds on both.
    """
    upstream_root, bare = make_upstream(broken=True)
    deploy_root, app = make_deployment()
    try:
        result = run_update(app, bare)
        assert result.returncode == 1

        assert (app / "Thunder" / "logs" / "bot.txt").exists(), \
            "the live log directory must survive a rollback"
        # The running process appends to it, so the original line is still there.
        assert "live log" in (app / "Thunder" / "logs" / "bot.txt").read_text(
            encoding="utf-8", errors="replace")
        combined = (result.stderr or "") + (result.stdout or "")
        assert "Could not clear" not in combined and "Could not remove" not in combined, \
            f"the rollback must skip the live log, not fail on it:\n{combined[-600:]}"
        # ...while the code it does not own is still replaced.
        assert (app / "Thunder" / "utils" / "logger.py").exists()
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)


def test_an_unusable_rollback_location_is_reported_not_crashed():
    """If the snapshot cannot be written, nothing must be changed.

    An existing checkout whose parent directory cannot hold the rollback copy:
    the update has to be refused up front rather than half-applied, and it must
    not crash with a traceback while the tree is mid-replacement.
    """
    if os.name == "nt":
        pytest.skip("making a directory unwritable is not reliable on Windows")

    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        assert (app / ".git").exists(), "this case is about a real checkout"
        before = sorted(p.name for p in app.iterdir())
        # The parent directory must accept ../thunder_rollback; deny it that.
        os.chmod(deploy_root, 0o500)
        try:
            result = run_update(app, bare)
        finally:
            os.chmod(deploy_root, 0o700)

        assert result.returncode == 1, "an unsafe update must be refused"
        assert "Traceback" not in (result.stderr or ""), \
            "an unusable rollback location must be reported, not crash"
        assert sorted(p.name for p in app.iterdir()) == before, \
            "nothing may be changed when the update cannot be made safe"
        assert (app / "config.env").read_text().startswith("KEEP_ME")
        assert not (app / "UPSTREAM_MARKER.txt").exists(), "nothing may be applied"
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)


def test_a_stale_file_where_the_rollback_copy_goes_does_not_stop_the_update():
    """A stray file left by an earlier run must not be treated as a hard failure.

    _snapshot replaces whatever occupies the rollback path; only a genuinely
    unwritable location is an error.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        (deploy_root / "thunder_rollback").write_text("stale file from an older run\n")

        result = run_update(app, bare)

        assert result.returncode == 0, result.stderr[-400:]
        assert (app / "UPSTREAM_MARKER.txt").exists(), "the update should still apply"
        assert (deploy_root / "thunder_rollback").is_dir(), \
            "the rollback copy must be a directory afterwards"
    finally:
        shutil.rmtree(deploy_root, ignore_errors=True)
        shutil.rmtree(upstream_root, ignore_errors=True)
