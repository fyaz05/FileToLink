"""Verify update.py preconditions and rollback in isolated project copies."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

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

# Generated state is excluded; see test_update_recovery.py for why.
SKIP = shutil.ignore_patterns("__pycache__", "logs", "*.session*", "config.env", ".git",
                              ".venv", "venv", ".pytest_cache", ".ruff_cache")


def force_rmtree(target):
    """Remove a tree that may contain read-only files.

    A real git repository keeps its loose objects and pack files read-only, and
    Windows refuses to delete those, so the tests need the same retry update.py
    uses on itself.
    """
    def on_error(func, path_, _exc):
        try:
            os.chmod(path_, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(path_)
        except OSError:
            pass

    shutil.rmtree(target, onexc=on_error)


def make_sandbox():
    """A copy of the project with a real git repository and a config.env to protect.

    The repository has to be genuine: update.py fetches and resets in place
    rather than re-initialising, so a stub .git directory is not enough. A real
    repository also makes the canary meaningful -- the history it protects is one
    git can actually read.
    """
    target = Path(tempfile.mkdtemp(prefix="thunder_upd_"))
    shutil.copytree(REPO, target, dirs_exist_ok=True, ignore=SKIP)
    force_rmtree(target / ".git")
    (target / "config.env").write_text("KEEP_ME=1\n")

    def git(*args):
        return subprocess.run(["git", *args], cwd=target, capture_output=True,
                              timeout=120)

    git("init", "-q")
    git("config", "user.email", "deploy@example.invalid")
    git("config", "user.name", "Deploy")
    git("add", "-A")
    git("commit", "-qm", "deployment state")
    git("branch", "-M", "main")
    (target / ".git" / "CANARY").write_text("history that must survive\n")
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
