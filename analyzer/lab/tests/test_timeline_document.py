"""Tests of ``tests.test_timeline_document`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from tests import localdata
from tracen_replay.timeline_document import build


class TimelineDocumentTests(unittest.TestCase):




    def test_real_report_when_available(self):
        path = localdata.root("held_out_recording_report", "report.json")
        if not path.is_file():
            self.skipTest("local evidence 'held_out_recording_report' is not present")
        report = json.loads(path.read_text(encoding='utf-8'))
        payload = json.dumps(build(report), ensure_ascii=False, separators=(',', ':'))
        self.assertLess(len(payload.encode('utf-8')), 3_000_000)
        self.assertNotIn('.png', payload)
        self.assertNotIn('.jpg', payload)
