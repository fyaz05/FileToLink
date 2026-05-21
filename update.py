from os import getenv, rename
from os import path as opath
from subprocess import run as srun, CalledProcessError

from dotenv import load_dotenv

from Thunder.utils.logger import logger

load_dotenv('config.env', override=True)

UPSTREAM_REPO = getenv('UPSTREAM_REPO', "")
UPSTREAM_BRANCH = getenv('UPSTREAM_BRANCH', "main")

if UPSTREAM_REPO:
    config_backup = '../config.env.tmp'

    try:
        if opath.exists('config.env'):
            rename('config.env', config_backup)

        if opath.exists('.git'):
            srun(["rm", "-rf", ".git"])

        try:
            srun(["git", "init", "-q"], check=True)
            srun(["git", "config", "user.email", "thunder@update.local"], check=True)
            srun(["git", "config", "user.name", "Thunder"], check=True)
            srun(["git", "add", "."], check=True)
            srun(["git", "commit", "-sm", "update", "-q"], check=True)
            srun(["git", "remote", "add", "origin", UPSTREAM_REPO], check=True)
            srun(["git", "fetch", "origin", "-q"], check=True)
            srun(["git", "reset", "--hard", f"origin/{UPSTREAM_BRANCH}", "-q"], check=True)
            logger.info('Successfully updated with latest commit from UPSTREAM_REPO')
        except CalledProcessError as e:
            logger.error(f'Something went wrong while updating (exit code {e.returncode}). Check UPSTREAM_REPO!')
        except Exception as e:
            logger.error(f'Update failed: {e}')

    finally:
        if opath.exists(config_backup):
            rename(config_backup, 'config.env')
