from Thunder.utils.error_handling import log_errors

_TIME_PERIODS = (('d', 86400), ('h', 3600), ('m', 60), ('s', 1))

@log_errors
def get_readable_time(seconds: int) -> str:
    result = []
    for suffix, period in _TIME_PERIODS:
        if seconds >= period:
            value, seconds = divmod(seconds, period)
            result.append(f"{value}{suffix}")
    return ' '.join(result) if result else '0s'
