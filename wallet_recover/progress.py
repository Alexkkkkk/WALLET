"""Terminal progress: at most 10 redraws/s, plain lines when redirected."""
import math
import sys
import time


class Progress:
    def __init__(self, total, stream=None, width=36):
        self.total = max(0, total)
        self.stream = sys.stderr if stream is None else stream
        self.width = max(1, width)
        self.started = time.monotonic()
        self.completed = 0
        self._last = 0.0
        self._rate = 0.0
        self._eta = float("inf")
        self._closed = False
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())

    def update(self, completed, rate=None, eta=None):
        self.completed = max(0, min(completed, self.total))
        self._rate = (self.completed / max(time.monotonic() - self.started, 0.001)
                      if rate is None else max(0.0, rate))
        self._eta = ((self.total - self.completed) / self._rate if self._rate else float("inf")) if eta is None else eta
        now = time.monotonic()
        interval = 0.1 if self._tty else 5.0
        if now - self._last >= interval or self.completed == self.total:
            self._last = now
            self._render()

    @staticmethod
    def _fmt(seconds):
        if not math.isfinite(seconds):
            return "unknown"
        minutes, seconds = divmod(int(max(0, seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return "%dh%02dm" % (hours, minutes)
        if minutes:
            return "%dm%02ds" % (minutes, seconds)
        return "%ds" % seconds

    def _render(self):
        frac = self.completed / self.total if self.total else 0.0
        filled = int(self.width * frac)
        bar = "#" * filled + "." * (self.width - filled)
        text = "[%s] %d/%d (%5.1f%%) %.1f p/s ETA %s" % (
            bar, self.completed, self.total, frac * 100, self._rate, self._fmt(self._eta))
        self.stream.write(("\r" if self._tty else "") + text + ("" if self._tty else "\n"))
        self.stream.flush()

    def close(self):
        if not self._closed:
            self._render()
            if self._tty:
                self.stream.write("\n")
                self.stream.flush()
            self._closed = True
