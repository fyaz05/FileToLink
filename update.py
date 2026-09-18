"""Boot-time best-effort self-update.

Non-destructive ``pull --ff-only`` via argv-list subprocess; any failure
logs and keeps running the old code.
"""

import os
import re
import shutil
import subprocess

from dotenv import load_dotenv

from Thunder.utils.logger import logger

# Guarantees (the old shell=True chain had none): argv-list only, no
# destructive git ops, no global config mutation; no-ops cleanly without git.

# Real environment wins over files; .local wins over base (same precedence
# as Thunder/vars.py — load local first so setdefault keeps its values).
load_dotenv("config.env.local", override=False)
load_dotenv("config.env", override=False)

UPSTREAM_REPO = os.getenv("UPSTREAM_REPO", "")
UPSTREAM_BRANCH = os.getenv("UPSTREAM_BRANCH", "main")

# config.env lives beside the app and must survive the pull
_CONFIG_BACKUP = "config.env.bak"


def _recover_config_backup() -> None:
    """A crash between _backup_config and _restore_config would otherwise
    leave the app permanently without config.env (next boot hard-fails).
    Restore any orphaned backup before doing anything else.

    A config.env that git tracks was just shipped by the upstream pull, not
    written by the operator (P3-11): the operator's backup wins there too.
    An untracked config.env is the operator's own (re-created after the
    crash) and is left alone; the stale backup stays in place for safety.
    """
    if not os.path.exists(_CONFIG_BACKUP):
        return
    restore = not os.path.exists("config.env")
    if not restore and os.path.isdir(".git") and shutil.which("git") is not None:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "config.env"],
            capture_output=True,
            timeout=10,
        )
        restore = tracked.returncode == 0
    if restore:
        try:
            os.replace(_CONFIG_BACKUP, "config.env")
            logger.info("Recovered config.env from orphaned backup.")
        except OSError as e:
            logger.error(f"Could not recover config.env backup: {e}")


def _backup_config() -> bool:
    try:
        if os.path.exists("config.env"):
            os.replace("config.env", _CONFIG_BACKUP)
            return True
    except OSError as e:
        logger.warning(f"Could not back up config.env: {e}")
    return False


def _restore_config(backed_up: bool) -> None:
    if backed_up and os.path.exists(_CONFIG_BACKUP):
        try:
            os.replace(_CONFIG_BACKUP, "config.env")
        except OSError as e:
            logger.error(f"Could not restore config.env: {e}")


def _redact_credentials(text: str) -> str:
    """Git echoes remote URLs on failure; strip embedded tokens (user:pass
    and user@host forms) before the output reaches the logs."""
    return re.sub(r"(?<=//)[^@/\s]+@", "<redacted>@", text)


def main() -> None:
    if not UPSTREAM_REPO:
        return
    if UPSTREAM_REPO.startswith("-") or UPSTREAM_BRANCH.startswith("-"):
        # operator-supplied env vars, but git would treat leading-dash values as options
        logger.info("UPSTREAM_REPO/UPSTREAM_BRANCH must not start with '-'; skipping self-update.")
        return
    _recover_config_backup()
    if shutil.which("git") is None:
        logger.info("git not available; skipping self-update (image without git).")
        return
    if not os.path.isdir(".git"):
        logger.info("Not a git repository; skipping self-update.")
        return

    # defense-in-depth: the argv/dash guards do not stop the git-remote-ext family
    # (ext::sh -c ...) -- allowlist ordinary transport schemes only. ssh:// is
    # excluded on purpose: the image ships no keys, so it would fail noisily
    # on every boot; use https with a token URL instead.
    if "://" in UPSTREAM_REPO and UPSTREAM_REPO.split("://", 1)[0] not in {
        "https",
        "http",
        "git",
    }:
        logger.error("UPSTREAM_REPO uses an unsupported scheme; skipping self-update.")
        return
    if UPSTREAM_REPO.startswith("ext::"):
        logger.error("UPSTREAM_REPO uses a forbidden scheme; skipping self-update.")
        return

    backed_up = _backup_config()
    try:
        # git >= 2.27 warns without the refspec; be explicit.
        result = subprocess.run(
            ["git", "pull", "--ff-only", UPSTREAM_REPO, UPSTREAM_BRANCH],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("Self-update pulled latest commit from UPSTREAM_REPO.")
        else:
            # keep running the old code; never hard-fail the boot
            logger.error(
                "Self-update failed (non-destructive, keeping current code): "
                f"{_redact_credentials((result.stderr or result.stdout or '').strip()[:500])}"
            )
    except subprocess.TimeoutExpired:
        logger.error("Self-update timed out; keeping current code.")
    except Exception as e:
        logger.error(f"Self-update failed: {e}; keeping current code.")
    finally:
        _restore_config(backed_up)


if __name__ == "__main__":
    main()
