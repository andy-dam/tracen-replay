"""Tests of ``tests.test_preview_recovery`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import copy
import hashlib
import json
import unittest
from pathlib import Path
from PIL import Image
from tests import localdata
from tests.test_gameplay import workspace_temp
from tracen_replay.preview_recovery import (
    SCHEMA,
    apply,
    candidate_requests,
    load,
    recover,
    recover_in_memory,
    validated_recovery,
)
from tracen_replay.vision import parse


class PreviewRecoveryTests(unittest.TestCase):
    def test_real_selected_frames_schedule_fixed_rows_and_keep_roles(self):
        root = localdata.root("development_third_recording_baseline", "neural")
        if not root.is_dir():
            self.skipTest("preserved independent-02 initial-baseline caches are not present")
        names = (
            "part-003-frame-000165", "part-003-frame-000170",
            "part-010-frame-000059", "part-010-frame-000064",
            "part-010-frame-000066", "part-010-frame-000392",
            "part-010-frame-000395",
        )
        for name in names:
            raw = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            requests = candidate_requests(raw)
            self.assertTrue(requests, name)
            self.assertTrue(any(item["role"] == "main" for item in requests))
            for request in requests:
                self.assertNotIn("expected", request)
                self.assertNotIn("amount", request)
                left, top, right, bottom = request["box"]
                self.assertGreaterEqual(left, 148)
                self.assertLessEqual(right, 958)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(bottom, 1080)
        marker_name = "part-010-frame-000064"
        marker_raw = json.loads((root / f"{marker_name}.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(item["role"] == "modifier" for item in candidate_requests(marker_raw)), 6)
