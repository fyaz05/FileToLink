# Thunder/utils/human_readable.py

def humanbytes(size: int, decimal_places: int = 2) -> str:
    # Converts bytes to human-readable format (KB, MB, GB, etc.)
    if not size:
        return "0 B"
        
    power = 2 ** 10
    n = 0
    units = ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']
    
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
        
    return f"{round(size, decimal_places)} {units[n]}B"
