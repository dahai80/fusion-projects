import threading
from collections import defaultdict

_lock = threading.Lock()
_requests_total = defaultdict(int)
_requests_by_status = defaultdict(int)
_rate_limit_rejected = 0
_auth_rejected = 0
_body_oversize = 0


def record_request(path: str, status_code: int) -> None:
    global _requests_total, _requests_by_status
    with _lock:
        _requests_total[path] += 1
        _requests_by_status[status_code] += 1


def record_rate_limit_reject() -> None:
    global _rate_limit_rejected
    with _lock:
        _rate_limit_rejected += 1


def record_auth_reject() -> None:
    global _auth_rejected
    with _lock:
        _auth_rejected += 1


def record_body_oversize() -> None:
    global _body_oversize
    with _lock:
        _body_oversize += 1


def snapshot() -> dict:
    with _lock:
        return {
            "requests_total": dict(_requests_total),
            "requests_by_status": {str(k): v for k, v in _requests_by_status.items()},
            "rate_limit_rejected": _rate_limit_rejected,
            "auth_rejected": _auth_rejected,
            "body_oversize_rejected": _body_oversize,
            "total_requests": sum(_requests_total.values()),
        }


def reset() -> None:
    global _requests_total, _requests_by_status, _rate_limit_rejected, _auth_rejected, _body_oversize
    with _lock:
        _requests_total = defaultdict(int)
        _requests_by_status = defaultdict(int)
        _rate_limit_rejected = 0
        _auth_rejected = 0
        _body_oversize = 0
