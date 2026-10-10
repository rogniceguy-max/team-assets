"""Server-Sent Events (SSE) broadcasting and revision tracking."""
import asyncio
import json
from core.utils import now


class EventBusMixin:
    """Provides change listeners and event logging for SSE clients."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._change_listeners: set[tuple[asyncio.AbstractEventLoop | None, asyncio.Event]] = set()
        self._revision_cache: int | None = None
        self._pending_change_revision: int | None = None

    def register_change_listener(self) -> asyncio.Event:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        event = asyncio.Event()
        with self.lock:
            self._change_listeners.add((loop, event))
        return event

    def unregister_change_listener(self, event: asyncio.Event) -> None:
        with self.lock:
            self._change_listeners = {
                (loop, ev) for (loop, ev) in self._change_listeners if ev is not event
            }

    def cached_revision(self) -> int | None:
        with self.lock:
            return self._revision_cache

    def has_change_listeners(self) -> bool:
        with self.lock:
            return bool(self._change_listeners)

    def publish_revision(self, revision: int, *, notify: bool = True) -> int:
        revision = max(0, int(revision))
        with self.lock:
            previous = self._revision_cache
            current = revision if previous is None else max(previous, revision)
            changed = previous is None or current > previous
            self._revision_cache = current
            listeners = list(self._change_listeners) if notify and changed else []
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        for loop, event in listeners:
            try:
                if loop is current_loop:
                    event.set()
                elif loop and loop.is_running() and not loop.is_closed():
                    loop.call_soon_threadsafe(event.set)
                else:
                    event.set()
            except Exception:
                pass
        return current

    def notify_changes(self, revision: int) -> None:
        self.publish_revision(revision)

    def event(self, c, action: str, object_id: str, details: dict | None = None) -> None:
        cursor = c.execute(
            "INSERT INTO events(action,object_id,details,created_at) VALUES(?,?,?,?)",
            (action, object_id, json.dumps(details or {}, ensure_ascii=False), now()),
        )
        revision = int(cursor.lastrowid)
        pending = self._pending_change_revision
        self._pending_change_revision = revision if pending is None else max(pending, revision)

    def revision(self) -> int:
        with self.connect() as c:
            return c.execute("SELECT COALESCE(MAX(revision),0) FROM events").fetchone()[0]

    def history(self):
        with self.connect() as c:
            return {
                "events": [
                    {**dict(r), "details": json.loads(r["details"])}
                    for r in c.execute("SELECT * FROM events ORDER BY revision DESC LIMIT 100")
                ]
            }
