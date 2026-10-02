"""Bounded process-local evidence shared by a run's lead and research agents."""

from collections import OrderedDict
from collections.abc import Callable, Iterable
from threading import RLock
from time import monotonic

RunKey = tuple[str, str, str]


class EvidenceRegistry:
    """Fail closed on expiry, eviction, restart, or late child completion.

    Only the lead can start an entry. URLs are never recovered from a previous
    checkpoint. TTL is a backstop for termination between middleware hooks.
    """

    def __init__(self, *, max_runs: int = 256, max_urls: int = 2048, ttl_seconds: float = 7200, clock: Callable[[], float] = monotonic):
        self._entries: OrderedDict[RunKey, tuple[float, set[str], object]] = OrderedDict()
        self._lock = RLock()
        self._max_runs = max_runs
        self._max_urls = max_urls
        self._ttl = ttl_seconds
        self._clock = clock

    def _prune(self):
        now = self._clock()
        for key, (created, _, _) in list(self._entries.items()):
            if now - created >= self._ttl:
                del self._entries[key]

    @property
    def size(self) -> int:
        with self._lock:
            self._prune()
            return len(self._entries)

    def start(self, key: RunKey, session=None):
        with self._lock:
            self._prune()
            self._entries[key] = (self._clock(), set(), session)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_runs:
                self._entries.popitem(last=False)

    def active(self, key: RunKey | None) -> bool:
        with self._lock:
            self._prune()
            return key in self._entries

    def session(self, key: RunKey | None):
        with self._lock:
            self._prune()
            return self._entries[key][2] if key in self._entries else None

    def add(self, key: RunKey | None, urls: Iterable[str]):
        with self._lock:
            self._prune()
            if key in self._entries:
                seen = self._entries[key][1]
                for url in sorted(urls):
                    if len(seen) >= self._max_urls:
                        break
                    seen.add(url)

    def urls(self, key: RunKey | None) -> set[str]:
        with self._lock:
            self._prune()
            return set(self._entries[key][1]) if key in self._entries else set()

    def finish(self, key: RunKey | None):
        with self._lock:
            self._entries.pop(key, None)


RUN_EVIDENCE = EvidenceRegistry()
