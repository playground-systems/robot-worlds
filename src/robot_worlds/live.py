"""Bounded, best-effort spectator frames, separate from actor sensors and scoring."""
import time


class LiveView:
    def __init__(self, render, callback, *, fps=4, clock=time.monotonic):
        if not 0 < fps <= 10:
            raise ValueError("Live frame rate must be between zero and ten")
        self.render, self.callback, self.clock = render, callback, clock
        self.interval, self.next_due = 1/fps, float("-inf")
        self.sequence, self.error = 0, None

    def publish(self, simulation_time, *, force=False):
        if self.error:
            return
        now = self.clock()
        if not force and now < self.next_due:
            return
        try:
            pixels = self.render()
            self.callback({"time": float(simulation_time), "sequence": self.sequence,
                           "image": pixels, "source": "live_simulator_overview"})
            self.sequence += 1
            finished = self.clock()
            # Software rendering can take longer than the nominal frame period.
            # Leave at least 3x that cost for physics before another automatic
            # preview; otherwise each slow render immediately triggers the next.
            self.next_due = max(now+self.interval, finished+3*max(0, finished-now))
        except Exception as error:
            # Spectator failures must not change physical behavior or grades.
            self.error = {"type": type(error).__name__, "message": "Live preview unavailable; execution evidence is recorded separately."}
