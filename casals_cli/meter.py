"""One-line deploy progress: percentage and ETA from completed work units.

A unit is one mainnet round-trip (a store chunk, a store delete, a content
file written or removed). The total can grow when a later phase turns out
bigger than the plan; the percentage is always done/total.
"""

from __future__ import annotations

import sys
import time


def format_duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    minutes, s = divmod(s, 60)
    if minutes < 60:
        return f"{minutes}m {s:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class ProgressMeter:
    def __init__(self, stream=None) -> None:
        self.done = 0
        self.total = 0
        self.label = ""
        self.notes: dict[str, int] = {}
        self.stream = stream if stream is not None else sys.stderr
        self.t0 = time.monotonic()
        self._last_line = ""

    def add(self, n: int, label: str = "") -> None:
        if n:
            self.total += n
        if label:
            self.label = label
        self.render()

    def note(self, key: str, n: int) -> None:
        self.notes[key] = n

    def set_label(self, label: str) -> None:
        self.label = label
        self.render()

    def advance(self, n: int = 1, label: str = "") -> None:
        if n:
            self.done += n
        if self.done > self.total:
            self.total = self.done
        if label:
            self.label = label
        self.render()

    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.done * 100 / self.total))

    def eta_seconds(self) -> float | None:
        """Seconds left, or None while there is no completed unit to rate from."""
        if self.done <= 0 or self.total <= self.done:
            return 0.0 if self.total and self.done >= self.total else None
        elapsed = time.monotonic() - self.t0
        return elapsed / self.done * (self.total - self.done)

    def line(self) -> str:
        eta = self.eta_seconds()
        eta_s = "estimating" if eta is None else format_duration(eta)
        counts = f"{self.done}/{self.total} " if self.total else ""
        return f"[{self.percent():3d}%] {counts}{self.label} · ETA {eta_s}"

    def render(self) -> None:
        line = self.line()
        if line == self._last_line:
            return
        self._last_line = line
        tty = getattr(self.stream, "isatty", lambda: False)()
        if tty:
            self.stream.write("\r" + line + "    ")
            self.stream.flush()
        else:
            self.stream.write(line + "\n")
            self.stream.flush()

    def finish(self, label: str = "done") -> None:
        if self.total and self.done < self.total:
            self.done = self.total
        self.label = label
        self._last_line = ""
        self.render()
        tty = getattr(self.stream, "isatty", lambda: False)()
        if tty:
            self.stream.write("\n")
            self.stream.flush()
