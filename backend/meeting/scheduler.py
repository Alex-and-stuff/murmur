"""Background rollout ticker.

Keeps model inference off the request threads: appending ASR returns as soon as
the raw segments are stored, and the token/interval trigger fires here.
"""

from __future__ import annotations

import sys
import threading

from backend.meeting.engine import MeetingEngine


class RolloutScheduler:
    def __init__(self, engine: MeetingEngine, interval_seconds: float = 1.0):
        self.engine = engine
        self.interval_seconds = interval_seconds
        self._active: set[str] = set()
        self._guard = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

    def notify(self, meeting_id: str) -> None:
        with self._guard:
            self._active.add(meeting_id)
        self._wake.set()

    def forget(self, meeting_id: str) -> None:
        with self._guard:
            self._active.discard(meeting_id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="rollout-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self) -> None:
        while self._running:
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
            with self._guard:
                meetings = sorted(self._active)
            for meeting_id in meetings:
                if not self._running:
                    return
                try:
                    result = self.engine.maybe_rollout(meeting_id)
                    if result.status == "failed":
                        print(f"[rollout] {meeting_id} failed: {result.reason}", file=sys.stderr)
                except Exception as exc:  # a broken meeting must not kill the ticker
                    print(f"[rollout] {meeting_id} raised {type(exc).__name__}: {exc}", file=sys.stderr)
