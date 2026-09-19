"""A candidate spelling the run corroborates nowhere else joins the reading it damaged."""
import copy
import unittest
from collections import Counter

from tracen_replay.receipt_names import (
    collapse_uncorroborated_circle_base_variants,
    collapse_uncorroborated_recipient_variants,
)


def recipient_event():
    # The recorder A career at 309 s: one slot read as two spellings on
    # adjacent frames, both held as candidates and neither as an effect.
    return dict(
        id='outcome-0055', effects=[], field_evidence={
            'friendship_change||Agnes Tachyon': ['a.png', 'b.png'],
            'friendship_change||Ágnes Tachyon': ['c.png'],
        },
        conflicting_readings=[
            dict(field='friendship_change||Agnes Tachyon', reason='recipient_name_changes_in_adjacent_same_slot_receipt'),
            dict(field='friendship_change||Ágnes Tachyon', reason='recipient_name_changes_in_adjacent_same_slot_receipt'),
            dict(field='stat_change|speed|', reason='something_else'),
        ],
        ambiguous_effect_candidates=[
            dict(effect=dict(kind='friendship_change', name='Agnes Tachyon', amount=5,
                             raw_text='Friendship with Agnes Tachyon went up by 5.'),
                 reason='unresolved_recipient_identity', evidence=['a.png', 'b.png']),
            dict(effect=dict(kind='friendship_change', name='Ágnes Tachyon', amount=5,
                             raw_text='Friendship with Ágnes Tachyon went up by 5.'),
                 reason='unresolved_recipient_identity', evidence=['c.png']),
        ])


class RecipientVariantTests(unittest.TestCase):
    def test_the_spelling_the_run_knows_is_the_receipt(self):
        event = recipient_event()
        sightings = Counter({('friendship_change', 'Agnes Tachyon'): 11, ('friendship_change', 'Ágnes Tachyon'): 1})
        collapse_uncorroborated_recipient_variants(event, sightings)
        self.assertEqual(event['ambiguous_effect_candidates'], [])
        self.assertEqual([(e['name'], e['amount'], e['name_resolution']) for e in event['effects']],
                         [('Agnes Tachyon', 5, 'uncorroborated_spelling_joins_recurring_recipient')])
        self.assertEqual(event['effects'][0]['alternate_name_evidence'], [dict(name='Ágnes Tachyon', evidence=['c.png'])])
        self.assertEqual(event['field_evidence']['friendship_change||Agnes Tachyon'], ['a.png', 'b.png', 'c.png'])
        self.assertEqual([c['reason'] for c in event['conflicting_readings']], ['something_else'])

    def test_two_recurring_spellings_or_none_stay_undecided(self):
        for sightings in (Counter({('friendship_change', 'Agnes Tachyon'): 11, ('friendship_change', 'Ágnes Tachyon'): 3}),
                          Counter({('friendship_change', 'Agnes Tachyon'): 2, ('friendship_change', 'Ágnes Tachyon'): 1})):
            event = recipient_event()
            before = copy.deepcopy(event)
            collapse_uncorroborated_recipient_variants(event, sightings)
            self.assertEqual(event, before)

    def test_spellings_the_run_knows_nowhere_but_a_glyph_from_one_known_name_are_that_name(self):
        # The cursor crossed a different letter on each frame: "Agies" and
        # "Aghes" Tachyon, beside a run full of "Agnes Tachyon".
        event = recipient_event()
        for candidate, name in zip(event['ambiguous_effect_candidates'], ('Agies Tachyon', 'Aghes Tachyon')):
            candidate['effect']['name'] = name
        sightings = Counter({('friendship_change', 'Agnes Tachyon'): 11, ('friendship_change', 'Agies Tachyon'): 1,
                             ('friendship_change', 'Aghes Tachyon'): 1})
        vocabulary = {'supporter': Counter({'Agnes Tachyon': 11, 'Agies Tachyon': 1, 'Aghes Tachyon': 1}), 'skill': Counter(), 'together': {}}
        collapse_uncorroborated_recipient_variants(event, sightings, vocabulary)
        self.assertEqual(event['ambiguous_effect_candidates'], [])
        self.assertEqual([(e['name'], e['amount'], e['name_resolution']) for e in event['effects']],
                         [('Agnes Tachyon', 5, 'disputed_spellings_repaired_to_known_recipient')])
        self.assertEqual([a['name'] for a in event['effects'][0]['alternate_name_evidence']], ['Agies Tachyon', 'Aghes Tachyon'])
        self.assertEqual(event['field_evidence']['friendship_change||Agnes Tachyon'], ['a.png', 'b.png', 'c.png'])
        # Without the run's vocabulary, or with a spelling no known name is near, the slot stays undecided.
        event = recipient_event()
        for candidate, name in zip(event['ambiguous_effect_candidates'], ('Agies Tachyon', 'Aghes Tachyon')):
            candidate['effect']['name'] = name
        before = copy.deepcopy(event)
        collapse_uncorroborated_recipient_variants(event, sightings)
        self.assertEqual(event, before)
        event['ambiguous_effect_candidates'][1]['effect']['name'] = 'Someone Else'
        before = copy.deepcopy(event)
        collapse_uncorroborated_recipient_variants(event, sightings, vocabulary)
        self.assertEqual(event, before)

    def test_candidates_of_another_amount_are_a_separate_slot(self):
        event = recipient_event()
        event['ambiguous_effect_candidates'][1]['effect']['amount'] = 7
        before = copy.deepcopy(event)
        collapse_uncorroborated_recipient_variants(
            event, Counter({('friendship_change', 'Agnes Tachyon'): 11, ('friendship_change', 'Ágnes Tachyon'): 1}))
        self.assertEqual(event, before)


def circle_event(kind='skill_hint_change', strong='Corner Recovery ○', weak='Corner Recovery', amount=1):
    strong_key = f'{kind}||{strong}'
    weak_key = f'{kind}||{weak}'
    return dict(
        id='outcome-0105',
        effects=[dict(kind=kind, name=strong, amount=amount, raw_text=f'Gained 1 hint level(s) for {strong}.')],
        field_evidence={strong_key: ['s1.png', 's2.png'], weak_key: ['w1.png', 'w2.png', 'w3.png']},
        conflicting_readings=[dict(field=weak_key, reason='unresolved_circle_variant_relation'),
                              dict(field=strong_key, reason='unresolved_circle_variant_relation')],
        ambiguous_effect_candidates=[dict(
            effect=dict(kind=kind, name=weak, amount=amount), reason='unresolved_circle_base_variant',
            field=weak_key, evidence=['w1.png', 'w2.png', 'w3.png'],
            possible_duplicate_of=dict(field=strong_key, name=strong, amount=amount, evidence=['s1.png', 's2.png']),
            identity_status='possible_duplicate_or_additional_effect', continuity_proven=False)])


class CircleBaseVariantTests(unittest.TestCase):
    def test_a_bare_spelling_read_only_here_is_the_circle_award_with_its_glyph_unread(self):
        event = circle_event()
        collapse_uncorroborated_circle_base_variants(event, Counter({('skill_hint_change', 'Corner Recovery'): 3}))
        self.assertEqual(event['ambiguous_effect_candidates'], [])
        strong = event['effects'][0]
        self.assertEqual((strong['name'], strong['name_resolution']),
                         ('Corner Recovery ○', 'circle_glyph_unread_on_uncorroborated_base_reading'))
        self.assertEqual(strong['alternate_name_evidence'], [dict(name='Corner Recovery', evidence=['w1.png', 'w2.png', 'w3.png'])])
        self.assertEqual(event['field_evidence']['skill_hint_change||Corner Recovery ○'], ['s1.png', 's2.png', 'w1.png', 'w2.png', 'w3.png'])
        self.assertEqual(event['conflicting_readings'], [])

    def test_a_bare_spelling_the_run_read_elsewhere_stays_a_candidate(self):
        # The Gran Concert career: "Corner Recovery" is also read on its own
        # receipt at 1679 s, so the bare spelling may be an award of its own.
        event = circle_event()
        before = copy.deepcopy(event)
        collapse_uncorroborated_circle_base_variants(event, Counter({('skill_hint_change', 'Corner Recovery'): 6}))
        self.assertEqual(event, before)

    def test_only_a_trailing_circle_separates_the_two_spellings(self):
        event = circle_event(strong='Corner Recovery ◎', weak='Corner Recover')
        before = copy.deepcopy(event)
        collapse_uncorroborated_circle_base_variants(event, Counter({('skill_hint_change', 'Corner Recover'): 3}))
        self.assertEqual(event, before)
        event = circle_event(strong='Corner Recovery', weak='Corner Recovery')
        before = copy.deepcopy(event)
        collapse_uncorroborated_circle_base_variants(event, Counter({('skill_hint_change', 'Corner Recovery'): 3}))
        self.assertEqual(event, before)

    def test_an_inheritance_spark_folds_the_same_way(self):
        event = circle_event(kind='inheritance_spark', strong='Rainy Days ○', weak='Rainy Days ', amount=None)
        collapse_uncorroborated_circle_base_variants(event, Counter({('inheritance_spark', 'Rainy Days '): 3}))
        self.assertEqual(event['ambiguous_effect_candidates'], [])
        self.assertEqual(event['effects'][0]['alternate_name_evidence'][0]['name'], 'Rainy Days ')


if __name__ == '__main__':
    unittest.main()
