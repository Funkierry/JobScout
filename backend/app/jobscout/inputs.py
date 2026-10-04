"""Server-owned Base snapshots and bounded reads of current-thread uploads."""

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from time import monotonic


class BaseSnapshotStore:
    """Opaque references are not authority: every read checks user and thread."""

    def __init__(self, *, max_entries=64, ttl_seconds=1800, clock=monotonic):
        self._entries = OrderedDict()
        self._max_entries, self._ttl, self._clock = max_entries, ttl_seconds, clock
        self._lock = RLock()

    def _prune(self):
        for ref, (created, _, _, _) in list(self._entries.items()):
            if self._clock() - created >= self._ttl:
                del self._entries[ref]

    @property
    def size(self):
        with self._lock:
            self._prune()
            return len(self._entries)

    def put(self, user_id, thread_id, records, *, has_more=False, context_truncated=False):
        # Only the gateway's bounded read-only Base projection belongs here.
        with self._lock:
            self._prune()
            ref = token_urlsafe(32)
            snapshot = {"records": deepcopy(records[:200]), "has_more": bool(has_more), "context_truncated": bool(context_truncated)}
            self._entries[ref] = (self._clock(), user_id, thread_id, snapshot)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return ref

    def drop_thread(self, user_id, thread_id):
        with self._lock:
            for ref, entry in list(self._entries.items()):
                if entry[1:3] == (user_id, thread_id):
                    del self._entries[ref]

    def get(self, ref, user_id, thread_id):
        snapshot = self.get_snapshot(ref, user_id, thread_id)
        return snapshot["records"] if snapshot else None

    def get_snapshot(self, ref, user_id, thread_id):
        with self._lock:
            self._prune()
            entry = self._entries.get(ref) if isinstance(ref, str) else None
            if entry is None or entry[1:3] != (user_id, thread_id):
                return None
            return deepcopy(entry[3])


BASE_SNAPSHOTS = BaseSnapshotStore()


def read_uploaded_resumes(state, user_id, thread_id):
    """Snapshot human-upload filenames inside the owner's thread directory.

    No logging, private data in memory only. Latest turn containing uploads wins,
    permitting a follow-up to use an earlier upload in the same thread.
    """
    from deerflow.uploads.manager import get_uploads_dir

    files = []
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "human":
            metadata = getattr(message, "additional_kwargs", {}).get("files")
            if isinstance(metadata, list) and metadata:
                files = metadata[:4]
                break
    if not files:
        return {}
    try:
        base = get_uploads_dir(thread_id, user_id=user_id).resolve()
    except (OSError, ValueError):
        return {}
    result = {}
    for entry in files:
        name = entry.get("filename") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name or len(name) > 255 or any(c in name for c in "/\\:") or Path(name).suffix.lower() not in {".md", ".txt"}:
            continue
        path = base / name
        try:
            if path.is_symlink() or path.resolve().parent != base or not path.is_file() or path.stat().st_size > 200_000:
                continue
            with path.open("rb") as handle:
                data = handle.read(200_001)
            if len(data) <= 200_000:
                result[name] = data.decode("utf-8-sig")
        except (OSError, UnicodeError):
            continue
    return result
