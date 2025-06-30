# Thunder/utils/human_readable.py

_UNITS = ('', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')

def humanbytes(size: int, decimal_places: int = 2) -> str:
    try:
        if not size:
            return "0 B"
        n = 0
        while size >= 1024 and n < len(_UNITS) - 1:
            size /= 1024
            n += 1
        return f"{round(size, decimal_places)} {_UNITS[n]}B"
    except Exception as e:
        logger.error(f"Error in humanbytes for size {size}: {e}", exc_info=True)
        return "N/A"
