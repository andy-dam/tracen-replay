"""Source-root binding tests for persisted preview recovery promotion."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.preview_observations import (
    build_preview_observations,
    parse_preview_overlay,
)


REPORT = localdata.root("third_recording_receipt_logs", "report.json")
SOURCE_ROOT = localdata.root("prepared_snapshot_final", "independent-02")


if __name__ == "__main__":
    unittest.main()
