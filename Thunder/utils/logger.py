# Thunder/utils/logger.py

import logging
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
import os
import queue
import atexit

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'bot.txt')

logging._srcfile = None
logging.logThreads = 0
logging.logProcesses = 0 

log_queue = queue.Queue(maxsize=10000)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

listener = QueueListener(log_queue, file_handler, console_handler, respect_handler_level=True)
listener.start()

logger = logging.getLogger('ThunderBot')
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(QueueHandler(log_queue))

atexit.register(listener.stop)

__all__ = ['logger', 'LOG_FILE']
