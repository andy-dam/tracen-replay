"""Source-bound composition checks for the fresh and cached worker paths."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests import localdata


REPOSITORY_ROOT = Path(__file__).parents[2]
INDEPENDENT_ROOT = localdata.root("development_second_recording")
WEAK_SOURCE = localdata.root("weak_state_recovery_inputs", "independent-01-t063-before-performance.json")
NUMERIC_SOURCE = localdata.root("numeric_cap_refinements", "independent-01", "part-011-frame-000093.json")
FRAME_ID = "part-011-frame-000093"
TIMESTAMP = 1343000


if __name__ == "__main__":
    unittest.main()
