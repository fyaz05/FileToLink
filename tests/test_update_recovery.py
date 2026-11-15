"""Regression tests for the update.py defects found in review."""
import os
import subprocess

import pytest

from update_helpers import (force_rmtree, make_deployment, make_upstream,
                            run_update)


def test_a_failed_update_restores_the_original_git_history():
    """A failed update must leave the user's git history intact."""
    upstream_root, bare = make_upstream(broken=True)
    deploy_root, app = make_deployment()
    try:
        result = run_update(app, bare)
        assert result.returncode == 1, "a broken upstream must fail"
        assert (app / ".git" / "CANARY").exists(), \
            "the original .git must be intact after a rollback"
        assert (app / ".git" / "refs" / "heads" / "main").exists(), \
            "branches must survive too, not just a placeholder repository"
        config = (app / ".git" / "config").read_text(encoding="utf-8", errors="replace")
        assert "example.invalid/deploy.git" in config, \
            "the user's own remote must be untouched"
        assert "thunder@update.local" not in config, \
            "no throwaway repository may be created"
    finally:
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


def test_a_successful_update_preserves_branches_stash_and_remotes():
    """The user's local git work must survive an update that succeeds.

    A rollback test cannot cover this: the damage happened when nothing failed.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=app, capture_output=True,
                                  text=True, timeout=120).stdout.strip()

        git("checkout", "-qb", "my-local-branch")
        (app / "NOTES.md").write_text("local work\n")
        git("add", "NOTES.md")
        git("commit", "-qm", "local work")
        (app / "stashme.txt").write_text("stash content\n")
        git("add", "stashme.txt")
        git("stash", "push", "-qm", "my stash")
        git("checkout", "-q", "main")

        assert run_update(app, bare).returncode == 0

        assert "my-local-branch" in git("branch"), "a local branch was lost"
        assert "my stash" in git("stash", "list"), "the stash was lost"
        assert "example.invalid/deploy.git" in git("remote", "-v"), "the remote was lost"
        assert (app / "UPSTREAM_MARKER.txt").exists(), "the update still applied"
    finally:
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


def test_a_successful_update_leaves_a_workable_checkout():
    """After a successful update the deployment must be a usable git checkout.

    The update applies upstream code and resets the working tree onto it, so the
    repository has to survive that intact: still valid, still resolving to a
    commit, still the user's own repository rather than a replacement.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        result = run_update(app, bare)
        assert result.returncode == 0, result.stderr[-400:]
        assert (app / "UPSTREAM_MARKER.txt").exists()
        assert (app / "config.env").read_text().startswith("KEEP_ME")

        status = subprocess.run(["git", "status", "--porcelain"], cwd=app,
                                capture_output=True, timeout=120)
        assert status.returncode == 0, "the remaining .git must be a valid repository"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=app,
                              capture_output=True, text=True, timeout=120)
        assert head.returncode == 0 and len(head.stdout.strip()) == 40, \
            "the checkout must resolve to a commit"

        # CANARY is a stand-in for private history the original .git holds.
        assert (app / ".git" / "CANARY").exists(), \
            "the update replaced the user's repository"
        remotes = subprocess.run(["git", "remote", "-v"], cwd=app, capture_output=True,
                                 text=True, timeout=120).stdout
        assert "example.invalid/deploy.git" in remotes, \
            "the user's remote was replaced"
    finally:
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


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
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


def test_no_git_checkout_is_not_updated_even_when_git_is_installed():
    """A tarball deployment on a host that has git must be left alone.

    The check is on the checkout, not on git: without a .git directory there is
    nothing to update in place, and creating one would replace the deployment.
    """
    upstream_root, bare = make_upstream()
    deploy_root, app = make_deployment()
    try:
        force_rmtree(app / ".git")
        assert not (app / ".git").exists()

        result = run_update(app, bare)

        assert result.returncode == 0, "not applicable is not an error"
        assert not (app / ".git").exists(), "no repository may be created here"
        assert (app / "config.env").read_text().startswith("KEEP_ME")
        assert not (app / "UPSTREAM_MARKER.txt").exists(), "nothing may be applied"
    finally:
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


def test_rollback_keeps_the_open_log_file():
    """Thunder/logs is written to while the update runs.

    The assertion has to be that the rollback never *attempts* to remove it, not
    merely that the file survives: on Windows a delete of the open file fails
    silently, so a check on existence alone would pass even while the rollback
    was trying to delete the whole directory.
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
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


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
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)


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
        force_rmtree(deploy_root)
        force_rmtree(upstream_root)
