# Thunder/utils/time_format.py

from Thunder.utils.logger import logger

_TIME_PERIODS = (('d', 86400), ('h', 3600), ('m', 60), ('s', 1))

def get_readable_time(seconds: int) -> str:
    try:
        result = []
        for suffix, period in _TIME_PERIODS:
            if seconds >= period:
                value, seconds = divmod(int(seconds), period)
                result.append(f"{int(value)}{suffix}")
        return ' '.join(result) if result else '0s'
    except Exception as e:
        logger.error(f"Error in get_readable_time: {e}", exc_info=True)
        return "N/A"
