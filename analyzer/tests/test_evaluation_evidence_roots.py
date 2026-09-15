"""Batch evidence roots must belong to the exact report being graded."""
import hashlib
from pathlib import Path
import unittest

from tools.evaluate_final_reliability import _bound_evidence_root
from tests.test_gameplay import workspace_temp


class EvidenceRootBindingsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(workspace_temp())).resolve()
        self.report = self.root / 'report.json'
        self.report.write_bytes(b'{"example": 1}')
        self.document = {'source_sha256': 'a' * 64}
        self.binding = {'v1': {
            'source_sha256': 'a' * 64,
            'report_sha256': hashlib.sha256(self.report.read_bytes()).hexdigest(),
            'evidence_root': str(self.root),
        }}

    def test_exact_report_and_source_resolve_root(self):
        self.assertEqual(_bound_evidence_root(
            self.binding, 'v1', self.report, self.document), self.root)

    def test_changed_report_is_rejected(self):
        self.report.write_bytes(b'{"example": 2}')
        with self.assertRaises(ValueError):
            _bound_evidence_root(self.binding, 'v1', self.report, self.document)

    def test_other_source_is_rejected(self):
        self.document['source_sha256'] = 'b' * 64
        with self.assertRaises(ValueError):
            _bound_evidence_root(self.binding, 'v1', self.report, self.document)

    def test_missing_run_is_rejected(self):
        with self.assertRaises(ValueError):
            _bound_evidence_root(self.binding, 'independent-01', self.report, self.document)

    def test_relative_missing_and_non_directory_roots_are_rejected(self):
        for root in ('.', str(self.root / 'missing'), str(self.report), ''):
            with self.subTest(root=root):
                self.binding['v1']['evidence_root'] = root
                with self.assertRaises(ValueError):
                    _bound_evidence_root(self.binding, 'v1', self.report, self.document)


if __name__ == '__main__':
    unittest.main()
