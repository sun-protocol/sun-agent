import sys
import time
from collections import deque
from datetime import datetime, timedelta

import pytz


class RateLimit:
    """
    RateLimit not persistent
    """

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.timestamps = deque()

    def acquire_quota(self) -> bool:
        if self.limit == 0:
            return False
        current_time = int(time.time())
        self._release_quota(current_time)
        if len(self.timestamps) >= self.limit:
            return False
        self.timestamps.append(current_time)
        return True

    def rollback_quota(self) -> None:
        if len(self.timestamps) > 0:
            self.timestamps.pop()

    def remain_quota(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.limit - len(self.timestamps)

    def _fill_quota(self):
        current_time = int(time.time())
        for _ in range(self.limit - len(self.timestamps)):
            self.timestamps.append(current_time)

    def recover_time(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        if self.limit == 0:
            return sys.maxsize
        return self.timestamps[0] + self.window if len(self.timestamps) >= self.limit else 0

    def _release_quota(self, current_time: int):
        cutoff = current_time - self.window
        while len(self.timestamps) > 0 and self.timestamps[0] < cutoff:
            self.timestamps.popleft()


class DailyRateLimit(RateLimit):
    def __init__(self, limit: int, utc_timezone: int):
        super().__init__(limit, 86400)
        timezone = pytz.FixedOffset(utc_timezone * 60)
        now = datetime.now(timezone)
        tomorrow = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone) + timedelta(days=1)
        self.fresh_time = int(tomorrow.timestamp())
        self.cnt = 0

    def acquire_quota(self) -> bool:
        current_time = int(time.time())
        self._release_quota(current_time)
        if self.cnt >= self.limit:
            return False
        self.cnt += 1
        return True

    def rollback(self) -> None:
        self.cnt -= 1

    def remain_quota(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.limit - self.cnt

    def recover_time(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.fresh_time if len(self.timestamps) >= self.limit else 0

    def _release_quota(self, current_time: int):
        if current_time < self.fresh_time:
            return
        self.fresh_time += self.window
        self.cnt = 0
