"""Compact source regressions; fixtures are development observations, not labels for unseen runs."""
import copy
import json
from pathlib import Path
import unittest

from tracen_replay.gameplay import effects_from_lines
from tracen_replay.inspect_receipts import merge
from tracen_replay.receipt_recovery import plan, scoped_observations
from tracen_replay.transactions import outcome_events
from tracen_replay.turn_ledger import _corroborated_source_state, PERFORMANCE_FIELDS


class CoreAccountingSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent/'fixtures/core-accounting-source-cases.json').read_text(encoding='utf-8'))

    def test_dense_source_receipts_recover_passion_without_using_state_differences(self):
        source = copy.deepcopy(self.fixture['receipt'])
        old = source['before']
        before = outcome_events(old)
        missing = lambda events: [e for event in events for e in event['effects']
                                  if e['kind'] == 'performance_change' and e.get('field') == 'passion']
        self.assertEqual(missing(before), [])
        windows = plan(old, before, 1866333)
        self.assertTrue(any(t['field'] == 'passion' for w in windows for t in w['triggers']))
        # Reparse the visible lines, rather than trust fixture effects alone.
        for row in source['additional']:
            row['effects'] = effects_from_lines(row['ocr']['neural'])
            self.assertTrue(any(e['kind'] == 'performance_change' and e.get('field') == 'passion'
                                and e['amount'] == 10 for e in row['effects']))
        after = outcome_events(merge(old, scoped_observations(old, source['additional'], windows)))
        self.assertEqual([e['amount'] for e in missing(after)], [10])
        self.assertFalse(any(e.get('result_state_derived_fields') for e in after))

    def test_observed_performance_snapshot_uses_actual_complete_source_mapping(self):
        source = self.fixture['opening']
        state = _corroborated_source_state(source['readings'], PERFORMANCE_FIELDS, 'performance',
                                          635250, source['action_time_ms'], lambda a,b: False, [])
        self.assertEqual(state['values'], source['expected_values'])
        self.assertEqual(state['observed_at_ms'], 637750)
        self.assertEqual(state['values_ref'], '/gameplay_tracking/readings/1/facts/performance_points')
        self.assertTrue(all(len({p['observed_at_ms'] for p in proofs}) >= 2 for proofs in state['field_corroboration'].values()))


if __name__ == '__main__':
    unittest.main()
