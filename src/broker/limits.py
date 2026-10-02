import time
from typing import Dict, Tuple, Callable

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

    def _get_bucket(self, key: str, capacity: int, fill_rate: float) -> TokenBucket:
        if key not in self.buckets:
            self.buckets[key] = TokenBucket(capacity, fill_rate, self.clock)
        return self.buckets[key]

    def check_registration(self, ip: str) -> bool:
        # 5/IP/hour -> 5 capacity, rate = 5 / 3600 per second
        bucket = self._get_bucket(f"reg_{ip}", 5, 5 / 3600.0)
        return bucket.consume()

    def check_pairing(self, ip: str) -> bool:
        # 10/IP/minute -> 10 capacity, rate = 10 / 60
        bucket = self._get_bucket(f"pairing_{ip}", 10, 10 / 60.0)
        return bucket.consume()

    def check_authenticated_command(self, viewer_id: str) -> bool:
        # 120/viewer/minute -> 120 capacity, rate = 120 / 60 = 2/sec
        bucket = self._get_bucket(f"cmd_{viewer_id}", 120, 120 / 60.0)
        return bucket.consume()

    def check_preview(self, viewer_id: str) -> bool:
        # 2/viewer/second -> 2 capacity, rate = 2/sec
        bucket = self._get_bucket(f"preview_{viewer_id}", 2, 2.0)
        return bucket.consume()

    def check_credential_issuance(self, installation_id: str) -> bool:
        # 12/installation/hour -> 12 capacity, rate = 12 / 3600
        bucket = self._get_bucket(f"cred_{installation_id}", 12, 12 / 3600.0)
        return bucket.consume()
