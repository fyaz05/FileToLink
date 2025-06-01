# Thunder/utils/human_readable.py
from Thunder.utils.error_handling import log_errors

_UNITS = ('', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')

@log_errors
def humanbytes(size: int, decimal_places: int = 2) -> str:
    if not size:
        return "0 B"
    n = 0
    while size >= 1024 and n < len(_UNITS) - 1:
        size /= 1024
        n += 1
    return f"{round(size, decimal_places)} {_UNITS[n]}B"
