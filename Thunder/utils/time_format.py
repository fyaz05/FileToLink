# Thunder/utils/time_format.py

def get_readable_time(seconds: int) -> str:
    """
    Convert seconds into a human-readable time format.

    Args:
        seconds (int): Time in seconds.

    Returns:
        str: Human-readable time string.
    """
    periods = [
        ('d', 86400),  # 86400 seconds in a day
        ('h', 3600),   # 3600 seconds in an hour
        ('m', 60),     # 60 seconds in a minute
        ('s', 1),
    ]
    result = []

    for suffix, period in periods:
        if seconds >= period:
            value, seconds = divmod(seconds, period)
            result.append(f"{int(value)}{suffix}")

    return ' '.join(result) if result else '0s'
