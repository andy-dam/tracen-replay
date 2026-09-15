import json
import tempfile
import unittest
from pathlib import Path

from tests.test_report_contract import valid_report
from tracen_replay.timeline_document import SCHEMA, build, write


def _report():
    report = valid_report()
    report['gameplay_tracking']['readings'] = []
    report['gameplay_tracking']['performance_accounting'] = {'checkpoints': [], 'intervals': []}
    from tracen_replay.turn_ledger import build as build_ledger
    report['turn_ledger'] = build_ledger(report)
    from tracen_replay.causal_accounting import build as build_accounting
    try:
        report['causal_accounting'] = build_accounting(report)
    except (ValueError, KeyError):
        pass
    return report


class TimelineDocumentTests(unittest.TestCase):
    def test_document_is_a_subset_with_timestamps_and_no_image_paths(self):
        report = _report()
        document = build(report)
        self.assertEqual(document['schema_version'], SCHEMA)
        self.assertEqual(document['source']['sha256'], report['source']['sha256'])
        self.assertEqual(len(document['entries']), len(report['turn_ledger']['timeline']))
        self.assertEqual(len(document['turns']), len(report['turn_ledger']['turns']))
        text = json.dumps(document)
        for marker in ('.png', '.jpg', 'gameplay/', 'part-0'):
            self.assertNotIn(marker, text)
        for entry, source in zip(document['entries'], report['turn_ledger']['timeline']):
            self.assertEqual((entry['kind'], entry['first_seen_ms'], entry['last_seen_ms']),
                             (source['kind'], source['first_seen_ms'], source['last_seen_ms']))

    def test_write_returns_the_size(self):
        report = _report()
        with tempfile.TemporaryDirectory() as tmp:
            size = write(report, Path(tmp) / 'timeline.json')
            self.assertGreater(size, 0)
            self.assertTrue((Path(tmp) / 'timeline.json').is_file())

    def test_real_report_when_available(self):
        path = Path('.local/final-reliability-v1/worker-runs/untouched-creatorB-gran-concert-v28-logs/report.json')
        if not path.is_file():
            self.skipTest('local run report unavailable')
        report = json.loads(path.read_text(encoding='utf-8'))
        payload = json.dumps(build(report), ensure_ascii=False, separators=(',', ':'))
        self.assertLess(len(payload.encode('utf-8')), 3_000_000)
        self.assertNotIn('.png', payload)
        self.assertNotIn('.jpg', payload)


if __name__ == '__main__':
    unittest.main()
