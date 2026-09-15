import hashlib
import json
import unittest
from unittest.mock import patch

from lab.audit_derived_source_causes import _bind_external_evidence_root, main
from tests.test_gameplay import workspace_temp


class EvidenceRootBindingTests(unittest.TestCase):
    def test_cli_returns_failure_for_audit_errors_while_preserving_diagnostic(self):
        with workspace_temp() as directory:
            root = directory.resolve()
            for errors in ([], [{'kind': 'missing_source_evidence'}]):
                output = root / ('failed.json' if errors else 'passed.json')
                result = dict(summary={}, errors=errors)
                argv = ['audit', '--output', str(output),
                        '--markdown-output', str(output.with_suffix('.md'))]
                with patch('sys.argv', argv), \
                     patch('lab.audit_derived_source_causes.audit', return_value=result), \
                     patch('lab.audit_derived_source_causes._render_markdown', return_value='audit'), \
                     patch('builtins.print'):
                    self.assertEqual(main(), int(bool(errors)))
                self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['errors'], errors)

    def test_binding_preserves_report_bytes_and_rejects_stale_or_foreign_input(self):
        with workspace_temp() as directory:
            root = directory.resolve()
            path = root / 'report.json'
            raw = json.dumps({'source': {'sha256': 'a' * 64}}).encode()
            path.write_bytes(raw)
            entry = dict(report_sha256=hashlib.sha256(raw).hexdigest(),
                         source_sha256='a' * 64, evidence_root=str(root))
            report = json.loads(raw)
            _bind_external_evidence_root(report, path, 'run', {'run': entry})
            self.assertEqual(report['evaluation_context']['evidence_root'], str(root))
            self.assertEqual(path.read_bytes(), raw)
            for key, value in [('report_sha256', 'b' * 64),
                               ('source_sha256', 'b' * 64),
                               ('evidence_root', 'relative')]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    _bind_external_evidence_root(json.loads(raw), path, 'run',
                                                 {'run': {**entry, key: value}})
            with self.assertRaises(ValueError):
                _bind_external_evidence_root(json.loads(raw), path, 'run', {})
            other = root / 'other'
            other.mkdir()
            report['evaluation_context']['evidence_root'] = str(other)
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                _bind_external_evidence_root(report, path, 'run', {'run': entry})


if __name__ == '__main__':
    unittest.main()
