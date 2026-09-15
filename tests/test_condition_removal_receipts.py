import unittest

from tracen_replay.gameplay import effects_from_lines
from tracen_replay.vision import parse
from tracen_replay.transactions import outcome_events
from tests.test_outcome_effect_grammar import line, raw


class ConditionRemovalReceiptTests(unittest.TestCase):
    def test_condition_cured_banner_is_merged_with_receipt_in_event_outcome(self):
        result = parse(raw([
            line('CONDITIONCURED!', (392, 605, 711, 640), 99.5),
            line('Night Owl', (485, 650, 613, 690), 99.5),
            line('Recovered from Night Owl.', (316, 829, 650, 861), 99.5),
        ]))
        self.assertEqual(result['screen'], 'event_outcome')
        removals = [effect for effect in result['effects']
                    if effect['kind'] == 'condition_removed']
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0]['name'], 'Night Owl')
        self.assertIn('condition_banner_proof', removals[0])

    def test_condition_cured_banner_does_not_promote_a_training_result(self):
        source = raw([
            line('CONDITIONCURED!', (392, 605, 711, 640), 99.5),
            line('Night Owl', (485, 650, 613, 690), 99.5),
        ])
        source.update(header='Training', current_grid=False, result_grid=True)
        parsed = parse(source)
        self.assertNotEqual(parsed['screen'], 'event_outcome')
        self.assertFalse([effect for effect in parsed['effects']
                          if effect['kind'] == 'condition_removed'])

    def test_complete_receipts_preserve_arbitrary_names(self):
        for name in ('Practice Poor', 'Night Owl', 'Unfamiliar Condition'):
            with self.subTest(name=name):
                text = f'Recovered from {name}.'
                effect = effects_from_lines([line(text)])[0]
                self.assertEqual(effect['kind'], 'condition_removed')
                self.assertEqual(effect['name'], name)
                self.assertEqual(effect['raw_text'], text)
                self.assertIsNone(effect['mechanical_effect'])

    def test_incomplete_and_hypothetical_receipts_abstain(self):
        for text in ('Recovered from Night ', 'Recovered from .',
                     'Recover from Practice Poor.',
                     'If recovered from Practice Poor.',
                     'Recovered from Practice Poor. Maybe.'):
            with self.subTest(text=text):
                self.assertEqual(effects_from_lines([line(text)]), [])

    def test_source_receipt_coexists_with_energy(self):
        result = parse(raw([
            line('Energy recovered by 20.', (317, 808, 553, 835), 99.669),
            line('Recovered from Practice Poor.', (316, 830, 610, 858), 99.958),
        ]))
        self.assertEqual([e['kind'] for e in result['effects']],
                         ['energy_change', 'condition_removed'])

    def test_neural_parser_rejects_outside_receipt_and_weak_text(self):
        for observed in (line('Recovered from Practice Poor.', (310, 200, 700, 230)),
                         line('Recovered from Practice Poor.', confidence=80)):
            self.assertEqual(parse(raw([observed]))['effects'], [])

    def test_repeated_receipt_frames_form_one_source_linked_effect(self):
        readings = []
        for timestamp in (1000, 1250, 1500):
            parsed = parse(raw([
                line('Energy recovered by 20.'),
                line('Recovered from Practice Poor.', (316, 840, 610, 870)),
            ]))
            readings.append(dict(parsed, source_timestamp_ms=timestamp,
                                 evidence=f'gameplay/{timestamp}.png',
                                 context_title='At the Infirmary'))
        events = outcome_events(readings)
        self.assertEqual(len(events), 1)
        self.assertEqual([e['kind'] for e in events[0]['effects']],
                         ['energy_change', 'condition_removed'])
        self.assertEqual(len(events[0]['field_evidence']['condition_removed||Practice Poor']), 3)

    def test_separate_later_recovery_is_not_globally_deduplicated(self):
        readings = []
        for timestamp in (1000, 3000):
            parsed = parse(raw([line('Recovered from Practice Poor.')]))
            readings.append(dict(parsed, source_timestamp_ms=timestamp,
                                 evidence=f'gameplay/{timestamp}.png',
                                 context_title='At the Infirmary'))
        self.assertEqual(len(outcome_events(readings)), 2)


if __name__ == '__main__':
    unittest.main()
