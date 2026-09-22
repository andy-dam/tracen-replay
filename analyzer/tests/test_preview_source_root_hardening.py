"""Source-root binding tests for persisted preview recovery promotion."""

from __future__ import annotations

import json
import unittest

from tests import localdata


REPORT = localdata.root("third_recording_receipt_logs", "report.json")
SOURCE_ROOT = localdata.root("prepared_snapshot_final", "independent-02")


if __name__ == "__main__":
    unittest.main()
