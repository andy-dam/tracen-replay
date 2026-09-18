from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path

from tests import localdata
from tests.test_causal_accounting import fixture as report_fixture
from tests.test_gameplay import workspace_temp
from tracen_replay.evaluation_adapters import report_document
from tracen_replay.full_recording import _manifest_group_rows
from tracen_replay.inspect_training import reparse_inspection, _project_refined_frame_paths
from tracen_replay.pipeline import PipelineError
from tracen_replay.transactions import training_actions, training_events


REPOSITORY_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = localdata.root("development_first_recording")
BUNDLE_ROOT = localdata.root("diagnostic_numeric_dense_registration")
SOURCE_SHA256 = "a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174"
SOURCE_REFINEMENT_KEY = "training_gain_source_refinement_sidecars"


def _available_source_cases() -> bool:
    return all(
        (
            SOURCE_ROOT / "training-inspection" / window / "frame-000015.png"
        ).is_file()
        and (
            BUNDLE_ROOT / "training-gain-source-refinement" / sidecar
        ).is_file()
        for window, sidecar in (
            ("845250", "7dd06ed3b5d4cda64eee4e91.json"),
            ("1420750", "3f06f468c6fae013c00968e2.json"),
        )
    )


class RefinedFrameProjectionTests(unittest.TestCase):
    def test_projection_preserves_source_and_rejects_wrong_identity(self):
        with workspace_temp() as root:
            frame = root / 'inspection/window/frames/000015.jpg'
            frame.parent.mkdir(parents=True)
            frame.write_bytes(b'fixed source bytes')
            sha = hashlib.sha256(frame.read_bytes()).hexdigest()
            binding = dict(source_frame_path=frame, source_frame_sha256=sha,
                           source_frame_evidence='frames/000015.jpg')
            candidate = dict(source_frame_evidence='frames/000015.jpg',
                             source_frame_sha256=sha, amount=36)
            reading = {'facts': {'candidates': [candidate]}}
            projected = _project_refined_frame_paths(reading, binding, root)
            self.assertEqual(projected['facts']['candidates'][0]['source_frame_evidence'],
                             'inspection/window/frames/000015.jpg')
            self.assertEqual(candidate['source_frame_evidence'], 'frames/000015.jpg')
            self.assertEqual(frame.read_bytes(), b'fixed source bytes')
            for change in ({'source_frame_sha256': '0' * 64},
                           {'source_frame_evidence': 'other/000015.jpg'}):
                with self.subTest(change=change), self.assertRaises(PipelineError):
                    _project_refined_frame_paths(dict(candidate, **change), binding, root)
            with self.assertRaises(PipelineError):
                _project_refined_frame_paths(reading, binding, root / 'unrelated')
            frame.write_bytes(b'changed source bytes')
            with self.assertRaises(PipelineError):
                _project_refined_frame_paths(reading, binding, root)


if __name__ == "__main__":
    unittest.main()
