import copy
import json
import unittest
from pathlib import Path

from tests.test_neural_transactions import line, raw
from tests.test_performance_panel_recovery import panel_lines
from tracen_replay.performance_panel_refinement import candidate_fields
from tracen_replay.vision import (
    _performance_panel_field,
    _performance_panel_localized_requests,
    parse,
    performance_panel_facts,
    terminal_skill_point_observation,
)


class VisionSourceGapTests(unittest.TestCase):
    def test_panel_identity_accepts_only_fixed_keyword_prefix_and_low_cap_floor(self):
        lines = panel_lines(values={
            'dance': 84, 'passion': 49, 'vocal': 49, 'visual': 56,
            'composure': 92,
        }, caps=dict.fromkeys(('dance', 'passion', 'vocal', 'visual', 'composure'), 250))
        points = next(item for item in lines if item['text'] == 'Points')
        points.update(text='Poin', confidence=84.8)
        vocal_cap = [item for item in lines if item['text'] == '/250'][2]
        vocal_cap['confidence'] = 89.127

        facts = performance_panel_facts(lines, 'unknown', {})

        self.assertEqual(facts['performance_points'], {
            'dance': 84, 'passion': 49, 'vocal': 49, 'visual': 56,
            'composure': 92,
        })

    def test_panel_geometry_rejects_bad_prefix_even_when_values_are_clear(self):
        lines = panel_lines()
        points = next(item for item in lines if item['text'] == 'Points')
        points.update(text='Poinx', confidence=99)
        self.assertEqual(performance_panel_facts(lines, 'unknown', {}), {})

    def test_localized_requests_use_neighbor_row_geometry_for_missing_rows(self):
        lines = panel_lines(values={
            'dance': 117, 'passion': 52, 'vocal': 53, 'visual': 49,
            'composure': 50,
        }, caps=dict.fromkeys(('dance', 'passion', 'vocal', 'visual', 'composure'), 400))
        lines = [item for item in lines if item['text'] not in ('53', '49')]

        requests = _performance_panel_localized_requests(lines)

        self.assertEqual(
            [name for name, _box, _meta in requests],
            # The rows the detector did read are asked for their whole slot;
            # the rows it missed are read from the neighbours' geometry.
            ['performance_panel_localized_slot.dance',
             'performance_panel_localized_slot.passion',
             'performance_panel_localized_current.vocal',
             'performance_panel_localized_current.visual',
             'performance_panel_localized_slot.composure'],
        )
        missing = [request for request in requests if 'localized_current' in request[0]]
        # The crop stops at the row's cap rather than running into it.
        self.assertEqual(missing[0][1], [200, 401, 270, 433])
        self.assertEqual(missing[0][2]['preprocess'], 'panel_grayscale_autocontrast')

    def test_a_confident_row_is_still_asked_for_its_whole_slot(self):
        # The detector boxed only the last digit of ``18`` and read that digit
        # perfectly, so nothing in the line itself reports the clipping.
        lines = panel_lines()
        vocal = next(item for item in lines if item['text'] == '27')
        vocal.update(text='8', box=[229, 406, 251, 441], confidence=100.0)

        requests = {name: box for name, box, _meta in _performance_panel_localized_requests(lines)}

        self.assertEqual(requests['performance_panel_localized_slot.vocal'],
                         [200, 403, 270, 444])
        # The row was never treated as settled, and the widened crop keeps the
        # height the detector read the digit at.
        self.assertEqual(requests['performance_panel_localized_slot.dance'][1:4:2], [291, 332])

    def test_a_widened_crop_that_extends_the_reading_proves_the_clipped_digit(self):
        lines = panel_lines()
        vocal = next(item for item in lines if item['text'] == '27')
        vocal.update(text='8', box=[229, 406, 251, 441], confidence=100.0)
        regions = {'performance_panel_localized_slot.vocal': line(
            '18', [212, 406, 252, 441], 99) | {
                'role': 'panel_localized_current',
                'input_eligible': True,
                'component': 'current',
                'geometry_basis': 'slot_widened_value_geometry',
            }}

        facts = performance_panel_facts(lines, 'unknown', {}, regions)
        observation = _performance_panel_field(lines, 'vocal', 432, regions=regions)

        self.assertEqual(facts['performance_points']['vocal'], 18)
        self.assertEqual(observation['basis'], 'slot_widened_panel_crop')
        self.assertEqual(observation['current']['value'], 18)

    def test_a_widened_crop_settles_nothing_unless_it_extends_the_reading(self):
        lines = panel_lines()
        vocal = next(item for item in lines if item['text'] == '27')
        vocal.update(text='8', box=[229, 406, 251, 441], confidence=100.0)

        def read(text, box=(212, 406, 252, 441)):
            regions = {'performance_panel_localized_slot.vocal': line(text, box, 99) | {
                'role': 'panel_localized_current',
                'input_eligible': True,
                'component': 'current',
                'geometry_basis': 'slot_widened_value_geometry',
            }}
            return performance_panel_facts(lines, 'unknown', {}, regions)['performance_points']

        # The same number from a wider view is the row confirming itself.
        self.assertEqual(read('8')['vocal'], 8)
        # A different number that is not the reading extended is a conflict.
        self.assertNotIn('vocal', read('35'))
        # ``8`` is the tail of ``18``, but a crop that did not reach further
        # left than the detector's own box cannot have seen another digit.
        self.assertNotIn('vocal', read('18', (229, 406, 251, 441)))

    def test_localized_rows_complete_a_fixed_geometry_panel_without_merging_phases(self):
        lines = panel_lines(values={
            'dance': 117, 'passion': 52, 'vocal': 53, 'visual': 49,
            'composure': 50,
        }, caps=dict.fromkeys(('dance', 'passion', 'vocal', 'visual', 'composure'), 400))
        lines = [item for item in lines if item['text'] not in ('53', '49')]
        regions = {
            'performance_panel_localized_current.vocal': line(
                '53', [178, 401, 242, 446], 99) | {
                    'role': 'panel_localized_current',
                    'input_eligible': True,
                    'component': 'current',
                    'geometry_basis': 'fixed_row_panel_geometry',
                },
            'performance_panel_localized_current.visual': line(
                '49', [178, 457, 242, 502], 99) | {
                    'role': 'panel_localized_current',
                    'input_eligible': True,
                    'component': 'current',
                    'geometry_basis': 'fixed_row_panel_geometry',
                },
        }
        lines = [item for item in lines if item['text'] != 'Points']
        stats = {'values': {'speed': 1094, 'stamina': 623, 'power': 822}}

        facts = performance_panel_facts(lines, 'unknown', stats, regions)

        self.assertEqual(facts['performance_points'], {
            'dance': 117, 'passion': 52, 'vocal': 53, 'visual': 49,
            'composure': 50,
        })
        self.assertEqual(
            facts['performance_panel_provenance']['vocal']['basis'],
            'same_row_localized_panel_crop',
        )
        self.assertNotIn('projected_performance_gains', facts)

    def test_localized_row_cannot_split_a_merged_phase(self):
        lines = panel_lines()
        dance = next(item for item in lines if item['text'] == '59')
        dance.update(text='59+18', confidence=98, box=[208, 294, 317, 333])
        regions = {
            'performance_panel_localized_current.dance': line(
                '59', [215, 297, 258, 330], 99) | {
                    'role': 'panel_localized_current',
                    'input_eligible': True,
                    'component': 'current',
                    'geometry_basis': 'fixed_row_panel_geometry',
                },
        }
        facts = performance_panel_facts(lines, 'unknown', {}, regions)
        self.assertNotIn('dance', facts['performance_points'])

    def test_current_stat_weak_crop_uses_same_frame_line_crosscheck(self):
        sample = raw([
            line('623', (407, 719, 460, 747), 99.994),
        ])
        sample['current_grid'] = True
        sample['regions'] = {
            'current.stamina': line('623', (406, 721, 463, 747), 89.315),
        }

        parsed = parse(sample)

        self.assertEqual(parsed['stats']['values']['stamina'], 623)
        self.assertEqual(parsed['stats']['value_provenance']['stamina']['basis'],
                         'same_frame_numeric_region_line_crosscheck')

    def test_current_stat_crosscheck_rejects_disagreement(self):
        sample = raw([
            line('629', (407, 719, 460, 747), 99.994),
        ])
        sample['current_grid'] = True
        sample['regions'] = {
            'current.stamina': line('623', (406, 721, 463, 747), 89.315),
        }

        self.assertIsNone(parse(sample)['stats']['values']['stamina'])

    def test_terminal_skill_points_accepts_low_confidence_label_with_unique_counter(self):
        lines = [
            line('Remaining Skill Points', (417, 564, 580, 587), 96.812),
            line('532 pt(s)', (595, 559, 696, 593), 99.904),
        ]
        found, value, proof = terminal_skill_point_observation(lines)
        self.assertTrue(found)
        self.assertEqual(value, 532)
        self.assertEqual(proof['basis'], 'same_modal_label_counter')

        sample = raw([line('Complete Career', (458, 259, 648, 293), 99.9),
                      line('Finish this Career playthrough?', (406, 511, 706, 543), 99.9),
                      *lines])
        sample['header'] = 'Complete Career'
        self.assertEqual(parse(sample)['facts']['current_skill_points'], 532)

    def test_terminal_skill_points_keeps_duplicate_counter_unresolved(self):
        lines = [
            line('Remaining Skill Points', (417, 564, 580, 587), 96.812),
            line('532 pt(s)', (595, 559, 696, 593), 99.904),
            line('531 pt(s)', (595, 559, 696, 593), 99.904),
        ]
        found, value, proof = terminal_skill_point_observation(lines)
        self.assertTrue(found)
        self.assertIsNone(value)
        self.assertIsNone(proof)

    def test_candidate_discovery_contains_no_expected_amounts(self):
        lines = panel_lines()
        dance = next(item for item in lines if item['text'] == '59')
        dance.update(text='59+18', confidence=91)
        discovered = candidate_fields({'lines': lines})
        component = next(item for item in discovered if item['field'] == 'dance')
        self.assertEqual(component['kind'], 'component_split')
        self.assertNotIn('value', component)
        self.assertNotIn('expected', component)


if __name__ == '__main__':
    unittest.main()
