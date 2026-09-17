"""A frame's proof image, written while the same frame is read.

PNG encoding releases the GIL, so a pane can be saved on a background thread
while the pane is OCR'd. The caller waits for the write before it returns, so
the file is complete before anything can read it and a failed write still
raises where it happened; the file is exactly what ``Image.save`` writes.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

_LOCK = threading.Lock()
_POOL = None
_POOL_PID = None


def _pool():
    global _POOL, _POOL_PID
    with _LOCK:
        # A forked child inherits this object but not its threads.
        if _POOL is None or _POOL_PID != os.getpid():
            _POOL = ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2)),
                                       thread_name_prefix='proof-writer')
            _POOL_PID = os.getpid()
        return _POOL


def save_while(image, path):
    """Start saving a copy of ``image`` to ``path``; return a function that waits for the write."""
    return _pool().submit(image.copy().save, path).result
