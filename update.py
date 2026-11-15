from fnmatch import fnmatch
from os import chmod, environ, listdir, path, remove, rename, rmdir
from pathlib import PurePath
from shutil import copytree, ignore_patterns, rmtree
from stat import S_IWRITE
from subprocess import run as srun
from sys import executable, exit

from dotenv import load_dotenv

from Thunder.utils.logger import logger

# Rollback state lives beside the tree, so replacing the tree cannot overwrite it.
_BACKUP = path.join("..", "thunder_rollback")
_GIT_BACKUP = path.join("..", "thunder_git_backup")
_CONFIG_BACKUP = path.join("..", ".config.env.thunder.tmp")

# Regenerated or machine-specific, so never part of the code the update replaces.
# "logs" is a directory name: it also covers the live log directory, Thunder/logs,
# whose file is open while this runs.
_SKIP = (".git", "config.env", "logs", "__pycache__",
         "*.session", "*.session-journal", "*.session-shm", "*.session-wal")

# A run killed between moving config.env aside and putting it back would leave the
# deployment with no configuration at all. The name is fixed rather than
# per-process precisely so that a later run can find it and recover it.
if not path.exists("config.env") and path.exists(_CONFIG_BACKUP):
    try:
        rename(_CONFIG_BACKUP, "config.env")
        logger.warning(f"Recovered config.env from an interrupted update ({_CONFIG_BACKUP}).")
    except OSError as e:
        logger.error(f"Could not restore {_CONFIG_BACKUP}: {e}")

load_dotenv('config.env', override=True)

UPSTREAM_REPO = environ.get("UPSTREAM_REPO", "")
UPSTREAM_BRANCH = environ.get("UPSTREAM_BRANCH", "main")


def _generated(name: str) -> bool:
    """True when a single path component is local state rather than deployed code."""
    return any(fnmatch(name, pattern) for pattern in _SKIP)


def _generated_relative(relative: str) -> bool:
    return any(_generated(part) for part in PurePath(relative).parts)


def _on_rmtree_error(func, target, exc):
    # git pack files are read-only on Windows; clear the bit and retry once.
    try:
        chmod(target, S_IWRITE)
    except OSError:
        pass
    func(target)


def _preflight(repo: str, branch: str) -> bool | None:
    """Decide whether this deployment can self-update at all.

    Returns True to proceed, None when the deployment structurally cannot update
    (container image: no git and no history), False when it should be able to but
    something is wrong.
    """
    try:
        probe = srun(["git", "--version"], capture_output=True)
    except OSError as e:
        detail = str(e)
    else:
        detail = "" if probe.returncode == 0 else "git returned a non-zero exit code"

    has_history = path.exists(".git")
    if not has_history:
        # Without a checkout there is nothing to update in place, whether or not
        # git happens to be installed. A tarball unpacked onto a host with git is
        # the common case, and re-initialising a repository here would replace
        # the deployment instead of updating it.
        logger.info(
            f"Self-update is not applicable in this deployment"
            f"{f' ({detail})' if detail else ''}: no .git checkout to update. "
            f"UPSTREAM_REPO is set but this deployment cannot update itself. "
            f"Nothing has been changed.")
        return None

    if detail:
        logger.error(
            f"git is not runnable ({detail}). Install git and put it on PATH "
            f"before updating; nothing has been changed.")
        return False

    try:
        check = srun(
            ["git", "ls-remote", "--exit-code", "--heads", repo, branch],
            capture_output=True,
        )
    except OSError as e:
        logger.error(f"Could not reach {repo}: {e}. Nothing has been changed.")
        return False
    if check.returncode != 0:
        logger.error(
            f"Cannot reach {repo} branch {branch}. Check UPSTREAM_REPO and "
            f"UPSTREAM_BRANCH; nothing has been changed.")
        return False

    return True


def _prune(target: str) -> None:
    """Delete deployed code under `target`, leaving generated state in place.

    Recurses instead of calling rmtree on the directory as a whole, so a nested
    generated path survives. Thunder/logs is the one that matters: its log file
    is open while this runs, and rmtree("Thunder") would delete it.
    """
    if _generated_relative(path.relpath(target)):
        return
    if path.isdir(target):
        for entry in listdir(target):
            _prune(path.join(target, entry))
        try:
            rmdir(target)
        except OSError:
            # Not empty: something generated is still inside, which is expected.
            pass
        return
    try:
        remove(target)
    except OSError as e:
        logger.warning(f"Could not remove {target} during rollback: {e}")


def _restore_git() -> None:
    """Put the original repository back, discarding the one git init created.

    Only ever called from a failed update. If there is no backup the deployment
    had no history to begin with, so what is in .git now is disposable.
    """
    if not path.exists(_GIT_BACKUP):
        return
    if path.exists(".git"):
        rmtree(".git", onexc=_on_rmtree_error)
    rename(_GIT_BACKUP, ".git")
    logger.info("Original git history restored.")


def _snapshot() -> None:
    if path.isdir(_BACKUP):
        rmtree(_BACKUP, onexc=_on_rmtree_error)
    elif path.exists(_BACKUP):
        remove(_BACKUP)
    copytree(".", _BACKUP, ignore=ignore_patterns(*_SKIP), dirs_exist_ok=True)


def _rollback() -> None:
    if not path.exists(_BACKUP):
        logger.error("Update failed and no rollback copy exists; code is unchanged.")
        return

    # Generated state is neither deleted nor restored, so live files are left
    # untouched.
    for entry in listdir("."):
        if _generated(entry):
            continue
        _prune(path.join(".", entry))

    copytree(_BACKUP, ".", dirs_exist_ok=True)

    _restore_git()
    logger.error("Update failed; rolled back to the previous working code.")


def _apply_upstream(repo: str, branch: str) -> bool:
    # Keep the existing history aside rather than deleting it: a failure after
    # this point restores it, and the tree itself is replaced by the fetch below.
    if path.exists(_GIT_BACKUP):
        rmtree(_GIT_BACKUP, onexc=_on_rmtree_error)
    if path.isdir(".git"):
        rename(".git", _GIT_BACKUP)
    elif path.exists(".git"):
        remove(".git")

    # Argument lists, never a shell (no injection); git config is local, not --global.
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "thunder@update.local"],
        ["git", "config", "user.name", "Thunder"],
        ["git", "add", "."],
        ["git", "commit", "-sm", "update", "-q"],
        ["git", "remote", "add", "origin", repo],
        ["git", "fetch", "origin", "-q"],
        ["git", "reset", "--hard", f"origin/{branch}", "-q"],
    ):
        try:
            result = srun(cmd)
        except OSError as e:
            logger.error(f"Could not run {cmd[0]}: {e}.")
            return False
        if result.returncode != 0:
            logger.error(f"git {cmd[1] if len(cmd) > 1 else ''} failed.")
            return False

    # The preserved history stays on disk until the whole update has been
    # validated: a failure in _validate() afterwards still needs to roll back,
    # and by then .git holds only the throwaway repository created above.
    return True


def _install_requirements() -> bool:
    # A package that will not build here is reported, never a rollback.
    if not path.exists("requirements.txt"):
        return True
    try:
        result = srun(
            [executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
            capture_output=True,
        )
    except OSError as e:
        logger.warning(f"Could not run pip: {e}")
        return True
    if result.returncode != 0:
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        logger.warning(
            f"Dependencies could not be fully installed "
            f"({tail[-1] if tail else 'unknown error'}); continuing.")
    return True


def _validate() -> bool:
    try:
        result = srun(
            [executable, "-m", "compileall", "-q", "Thunder", "update.py"],
            capture_output=True,
        )
    except OSError as e:
        logger.error(f"Could not run compileall: {e}")
        return False
    return result.returncode == 0


def _run_update(repo: str, branch: str) -> bool | None:
    preflight = _preflight(repo, branch)
    if preflight is not True:
        # False: preconditions failed. None: self-update does not apply here.
        return preflight

    try:
        _snapshot()
    except OSError as e:
        # Nothing has been changed yet, so report rather than risk a half state.
        logger.error(
            f"Could not create a rollback copy in {path.dirname(_BACKUP)} ({e}). "
            f"That directory must be writable for an update to be safe. "
            f"Nothing has been changed.")
        return False

    # Move config aside so the reset cannot touch it. The name is fixed, not
    # per-process: module import already recovered any backup a killed run left,
    # so a file still here is this run's own, from a previous attempt.
    if path.exists(_CONFIG_BACKUP):
        remove(_CONFIG_BACKUP)
    if path.exists("config.env"):
        rename("config.env", _CONFIG_BACKUP)

    try:
        ok = _apply_upstream(repo, branch)
        if ok:
            ok = _install_requirements()
        if ok:
            ok = _validate()
        if not ok:
            _rollback()
            return False

        # The update is complete and valid, so the preserved history is obsolete.
        if path.exists(_GIT_BACKUP):
            rmtree(_GIT_BACKUP, onexc=_on_rmtree_error)

        logger.info("Successfully updated with Latest Updates!")
        return True
    finally:
        if path.exists(_CONFIG_BACKUP):
            rename(_CONFIG_BACKUP, "config.env")


def main() -> int:
    if not UPSTREAM_REPO:
        logger.info("No UPSTREAM_REPO set, skipping update")
        return 0
    result = _run_update(UPSTREAM_REPO, UPSTREAM_BRANCH)
    return 0 if result in (True, None) else 1


if __name__ == "__main__":
    exit(main())
