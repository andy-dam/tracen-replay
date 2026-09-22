"""Process-local cache of decoded gameplay frames.

Several analysis stages open the same PNG evidence file, convert it to RGB and
hash its pixels.  Decoding an 810x1080 PNG costs several milliseconds and a
fresh run does it tens of thousands of times.  This module keeps a small
least-recently-used cache of decoded RGB images keyed by the file's absolute
path, size and modification time, so a file rewritten on disk is decoded
again.  Callers receive an independent copy and may mutate it freely; the
cached pixels are never handed out directly.  Pixel hashes are cached beside
the image because every consumer computes the same SHA-256 of the RGB bytes.

The cache lives in one process only.  Worker processes each keep their own.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict

from PIL import Image

MAX_ENTRIES = 24

_lock = threading.Lock()
_entries: "OrderedDict[tuple[str, int, int], tuple[Image.Image, str | None]]" = OrderedDict()
# Pixel digests and sizes outlive the decoded images: a run fingerprints tens
# of thousands of files, several times each, and a digest is a few bytes.
_digests: "dict[tuple[str, int, int], tuple[str, tuple[int, int]]]" = {}


def _key(path):
    text = os.path.abspath(os.fspath(path))
    stat = os.stat(text)
    return text, stat.st_size, stat.st_mtime_ns


def _decoded(path):
    key = _key(path)
    with _lock:
        entry = _entries.get(key)
        if entry is not None:
            _entries.move_to_end(key)
            return key, entry
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            entry = (image, None)
            _entries[key] = entry
            while len(_entries) > MAX_ENTRIES:
                _entries.popitem(last=False)
        else:
            _entries.move_to_end(key)
    return key, entry


def open_rgb(path) -> Image.Image:
    """Return a private RGB copy of the decoded image at ``path``."""
    _, (image, _) = _decoded(path)
    return image.copy()


def rgb_digest(path) -> tuple[str, tuple[int, int]]:
    """SHA-256 of the decoded RGB bytes of the image at ``path``, and its size."""
    key = _key(path)
    with _lock:
        known = _digests.get(key)
    if known is not None:
        return known
    key, (image, digest) = _decoded(path)
    if digest is None:
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        with _lock:
            if key in _entries:
                _entries[key] = (image, digest)
    known = (digest, image.size)
    with _lock:
        _digests[key] = known
    return known


def rgb_sha256(path) -> str:
    """SHA-256 of the decoded RGB bytes of the image at ``path``."""
    return rgb_digest(path)[0]


def clear() -> None:
    with _lock:
        _entries.clear()
        _digests.clear()
