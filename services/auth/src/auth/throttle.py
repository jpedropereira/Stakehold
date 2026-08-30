import time
from collections import OrderedDict

# key = (normalized_email, client_ip) -- keying on the pair means one attacker can't lock out a
# legitimate user's login by hammering just their email from many IPs, and can't exhaust the
# limiter for the whole service by cycling emails from one IP.
ThrottleKey = tuple[str, str]


class LoginThrottle:
    """Bounded, in-process sliding-window limiter for failed logins.

    In-process and per-worker: correct only while `auth` runs as a single uvicorn worker, and
    the map resets on restart. Accepted for now -- see architecture.md's open questions for the
    trigger to replace this with a shared store (Redis, or a login_attempts table).
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: int = 15 * 60,
        capacity: int = 10_000,
        clock=time.monotonic,
    ):
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._capacity = capacity
        self._clock = clock
        self._failures: OrderedDict[ThrottleKey, list[float]] = OrderedDict()

    def _pruned(self, key: ThrottleKey, now: float) -> list[float]:
        cutoff = now - self._window_seconds
        return [t for t in self._failures.get(key, ()) if t > cutoff]

    def is_locked_out(self, key: ThrottleKey) -> bool:
        return len(self._pruned(key, self._clock())) >= self._max_attempts

    def record_failure(self, key: ThrottleKey) -> None:
        now = self._clock()
        timestamps = self._pruned(key, now)
        timestamps.append(now)
        self._failures[key] = timestamps
        self._failures.move_to_end(key)
        # Capacity bound so an attacker cycling through keys can't grow this map without limit;
        # evict the least-recently-touched key first.
        while len(self._failures) > self._capacity:
            self._failures.popitem(last=False)

    def reset(self, key: ThrottleKey) -> None:
        self._failures.pop(key, None)
