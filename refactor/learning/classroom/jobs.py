"""Bounded, page-owned daemon jobs. Workers never retain or call a QWidget."""
from __future__ import annotations

from queue import Queue, Empty
import threading


class Jobs:
    def __init__(self):
        self.results = Queue()
        self.sequence = 0
        self.current = None
        self.workers = []

    def cancel(self):
        self.sequence += 1
        if self.current:
            self.current.set()
        self.current = None

    def start(self, operation):
        self.workers = [worker for worker in self.workers if worker.is_alive()]
        if len(self.workers) >= 4:
            raise RuntimeError("之前的请求仍未结束，请稍后重试")
        self.cancel()
        token, cancel, results = self.sequence, threading.Event(), self.results
        self.current = cancel

        def run():
            try:
                if cancel.is_set():
                    return
                value, error = operation(cancel), False
            except Exception:
                value, error = None, True
            # Dropping cancelled results here also releases large replies promptly.
            if not cancel.is_set():
                results.put((token, value, error))

        thread = threading.Thread(target=run, name="mf-classroom", daemon=True)
        self.workers.append(thread)
        thread.start()
        return token

    def drain(self):
        while True:
            try:
                yield self.results.get_nowait()
            except Empty:
                return
