# Thunder/utils/logger.py

import logging
from logging.handlers import RotatingFileHandler
import sys
import os

# ==============================
# Configuration Settings
# ==============================

# Get the absolute path to the project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Define the log directory and ensure it exists
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# Define the log file path
LOG_FILE = os.path.join(LOG_DIR, 'bot.log')

# ==============================
# Logger Setup
# ==============================

# Create a logger object with a centralized name
logger = logging.getLogger('ThunderBot')
logger.setLevel(logging.INFO)  # Set the desired logging level

# Define the format for log messages
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Set up a rotating file handler to prevent the log file from growing indefinitely
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10MB per file
    backupCount=5               # Keep 5 backup files
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Set up a console handler to output logs to the console
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# Add handlers to the logger if they haven't been added already
if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# ==============================
# Exported Variables
# ==============================

__all__ = ['logger', 'LOG_FILE']
