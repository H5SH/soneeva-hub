import time

MAX_ENTRIES = 20_000


class ReplayGuard:
    """Rejects a jti that has already been seen.

    Tokens expire in 60s, so in-process memory is enough. Per-instance, so a replay could land
    on another container — shared state would close that for very little gain at this size.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def check_and_record(self, jti: str, expires_at: int) -> bool:
        """True when this jti is fresh. False means it has been used before."""
        now = time.time()
        if len(self._seen) > MAX_ENTRIES:
            self._purge(now)
        if self._seen.get(jti, 0.0) > now:
            return False
        self._seen[jti] = float(expires_at)
        return True

    def _purge(self, now: float) -> None:
        self._seen = {jti: exp for jti, exp in self._seen.items() if exp > now}
