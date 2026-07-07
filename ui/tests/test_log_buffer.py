#!/usr/bin/env python3
"""
Unit tests for utils/log_buffer.py — in-memory log capture for the
/logs debug page.
"""

import logging
import sys
import threading
import unittest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.log_buffer import (
    RingBufferHandler,
    install_capture,
    get_handler,
    CAPTURED_LOGGERS,
)


def make_record(msg="hello", level=logging.INFO, name="test.logger", exc_info=None):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=exc_info
    )


class TestRingBufferHandler(unittest.TestCase):

    def setUp(self):
        self.handler = RingBufferHandler(maxlen=5)

    def test_captures_record_fields(self):
        self.handler.emit(make_record("boom", logging.WARNING, "app.sync"))
        result = self.handler.query()
        self.assertEqual(len(result["entries"]), 1)
        entry = result["entries"][0]
        self.assertEqual(entry["message"], "boom")
        self.assertEqual(entry["level"], "WARNING")
        self.assertEqual(entry["logger"], "app.sync")
        self.assertEqual(entry["id"], 1)
        self.assertEqual(result["last_id"], 1)

    def test_level_name_immune_to_ansi_mutation(self):
        """ColoredFormatter mutates record.levelname; levelno must win."""
        record = make_record(level=logging.ERROR)
        record.levelname = "\033[31mERROR   \033[0m"
        self.handler.emit(record)
        self.assertEqual(self.handler.query()["entries"][0]["level"], "ERROR")

    def test_ring_evicts_oldest(self):
        for i in range(8):
            self.handler.emit(make_record(f"msg-{i}"))
        entries = self.handler.query()["entries"]
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0]["message"], "msg-3")
        self.assertEqual(self.handler.query()["last_id"], 8)

    def test_after_id_cursor(self):
        for i in range(3):
            self.handler.emit(make_record(f"msg-{i}"))
        entries = self.handler.query(after_id=2)["entries"]
        self.assertEqual([e["message"] for e in entries], ["msg-2"])

    def test_min_level_filter(self):
        self.handler.emit(make_record("fine", logging.DEBUG))
        self.handler.emit(make_record("bad", logging.ERROR))
        entries = self.handler.query(min_levelno=logging.WARNING)["entries"]
        self.assertEqual([e["message"] for e in entries], ["bad"])

    def test_search_matches_message_and_logger(self):
        self.handler.emit(make_record("sync started", name="app.aws"))
        self.handler.emit(make_record("other", name="app.db"))
        self.assertEqual(len(self.handler.query(search="SYNC")["entries"]), 1)
        self.assertEqual(len(self.handler.query(search="app.db")["entries"]), 1)
        self.assertEqual(len(self.handler.query(search="nomatch")["entries"]), 0)

    def test_last_id_advances_even_when_filtered_out(self):
        self.handler.emit(make_record("quiet", logging.DEBUG))
        result = self.handler.query(min_levelno=logging.ERROR)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["last_id"], 1)

    def test_limit_keeps_newest(self):
        big = RingBufferHandler(maxlen=100)
        for i in range(10):
            big.emit(make_record(f"msg-{i}"))
        entries = big.query(limit=3)["entries"]
        self.assertEqual([e["message"] for e in entries], ["msg-7", "msg-8", "msg-9"])

    def test_exception_appended_to_message(self):
        try:
            raise ValueError("kaputt")
        except ValueError:
            record = make_record("failed", logging.ERROR, exc_info=sys.exc_info())
        self.handler.emit(record)
        message = self.handler.query()["entries"][0]["message"]
        self.assertIn("failed", message)
        self.assertIn("ValueError: kaputt", message)

    def test_thread_safety_smoke(self):
        big = RingBufferHandler(maxlen=1000)

        def worker(n):
            for i in range(100):
                big.emit(make_record(f"w{n}-{i}"))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        result = big.query(limit=2000)
        self.assertEqual(result["last_id"], 400)
        ids = [e["id"] for e in result["entries"]]
        self.assertEqual(len(ids), len(set(ids)), "ids must be unique")


class TestInstallCapture(unittest.TestCase):

    def test_idempotent_and_on_root(self):
        handler1 = install_capture()
        handler2 = install_capture()
        self.assertIs(handler1, handler2)
        self.assertIs(get_handler(), handler1)
        hits = [h for h in logging.getLogger().handlers if h is handler1]
        self.assertEqual(len(hits), 1, "handler must be attached once to root")

    def test_no_duplicate_capture_through_propagation(self):
        """A record must be captured exactly once, whatever the logger."""
        handler = install_capture()
        for name in CAPTURED_LOGGERS + ["app.module.sub"]:
            before = handler.query()["last_id"]
            logging.getLogger(name or None).warning("dedupe-check %s", name)
            entries = [
                e
                for e in handler.query(after_id=before)["entries"]
                if "dedupe-check" in e["message"]
            ]
            self.assertEqual(len(entries), 1, f"logger {name!r} captured {len(entries)} times")

    def test_no_duplicate_when_propagation_is_cut(self):
        """Mimic uvicorn: a child logger whose parent cuts propagation."""
        parent = logging.getLogger("fakeuvi")
        parent.propagate = False
        try:
            import utils.log_buffer as lb

            original = lb.CAPTURED_LOGGERS
            lb.CAPTURED_LOGGERS = ["", "fakeuvi", "fakeuvi.error"]
            try:
                handler = install_capture()
                before = handler.query()["last_id"]
                logging.getLogger("fakeuvi.error").error("cut-check")
                entries = [
                    e
                    for e in handler.query(after_id=before)["entries"]
                    if "cut-check" in e["message"]
                ]
                self.assertEqual(len(entries), 1)
            finally:
                lb.CAPTURED_LOGGERS = original
        finally:
            parent.propagate = True
            if get_handler() in parent.handlers:
                parent.removeHandler(get_handler())

    def test_captures_via_root_propagation(self):
        handler = install_capture()
        before = handler.query()["last_id"]
        logging.getLogger("some.module").warning("propagated %s", "ok")
        entries = handler.query(after_id=before)["entries"]
        self.assertTrue(any(e["message"] == "propagated ok" for e in entries))


if __name__ == "__main__":
    unittest.main()
