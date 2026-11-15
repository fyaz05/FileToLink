"""Shared fixtures for the two update.py test modules.

Both modules built the same sandboxes and ran the same script, so the helpers
were duplicated -- force_rmtree had already drifted by three words between the
copies, which is how duplication fails. This is the single copy.

Each helper keeps the exact semantics its original had, so the tests above it do
not change: `make_sandbox` is the plain checkout-shaped copy, `make_deployment`
also has a log directory and a user remote.
"""
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

# Every test copies the project into a sandbox. Virtualenvs and tool caches are
# excluded: a .venv here is ~75 MB across thousands of files and irrelevant to
# what is tested, and without this a run copies it once per test.
SKIP = shutil.ignore_patterns("__pycache__", "logs", "*.session*", "config.env", ".git",
                              ".venv", "venv", ".pytest_cache", ".ruff_cache")


def force_rmtree(target):
    """Remove a tree that may contain read-only files.

    A real git repository keeps its loose objects and pack files read-only, and
    Windows refuses to delete those, so the tests need the same retry update.py
    applies to itself.
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


def run_update(cwd, upstream="", branch="main", timeout=400):
    """Run update.py in `cwd` against `upstream`, with placeholders in the env."""
    env = dict(BASE_ENV)
    env["UPSTREAM_REPO"] = str(upstream)
    env["UPSTREAM_BRANCH"] = branch
    return subprocess.run([sys.executable, "update.py"], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def make_upstream(broken=False):
    """A real bare repository to update from.

    `broken=True` adds a file that fails bytecode compilation, so the update
    applies and then fails validation -- which is what exercises the rollback.
    """
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


def _git_in(target):
    def git(*args):
        return subprocess.run(["git", *args], cwd=target, capture_output=True,
                              timeout=120)
    return git


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

    git = _git_in(target)
    git("init", "-q")
    git("config", "user.email", "deploy@example.invalid")
    git("config", "user.name", "Deploy")
    git("add", "-A")
    git("commit", "-qm", "deployment state")
    git("branch", "-M", "main")
    (target / ".git" / "CANARY").write_text("history that must survive\n")
    return target


def make_deployment(history: bool = True):
    """make_sandbox plus a log directory and a user remote.

    The extra state is what the recovery tests assert about: a live log file the
    rollback must not delete, and a remote the update must not replace.
    """
    root = Path(tempfile.mkdtemp(prefix="thunder_deploy_"))
    app = root / "app"
    shutil.copytree(REPO, app, ignore=SKIP)
    force_rmtree(app / ".git")
    (app / "config.env").write_text("KEEP_ME=1\n")
    (app / "Thunder" / "logs").mkdir(parents=True, exist_ok=True)
    (app / "Thunder" / "logs" / "bot.txt").write_text("live log\n")

    if history:
        git = _git_in(app)
        git("init", "-q")
        git("config", "user.email", "deploy@example.invalid")
        git("config", "user.name", "Deploy")
        git("add", "-A")
        git("commit", "-qm", "deployment state")
        git("branch", "-M", "main")
        git("remote", "add", "origin", "https://example.invalid/deploy.git")
        (app / ".git" / "CANARY").write_text("private history that must survive\n")
    return root, app
