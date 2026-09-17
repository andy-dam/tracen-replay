"""Keep an OCR worker's memory steady from frame to frame.

Under Python 3.14 a worker reading frame after frame piles up cyclic garbage
that still holds decoded images: its memory climbs from about 1.4 GB past
6 GB before the collector frees it, and every worker does so at once.
Collecting every few dozen frames keeps each worker near its floor at no
measurable cost, which leaves room on the machine for more workers.
"""
import gc
import threading

EVERY = 25

_lock = threading.Lock()
_frames = 0


def frame_done() -> None:
    """Count one processed frame; collect garbage every ``EVERY`` frames."""
    global _frames
    with _lock:
        _frames += 1
        due = _frames % EVERY == 0
    if due:
        gc.collect()
