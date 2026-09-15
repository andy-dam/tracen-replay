from copy import deepcopy
import unittest

from scripts.compare_final_report_atoms import compare


def report():
    return dict(source={'sha256': 'same-source'}, gameplay_tracking={'turn_action_receipts': []},
                causal_accounting={'contributions': [dict(id='old-id', source_ref='/old/0', field='speed',
                    channel='stats', amount=5, turn_id='turn-1', basis='observed_receipt', evidence=['a.png'])],
                    'other_effects': []})


class FinalReportAtomComparisonTests(unittest.TestCase):
    def test_renumbering_is_not_an_effect_change(self):
        old = report()
        new = deepcopy(old)
        new['causal_accounting']['contributions'][0].update(id='new-id', source_ref='/new/23')
        self.assertFalse(compare(old, new)['review_required'])

    def test_duplicate_and_canceling_effects_are_visible(self):
        old = report()
        new = deepcopy(old)
        row = new['causal_accounting']['contributions'][0]
        new['causal_accounting']['contributions'] += [deepcopy(row), dict(row, amount=-5)]
        result = compare(old, new)
        self.assertEqual(len(result['added']), 2)
        self.assertTrue(result['review_required'])

    def test_changed_owner_basis_and_evidence_require_review(self):
        for change in ({'turn_id': 'turn-2'}, {'basis': 'state_difference'}, {'evidence': ['b.png']}):
            old = report()
            new = deepcopy(old)
            new['causal_accounting']['contributions'][0].update(change)
            self.assertTrue(compare(old, new)['review_required'])

    def test_stored_baseline_is_not_recomputed(self):
        old = report()
        original = deepcopy(old)
        compare(old, deepcopy(old))
        self.assertEqual(old, original)
        with self.assertRaises(ValueError):
            compare(old, dict(old, source={'sha256': 'different'}))

    def test_non_numeric_effect_moved_to_another_event_is_visible(self):
        old = report()
        old['gameplay_tracking']['events'] = [dict(kind='event', first_seen_ms=100, last_seen_ms=200),
                                             dict(kind='event', first_seen_ms=300, last_seen_ms=400)]
        old['causal_accounting']['other_effects'] = [dict(source_ref='/effect/0',
            event_ref='/gameplay_tracking/events/0', effect={'kind': 'condition_removed', 'name': 'Practice Poor'},
            evidence=['condition.png'])]
        new = deepcopy(old)
        new['causal_accounting']['other_effects'][0]['event_ref'] = '/gameplay_tracking/events/1'
        result = compare(old, new)
        self.assertEqual(len(result['removed']), 1)
        self.assertEqual(len(result['added']), 1)


if __name__ == '__main__':
    unittest.main()
