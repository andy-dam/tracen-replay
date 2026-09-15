"""A race result whose fan total is still counting up is one race."""
import copy
import unittest

from tracen_replay.transactions import races


def result(time, fans, fans_gained=57609, name='URA Finale Finals'):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='race_result',
                facts=dict(race_name=name, placing=1, fans=fans, fans_gained=fans_gained, race_grade='EX',
                           course=dict(venue='Tokyo', surface='turf', distance_m=2400,
                                       distance_category='long', direction='left')))


class RaceFanCounterTests(unittest.TestCase):
    def test_rising_total_with_the_same_gain_is_one_race(self):
        rows = [result(2288250, 473506), result(2288500, 500109), result(2288750, 504047),
                result(2289000, 504047), result(2289250, 504047)]
        original = copy.deepcopy(rows)
        found = races(rows)
        self.assertEqual(rows, original)
        self.assertEqual(len(found), 1)
        race = found[0]
        self.assertEqual((race['first_seen_ms'], race['last_seen_ms']), (2288250, 2289250))
        self.assertEqual((race['fans'], race['fans_gained'], race['placing']), (504047, 57609, 1))
        self.assertNotIn('fans', race['conflicting_readings'])
        self.assertEqual([o['fans'] for o in race['fan_counter_observations']], [473506, 500109, 504047, 504047, 504047])
        self.assertEqual(race['evidence'], [f'{t}.png' for t in (2288250, 2288500, 2288750, 2289000, 2289250)])

    def test_a_different_gain_is_another_race(self):
        rows = [result(1000, 12000, fans_gained=3000, name='Example Cup'),
                result(1250, 12000, fans_gained=3000, name='Example Cup'),
                result(1750, 20000, fans_gained=8000, name='Other Cup')]
        found = races(rows)
        self.assertEqual([(r['race_name'], r['fans'], r['fans_gained']) for r in found],
                         [('Example Cup', 12000, 3000), ('Other Cup', 20000, 8000)])

    def test_a_falling_total_stays_a_conflict(self):
        rows = [result(1000, 12000, fans_gained=3000), result(1250, 12000, fans_gained=3000), result(1500, 11000, fans_gained=3000)]
        found = races(rows)
        # A drop is not a counter; the second frame starts a new group as before.
        self.assertEqual(len(found), 2)
        self.assertEqual([r['fans'] for r in found], [12000, 11000])

    def test_a_pause_of_more_than_a_second_separates_races(self):
        rows = [result(1000, 473506), result(2500, 504047)]
        found = races(rows)
        self.assertEqual(len(found), 2)


if __name__ == '__main__':
    unittest.main()
