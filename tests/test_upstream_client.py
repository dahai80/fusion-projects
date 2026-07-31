import time

from project_service.engine.upstream_client import CircuitBreaker, CircuitState


def test_circuit_starts_closed():
    cb = CircuitBreaker(name="test")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_call()


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(name="test", failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert not cb.can_call()


def test_circuit_half_open_after_recovery_timeout():
    cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    assert cb.can_call()
    assert cb.state == CircuitState.HALF_OPEN  # state transitions in can_call


def test_circuit_closes_on_success_in_half_open():
    cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    cb.can_call()  # triggers OPEN -> HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_circuit_reopens_on_failure_in_half_open():
    cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    cb.can_call()  # triggers OPEN -> HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_on_call_start_increments_half_open_calls():
    cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    cb.can_call()  # OPEN -> HALF_OPEN
    cb.on_call_start()
    assert cb.half_open_calls == 1


def test_circuit_state_and_counts():
    cb = CircuitBreaker(name="svc", failure_threshold=5, recovery_timeout=30)
    cb.record_failure()
    assert cb.name == "svc"
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 1
    assert cb.failure_threshold == 5
