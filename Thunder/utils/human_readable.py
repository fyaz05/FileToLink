# Thunder/utils/human_readable.py

def humanbytes(size: int, decimal_places: int = 2) -> str:
    """
    Converts bytes to a human-readable format (e.g., KB, MB, GB).

    Args:
        size (int): The size in bytes.
        decimal_places (int): Number of decimal places for rounding.

    Returns:
        str: The human-readable size.
    """
    if not size:
        return "0 B"
    power = 2 ** 10
    n = 0
    units = ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{round(size, decimal_places)} {units[n]}B"
