"""Keep a wrapped receipt's source witness attached to its semantic effect."""
from copy import deepcopy
import unittest

from tracen_replay.vision import parse


class WrappedSongProvenanceTests(unittest.TestCase):
    def raw(self):
        prefix = dict(text='Learned the song "Example Song',
                      confidence=98.5, box=[308, 788, 690, 813])
        original_tail = dict(text='Finale".', confidence=99.1,
                             box=[308, 816, 410, 840])
        proof = dict(method='outlined_star_one_hole_ten_alternating_turns',
                     independent_observations=False,
                     title_evidence=dict(raw_lines=[deepcopy(prefix), deepcopy(original_tail)]))
        tail = dict(original_tail, text='Finale\u2606".',
                    original_symbol_text=original_tail['text'], visual_symbol_observation=proof)
        return dict(lines=[prefix, tail], regions={}, header='',
                    current_grid=False, result_grid=False)

    def test_joined_effect_preserves_complete_original_sentence_and_physical_lines(self):
        raw = self.raw()
        before = deepcopy(raw)

        effects = parse(raw)['effects']

        self.assertEqual(len(effects), 1)
        effect = effects[0]
        self.assertEqual(effect['name'], 'Example Song Finale\u2606')
        self.assertEqual(effect['original_text'], 'Learned the song "Example Song Finale".')
        self.assertEqual(effect['confidence'], 98.5)
        self.assertEqual(effect['visual_symbol_observation'],
                         raw['lines'][1]['visual_symbol_observation'])
        self.assertEqual(raw, before)

    def test_unrelated_or_unreadable_continuation_cannot_supply_a_song_witness(self):
        for change in ({'box': [308, 870, 410, 894]}, {'confidence': 94.9}):
            with self.subTest(change=change):
                raw = self.raw()
                raw['lines'][1].update(change)
                self.assertFalse(any(effect['kind'] == 'song_learned'
                                     for effect in parse(raw)['effects']))


if __name__ == '__main__':
    unittest.main()
