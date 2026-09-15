"""A result panel read again after unreadable frames, with the same settled numbers, is the same race."""
import unittest

from tracen_replay.transactions import races


def result(time, fans=225552, fans_gained=30470, name='Takarazuka Kinen', placing=1):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='race_result',
                facts=dict(race_name=name, placing=placing, fans=fans, fans_gained=fans_gained, race_grade='G1',
                           course=dict(venue='Hanshin', surface='turf', distance_m=2200,
                                       distance_category='medium', direction='right')))


def unknown(time):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown', facts={})


def panel_with_gaps():
    # The panel as read in a recording: the counter still running on the first two frames, the settled total on the next
    # two, then 1.5 s of frames the classifier could not read, the same panel again, another hole, one more frame.
    rows = [result(1544000, fans=203850, placing=None), result(1544250, fans=221215), result(1544500), result(1544750, placing=None)]
    rows += [unknown(t) for t in range(1545000, 1546500, 250)]
    rows += [result(1546500), result(1546750), result(1547000)]
    rows += [unknown(t) for t in range(1547250, 1548750, 250)]
    rows += [result(1549000)]
    return rows


class RacePanelReappearanceTests(unittest.TestCase):
    def test_the_same_settled_panel_after_unreadable_frames_is_one_race(self):
        found = races(panel_with_gaps())
        self.assertEqual(len(found), 1)
        race = found[0]
        self.assertEqual((race['race_name'], race['placing'], race['fans'], race['fans_gained']), ('Takarazuka Kinen', 1, 225552, 30470))
        self.assertEqual((race['first_seen_ms'], race['last_seen_ms']), (1544000, 1549000))
        self.assertEqual(len(race['evidence']), 8)
        self.assertNotIn('fans', race['conflicting_readings'])
        self.assertEqual([o['fans'] for o in race['fan_counter_observations']][:3], [203850, 221215, 225552])

    def test_the_same_race_in_another_year_is_another_race(self):
        # Takarazuka Kinen can be run in Classic year and again in Senior year: same name, even the same gain,
        # but a higher total and minutes apart.
        rows = [result(1000), result(1250)] + [result(601000, fans=300000), result(601250, fans=300000)]
        found = races(rows)
        self.assertEqual([(r['race_name'], r['fans']) for r in found], [('Takarazuka Kinen', 225552), ('Takarazuka Kinen', 300000)])

    def test_a_higher_total_after_the_gap_is_another_race(self):
        rows = [result(1000), result(1250)] + [unknown(t) for t in (1500, 1750, 2000)] + [result(2250, fans=260000, fans_gained=34448), result(2500, fans=260000, fans_gained=34448)]
        self.assertEqual([r['fans'] for r in races(rows)], [225552, 260000])

    def test_a_different_name_with_the_same_numbers_is_not_the_same_panel(self):
        rows = [result(1000), result(1250)] + [unknown(t) for t in (1500, 1750, 2000, 2250)] + [result(2500, name='Other Cup'), result(2750, name='Other Cup')]
        self.assertEqual(len(races(rows)), 2)

    def test_an_unread_name_on_the_returning_frame_still_joins(self):
        rows = [result(1000), result(1250)] + [unknown(t) for t in (1500, 1750, 2000)] + [result(2250, name=None), result(2500)]
        found = races(rows)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['race_name'], 'Takarazuka Kinen')

    def test_another_screen_between_still_starts_a_new_group(self):
        # A screen the classifier did read is an interruption; only the dialog-return rules may join across it.
        rows = [result(1000), result(1250), dict(source_timestamp_ms=1500, evidence='1500.png', screen='career_hub', facts={}),
                result(2250), result(2500)]
        self.assertEqual(len(races(rows)), 2)

    def test_a_long_gap_is_not_bridged(self):
        rows = [result(1000), result(1250)] + [unknown(t) for t in range(1500, 12000, 250)] + [result(12250), result(12500)]
        self.assertEqual(len(races(rows)), 2)

    def test_unread_totals_never_bridge(self):
        rows = [result(1000, fans=None, fans_gained=None), result(1250, fans=None, fans_gained=None)] + [unknown(t) for t in (1500, 1750, 2000, 2250)]
        rows += [result(2500, fans=None, fans_gained=None), result(2750, fans=None, fans_gained=None)]
        self.assertEqual(len(races(rows)), 2)
        rows = [result(1000, fans_gained=0), result(1250, fans_gained=0)] + [unknown(t) for t in (1500, 1750, 2000, 2250)]
        rows += [result(2500, fans_gained=0), result(2750, fans_gained=0)]
        self.assertEqual(len(races(rows)), 2)


if __name__ == '__main__':
    unittest.main()
