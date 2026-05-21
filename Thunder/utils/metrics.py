import time
from collections import defaultdict

_start_time = time.time()
_request_counts: dict[str, int] = defaultdict(int)
_error_counts: dict[int, int] = defaultdict(int)
_MAX_REQUEST_KEYS = 1000
_MAX_ERROR_KEYS = 100
_active_streams: int = 0
_total_bytes_served: int = 0


def record_request(path: str, status: int) -> None:
    if len(_request_counts) >= _MAX_REQUEST_KEYS and path not in _request_counts:
        min_key = min(_request_counts, key=_request_counts.get)
        del _request_counts[min_key]
    _request_counts[path] += 1
    if status >= 400:
        if len(_error_counts) >= _MAX_ERROR_KEYS and status not in _error_counts:
            min_key = min(_error_counts, key=_error_counts.get)
            del _error_counts[min_key]
        _error_counts[status] += 1


def inc_active_streams() -> None:
    global _active_streams
    _active_streams += 1


def dec_active_streams() -> None:
    global _active_streams
    _active_streams = max(0, _active_streams - 1)


def add_bytes_served(n: int) -> None:
    global _total_bytes_served
    _total_bytes_served += n


def get_metrics_text() -> str:
    uptime = time.time() - _start_time
    lines = [
        "# HELP thunder_uptime_seconds Time since bot started",
        "# TYPE thunder_uptime_seconds gauge",
        f"thunder_uptime_seconds {uptime:.0f}",
        "",
        "# HELP thunder_active_streams Currently active file streams",
        "# TYPE thunder_active_streams gauge",
        f"thunder_active_streams {_active_streams}",
        "",
        "# HELP thunder_bytes_served_total Total bytes served via streaming",
        "# TYPE thunder_bytes_served_total counter",
        f"thunder_bytes_served_total {_total_bytes_served}",
        "",
        "# HELP thunder_requests_total Total HTTP requests by path",
        "# TYPE thunder_requests_total counter",
    ]
    for path, count in sorted(_request_counts.items()):
        lines.append(f'thunder_requests_total{{path="{path}"}} {count}')

    lines.append("")
    lines.append("# HELP thunder_errors_total Total HTTP errors by status code")
    lines.append("# TYPE thunder_errors_total counter")
    for status, count in sorted(_error_counts.items()):
        lines.append(f"thunder_errors_total{{status=\"{status}\"}} {count}")

    return "\n".join(lines) + "\n"
