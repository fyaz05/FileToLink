from os import chmod, environ, getpid, listdir, path, remove, rename, walk
from shutil import copytree, ignore_patterns, rmtree
from stat import S_IWRITE
from subprocess import run as srun
from sys import executable, exit

from dotenv import load_dotenv

from Thunder.utils.logger import logger

load_dotenv('config.env', override=True)

UPSTREAM_REPO = environ.get("UPSTREAM_REPO", "")
UPSTREAM_BRANCH = environ.get("UPSTREAM_BRANCH", "main")

_BACKUP = path.join("..", "thunder_rollback")
# Exclude regenerated and machine-specific files (sessions, logs, caches) from rollback.
_SKIP = (".git", "config.env", "logs", "__pycache__",
         "*.session", "*.session-journal", "*.session-shm", "*.session-wal")


def _on_rmtree_error(func, target, exc):
    # git pack files are read-only on Windows; clear the bit and retry once.
    try:
        chmod(target, S_IWRITE)
    except OSError:
        pass
    func(target)


def _preflight(repo: str, branch: str) -> bool | None:
    # Returns None when self-update cannot apply (e.g. no git in container), False on error.
    try:
        probe = srun(["git", "--version"], capture_output=True)
    except OSError as e:
        detail = str(e)
    else:
        if probe.returncode == 0:
            detail = ""
        else:
            detail = "git returned a non-zero exit code"

    if detail:
        if not path.exists(".git"):
            # Container image: no git and no history is normal, not a failure.
            logger.info(
                f"Self-update is not applicable in this deployment ({detail}); "
                f"UPSTREAM_REPO is set but this deployment cannot update itself. "
                f"Nothing has been changed.")
            return None
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


def _snapshot() -> None:
    if path.exists(_BACKUP):
        rmtree(_BACKUP, onexc=_on_rmtree_error)
    copytree(".", _BACKUP, ignore=ignore_patterns(*_SKIP), dirs_exist_ok=True)


def _rollback() -> None:
    if not path.exists(_BACKUP):
        logger.error("Update failed and no rollback copy exists; code is unchanged.")
        return

    # Remove files absent from snapshot; skip open files (e.g. active log).
    for entry in listdir("."):
        if entry in (".git", "config.env", "logs", _BACKUP.split(path.sep)[-1]):
            continue
        target = path.join(".", entry)
        try:
            if path.isdir(target):
                rmtree(target, onexc=_on_rmtree_error)
            else:
                remove(target)
        except OSError as e:
            logger.warning(f"Could not clear {target} during rollback: {e}")

    copytree(_BACKUP, ".", dirs_exist_ok=True)

    # Delete new files inside directories that could not be removed directly.
    for root, _dirs, files in walk(".", topdown=False):
        if root.startswith("." + path.sep + ".git") or root == "." + path.sep + "logs":
            continue
        for name in files:
            candidate = path.join(root, name)
            if not path.exists(path.join(_BACKUP, candidate)):
                try:
                    remove(candidate)
                except OSError:
                    pass

    logger.error("Update failed; rolled back to the previous working code.")


def _apply_upstream(repo: str, branch: str) -> bool:
    if path.exists(".git"):
        rmtree(".git", onexc=_on_rmtree_error)

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

    _snapshot()

    # Move config aside for the git reset; a stale backup from a killed run is removed first.
    config_backup = path.join("..", f".config.env.{getpid()}.tmp")
    if path.exists(config_backup):
        remove(config_backup)
    if path.exists("config.env"):
        rename("config.env", config_backup)

    try:
        ok = _apply_upstream(repo, branch)
        if ok:
            ok = _install_requirements()
        if ok:
            ok = _validate()
        if not ok:
            _rollback()
            return False
        logger.info("Successfully updated with Latest Updates!")
        return True
    finally:
        if path.exists(config_backup):
            rename(config_backup, "config.env")


def main() -> int:
    if not UPSTREAM_REPO:
        logger.info("No UPSTREAM_REPO set, skipping update")
        return 0
    result = _run_update(UPSTREAM_REPO, UPSTREAM_BRANCH)
    return 0 if result in (True, None) else 1


if __name__ == "__main__":
    exit(main())
