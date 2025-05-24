# Thunder/utils/time_format.py

def get_readable_time(seconds: int) -> str:
    # Convert seconds to human-readable time format (e.g., "2d 5h 30m 10s")
    periods = [
        ('d', 86400),  # Days
        ('h', 3600),   # Hours
        ('m', 60),     # Minutes
        ('s', 1)       # Seconds
    ]
    result = []

    for suffix, period in periods:
        if seconds >= period:
            value, seconds = divmod(seconds, period)
            result.append(f"{int(value)}{suffix}")

    return ' '.join(result) if result else '0s'
