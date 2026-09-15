import unittest
from pathlib import Path
from tests.test_gameplay import workspace_temp

from scripts.snapshot_analyzer_implementation import capture, digest, verify


class ImplementationSnapshotTests(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(workspace_temp())
        self.root = scratch / 'repo'
        (self.root / 'tracen_replay').mkdir(parents=True)
        (self.root / 'scripts').mkdir()
        (self.root / 'tracen_replay/analysis_job.py').write_text('pass\n', encoding='utf-8')
        (self.root / 'tracen_replay/resource.json').write_text('{}', encoding='utf-8')
        self.snapshot = scratch / 'snapshot'
        capture(self.root, self.snapshot)
        self.hash = digest(self.snapshot / 'code-manifest.json')

    def test_resource_and_code_bytes_are_preserved(self):
        self.assertEqual(verify(self.root, self.snapshot, self.hash), 2)
        (self.snapshot / 'files/tracen_replay/resource.json').write_text('{"changed":true}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Preserved implementation changed'):
            verify(self.root, self.snapshot, self.hash)

    def test_added_module_is_detected(self):
        (self.root / 'scripts/new.py').write_text('pass', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'differs'):
            verify(self.root, self.snapshot, self.hash)

    def test_changed_module_is_detected(self):
        (self.root / 'tracen_replay/analysis_job.py').write_text('raise Exception()', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'differs'):
            verify(self.root, self.snapshot, self.hash)

    def test_replaced_manifest_is_detected(self):
        (self.snapshot / 'code-manifest.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            verify(self.root, self.snapshot, self.hash)
