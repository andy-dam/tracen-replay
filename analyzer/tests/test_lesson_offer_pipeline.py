import json
import unittest
from pathlib import Path

from tracen_replay.full_recording import cached_readings
from tracen_replay.preview_observations import build
from tracen_replay.transactions import lesson_receipts
from tracen_replay.analysis_job import _evidence_paths, _check_evidence_path

from tests import localdata


class LessonOfferPipelineTests(unittest.TestCase):
    def test_cached_loader_composes_preview_and_source_bound_cost_readers(self):
        root = localdata.root("prepared_snapshot_early", "independent-02", "initial-baseline")
        if not (root / 'capture.json').is_file():
            self.skipTest('Prepared source recording cache unavailable')
        capture = json.loads((root / 'capture.json').read_text(encoding='utf-8'))
        capture['frames'] = [frame for frame in capture['frames']
                             if frame['id'] == 'part-004-frame-000163']
        self.assertEqual(len(capture['frames']), 1)
        self.assertTrue((root / 'lesson-offer-refinement' /
                         'part-004-frame-000163.json').is_file())
        readings = cached_readings(capture, root)
        facts = readings[0]['facts']
        self.assertIsInstance(facts['lesson_offer_preview'], dict)
        self.assertTrue(facts['lesson_offer_preview']['offers'])
        self.assertIsInstance(facts['lesson_offer_observations'], list)
        self.assertTrue(facts['lesson_offer_observations'])
        self.assertTrue(facts['lesson_offer_refinement_provenance'])
        previews = build(readings)['observations']
        self.assertTrue(previews)
        self.assertTrue(all(row['phase'] == 'preview' for row in previews))
        self.assertEqual(lesson_receipts(readings, []), [])
        for field, value in _evidence_paths({'readings': readings}, 'report'):
            _check_evidence_path(value, field, root.resolve())

    def test_normal_cached_loader_preserves_grouped_offers_as_preview_only(self):
        root = localdata.root("development_third_recording_baseline")
        if not (root / 'capture.json').is_file():
            self.skipTest('Source recording cache unavailable')
        capture = json.loads((root / 'capture.json').read_text(encoding='utf-8'))
        capture['frames'] = [frame for frame in capture['frames']
                             if frame['id'] in ('part-005-frame-000094', 'part-005-frame-000121')]
        self.assertEqual(len(capture['frames']), 2)
        readings = cached_readings(capture, root)
        self.assertEqual([len(row['facts']['lesson_offer_preview']['offers'])
                          for row in readings], [3, 3])
        previews = build(readings)['observations']
        self.assertEqual(len(previews), 13)
        self.assertEqual(sum(row['category'] == 'purchase' for row in previews), 6)
        self.assertTrue(all(row['phase'] == 'preview' for row in previews))
        isolation = [row for row in previews if row.get('context') == 'Isolation Basics']
        self.assertEqual(sum(row['category'] == 'purchase' for row in isolation), 1)
        self.assertEqual({row['payload'].get('field') for row in isolation
                          if row['category'] == 'effect'}, {'speed', 'wit'})
        self.assertEqual(lesson_receipts(readings, []), [])
        self.assertTrue(all((root / proof).is_file()
                            for row in previews for proof in row['evidence']))
        for field, value in _evidence_paths({'readings': readings, 'previews': previews}, 'report'):
            _check_evidence_path(value, field, root.resolve())


if __name__ == '__main__':
    unittest.main()
