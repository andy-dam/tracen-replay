"""Time on a recording's timeline counted in sampling steps where the recording lags them.

The sampler keeps the first frame at least one step after the last it kept,
so a wait longer than a step between two sampled frames is time the source
had no frame in: a 30 fps recording lags each 250 ms step by up to a frame,
a phone's or tablet's screen recorder, which writes a frame only when the
screen changes, by anything up to a second or more. The analyzer's limits
of a few steps on how long something may go unseen, or how far apart two
sightings may be, are counted in sampling steps of a 60 fps recording,
whose frames fall on every step exactly. ``elapsed`` measures time without
the part of each sampling interval beyond one step, so those limits mean
the same number of samples on any recording, and a 60 fps recording
measures plain time. Budgets of several seconds and minimum display times
stay in plain time: a screen the recorder wrote no frame of was still shown.
"""
import bisect


class Clock:
    """Time without some stretches, ``[start, end]`` pairs in time order that do not overlap."""

    def __init__(self, gaps=()):
        self._gaps = [tuple(gap) for gap in gaps]
        self._ends = [end for _, end in self._gaps]
        self._before = [0]
        for start, end in self._gaps:
            self._before.append(self._before[-1] + end - start)

    def _skipped(self, time):
        index = bisect.bisect_right(self._ends, time)
        skipped = self._before[index]
        if index < len(self._gaps) and self._gaps[index][0] < time:
            skipped += time - self._gaps[index][0]
        return skipped

    def elapsed(self, start, end):
        """Milliseconds from ``start`` to ``end`` without the stretches; negative when ``end`` comes first."""
        return end - start - (self._skipped(end) - self._skipped(start))


_clock = Clock()


def use(frames_ms=(), step_ms=None):
    """Measure from here on without the part of each interval between ``frames_ms`` (the sampled frames' times, in order) beyond ``step_ms``."""
    global _clock
    gaps = []
    if step_ms:
        for before, after in zip(frames_ms, frames_ms[1:]):
            if after - before > step_ms:
                gaps.append((before + step_ms, after))
    _clock = Clock(gaps)


def elapsed(start, end):
    """Milliseconds from ``start`` to ``end`` without the stretches given to ``use``; negative when ``end`` comes first."""
    return _clock.elapsed(start, end)
