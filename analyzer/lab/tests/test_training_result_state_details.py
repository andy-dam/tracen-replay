"""Tests of ``tests.test_training_result_state_details`` that need locally preserved evidence; they run only where it is."""
import copy
import json
import unittest
from pathlib import Path
from tracen_replay.stat_state_details import (
    read_goal_turns,
    read_training_result_values,
)
from tracen_replay.source_state_observations import build_observations
from tracen_replay.transactions import training_events
from tests import localdata
from tracen_replay.vision import parse
from tests.test_training_result_state_details import FIRST_RECORDING_GOAL_RAW, T049_RAW, _line


class TrainingResultStateDetailsTests(unittest.TestCase):










    def test_actual_t049_cached_source_recovers_speed_and_goal_countdown(self):
        if not T049_RAW.is_file():
            self.skipTest('independent-02 T049 source cache is unavailable')
        raw = json.loads(T049_RAW.read_text(encoding='utf-8'))

        values, proof = read_training_result_values(raw)
        self.assertEqual(values.get('speed'), 556)
        self.assertEqual(proof['speed']['region']['text'], '556/1.0')

        parsed = parse(raw)
        self.assertEqual(parsed['screen'], 'training_result')
        self.assertEqual(parsed['facts']['result_values']['speed'], 556)
        self.assertEqual(parsed['stats']['turns_remaining_to_goal'], 5)
        # The visible cap is occluded in this cached recognition; it remains
        # absent until a separately validated cap read supplies it.
        self.assertNotIn('speed', parsed['facts']['stat_caps'])


    def test_goal_countdown_requires_unique_number_and_left_anchor(self):
        actual = json.loads(T049_RAW.read_text(encoding='utf-8')) if T049_RAW.is_file() else None
        if actual is None:
            self.skipTest('independent-02 T049 source cache is unavailable')

        duplicate = copy.deepcopy(actual['lines'])
        duplicate.append(_line('4', (256, 50, 316, 106), 99.0))
        self.assertEqual(read_goal_turns(duplicate), (None, None))

        missing_left = [line for line in actual['lines'] if line['text'] != 'left']
        self.assertEqual(read_goal_turns(missing_left), (None, None))

    def test_bound_countdown_region_recovers_readable_number_without_ocr_line(self):
        if not FIRST_RECORDING_GOAL_RAW.is_file():
            self.skipTest('preserved goal-countdown source cache is unavailable')
        raw = json.loads(FIRST_RECORDING_GOAL_RAW.read_text(encoding='utf-8'))

        value, proof = read_goal_turns(raw['lines'], raw['regions']['countdown'])

        self.assertEqual(value, 1)
        self.assertEqual(proof['basis'], 'same_frame_goal_countdown_geometry')
        self.assertEqual(proof['number'], raw['regions']['countdown'])
        self.assertEqual(proof['countdown_region'], raw['regions']['countdown'])
        self.assertEqual(parse(raw)['stats']['turns_remaining_to_goal'], 1)

    def test_bound_countdown_region_conflict_remains_unknown(self):
        if not FIRST_RECORDING_GOAL_RAW.is_file():
            self.skipTest('preserved goal-countdown source cache is unavailable')
        raw = json.loads(FIRST_RECORDING_GOAL_RAW.read_text(encoding='utf-8'))
        lines = copy.deepcopy(raw['lines'])
        lines.append(_line('4', (256, 50, 316, 106), 99.0))

        self.assertEqual(
            read_goal_turns(lines, raw['regions']['countdown']), (None, None))

    def test_bound_countdown_region_requires_goal_header_geometry(self):
        if not FIRST_RECORDING_GOAL_RAW.is_file():
            self.skipTest('preserved goal-countdown source cache is unavailable')
        raw = json.loads(FIRST_RECORDING_GOAL_RAW.read_text(encoding='utf-8'))
        foreign_region = copy.deepcopy(raw['regions']['countdown'])
        foreign_region['box'] = [600, 700, 660, 740]

        self.assertEqual(read_goal_turns(raw['lines'], foreign_region), (None, None))
