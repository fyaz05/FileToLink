# Thunder/utils/logger.py

import logging
from logging.handlers import RotatingFileHandler
import sys
import os

# Get project root and setup log directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'bot.txt')

# Configure logger
logger = logging.getLogger('ThunderBot')
logger.setLevel(logging.INFO)
logger.propagate = False

# Setup log formatting
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Configure rotating file handler (10MB max, 5 backup files)
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,
    backupCount=5
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)


# Configure console output
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)


# Add handlers to our logger instance
logger.addHandler(file_handler)
logger.addHandler(console_handler)

__all__ = ['logger', 'LOG_FILE']
