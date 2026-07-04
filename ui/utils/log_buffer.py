#!/usr/bin/env python3
"""
In-memory log capture for the Wasteless UI debug page
=======================================================

A logging.Handler that keeps the last N records of the UI process in a
thread-safe ring buffer, so the /logs page can poll them incrementally.
Debug tooling only: nothing is persisted, a restart empties the buffer.
"""

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

BUFFER_SIZE = 2000

# uvicorn loggers don't propagate to root; capture them explicitly
CAPTURED_LOGGERS = ['', 'uvicorn', 'uvicorn.access', 'uvicorn.error']


class RingBufferHandler(logging.Handler):
    """Keep the last BUFFER_SIZE log records as dicts, thread-safe."""

    def __init__(self, maxlen: int = BUFFER_SIZE):
        super().__init__(level=logging.DEBUG)
        self._lock_buf = threading.Lock()
        self._buffer = deque(maxlen=maxlen)
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # levelno, not levelname: other formatters (ColoredFormatter)
            # mutate record.levelname in place with ANSI codes.
            entry = {
                'ts': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                'level': logging.getLevelName(record.levelno),
                'levelno': record.levelno,
                'logger': record.name,
                'message': record.getMessage(),
            }
            if record.exc_info and record.exc_info[0] is not None:
                entry['message'] += '\n' + self.formatException(record.exc_info)
        except Exception:
            return  # a broken record must never break the app
        with self._lock_buf:
            entry['id'] = self._next_id
            self._next_id += 1
            self._buffer.append(entry)

    def formatException(self, exc_info) -> str:
        import traceback
        return ''.join(traceback.format_exception(*exc_info)).rstrip()

    def query(self, after_id: int = 0, min_levelno: int = 0,
              search: str = '', limit: int = 500) -> Dict[str, Any]:
        """Entries newer than after_id, filtered, oldest first.

        `last_id` always reflects the newest captured entry so pollers
        advance their cursor even when filters exclude everything.
        """
        needle = search.lower()
        with self._lock_buf:
            entries = [e for e in self._buffer if e['id'] > after_id]
            last_id = self._next_id - 1
        matched = [
            e for e in entries
            if e['levelno'] >= min_levelno
            and (not needle
                 or needle in e['message'].lower()
                 or needle in e['logger'].lower())
        ]
        return {'entries': matched[-limit:], 'last_id': last_id}


_handler: Optional[RingBufferHandler] = None
_install_lock = threading.Lock()


def install_capture() -> RingBufferHandler:
    """Attach the ring buffer to the process loggers (idempotent).

    The handler goes on the root logger, plus on the effective sink of
    each captured logger whose propagation chain never reaches root
    (uvicorn cuts propagation on some of its loggers). Attaching to the
    sink only — never to intermediate loggers — guarantees each record
    is captured exactly once.
    """
    global _handler
    with _install_lock:
        if _handler is None:
            _handler = RingBufferHandler()
        root = logging.getLogger()
        if _handler not in root.handlers:
            root.addHandler(_handler)
        for name in CAPTURED_LOGGERS:
            if not name:
                continue
            sink = logging.getLogger(name)
            while sink.propagate and sink.parent is not None:
                sink = sink.parent
            if sink is not root and _handler not in sink.handlers:
                sink.addHandler(_handler)
        # Root must let INFO records reach the handler; keep existing
        # handlers' own levels untouched.
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
    return _handler


def get_handler() -> Optional[RingBufferHandler]:
    """The installed handler, or None before install_capture()."""
    return _handler
