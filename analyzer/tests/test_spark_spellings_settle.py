"""Spark spellings that differ by punctuation, a circle glyph, or one glyph of a fixed name."""
import unittest

from tests.test_inheritance_spark_identity import event, row, spark
from tracen_replay.inheritance_spark_identity import resolve


def rows(*names_by_time):
    return {evidence: row(time, evidence, [spark(name)]) for time, evidence, name in names_by_time}


class SparkSpellingTests(unittest.TestCase):
    def test_two_supported_spellings_apart_by_punctuation_are_one_name(self):
        ts, tss = spark('TS Climax Scenario'), spark("T'S Climax Scenario")
        source = rows((1000, 'a', ts['name']), (1250, 'b', ts['name']), (1500, 'c', tss['name']), (1750, 'd', tss['name']))
        e = event([ts, tss], {ts['name']: ['a', 'b'], tss['name']: ['c', 'd']})
        resolve(e, source)
        self.assertEqual([(x['name'], x.get('punctuation_variants')) for x in e['effects']],
                         [("T'S Climax Scenario", ['TS Climax Scenario'])])
        self.assertEqual(e.get('ambiguous_effect_candidates', []), [])

    def test_a_slot_read_with_and_without_its_circle_glyph_once_each_is_the_circled_spark(self):
        plain, circled = spark('Corner Adept'), spark('Corner Adept ○')
        source = rows((1000, 'a', plain['name']), (1250, 'b', circled['name']))
        e = event([plain, circled], {plain['name']: ['a'], circled['name']: ['b']})
        resolve(e, source)
        self.assertEqual([(x['name'], x.get('name_resolution'), x.get('circle_glyph_variants')) for x in e['effects']],
                         [('Corner Adept ○', 'same_slot_circle_glyph_unread', ['Corner Adept'])])
        self.assertEqual(e.get('ambiguous_effect_candidates', []), [])

    def test_a_stat_spark_read_a_glyph_off_is_that_stat(self):
        misread, clean = spark('Speud'), spark('Speed')
        source = rows((1000, 'a', misread['name']), (1250, 'b', misread['name']), (1500, 'c', clean['name']))
        e = event([misread, clean], {misread['name']: ['a', 'b'], clean['name']: ['c']})
        resolve(e, source)
        self.assertEqual([(x['name'], x.get('name_resolution')) for x in e['effects']], [('Speed', 'fixed_spark_name_repaired')])
        self.assertIn('Speud', e['effects'][0]['observed_name_candidates'])
        self.assertEqual(e.get('ambiguous_effect_candidates', []), [])
        self.assertIn('inheritance_spark||Speed', e['field_evidence'])


if __name__ == '__main__':
    unittest.main()
