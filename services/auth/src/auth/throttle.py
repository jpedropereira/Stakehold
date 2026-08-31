import time
from collections import OrderedDict

# key = (subject, client_ip) -- for login the subject is the normalized email, for signup the
# fixed SIGNUP_THROTTLE_SUBJECT sentinel (there is no account yet to key on). Keying on the pair
# means one attacker can't lock a legitimate user's login out by hammering their email from many
# IPs, and can't exhaust the limiter for the whole service by cycling subjects from one IP.
#
# The client_ip half only does that job if it is the *browser's* address, not `web`'s -- see
# api/deps.py's client_ip() and architecture.md on why X-Forwarded-For is trusted here.
ThrottleKey = tuple[str, str]

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60

# Deliberately looser than login's, and counted per source rather than per account: signup has no
# account to protect, so the limit exists to bound account-enumeration probing and the Argon2 cost
# an unauthenticated caller can force, not to lock anyone out.
SIGNUP_MAX_ATTEMPTS = 10
SIGNUP_WINDOW_SECONDS = 60 * 60


class AttemptThrottle:
    """Bounded, in-process sliding-window limiter for attempts against one key.

    What counts as an "attempt" is the caller's choice: `/login` records only failures (a correct
    password resets the key), while `/signup` records every attempt, since a successful signup
    costs the same Argon2 work an abusive one does.

    In-process and per-worker: correct only while `auth` runs as a single uvicorn worker, and
    the map resets on restart. Accepted for now -- see architecture.md's open questions for the
    trigger to replace this with a shared store (Redis, or a login_attempts table).
    """

    def __init__(
        self,
        *,
        max_attempts: int = LOGIN_MAX_ATTEMPTS,
        window_seconds: int = LOGIN_WINDOW_SECONDS,
        capacity: int = 10_000,
        clock=time.monotonic,
    ):
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._capacity = capacity
        self._clock = clock
        self._attempts: OrderedDict[ThrottleKey, list[float]] = OrderedDict()

    def _pruned(self, key: ThrottleKey, now: float) -> list[float]:
        cutoff = now - self._window_seconds
        return [t for t in self._attempts.get(key, ()) if t > cutoff]

    def is_locked_out(self, key: ThrottleKey) -> bool:
        return len(self._pruned(key, self._clock())) >= self._max_attempts

    def record_attempt(self, key: ThrottleKey) -> None:
        now = self._clock()
        timestamps = self._pruned(key, now)
        timestamps.append(now)
        self._attempts[key] = timestamps
        self._attempts.move_to_end(key)
        # Capacity bound so an attacker cycling through keys can't grow this map without limit;
        # evict the least-recently-touched key first.
        while len(self._attempts) > self._capacity:
            self._attempts.popitem(last=False)

    def reset(self, key: ThrottleKey) -> None:
        self._attempts.pop(key, None)
