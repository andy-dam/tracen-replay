import copy
import unittest

from tracen_replay.review_suggestions import attach


class RecoverySuggestionsTest(unittest.TestCase):
    def fixture(self):
        effect = dict(kind='friendship_change', name='Super Creek', amount=7,
                      raw_text='Friendship with Super Creek went up by 7.', confidence=97)
        event = dict(id='event', first_seen_ms=100, last_seen_ms=500, effects=[effect],
                     field_evidence={'friendship_change||Super Creek': ['a.png', 'b.png']})
        readings = [dict(evidence=p, source_timestamp_ms=t, effects=[copy.deepcopy(effect)])
                    for p, t in [('a.png', 300), ('b.png', 400)]]
        queue = dict(summary={}, findings=[dict(reason='obscured_receipt', start_ms=200,
                     end_ms=200, evidence=['flag.png'], disposition='unreviewed',
                     details=[dict(text=effect['raw_text'])])], source_sweep=[(0, 1000)])
        return queue, dict(gameplay_tracking=dict(events=[event], readings=readings))

    def test_suggestion_does_not_close_review(self):
        queue, report = self.fixture()
        attach(queue, report)
        self.assertEqual(queue['summary']['recovery_suggestions'], 1)
        self.assertEqual(queue['findings'][0]['disposition'], 'unreviewed')
        self.assertEqual(queue['source_sweep'], [(0, 1000)])

    def test_abstains_on_unsafe_or_incomplete_support(self):
        def wrong_amount(q, d): d['readings'][0]['effects'][0]['amount'] = 8
        def wrong_name(q, d): d['readings'][0]['effects'][0]['name'] = 'Other'
        def duplicate_time(q, d): d['readings'][0]['source_timestamp_ms'] = 400
        def conflict(q, d): d['events'][0]['conflicting_readings'] = ['conflict']
        def covered(q, d): d['readings'][0]['facts'] = {'occluded_receipt_lines': [{}]}
        def covered_name(q, d): q['findings'][0]['details'][0]['recipient_name_occluded'] = True
        def partial(q, d): q['findings'][0]['details'].append({'text': 'Energy recovered by 5.'})
        def nearby(q, d): d['events'][0]['first_seen_ms'] = 250
        def ambiguous(q, d): d['events'].append(copy.deepcopy(d['events'][0]))
        def same_proof(q, d): q['findings'][0]['evidence'] = ['a.png']
        def malformed(q, d): q['findings'][0]['details'][0]['text'] += 'garbled'
        for mutation in (wrong_amount, wrong_name, duplicate_time, conflict, covered,
                         covered_name, partial, nearby, ambiguous, same_proof, malformed):
            with self.subTest(mutation=mutation.__name__):
                queue, report = self.fixture()
                mutation(queue, report['gameplay_tracking'])
                attach(queue, report)
                self.assertNotIn('recovery_suggestion', queue['findings'][0])


if __name__ == '__main__':
    unittest.main()
