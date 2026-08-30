from auth.throttle import LoginThrottle


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _throttle(clock, *, max_attempts=5, window_seconds=900, capacity=10_000):
    return LoginThrottle(
        max_attempts=max_attempts, window_seconds=window_seconds, capacity=capacity, clock=clock
    )


def test_not_locked_out_before_any_failures():
    clock = _FakeClock()
    throttle = _throttle(clock)

    assert throttle.is_locked_out(("a@example.com", "1.2.3.4")) is False


def test_not_locked_out_below_the_failure_threshold():
    clock = _FakeClock()
    throttle = _throttle(clock, max_attempts=5)
    key = ("a@example.com", "1.2.3.4")

    for _ in range(4):
        throttle.record_failure(key)

    assert throttle.is_locked_out(key) is False


def test_locked_out_at_the_failure_threshold():
    clock = _FakeClock()
    throttle = _throttle(clock, max_attempts=5)
    key = ("a@example.com", "1.2.3.4")

    for _ in range(5):
        throttle.record_failure(key)

    assert throttle.is_locked_out(key) is True


def test_lockout_expires_once_failures_age_out_of_the_window():
    clock = _FakeClock()
    throttle = _throttle(clock, max_attempts=5, window_seconds=900)
    key = ("a@example.com", "1.2.3.4")

    for _ in range(5):
        throttle.record_failure(key)
    assert throttle.is_locked_out(key) is True

    clock.advance(901)

    assert throttle.is_locked_out(key) is False


def test_reset_clears_recorded_failures():
    clock = _FakeClock()
    throttle = _throttle(clock, max_attempts=5)
    key = ("a@example.com", "1.2.3.4")

    for _ in range(5):
        throttle.record_failure(key)
    throttle.reset(key)

    assert throttle.is_locked_out(key) is False


def test_keys_are_independent():
    clock = _FakeClock()
    throttle = _throttle(clock, max_attempts=5)

    for _ in range(5):
        throttle.record_failure(("a@example.com", "1.2.3.4"))

    assert throttle.is_locked_out(("b@example.com", "1.2.3.4")) is False
    assert throttle.is_locked_out(("a@example.com", "5.6.7.8")) is False


def test_capacity_bound_evicts_the_least_recently_touched_key():
    clock = _FakeClock()
    throttle = _throttle(clock, max_attempts=1, capacity=2)

    throttle.record_failure(("a@example.com", "1.1.1.1"))
    throttle.record_failure(("b@example.com", "1.1.1.1"))
    throttle.record_failure(("c@example.com", "1.1.1.1"))

    assert throttle.is_locked_out(("a@example.com", "1.1.1.1")) is False
    assert throttle.is_locked_out(("b@example.com", "1.1.1.1")) is True
    assert throttle.is_locked_out(("c@example.com", "1.1.1.1")) is True
