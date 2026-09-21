"""A name read a glyph off folds into the name the run knows; a rare name near nothing, or near two, stays."""
import unittest
from collections import Counter

from tracen_replay.name_vocabulary import build_vocabulary, edits, letters, repair, repair_event_names


def sightings(**names):
    return Counter(names)


class RepairTests(unittest.TestCase):
    def test_the_hand_check_supporter_variants_fold_into_their_names(self):
        run = sightings(**{'Director Akikawa': 31, 'Kitasan Black': 30, 'Agnes Tachyon': 30, 'Light Hello': 30,
                           'Marvelous Sunday': 22, 'Daiwa Scarlet': 18, 'King Halo': 15, 'Etsuko Otonashi': 11,
                           'Light Hell': 2, 'Marvelos Sunday': 1, 'Seiun Sky': 1, 'Light ello': 1, 'Lint Hello': 1,
                           'Lght Hello': 1, 'Daivg Scarlet': 1, 'Kitasan Blad': 1, 'Direct r Akikawa': 1,
                           'Director A kikawa': 1, 'Director Akikay a': 1, 'Light He!': 1})
        for read, name in (('Light Hell', 'Light Hello'), ('Marvelos Sunday', 'Marvelous Sunday'), ('Light ello', 'Light Hello'),
                           ('Lint Hello', 'Light Hello'), ('Lght Hello', 'Light Hello'), ('Daivg Scarlet', 'Daiwa Scarlet'),
                           ('Kitasan Blad', 'Kitasan Black'), ('Direct r Akikawa', 'Director Akikawa'),
                           ('Director A kikawa', 'Director Akikawa'), ('Director Akikay a', 'Director Akikawa')):
            with self.subTest(read=read):
                self.assertEqual(repair(read, run, 'supporter'), name)
        # A real supporter named once, far from every other name, is left alone.
        self.assertIsNone(repair('Seiun Sky', run, 'supporter'))
        # Three letters gone was once too far for the hand-set rule; the
        # learned confusions know the recognizer does this to "Light Hello"
        # (a cut line read with a stray mark), so it folds in, still with
        # nothing else near.
        self.assertEqual(repair('Light He!', run, 'supporter'), 'Light Hello')
        # A known name is never itself repaired.
        self.assertIsNone(repair('Light Hello', run, 'supporter'))

    def test_the_hand_check_skill_variants_fold_into_the_menus_names(self):
        run = sightings(**{'Straightaway Adept': 3, 'Tactical Tweak': 15, 'Rainy Days': 3, 'On the Way to Our Dream': 2,
                           'Corner Adept': 2, 'Barcarole of Blessings': 4, 'Sunny Sign': 3, 'Muddy': 1, 'Hydrate': 1,
                           'S*raightaway Adept': 1, 'Tactal Tweak': 1, 'Rainays': 1, 'Qn the Way to Our Dream': 1,
                           'Cprner Adept': 1, 'Blucarole of Blessings': 1})
        for read, name in (('S*raightaway Adept', 'Straightaway Adept'), ('Tactal Tweak', 'Tactical Tweak'),
                           ('Rainays', 'Rainy Days'), ('Qn the Way to Our Dream', 'On the Way to Our Dream'),
                           ('Cprner Adept', 'Corner Adept'), ('Blucarole of Blessings', 'Barcarole of Blessings')):
            with self.subTest(read=read):
                self.assertEqual(repair(read, run, 'skill'), name)
        # Hints seen once and named nowhere else stay what they were read as.
        self.assertIsNone(repair('Muddy', run, 'skill'))
        self.assertIsNone(repair('Hydrate', run, 'skill'))

    def test_a_circle_grade_is_another_skill(self):
        run = sightings(**{'Front Runner Savvy': 4, 'Front Runner Savvy ○': 1, 'Winter Runner ○': 3, 'Winter Runner': 1})
        self.assertIsNone(repair('Front Runner Savvy ○', run, 'skill'))
        self.assertIsNone(repair('Winter Runner', run, 'skill'))
        # A skill seen twice is known, and is never itself repaired.
        run = sightings(**{'Corner Adept': 4, 'Corner Adapt': 2})
        self.assertIsNone(repair('Corner Adapt', run, 'skill'))

    def test_two_names_read_on_one_frame_are_two_names(self):
        readings = [dict(effects=[dict(kind='friendship_change', name='Director Akikawa', amount=7),
                                  dict(kind='friendship_change', name='Directo Akikawa', amount=7)], facts={})]
        readings += [dict(effects=[dict(kind='friendship_change', name='Director Akikawa', amount=2)], facts={})] * 4
        vocabulary = build_vocabulary(readings)
        self.assertEqual(vocabulary['together']['Directo Akikawa'], {'Director Akikawa'})
        self.assertIsNone(repair('Directo Akikawa', vocabulary['supporter'], 'supporter', vocabulary['together']['Directo Akikawa']))
        self.assertEqual(repair('Directo Akikawa', vocabulary['supporter'], 'supporter'), 'Director Akikawa')

    def test_two_known_names_within_reach_settle_nothing(self):
        run = sightings(**{'Corner Adept': 5, 'Corner Adapt': 5, 'Cprner Adept': 1})
        self.assertIsNone(repair('Cprner Adept', run, 'skill'))

    def test_a_short_name_takes_one_edit_only(self):
        run = sightings(**{'Muddy': 6, 'Mudy': 1, 'Mddyy': 1})
        self.assertEqual(repair('Mudy', run, 'skill'), 'Muddy')
        self.assertIsNone(repair('Mddyy', run, 'skill'))

    def test_letters_and_edits(self):
        self.assertEqual(letters('Director A kikawa'), 'directorakikawa')
        self.assertEqual(letters('Rainy Days ○'), 'rainydays')
        self.assertEqual(edits('cprneradept', 'corneradept'), 1)
        self.assertEqual(edits('lighthe', 'lighthello'), 3)

    def test_the_vocabulary_counts_receipts_menu_cards_and_owned_skills_once_per_frame(self):
        readings = [
            dict(effects=[dict(kind='friendship_change', name='Light Hello', amount=7),
                          dict(kind='friendship_status', name='Light Hello', value='maximum'),
                          dict(kind='skill_hint_change', name='Corner Adept', amount=1)],
                 facts=dict(skill_cards=[dict(name='Corner Adept'), dict(name='Tactical Tweak')])),
            dict(effects=[], facts=dict(skill_cards=[dict(name='Tactical Tweak')])),
        ]
        vocabulary = build_vocabulary(readings, owned_cards=[dict(name='Tactical Tweak')])
        self.assertEqual(vocabulary['supporter'], Counter({'Light Hello': 1}))
        # One hint box and one menu frame for Corner Adept; two menu frames and the summary for Tactical Tweak.
        self.assertEqual(vocabulary['skill'], Counter({'Corner Adept': 2, 'Tactical Tweak': 3}))
        # Given the grouped outcomes, a receipt counts once per box however
        # many frames read it.
        events = [dict(effects=[dict(kind='skill_hint_change', name='Rainays', amount=1)])]
        vocabulary = build_vocabulary(readings * 3, events=events)
        self.assertEqual(vocabulary['skill']['Rainays'], 1)
        # The menu cards still count per frame.
        self.assertEqual(vocabulary['skill']['Corner Adept'], 3)

    def test_a_trailing_o_is_the_circle_marker(self):
        from tracen_replay.name_vocabulary import circles
        self.assertEqual(circles('Corner Adept O'), '○')
        self.assertEqual(circles('Corner Adept ○'), '○')
        self.assertEqual(circles('Corner Adept'), '')
        self.assertEqual(letters('Corner Adept O'), 'corneradept')
        self.assertEqual(circles('Nakayama RacecourseO'), '○')
        self.assertEqual(circles('Tokai Teio'), '')
        # A run that knows the skill in both grades cannot tell which one a
        # rare spelling without the marker is: it stays as read.
        run = sightings(**{'Corner Adept': 5, 'Corner Adept O': 2, 'Cprner Adept': 1})
        self.assertIsNone(repair('Cprner Adept', run, 'skill'))
        run = sightings(**{'Corner Adept': 5, 'Cprner Adept': 1})
        self.assertEqual(repair('Cprner Adept', run, 'skill'), 'Corner Adept')

    def test_a_repaired_award_merges_into_its_twin_on_the_same_box(self):
        vocabulary = {'supporter': sightings(**{'Director Akikawa': 31, 'Direct r Akikawa': 1, 'Director A kikawa': 1}),
                      'skill': sightings(**{'Straightaway Adept': 3, 'S*raightaway Adept': 1})}
        event = dict(effects=[
            dict(kind='friendship_change', name='Director Akikawa', amount=5),
            dict(kind='friendship_change', name='Direct r Akikawa', amount=5),
            dict(kind='friendship_change', name='Director A kikawa', amount=5),
            dict(kind='skill_hint_change', name='S*raightaway Adept', amount=1),
            dict(kind='skill_hint_change', name='Straightaway Adept', amount=1),
        ], field_evidence={
            'friendship_change||Director Akikawa': ['a.png'], 'friendship_change||Direct r Akikawa': ['b.png'],
            'friendship_change||Director A kikawa': ['c.png'],
            'skill_hint_change||S*raightaway Adept': ['d.png'], 'skill_hint_change||Straightaway Adept': ['e.png']})
        repaired = repair_event_names(event, vocabulary)
        self.assertEqual([(e['kind'], e['name'], e['amount']) for e in event['effects']],
                         [('friendship_change', 'Director Akikawa', 5), ('skill_hint_change', 'Straightaway Adept', 1)])
        self.assertEqual(event['field_evidence'], {'friendship_change||Director Akikawa': ['a.png', 'b.png', 'c.png'],
                                                   'skill_hint_change||Straightaway Adept': ['e.png', 'd.png']})
        self.assertEqual([r['read'] for r in repaired], ['Direct r Akikawa', 'Director A kikawa', 'S*raightaway Adept'])
        self.assertEqual({a['name'] for a in event['effects'][0]['alternate_name_evidence']}, {'Direct r Akikawa', 'Director A kikawa'})
        self.assertEqual(event['effects'][1]['name_resolution'], 'vocabulary_repair')

    def test_a_repaired_award_with_no_twin_keeps_its_amount_under_the_known_name(self):
        vocabulary = {'supporter': sightings(**{'Daiwa Scarlet': 18, 'Daivg Scarlet': 1}), 'skill': Counter()}
        event = dict(effects=[dict(kind='friendship_status', name='Daivg Scarlet', value='maximum')],
                     field_evidence={'friendship_status||Daivg Scarlet': ['f.png']})
        repair_event_names(event, vocabulary)
        self.assertEqual(event['effects'][0]['name'], 'Daiwa Scarlet')
        self.assertEqual(event['effects'][0]['alternate_name_evidence'], [dict(name='Daivg Scarlet', evidence=['f.png'])])
        self.assertEqual(event['field_evidence'], {'friendship_status||Daiwa Scarlet': ['f.png']})


if __name__ == '__main__':
    unittest.main()
