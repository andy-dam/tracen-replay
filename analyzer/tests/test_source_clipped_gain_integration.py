import copy
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.transactions import training_events
from tracen_replay.evaluation_adapters import report_document


REPORT = localdata.root("full_worker_candidate_batch_reports", "independent-01/report.json")


@unittest.skipUnless(REPORT.is_file(), 'preserved full-worker report is unavailable')
class SourceClippedGainIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        report = json.loads(REPORT.read_text(encoding='utf-8'))
        original = report['gameplay_tracking']['events'][317]
        cls.rows = [row for row in report['gameplay_tracking']['readings']
                    if original['first_seen_ms'] <= row['source_timestamp_ms']
                    <= original['last_seen_ms']]
        cls.states = report['gameplay_tracking']['checkpoints']

    def test_source_crop_resolution_reaches_one_event_without_rewriting_ocr(self):
        rows = copy.deepcopy(self.rows)
        before = copy.deepcopy(rows)
        events = training_events(rows, self.states)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['deltas'], {'speed': 17, 'wit': 65, 'skill_points': 34})
        proof = event['direct_gain_provenance']['wit']
        self.assertEqual(proof['basis'], 'source_clipped_training_gain')
        self.assertEqual(proof['value'], 65)
        self.assertEqual(proof['observation_count'], 3)
        identities = {(row['source_timestamp_ms'], row['evidence'])
                      for row in proof['observations']}
        self.assertEqual(len(identities), 3)
        self.assertEqual(rows, before)
        document = report_document({'source': {'sha256': 'a' * 64},
                                    'gameplay_tracking': {'readings': rows,
                                                         'events': events,
                                                         'turn_action_receipts': []}})
        applied = [observation for observation in document['observations']
                   if observation.get('phase') == 'applied'
                   and observation.get('payload', {}).get('kind') == 'stat_change'
                   and observation['payload'].get('field') == 'wit']
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]['payload']['amount'], 65)

    def test_preview_rows_cannot_recover_an_applied_gain(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row['screen'] = 'training_preview'
        self.assertEqual(training_events(rows, self.states), [])

    def test_conflicting_full_badges_do_not_fall_back_to_clipped_prefix(self):
        rows = copy.deepcopy(self.rows)
        changed = False
        for row in rows:
            if row['source_timestamp_ms'] != 1644683:
                continue
            for candidate in row['facts']['training_gain_crop_provenance']['wit']['candidates']:
                if candidate.get('crop_family') == 'result' and candidate.get('amount') == 65:
                    candidate['amount'] = 66
                    candidate['raw_text'] = '+66'
                    changed = True
        self.assertTrue(changed)
        event = training_events(rows, self.states)[0]
        self.assertNotIn('wit', event['deltas'])
        self.assertEqual(event['conflicting_readings']['wit'], [6, 65, 66])
        self.assertNotIn('wit', event['direct_gain_provenance'])
        document = report_document({'source': {'sha256': 'a' * 64},
                                    'gameplay_tracking': {'readings': rows,
                                                         'events': [event],
                                                         'turn_action_receipts': []}})
        self.assertFalse(any(observation.get('phase') == 'applied'
                             and observation.get('payload', {}).get('kind') == 'stat_change'
                             and observation['payload'].get('field') == 'wit'
                             for observation in document['observations']))

    def test_ordinary_short_badges_remain_usable_without_longer_source_views(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            provenance = row.get('facts', {}).get('training_gain_crop_provenance', {}).get('wit')
            if provenance:
                provenance['candidates'] = [candidate for candidate in provenance['candidates']
                                            if candidate.get('amount') == 6]
        event = training_events(rows, self.states)[0]
        self.assertEqual(event['deltas']['wit'], 6)
        self.assertNotIn('wit', event.get('source_clipped_gain_resolutions', {}))

    def test_separate_training_options_cannot_combine_crop_support(self):
        rows = copy.deepcopy(self.rows)
        for index, row in enumerate(rows):
            row['training_option'] = 'wit' if index % 2 else 'speed'
        events = training_events(rows, self.states)
        self.assertFalse(any(
            resolution.get('status') == 'accepted'
            for event in events
            for resolution in event.get('source_clipped_gain_resolutions', {}).values()))


if __name__ == '__main__':
    unittest.main()
