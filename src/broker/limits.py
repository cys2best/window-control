import time
import threading
from collections import deque
from typing import Dict, Tuple, Callable

MAX_RATE_LIMIT_KEYS = 4096


class TokenBucket:
    def __init__(self, capacity: int, fill_rate: float, clock: Callable[[], float]):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.clock = clock
        self.tokens = capacity
        self.last_fill = clock()

    def consume(self, amount: int = 1) -> bool:
        now = self.clock()
        elapsed = now - self.last_fill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        self.last_fill = now
        
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

class BrokerLimits:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.buckets: Dict[str, TokenBucket] = {}
        self.issuances = {}
        # HTTP registrations can arrive on multiple threadpool workers.
        self._lock = threading.RLock()

    def _consume(self, key: str, capacity: int, fill_rate: float) -> bool:
        with self._lock:
            now = self.clock()
            for old_key, bucket in list(self.buckets.items()):
                # Recreating this key now grants no more than natural refill.
                if now - bucket.last_fill >= bucket.capacity / bucket.fill_rate:
                    del self.buckets[old_key]
            if key not in self.buckets:
                if len(self.buckets) >= MAX_RATE_LIMIT_KEYS:
                    return False
                self.buckets[key] = TokenBucket(capacity, fill_rate, self.clock)
            return self.buckets[key].consume()

    def check_registration(self, ip: str) -> bool:
        return self._consume(f"reg_{ip}", 5, 5 / 3600.0)

    def check_pairing(self, ip: str) -> bool:
        return self._consume(f"pairing_{ip}", 10, 10 / 60.0)

    def check_authenticated_command(self, viewer_id: str) -> bool:
        return self._consume(f"cmd_{viewer_id}", 120, 2.0)

    def check_preview(self, viewer_id: str) -> bool:
        return self._consume(f"preview_{viewer_id}", 2, 2.0)

    def check_credential_issuance(self, installation_id: str) -> bool:
        with self._lock:
            now = self.clock()
            for key, entries in list(self.issuances.items()):
                while entries and entries[0] <= now - 3600:
                    entries.popleft()
                if not entries:
                    del self.issuances[key]
            if installation_id not in self.issuances:
                if len(self.issuances) >= MAX_RATE_LIMIT_KEYS:
                    return False
                self.issuances[installation_id] = deque()
            entries = self.issuances[installation_id]
            if len(entries) >= 12:
                return False
            entries.append(now)
            return True
