"""The closing quote of a song receipt read as a stray glyph after the name."""
import unittest

from tracen_replay.mechanics_audit import song_acquisitions
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

    def test_the_paid_lesson_names_a_song_whose_note_was_read_as_a_letter(self):
        # The note after the title read as "D" on most frames and dropped on
        # the rest; the lesson's own dialog named the song without it.
        receipt = dict(id='outcome-1', first_seen_ms=100, evidence='r.png', context_title=None,
                       effects=[dict(kind='song_learned', name=n) for n in ('Hoppity Sunny Days D', 'Hoppity Sunny Days')])

        def lesson(requested):
            return dict(id='lesson-1', name='Hoppity Sunny Days', requested_name=requested, receipt_event_id='outcome-1',
                        performance_cost=dict(passion=42, vocal=21))
        song, = song_acquisitions([receipt], [lesson('Hoppity Sunny Days')])
        self.assertEqual((song['name'], song['name_conflicted'], song['name_resolution']),
                         ('Hoppity Sunny Days', False, 'paid_lesson_name_with_stray_glyph_spellings'))
        self.assertEqual(song['observed_name_candidates'], ['Hoppity Sunny Days D', 'Hoppity Sunny Days'])
        # Without the lesson's own name, or beside a spelling that is more than
        # a stray glyph, the names stay in dispute.
        for lessons, names in (([], None), ([lesson('Hoppity Sunny')], None),
                               ([lesson('Hoppity Sunny Days')], ('Hoppity Sunny Days II', 'Hoppity Sunny Days'))):
            if names:
                receipt['effects'] = [dict(kind='song_learned', name=n) for n in names]
            song, = song_acquisitions([receipt], lessons)
            with self.subTest(lessons=lessons, names=names):
                self.assertTrue(song['name_conflicted'])
                self.assertNotIn('name_resolution', song)


if __name__ == '__main__':
    unittest.main()
