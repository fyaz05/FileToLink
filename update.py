from os import path as opath, getenv, rename
from subprocess import run as srun
from dotenv import load_dotenv
from Thunder.utils.logger import logger

load_dotenv('config.env', override=True)

UPSTREAM_REPO = getenv('UPSTREAM_REPO', "https://github.com/fyaz05/FileToLink")
UPSTREAM_BRANCH = getenv('UPSTREAM_BRANCH', "main")

if UPSTREAM_REPO:
    config_backup = '../config.env.tmp'
    
    try:
        if opath.exists('config.env'):
            rename('config.env', config_backup)
        
        if opath.exists('.git'):
            srun(["rm", "-rf", ".git"])
        
        git_commands = (
            f"git init -q && "
            f"git config --global user.email thunder@update.local && "
            f"git config --global user.name Thunder && "
            f"git add . && "
            f"git commit -sm update -q && "
            f"git remote add origin {UPSTREAM_REPO} && "
            f"git fetch origin -q && "
            f"git reset --hard origin/{UPSTREAM_BRANCH} -q"
        )
        
        update = srun(git_commands, shell=True)
        
        logger.info('Successfully updated' if update.returncode == 0 else 'Update failed, check UPSTREAM_REPO')
            
    finally:
        if opath.exists(config_backup):
            rename(config_backup, 'config.env')
