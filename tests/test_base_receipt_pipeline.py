import json
import unittest
from unittest.mock import patch

from tests.test_base_receipt_refinement import workspace_temp, _raw, _refinement
from tracen_replay.full_recording import cached_readings
from tracen_replay.pipeline import PipelineError


class BaseReceiptPipelineTests(unittest.TestCase):
    def run_case(self, root, raw, extra):
        (root/'neural').mkdir(exist_ok=True)
        (root/'base-receipt-refinement').mkdir(exist_ok=True)
        (root/'neural/frame.json').write_text(json.dumps(raw),encoding='utf-8')
        (root/'base-receipt-refinement/frame.json').write_text(json.dumps(extra),encoding='utf-8')
        capture=dict(frames=[dict(id='frame',source_timestamp_ms=100,evidence='frame.jpg')])
        return cached_readings(capture,root)[0]

    def test_cached_reading_applies_sidecar_and_retains_one_frame_provenance(self):
        with workspace_temp() as root:
            raw=_raw(root)
            extra=_refinement(raw,root/'frame.png',root/'frame.jpg')
            row=self.run_case(root,raw,extra)
            self.assertEqual([e['amount'] for e in row['effects']],[-18])
            self.assertEqual(row['source_timestamp_ms'],100)
            self.assertFalse(row['base_receipt_refinement']['independent_observations'])
            self.assertEqual(row['base_receipt_refinement']['applied_line_indices'],[0])

    def test_pixel_occlusion_runs_after_reread(self):
        with workspace_temp() as root:
            raw=_raw(root)
            extra=_refinement(raw,root/'frame.png',root/'frame.jpg')
            with patch('tracen_replay.receipt_occlusion.overlay_boxes',return_value=[[315,820,620,850]]):
                row=self.run_case(root,raw,extra)
            self.assertEqual(row['effects'],[])
            self.assertEqual(row['base_receipt_refinement']['applied_line_indices'],[0])

    def test_invalid_sidecar_stops_cache_load(self):
        with workspace_temp() as root:
            raw=_raw(root)
            extra=_refinement(raw,root/'frame.png',root/'frame.jpg')
            extra['source_timestamp_ms']=101
            with self.assertRaisesRegex(PipelineError,'Base receipt refinement evidence invalid'):
                self.run_case(root,raw,extra)


if __name__=='__main__':unittest.main()
