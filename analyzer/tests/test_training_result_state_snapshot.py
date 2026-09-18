"""Regression coverage for same-frame result-card state snapshots."""

import copy
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.source_state_observations import build_observations
from tracen_replay.stat_state_details import read_training_result_values
from tracen_replay.vision import parse


REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = localdata.root("prepared_snapshot_final", "independent-02/initial-baseline")
SOURCE_RAW = SOURCE_ROOT / "neural/part-010-frame-000077.json"


if __name__ == "__main__":
    unittest.main()
