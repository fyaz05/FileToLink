import logging
import threading
import time
from typing import Callable

# Configure logging
LOGGER = logging.getLogger(__name__)
SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

class SetInterval:
    """A class that mimics setInterval from JavaScript using a threading approach."""

    def __init__(self, interval: float, action: Callable):
        """
        Initialize the interval and the action (function) to run.
        :param interval: Time in seconds between each action execution.
        :param action: The function to be executed periodically.
        """
        self.interval = interval
        self.action = action
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.__set_interval)
        self.thread.daemon = True
        self.thread.start()

    def __set_interval(self):
        """Runs the action every `interval` seconds."""
        next_time = time.time() + self.interval
        while not self.stop_event.wait(next_time - time.time()):
            next_time += self.interval
            self.action()

    def cancel(self):
        """Cancel the interval execution."""
        self.stop_event.set()

def get_readable_file_size(size_in_bytes: int) -> str:
    """
    Convert a file size in bytes to a human-readable string with units.
    :param size_in_bytes: Size in bytes.
    :return: Human-readable size string.
    """
    if size_in_bytes is None:
        return '0B'

    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1

    return f'{round(size_in_bytes, 2)} {SIZE_UNITS[index]}'

def get_readable_time(seconds: int) -> str:
    """
    Convert seconds into a human-readable time format.
    :param seconds: Time in seconds.
    :return: Human-readable time string.
    """
    periods = [
        ('d', 86400),
        ('h', 3600),
        ('m', 60),
        ('s', 1),
    ]
    result = []

    for suffix, period in periods:
        if seconds >= period:
            value, seconds = divmod(seconds, period)
            result.append(f'{int(value)}{suffix}')

    return ' '.join(result) if result else '0s'

# Example usage
if __name__ == "__main__":
    # Just a demonstration of how SetInterval might be used.
    def example_action():
        print("Action executed!")

    interval_runner = SetInterval(5, example_action)

    # Wait for a while before canceling
    time.sleep(20)
    interval_runner.cancel()
