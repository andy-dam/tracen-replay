"""One line of a scrolling receipt list is one hint award, however it was read."""
import unittest

from tracen_replay.receipt_names import collapse_same_list_item_hint_variants

ABOVE = 'Gained 2 hint level(s) for Triumphant Pulse.'
RING = 'Gained 2 hint level(s) for Rainy Days ○.'
LETTER = 'Gained 2 hint level(s) for Rainy Days O.'
COVERED = 'Gained 2 hint level(s) for Rainays .'


def row(top, text, confidence=98, above=ABOVE, extra=()):
    lines = [dict(text=text, confidence=confidence, box=[317, top, 698, top + 28])]
    if above:
        lines.insert(0, dict(text=above, confidence=97, box=[317, top - 24, 731, top + 4]))
    return dict(ocr=dict(neural=lines + list(extra)))


def event(rows):
    effects = [
        dict(kind='skill_hint_change', name='Rainy Days O', amount=2, raw_text=LETTER),
        dict(kind='skill_hint_change', name='Rainy Days ○', amount=2, raw_text=RING, original_text=LETTER,
             visual_symbol_observation=dict(method='strict_terminal_ring_geometry')),
        dict(kind='skill_hint_change', name='Rainy Days', amount=2, raw_text=COVERED,
             source_bound_receipt_proof=dict(line_box=[317, 853, 698, 881])),
        dict(kind='skill_hint_change', name='Full Tilt', amount=5, raw_text='Gained 5 hint level(s) for Full Tilt.'),
    ]
    evidence = {'skill_hint_change||Rainy Days O': ['a', 'd'], 'skill_hint_change||Rainy Days ○': ['b'],
                'skill_hint_change||Rainy Days': ['c'], 'skill_hint_change||Full Tilt': ['d']}
    return dict(effects=effects, field_evidence=evidence, conflicting_readings=[]), rows


def default_rows():
    return {'a': row(909, LETTER), 'b': row(901, RING), 'c': row(853, COVERED, confidence=0),
            'd': row(829, LETTER)}


class SameListItemTests(unittest.TestCase):
    def names(self, ev):
        return [e['name'] for e in ev['effects']]

    def test_three_readings_under_one_preceding_line_are_one_award(self):
        ev, rows = event(default_rows())
        collapse_same_list_item_hint_variants(ev, rows)
        self.assertEqual(self.names(ev), ['Rainy Days ○', 'Full Tilt'])
        kept = ev['effects'][0]
        self.assertEqual(kept['name_resolution'], 'same_preceding_line_in_scrolling_receipt')
        self.assertEqual(kept['observed_name_candidates'], ['Rainy Days ○', 'Rainy Days O', 'Rainy Days'])
        self.assertEqual(ev['field_evidence']['skill_hint_change||Rainy Days ○'], ['b', 'a', 'd', 'c'])

    def test_a_different_preceding_line_is_another_list_item(self):
        rows = default_rows()
        rows['d'] = row(829, LETTER, above='Gained 1 hint level(s) for Corner Recovery ○.')
        ev, rows = event(rows)
        collapse_same_list_item_hint_variants(ev, rows)
        self.assertEqual(len(ev['effects']), 4)

    def test_two_lines_of_the_skill_in_one_frame_are_two_awards(self):
        rows = default_rows()
        rows['a'] = row(909, LETTER, extra=[dict(text=RING, confidence=98, box=[317, 933, 698, 961])])
        ev, rows = event(rows)
        collapse_same_list_item_hint_variants(ev, rows)
        self.assertEqual(len(ev['effects']), 4)

    def test_a_reading_never_seen_under_a_line_stays(self):
        rows = default_rows()
        rows['c'] = row(853, COVERED, confidence=0, above=None)
        ev, rows = event(rows)
        collapse_same_list_item_hint_variants(ev, rows)
        self.assertEqual(len(ev['effects']), 4)

    def test_another_amount_is_another_award(self):
        ev, rows = event(default_rows())
        ev['effects'][0]['amount'] = 1
        collapse_same_list_item_hint_variants(ev, rows)
        self.assertIn('Rainy Days O', self.names(ev))


if __name__ == '__main__':
    unittest.main()
