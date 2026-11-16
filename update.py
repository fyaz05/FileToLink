from fnmatch import fnmatch
from os import chmod, environ, listdir, path, remove, replace, rmdir
from pathlib import Path, PurePath
from shutil import copytree, ignore_patterns, rmtree
from sqlite3 import connect as sqlite_connect
from stat import S_IWRITE
from subprocess import run as srun
from sys import executable, exit
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from Thunder.utils.logger import logger

# Rollback state lives beside the tree, so replacing the tree cannot overwrite it.
_BACKUP = path.join("..", "thunder_rollback")
_CONFIG_BACKUP = path.join("..", ".config.env.thunder.tmp")

# Regenerated or machine-specific, so never part of the code the update replaces.
# Matched against every path component, so a bare "logs" also covers the nested
# Thunder/logs, whose log file is open while this runs.
_SKIP = (".git", "config.env", "logs", "__pycache__",
         "*.session", "*.session-journal", "*.session-shm", "*.session-wal")

# A run killed between moving config.env aside and putting it back would leave the
# deployment with no configuration at all. The name is fixed rather than
# per-process precisely so that a later run can find it and recover it.
if not path.exists("config.env") and path.exists(_CONFIG_BACKUP):
    try:
        replace(_CONFIG_BACKUP, "config.env")
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
    """True when any part of a relative path is local state rather than code."""
    return any(_generated(part) for part in PurePath(relative).parts)


def _on_rmtree_error(func, target, exc):
    # Windows refuses to delete a read-only file, and copytree preserves that bit,
    # so a read-only file anywhere in the tree blocks the rollback cleanup. Clear
    # the bit and retry once.
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

    logger.error("Update failed; rolled back to the previous working code.")


def _apply_upstream(repo: str, branch: str) -> bool:
    """Fetch and reset onto the upstream branch, in the user's own repository.

    Deliberately never re-initialises the repository. Doing so replaces it, which
    costs the user their branches, stashes and remotes. Only the working tree
    changes here, and it is snapshotted for rollback before this runs.
    """
    # Argument lists, never a shell (no injection). Anything the user configured
    # stays configured: only the remote we need is added if it is missing.
    remotes = srun(["git", "remote"], capture_output=True)
    known = (remotes.stdout or b"").decode("utf-8", "replace").split()

    commands = [["git", "fetch", "--prune", "--quiet", repo, branch]]
    if "origin" not in known:
        commands.append(["git", "remote", "add", "origin", repo])
    commands.append(["git", "reset", "--hard", "--quiet", "FETCH_HEAD"])

    for cmd in commands:
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

    # pip is not guaranteed: `uv venv` creates an environment without it, and a
    # deployment set up that way would otherwise fail here on every update while
    # still reporting success. Try pip, then uv, and say plainly when neither is
    # available rather than continuing silently.
    attempts = (
        [executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
        ["uv", "pip", "install", "--python", executable, "-q", "-r", "requirements.txt"],
    )
    last_error = ""
    for command in attempts:
        try:
            result = srun(command, capture_output=True)
        except OSError as e:
            last_error = f"could not run {command[0]}: {e}"
            continue
        if result.returncode == 0:
            return True
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        last_error = tail[-1] if tail else f"{command[0]} exited {result.returncode}"

    logger.warning(
        f"Dependencies could not be fully installed ({last_error}); continuing. "
        f"New requirements may be missing, in which case the bot will fail to "
        f"start: install them yourself, or recreate the environment with "
        f"`uv venv --seed` so it contains pip.")
    return True


# Placeholders for the probe below: Thunder.vars validates its configuration at
# import, and _run_update moves config.env aside before validating, so the child
# would otherwise fail on a good tree. The CI smoke test uses the same values.
_PROBE_ENV = {
    "API_ID": "123456",
    "API_HASH": "0123456789abcdef0123456789abcdef",
    "BOT_TOKEN": "123456:AA" + "x" * 30,
    "BIN_CHANNEL": "-1001234567890",
    "OWNER_ID": "123456",
    "DATABASE_URL": "mongodb://localhost:27017/probe",
}


def _validate() -> bool:
    """Confirm the tree compiles and can import what startup needs.

    compileall alone passes on an environment whose packages are broken, so the
    import probe is what catches it.
    """
    try:
        result = srun(
            [executable, "-m", "compileall", "-q", "Thunder", "update.py"],
            capture_output=True,
        )
    except OSError as e:
        logger.error(f"Could not run compileall: {e}")
        return False
    if result.returncode != 0:
        return False

    try:
        # The startup chain, not just the package: an unresolvable import in
        # Thunder.bot or Thunder.__main__ compiles and leaves `import Thunder`
        # working, while the bot cannot start. Binding names also catches a
        # directory left empty by an in-place upgrade (a namespace package).
        probe = srun(
            [executable, "-c",
             "from pyrogram import Client, filters, enums; "
             "import warpcrypto, Thunder, Thunder.bot, Thunder.__main__"],
            capture_output=True,
            env={**environ, **_PROBE_ENV},
        )
    except OSError as e:
        logger.error(f"Could not import the application: {e}")
        return False
    if probe.returncode != 0:
        tail = (probe.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        logger.error(
            "The updated tree cannot import its dependencies: "
            f"{tail[-1] if tail else 'no output'}. Recreate the environment "
            "(`uv venv --seed .venv`) rather than installing over the old one."
        )
        return False
    return True


def _stale_sessions() -> list[str]:
    """*.session files the installed library refuses to open.

    Reported, never deleted: the file holds the bot's authorisation, so the
    operator removes it.

    Asked of the library rather than inferred from the stored schema version, which
    does not identify the format -- pyrofork's v4 layout fails to migrate while
    wzgram migrates v6 and v7. The library is tried on a snapshot, so the original
    is untouched.
    """
    try:
        import asyncio
        from pyrogram.storage import FileStorage
    except Exception:
        return []

    stale: list[str] = []

    def snapshot(source: str, destination: str) -> None:
        """Write a consistent copy of a SQLite database, WAL included.

        Copying the file alone loses committed pages still in the -wal sidecar,
        which an unclean stop leaves behind: the copy then looks corrupt when it is
        only uncheckpointed. The backup API reads through the WAL.
        """
        origin = sqlite_connect(f"file:{source}?mode=ro", uri=True)
        try:
            target = sqlite_connect(destination)
            try:
                origin.backup(target)
            finally:
                target.close()
        finally:
            origin.close()

    async def unreadable(source: str) -> bool:
        """Snapshot the session, open the copy, and report whether it failed."""
        with TemporaryDirectory(prefix="thunder-session-check-") as holder:
            copy = path.join(holder, "probe.session")
            try:
                snapshot(source, copy)
            except Exception:
                # Cannot snapshot it, so say nothing: an inconclusive answer must
                # not send the operator to delete a session that may be fine.
                return False
            storage = FileStorage("probe", Path(holder))
            try:
                await storage.open()
                return False
            except Exception:
                return True
            finally:
                try:
                    await storage.conn.close()
                except Exception:
                    pass

    for name in listdir("."):
        if not name.endswith(".session"):
            continue
        try:
            with open(name, "rb") as handle:
                if handle.read(15) != b"SQLite format 3":
                    continue
            if asyncio.run(unreadable(name)):
                stale.append(name)
        except Exception:
            continue
    return stale


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

    # Move config aside: the reset below replaces tracked files, and config.env
    # is untracked in a user's checkout. The name is fixed, not per-process, so a
    # leftover from a killed run can be recovered by the next import.
    if path.exists(_CONFIG_BACKUP):
        remove(_CONFIG_BACKUP)
    if path.exists("config.env"):
        replace("config.env", _CONFIG_BACKUP)

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

        # Advisory: never turns a good update into a failed one.
        try:
            for stale in _stale_sessions():
                logger.warning(
                    f"Session {stale} cannot be read by the installed library; "
                    "delete it and restart to re-authorise from BOT_TOKEN."
                )
        except Exception:
            logger.debug("Could not check session compatibility", exc_info=True)

        return True
    finally:
        # replace, not rename: on Windows rename raises FileExistsError if the
        # destination was recreated during the update, which would escape the
        # finally block as a crash.
        if path.exists(_CONFIG_BACKUP):
            replace(_CONFIG_BACKUP, "config.env")


def main() -> int:
    if not UPSTREAM_REPO:
        logger.info("No UPSTREAM_REPO set, skipping update")
        return 0
    result = _run_update(UPSTREAM_REPO, UPSTREAM_BRANCH)
    return 0 if result in (True, None) else 1


if __name__ == "__main__":
    exit(main())
