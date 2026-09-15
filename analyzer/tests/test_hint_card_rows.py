from copy import deepcopy
import unittest

from tests.test_hint_card_events import SOURCE, wrapped_fixture
from tests.test_neural_transactions import row
from tracen_replay.hint_card_rows import prepare
from tracen_replay.transactions import outcome_events, reconstruct


def source_rows():
    _, rows, candidates = wrapped_fixture()
    for reading in rows:
        reading['stats'] = {}
    earlier = row(750, 'event_outcome', effects=[dict(
        kind='stat_change', field='power', amount=5,
        raw_text='Power went up by 5.', confidence=99)])
    # A complete unrelated receipt after the formerly unparsed wrapped line.
    rows[1]['effects'] = [dict(kind='skill_hint_change', name='Other Skill', amount=3,
                               raw_text='Gained 3 hint level(s) for Other Skill.', confidence=99)]
    return [earlier] + rows, candidates


class HintCardRowsTests(unittest.TestCase):
    def test_recovery_precedes_parser_derived_boundaries_without_changing_raw_rows(self):
        rows, candidates = source_rows()
        before = deepcopy((rows, candidates))
        self.assertEqual(len(outcome_events(rows)), 2)
        prepared, eligible, audit = prepare(rows, candidates, source_sha256=SOURCE)
        events = outcome_events(prepared)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['deltas'], {'power': 5})
        hints = {(effect['name'], effect['amount']) for effect in events[0]['effects']
                 if effect['kind'] == 'skill_hint_change'}
        self.assertEqual(hints, {('Wrapped Skill', 2), ('Other Skill', 3)})
        self.assertEqual(len(audit['accepted']), 1)
        self.assertEqual(eligible, candidates)
        self.assertEqual(audit['eligible_input_indices'], [0])
        self.assertEqual((rows, candidates), before)

    def test_full_reconstruction_attaches_source_proof_after_grouping(self):
        rows, candidates = source_rows()
        result = reconstruct(rows, hint_card_observations=candidates, source_sha256=SOURCE)
        self.assertEqual(len(result['hint_card_recovery']['accepted']), 1)
        self.assertEqual(len(result['hint_card_recovery']['row_recovery']['accepted']), 1)
        hints = [effect for event in result['events'] for effect in event['effects']
                 if effect['kind'] == 'skill_hint_change' and effect['name'] == 'Wrapped Skill']
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]['hint_card_evidence'], candidates)

    def test_visible_scene_boundary_prevents_receipt_bridge(self):
        rows, candidates = source_rows()
        for boundary in ('training_selection', 'event_choice', 'unknown'):
            with self.subTest(boundary=boundary):
                separated = rows[:2] + [row(1100, boundary)] + rows[2:]
                prepared, eligible, audit = prepare(separated, candidates, source_sha256=SOURCE)
                self.assertEqual(prepared, separated)
                self.assertEqual(eligible, [])
                self.assertEqual(audit['rejected'][0]['reason'], 'observed_scene_boundary')

    def test_changed_source_and_existing_amount_conflict_cannot_be_seeded(self):
        for mutation in ('source', 'amount', 'context'):
            with self.subTest(mutation=mutation):
                rows, candidates = source_rows()
                if mutation == 'source':
                    candidates[0]['source_sha256'] = 'b' * 64
                elif mutation == 'amount':
                    rows[1]['effects'] = [dict(kind='skill_hint_change',name='Wrapped Skill',amount=1,
                                               raw_text='Gained 1 hint level(s) for Wrapped Skill.')]
                else:
                    rows[1]['context_title']='One';rows[2]['context_title']='Two'
                prepared, eligible, audit = prepare(rows, candidates, source_sha256=SOURCE)
                self.assertEqual(prepared, rows)
                self.assertEqual(eligible, [])
                self.assertTrue(audit['rejected'])


if __name__ == '__main__':
    unittest.main()
