import copy
import unittest

from tracen_replay.transactions import races


def result(time, evidence=None):
    return dict(source_timestamp_ms=time, evidence=evidence or f'{time}.png',
                screen='race_result', facts=dict(
                    race_name='Example Cup', placing=2, fans=12000, fans_gained=3000,
                    course=dict(venue='Example', surface='turf', distance_m=2000,
                                distance_category='medium', direction='left'),
                    visible_item_quantities=[dict(quantity=40, box=[425, 699, 489, 727])]))


def screen(time, name):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen=name, facts={})


def sequence():
    return [result(0), result(250), screen(500, 'unknown'),
            screen(750, 'playback_confirmation'), screen(1000, 'playback_confirmation'),
            screen(1250, 'playback_confirmation'), result(1500), result(1750)]


class RaceResultContinuityTests(unittest.TestCase):
    def test_dialog_return_is_one_race_with_separate_item_snapshots(self):
        rows = sequence()
        original = copy.deepcopy(rows)
        found = races(rows)
        self.assertEqual(rows, original)
        self.assertEqual(len(found), 1)
        race = found[0]
        self.assertEqual((race['first_seen_ms'], race['last_seen_ms']), (0, 1750))
        self.assertEqual((race['fans'], race['fans_gained']), (12000, 3000))
        self.assertEqual(race['evidence'], ['0.png', '250.png', '1500.png', '1750.png'])
        self.assertEqual([s['evidence'] for s in race['visible_item_reward_snapshots']],
                         [['0.png', '250.png'], ['1500.png', '1750.png']])
        proof = race['result_panel_continuations'][0]
        self.assertEqual([p['source_timestamp_ms'] for p in proof['observations']], [500, 750, 1000, 1250])
        self.assertEqual(proof['identity']['race_name'], 'Example Cup')
        self.assertFalse(race['verified'])
        self.assertFalse(race['item_rewards_complete'])

    def test_different_or_missing_identity_is_not_joined(self):
        for field, value in [('race_name', 'Different Cup'), ('placing', 1), ('fans', 13000),
                             ('fans_gained', 4000), ('course', None)]:
            with self.subTest(field=field):
                rows = sequence()
                for row in rows[-2:]: row['facts'][field] = value
                self.assertEqual(len(races(rows)), 2)
        rows = sequence()
        rows[-1]['facts']['fans'] = 13000
        self.assertEqual(len(races(rows)), 3)

    def test_dialog_must_be_observed_at_distinct_times_and_paths(self):
        for mode in ('absent', 'one', 'copied'):
            with self.subTest(mode=mode):
                rows = sequence()
                for index in (3, 4, 5):
                    if mode == 'absent' or (mode == 'one' and index != 3):
                        rows[index]['screen'] = 'unknown'
                    elif mode == 'copied': rows[index]['evidence'] = 'copied.png'
                self.assertEqual(len(races(rows)), 2)

    def test_other_screen_or_uncertain_playback_breaks_the_bridge(self):
        for name in ('career_hub', 'race_selection', 'event_outcome', 'unknown'):
            rows = sequence()
            rows[4]['screen'] = name
            self.assertEqual(len(races(rows)), 2, name)

    def test_missing_source_and_long_unknown_edges_do_not_bridge(self):
        rows = [result(0), result(250), screen(1750, 'playback_confirmation'),
                screen(2000, 'playback_confirmation'), result(2250), result(2500)]
        self.assertEqual(len(races(rows)), 2)
        rows = [result(0), result(250), screen(500, 'unknown'), screen(750, 'unknown'),
                screen(1000, 'playback_confirmation'), screen(1250, 'playback_confirmation'),
                result(1500), result(1750)]
        self.assertEqual(len(races(rows)), 2)

    def test_single_result_observation_does_not_establish_continuation(self):
        for missing in (0, -1):
            rows = sequence()
            del rows[missing]
            self.assertEqual(len(races(rows)), 2)
        rows = sequence()
        rows[-1]['evidence'] = rows[-2]['evidence']
        self.assertEqual(len(races(rows)), 2)

    def test_following_race_ids_remain_unique(self):
        rows = sequence() + [result(10000), result(10250)]
        found = races(rows)
        self.assertEqual([r['id'] for r in found], ['race-001', 'race-002'])
        self.assertNotIn('result_panel_continuations', found[1])

    def test_partial_extra_frame_cannot_discard_repeated_complete_identity(self):
        rows = sequence()
        partial = result(1800)
        partial['facts']['course'] = None
        rows.append(partial)
        self.assertEqual(len(races(rows)), 1)
        partial['facts']['course'] = dict(venue='Example', distance_m=2000, direction=None)
        self.assertEqual(len(races(rows)), 1)
        partial['facts']['course']['distance_m'] = 2200
        self.assertEqual(len(races(rows)), 2)
        partial['facts']['course'] = None
        partial['facts']['race_name'] = 'Contradictory Cup'
        self.assertEqual(len(races(rows)), 2)

    def test_conflicting_course_conditions_prevent_continuation(self):
        rows = sequence()
        for row in rows:
            if row['screen'] == 'race_result': row['facts']['course_condition'] = 'firm'
        rows[-1]['facts']['course_condition'] = 'soft'
        self.assertEqual(len(races(rows)), 2)

    def test_fast_navigation_is_not_swallowed_by_initial_grouping(self):
        for same_name in (True, False):
            rows = [result(0), result(250), screen(500, 'career_hub'), result(750), result(1000)]
            if not same_name:
                for row in rows[-2:]: row['facts']['race_name'] = 'Another Cup'
            found = races(rows)
            self.assertEqual(len(found), 2)
            self.assertEqual(found[0]['race_name'], 'Example Cup')
            self.assertEqual(found[1]['race_name'], 'Example Cup' if same_name else 'Another Cup')

    def test_fast_modal_return_preserves_item_visibility_boundary(self):
        rows = [result(0), result(250), screen(500, 'playback_confirmation'),
                screen(750, 'playback_confirmation'), result(1000), result(1250)]
        found = races(rows)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]['result_panel_continuations']), 1)
        self.assertEqual([s['evidence'] for s in found[0]['visible_item_reward_snapshots']],
                         [['0.png', '250.png'], ['1000.png', '1250.png']])


if __name__ == '__main__':
    unittest.main()
