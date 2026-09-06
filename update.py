"""Boot-time best-effort self-update (plan H9).

Replaces the historical ``shell=True`` command chain that interpolated
``UPSTREAM_REPO``/``UPSTREAM_BRANCH`` env vars into a shell string executed
at every container boot (live injection vector), and that ran
``rm -rf .git``, ``git reset --hard`` and mutated **global** git config --
a failed update could leave a half-wiped tree that no longer boots.

New behaviour:

* argv-list subprocess, ``shell=False``; the remote URL from the
  environment is passed as an argv element, never through a shell;
* non-destructive: no ``rm -rf .git``, no ``reset --hard``, no global
  config mutation -- ``pull --ff-only`` keeps local history intact;
* strictly best-effort: any failure logs and leaves the existing tree
  running the old code;
* no-ops cleanly when ``git`` is missing or the tree is not a git repo
  (some PaaS images).
"""

import os
import re
import shutil
import subprocess

from dotenv import load_dotenv

from Thunder.utils.logger import logger

load_dotenv("config.env", override=True)

UPSTREAM_REPO = os.getenv("UPSTREAM_REPO", "")
UPSTREAM_BRANCH = os.getenv("UPSTREAM_BRANCH", "main")

# config.env lives beside the app and must survive the pull
_CONFIG_BACKUP = "../config.env.tmp"


def _recover_config_backup() -> None:
    """A crash between _backup_config and _restore_config would otherwise
    leave the app permanently without config.env (next boot hard-fails).
    Restore any orphaned backup before doing anything else."""
    if not os.path.exists(_CONFIG_BACKUP) and not os.path.exists("config.env"):
        return
    if os.path.exists(_CONFIG_BACKUP) and not os.path.exists("config.env"):
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
    """git echoes remote URLs on failure; strip embedded tokens (user:pass
    and user@host forms) before the output reaches the logs."""
    return re.sub(r"(?<=//)[^@/\s]+@", "<redacted>@", text)


def main() -> None:
    if not UPSTREAM_REPO:
        return
    if UPSTREAM_REPO.startswith("-") or UPSTREAM_BRANCH.startswith("-"):
        # git would treat leading-dash values as its own options; these are
        # operator-supplied env vars, but stay on the safe side of argv
        logger.info("UPSTREAM_REPO/UPSTREAM_BRANCH must not start with '-'; skipping self-update.")
        return
    _recover_config_backup()
    if shutil.which("git") is None:
        logger.info("git not available; skipping self-update (image without git).")
        return
    if not os.path.isdir(".git"):
        logger.info("Not a git repository; skipping self-update.")
        return

    # defense-in-depth: UPSTREAM_REPO flows into git's remote handling, and
    # the argv/dash guards do not stop the git-remote-ext family
    # (ext::sh -c ...) -- allowlist the ordinary transport schemes only.
    if "://" in UPSTREAM_REPO and UPSTREAM_REPO.split("://", 1)[0] not in {
        "https",
        "http",
        "git",
        "ssh",
    }:
        logger.error("UPSTREAM_REPO uses a unsupported scheme; skipping self-update.")
        return
    if UPSTREAM_REPO.startswith(("ext::", "ssh://;")):
        logger.error("UPSTREAM_REPO uses a forbidden scheme; skipping self-update.")
        return

    backed_up = _backup_config()
    try:
        # git >= 2.27 warns without the refspec; be explicit.
        result = subprocess.run(
            ["git", "-C", os.getcwd(), "pull", "--ff-only", UPSTREAM_REPO, UPSTREAM_BRANCH],
            shell=False,
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
