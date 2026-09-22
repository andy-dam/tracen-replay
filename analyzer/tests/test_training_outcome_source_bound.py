"""Tests for semantic promotion of source-bound training-result crops."""

from __future__ import annotations

import unittest


from tests import localdata

BASE = localdata.root("development_second_recording")
RAW_PATH = BASE / 'neural/part-011-frame-000114.json'
EVIDENCE_PATH = BASE / 'gameplay/part-011-frame-000114.png'
SOURCE_PATH = BASE / 'part-011/frames/000114.jpg'
SIDECAR_PATH = localdata.root(
    "weak_state_recovery_inputs", 'independent-01-t063-training-success.json'
)


if __name__ == '__main__':
    unittest.main()
