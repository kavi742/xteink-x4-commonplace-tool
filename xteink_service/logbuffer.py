"""
In-memory ring buffer of recent log records, exposed to the browser log viewer.

A single :class:`RingBufferHandler` is attached to the root logger so every
``xteink_service.*`` log line (watcher, archiver, alias resolution, KOReader
sync) is retained in memory and can be served to the ``/logs`` web page. This
is a live diagnostic window, NOT an audit log — nothing is written to disk
(Docker already persists stdout), the buffer is bounded, and it starts empty on
each container restart.

ponytail: a bounded in-memory deque is deliberate. If cross-restart history is
ever needed, tail the container stdout / add a file handler — don't grow this.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from typing import Any

DEFAULT_CAPACITY = 2000

# uvicorn access logs are excluded: the /logs page polls /api/logs, which would
# otherwise flood the buffer with self-referential "GET /api/logs" lines.
_EXCLUDED_LOGGERS = ("uvicorn.access",)


class RingBufferHandler(logging.Handler):
    """A logging handler that keeps the most recent records in memory.

    Each retained record gets a monotonically increasing ``id`` so the web
    client can poll incrementally (``?after=<last_id>``).
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__()
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        if record.name in _EXCLUDED_LOGGERS:
            return
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg = f"{msg}\n{''.join(traceback.format_exception(*record.exc_info))}"
        except Exception:  # never let logging raise
            msg = str(getattr(record, "msg", ""))
        with self._lock:
            self._buf.append({
                "id": self._next_id,
                "ts": record.created,
                "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "levelno": record.levelno,
                "name": record.name,
                "msg": msg,
            })
            self._next_id += 1

    def get(
        self,
        after: int = 0,
        level: str | None = None,
        contains: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Return retained records with ``id > after`` matching the filters.

        ``last_id`` is always the newest id in the buffer (even if the newest
        lines were filtered out) so the client never re-fetches skipped lines.
        """
        min_level = 0
        if level:
            min_level = logging.getLevelNamesMapping().get(level.upper(), 0)
        needle = contains.lower() if contains else None

        with self._lock:
            snapshot = list(self._buf)

        out: list[dict[str, Any]] = []
        for e in snapshot:
            if e["id"] <= after:
                continue
            if min_level and e["levelno"] < min_level:
                continue
            if needle and needle not in e["msg"].lower() and needle not in e["name"].lower():
                continue
            out.append(e)

        if limit and len(out) > limit:
            out = out[-limit:]
        last_id = snapshot[-1]["id"] if snapshot else after
        return {"logs": out, "last_id": last_id}

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


_handler: RingBufferHandler | None = None
_install_lock = threading.Lock()


def install(level: int = logging.INFO, capacity: int = DEFAULT_CAPACITY) -> RingBufferHandler:
    """Attach the ring buffer to the root logger (idempotent).

    Call this AFTER ``logging.basicConfig`` so the stdout handler is preserved —
    both handlers then coexist on the root logger.
    """
    global _handler
    with _install_lock:
        if _handler is not None:
            return _handler
        handler = RingBufferHandler(capacity)
        handler.setLevel(level)
        root = logging.getLogger()
        root.addHandler(handler)
        # Make sure records at `level` actually reach handlers.
        if root.level == logging.NOTSET or root.level > level:
            root.setLevel(level)
        _handler = handler
        return handler


def get_handler() -> RingBufferHandler | None:
    return _handler
