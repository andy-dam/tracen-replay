"""Parallel OCR pre-pass for bounded dense re-read windows.

Each recovery module plans a list of short source windows (about 1.5 s at
60 fps), then calls :func:`tracen_replay.inspect_receipts.inspect` per window
in a fixed order.  The OCR of those frames dominated the refinement phase and
ran in the main process only.  This module OCRs the new windows in a process
pool first (one reader per process, exactly as the base OCR stage does) so
that the sequential ``inspect`` calls afterwards find every frame cache in
place and only parse and record.  Window directories, frame caches and
gameplay proofs are produced by the same code path either way; the recorded
inspection manifest is still written by the sequential pass in plan order.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_READER = None


def build_reader(kind, model_dir):
    """Construct the reader a recovery module uses for its windows."""
    from .vision import NeuralReader
    if kind == 'training':
        from .training_gain_recovery import TrainingReader
        return TrainingReader(model_dir)
    if kind == 'base':
        return NeuralReader(model_dir)
    raise ValueError(f'Unknown dense reader kind: {kind!r}')


def _initialize(model_dir, kind, layout=None):
    global _READER
    from .layout import use
    use(layout)
    _READER = build_reader(kind, model_dir)


def _run(task):
    source, root, start, end, fps = task
    from .inspect_receipts import prepare_window
    return prepare_window(source, root, start, end, fps, reader=_READER)


def prepare_windows(source, root, windows, fps, *, kind, model_dir, workers):
    """OCR ``windows`` (dicts with ``start_ms``/``end_ms``) in a pool.

    Returns the number of windows prepared.  ``workers <= 1`` or an empty
    list is a no-op, leaving the sequential path unchanged.
    """
    windows = [w for w in windows if isinstance(w, dict)]
    if workers is None or workers <= 1 or not windows:
        return 0
    from .layout import current
    tasks = [(str(source), str(Path(root)), int(w['start_ms']), int(w['end_ms']), int(fps)) for w in windows]
    with ProcessPoolExecutor(max_workers=min(int(workers), len(tasks)),
                             initializer=_initialize, initargs=(str(model_dir), kind, current().to_dict())) as pool:
        for _ in pool.map(_run, tasks):
            pass
    return len(tasks)
