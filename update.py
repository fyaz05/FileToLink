from os import path as opath, getenv, rename
from subprocess import run as srun
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
        
        result = srun(git_commands, shell=True)
        
        if result.returncode == 0:
            logger.info('Successfully updated with latest commit from UPSTREAM_REPO')
        else:
            logger.error('Something went wrong while updating, check UPSTREAM_REPO if valid or not!')
            
    finally:
        if opath.exists(config_backup):
            rename(config_backup, 'config.env')
