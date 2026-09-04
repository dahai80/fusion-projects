import threading
import time
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
# M12: upstream gateway observability. track per-upstream request count,
# error count, retry count and rolling latency sum so /metrics can surface
# a degraded upstream instead of failing silently.
_gateway_requests = defaultdict(int)
_gateway_errors = defaultdict(int)
_gateway_retries = defaultdict(int)
_gateway_latency_sum = defaultdict(float)
_gateway_latency_count = defaultdict(int)
# M10: UDS dispatch counters (the REST middleware already records path-level
# requests; this is the UDS surface which previously had zero metrics).
_uds_requests_total = defaultdict(int)
_uds_requests_by_status = defaultdict(int)
# M14: identity verify outcome counters (positive hits, misses, denials).
_identity_verify_ok = 0
_identity_verify_fail = 0
_identity_verify_cached = 0


def record_request(path: str, status_code: int) -> None:
    global _requests_total, _requests_by_status
    with _lock:
        _requests_total[path] += 1
        _requests_by_status[status_code] += 1


def record_uds_request(method: str, status_code: int) -> None:
    # M10: UDS dispatch had no metrics; record method + outcome (ok/error code).
    global _uds_requests_total, _uds_requests_by_status
    with _lock:
        _uds_requests_total[method] += 1
        _uds_requests_by_status[status_code] += 1


def record_gateway(upstream: str, *, ok: bool, retries: int = 0, latency: float = 0.0) -> None:
    # M12: per-upstream request/error/retry/latency counters.
    global _gateway_errors
    with _lock:
        _gateway_requests[upstream] += 1
        if not ok:
            _gateway_errors[upstream] += 1
        if retries:
            _gateway_retries[upstream] += retries
        if latency:
            _gateway_latency_sum[upstream] += latency
            _gateway_latency_count[upstream] += 1


def record_identity_verify(*, ok: bool, cached: bool = False) -> None:
    # M14/M12: identity verify outcome so a flood of denials is visible.
    global _identity_verify_ok, _identity_verify_fail, _identity_verify_cached
    with _lock:
        if cached:
            _identity_verify_cached += 1
        elif ok:
            _identity_verify_ok += 1
        else:
            _identity_verify_fail += 1


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
    # copy the counters under the lock, then compute derived values outside.
    # this keeps the critical section to a few dict copies so async recorders
    # (which call record_* without to_thread) never stall on a held lock long
    # enough to matter, and the computation can't block a recorder.
    with _lock:
        requests_total = dict(_requests_total)
        requests_by_status = {str(k): v for k, v in _requests_by_status.items()}
        rate_limit_rejected = _rate_limit_rejected
        auth_rejected = _auth_rejected
        body_oversize = _body_oversize
        rag_query_total = _rag_query_total
        rag_recall_sum = _rag_recall_sum
        rag_zero_recall = _rag_zero_recall
        rag_below_threshold = _rag_below_threshold
        uds_requests_total = dict(_uds_requests_total)
        uds_requests_by_status = {str(k): v for k, v in _uds_requests_by_status.items()}
        gateway_requests = dict(_gateway_requests)
        gateway_errors = dict(_gateway_errors)
        gateway_retries = dict(_gateway_retries)
        gateway_latency_sum = dict(_gateway_latency_sum)
        gateway_latency_count = dict(_gateway_latency_count)
        identity_verify_ok = _identity_verify_ok
        identity_verify_fail = _identity_verify_fail
        identity_verify_cached = _identity_verify_cached
    avg_recall = (rag_recall_sum / rag_query_total) if rag_query_total else 0.0
    gateway = {}
    for up in set(gateway_requests) | set(gateway_errors):
        cnt = gateway_latency_count.get(up, 0)
        gateway[up] = {
            "requests": gateway_requests.get(up, 0),
            "errors": gateway_errors.get(up, 0),
            "retries": gateway_retries.get(up, 0),
            "avg_latency_ms": round((gateway_latency_sum.get(up, 0.0) / cnt * 1000.0), 2) if cnt else 0.0,
        }
    return {
        "requests_total": requests_total,
        "requests_by_status": requests_by_status,
        "rate_limit_rejected": rate_limit_rejected,
        "auth_rejected": auth_rejected,
        "body_oversize_rejected": body_oversize,
        "total_requests": sum(requests_total.values()),
        "rag_query_total": rag_query_total,
        "rag_avg_recall": round(avg_recall, 2),
        "rag_zero_recall": rag_zero_recall,
        "rag_below_threshold": rag_below_threshold,
        "uds_requests_total": uds_requests_total,
        "uds_requests_by_status": uds_requests_by_status,
        "gateway": gateway,
        "identity_verify_ok": identity_verify_ok,
        "identity_verify_fail": identity_verify_fail,
        "identity_verify_cached": identity_verify_cached,
    }


def reset() -> None:
    # clear in place rather than rebinding the container globals — rebind
    # lets in-flight recorders keep writing to the old (discarded) dicts.
    global _rate_limit_rejected, _auth_rejected, _body_oversize
    global _rag_query_total, _rag_recall_sum, _rag_zero_recall, _rag_below_threshold
    global _identity_verify_ok, _identity_verify_fail, _identity_verify_cached
    with _lock:
        _requests_total.clear()
        _requests_by_status.clear()
        _rate_limit_rejected = 0
        _auth_rejected = 0
        _body_oversize = 0
        _rag_query_total = 0
        _rag_recall_sum = 0
        _rag_zero_recall = 0
        _rag_below_threshold = 0
        _uds_requests_total.clear()
        _uds_requests_by_status.clear()
        _gateway_requests.clear()
        _gateway_errors.clear()
        _gateway_retries.clear()
        _gateway_latency_sum.clear()
        _gateway_latency_count.clear()
        _identity_verify_ok = 0
        _identity_verify_fail = 0
        _identity_verify_cached = 0
