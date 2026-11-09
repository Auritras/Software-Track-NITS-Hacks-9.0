"""Thread-safe in-memory event store with cached analysis."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import engine
from .models import Event
from .parsing import normalize
from .topology import Topology


class EventStore:
    def __init__(self, topology: Topology | None = None, max_events: int = 20000):
        self.topology = topology or Topology()
        self.max_events = max_events
        self._events: list[Event] = []
        self._lock = threading.RLock()
        self._seq = 0
        self.version = 0
        self.window_s: float = engine.DEFAULT_WINDOW_S
        self._cache: tuple[int, float, float, list[dict]] | None = None
        self.source_counts: dict[str, int] = {}

    def add_raw(self, raws: list[dict[str, Any]], source: str) -> list[Event]:
        added = []
        with self._lock:
            for raw in raws:
                fields = normalize(raw)
                self._seq += 1
                ev = Event(seq=self._seq, id=f"evt-{self._seq:06d}", source=source, **fields)
                self.topology.ensure(ev.service)
                self._events.append(ev)
                added.append(ev)
            if added:
                self.source_counts[source] = self.source_counts.get(source, 0) + len(added)
                # keep chronological order even when old logs are uploaded later
                self._events.sort(key=lambda e: (e.timestamp, e.seq))
                if len(self._events) > self.max_events:
                    self._events = self._events[-self.max_events:]
                self.version += 1
        return added

    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def reset(self) -> int:
        """Clear all events, keeping a snapshot so the reset can be undone."""
        with self._lock:
            cleared = len(self._events)
            if cleared:
                self._trash = (list(self._events), dict(self.source_counts))
            self._events.clear()
            self.source_counts.clear()
            self.version += 1
            self._cache = None
            return cleared

    def undo_reset(self) -> int:
        """Restore the events cleared by the last reset (merged with anything added since)."""
        with self._lock:
            trash = getattr(self, "_trash", None)
            if not trash:
                return 0
            events, sources = trash
            self._trash = None
            self._events = sorted(events + self._events, key=lambda e: (e.timestamp, e.seq))
            for k, v in sources.items():
                self.source_counts[k] = self.source_counts.get(k, 0) + v
            self.version += 1
            self._cache = None
            return len(events)

    def incidents(self) -> list[dict]:
        with self._lock:
            now = time.monotonic()
            if self._cache and self._cache[0] == self.version and self._cache[2] == self.window_s \
                    and now - self._cache[1] < 2.0:
                return self._cache[3]
            result = engine.analyse(list(self._events), self.topology, self.window_s,
                                    now=datetime.now(timezone.utc))
            self._cache = (self.version, now, self.window_s, result)
            return result

    def incident(self, incident_id: str) -> dict | None:
        return next((i for i in self.incidents() if i["id"] == incident_id), None)
