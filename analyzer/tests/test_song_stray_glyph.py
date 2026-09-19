"""The closing quote of a song receipt read as a stray glyph after the name."""
import unittest

from tracen_replay.receipt_names import collapse_song_variants


def event(names_by_time):
    effects, evidence, timestamps = {}, {}, {}
    for time, name in names_by_time:
        effects.setdefault(name, dict(kind='song_learned', name=name))
        evidence.setdefault('song_learned||' + name, []).append(f'{time}.png')
        timestamps[f'{time}.png'] = time
    return dict(effects=list(effects.values()), field_evidence=evidence), timestamps


class SongStrayGlyphTests(unittest.TestCase):
    def test_a_name_plus_one_glyph_on_fewer_frames_is_the_name(self):
        e, timestamps = event([(1285250, 'Present March D'), (1285500, 'Present March'), (1285750, 'Present March'),
                               (1286000, 'Present March'), (1286250, 'Present March'), (1286500, 'Present March D'),
                               (1286750, 'Present March D')])
        collapse_song_variants(e, timestamps)
        self.assertEqual([(x['name'], x.get('observed_name_candidates')) for x in e['effects']],
                         [('Present March', ['Present March', 'Present March D'])])

    def test_a_longer_spelling_read_as_often_or_a_whole_extra_word_is_another_song(self):
        e, timestamps = event([(1000, 'Present March D'), (1250, 'Present March'), (1500, 'Present March'),
                               (1750, 'Present March'), (2000, 'Present March D'), (2250, 'Present March D')])
        collapse_song_variants(e, timestamps)
        self.assertEqual(sorted(x['name'] for x in e['effects']), ['Present March', 'Present March D'])
        e, timestamps = event([(1000, 'Present March II'), (1250, 'Present March'), (1500, 'Present March'), (1750, 'Present March')])
        collapse_song_variants(e, timestamps)
        self.assertEqual(sorted(x['name'] for x in e['effects']), ['Present March', 'Present March II'])


if __name__ == '__main__':
    unittest.main()
