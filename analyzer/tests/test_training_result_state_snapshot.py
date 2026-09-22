"""Regression coverage for same-frame result-card state snapshots."""

import json
import unittest
from pathlib import Path

from tests import localdata


REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = localdata.root("prepared_snapshot_final", "independent-02/initial-baseline")
SOURCE_RAW = SOURCE_ROOT / "neural/part-010-frame-000077.json"


if __name__ == "__main__":
    unittest.main()
