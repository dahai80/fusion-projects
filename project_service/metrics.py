import threading
from collections import defaultdict

_lock = threading.Lock()
_requests_total = defaultdict(int)
_requests_by_status = defaultdict(int)
_rate_limit_rejected = 0
_auth_rejected = 0
_body_oversize = 0
_rag_query_total = 0
_rag_recall_sum = 0
_rag_zero_recall = 0
_rag_below_threshold = 0


def record_request(path: str, status_code: int) -> None:
    global _requests_total, _requests_by_status
    with _lock:
        _requests_total[path] += 1
        _requests_by_status[status_code] += 1


def record_rag_query(recalled: int, below_threshold: int = 0) -> None:
    global _rag_query_total, _rag_recall_sum, _rag_zero_recall, _rag_below_threshold
    with _lock:
        _rag_query_total += 1
        _rag_recall_sum += recalled
        if recalled == 0:
            _rag_zero_recall += 1
        _rag_below_threshold += below_threshold


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
        avg_recall = (_rag_recall_sum / _rag_query_total) if _rag_query_total else 0.0
        return {
            "requests_total": dict(_requests_total),
            "requests_by_status": {str(k): v for k, v in _requests_by_status.items()},
            "rate_limit_rejected": _rate_limit_rejected,
            "auth_rejected": _auth_rejected,
            "body_oversize_rejected": _body_oversize,
            "total_requests": sum(_requests_total.values()),
            "rag_query_total": _rag_query_total,
            "rag_avg_recall": round(avg_recall, 2),
            "rag_zero_recall": _rag_zero_recall,
            "rag_below_threshold": _rag_below_threshold,
        }


def reset() -> None:
    global _requests_total, _requests_by_status, _rate_limit_rejected, _auth_rejected, _body_oversize
    global _rag_query_total, _rag_recall_sum, _rag_zero_recall, _rag_below_threshold
    with _lock:
        _requests_total = defaultdict(int)
        _requests_by_status = defaultdict(int)
        _rate_limit_rejected = 0
        _auth_rejected = 0
        _body_oversize = 0
        _rag_query_total = 0
        _rag_recall_sum = 0
        _rag_zero_recall = 0
        _rag_below_threshold = 0
