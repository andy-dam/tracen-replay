"""Result-card performance rows whose award the detector cut, merged or hid."""
import unittest

from tests.test_performance_panel_recovery import line, panel_lines
from tracen_replay.vision import _performance_panel_field, performance_panel_facts


def component(text, box, confidence, role):
    return line(text, box, confidence) | {
        'role': f'panel_{role}_component', 'component': role, 'input_eligible': True,
        'geometry_basis': 'same_row_merged_panel_line'}


class CutAwardTests(unittest.TestCase):
    def test_a_value_with_a_bare_plus_is_an_award_the_box_cut_off(self):
        # The Dance row read "5+" at 98: the badge's digits fell outside the
        # detector's box. A crop of the row still reads the 5, but the value
        # alone would say the row gave nothing this training.
        lines = panel_lines()
        dance = next(item for item in lines if item['text'] == '59')
        dance.update(text='5+', confidence=98.791, box=[224, 294, 316, 334])
        regions = {'performance_panel_localized_current.dance': line('5', [203, 289, 266, 318], 84.452) | {
            'role': 'panel_localized_current', 'component': 'current', 'input_eligible': True,
            'geometry_basis': 'fixed_row_panel_geometry'}}
        facts = performance_panel_facts(lines, 'training_result', {}, regions)
        self.assertNotIn('dance', facts['performance_points'])
        self.assertNotIn('dance', facts['awarded_performance_gains'])
        self.assertEqual(facts['performance_panel_provenance']['dance']['status'],
                         'unresolved_cut_merged_panel_value')
        # The same row on a frame that read the award beside it resolves.
        lines.append(line('+13', [262, 294, 322, 334], 99))
        facts = performance_panel_facts(lines, 'training_result', {}, regions)
        self.assertEqual(facts['performance_points']['dance'], 5)
        self.assertEqual(facts['awarded_performance_gains']['dance'], 13)


class AwardUnderBadgeTests(unittest.TestCase):
    def badge_lines(self):
        # A "12 more" concert badge sits over the Composure value, which the
        # detector never reads on its own; the award "+11" reads beside it.
        lines = [item for item in panel_lines() if item['text'] != '74']
        lines.append(line('12 more', [195, 511, 256, 532], 98))
        return lines

    def test_an_award_beside_a_badge_covered_value_is_that_row_s(self):
        lines = self.badge_lines()
        lines.append(line('+11', [251, 525, 322, 561], 100))
        facts = performance_panel_facts(lines, 'training_result', {})
        self.assertNotIn('composure', facts['performance_points'])
        self.assertEqual(facts['awarded_performance_gains']['composure'], 11)
        evidence = facts['performance_panel_provenance']['composure']
        self.assertEqual((evidence['status'], evidence['basis']),
                         ('resolved_projected_under_badge', 'award_beside_badge_covered_value'))
        self.assertIsNone(evidence['current'])

    def test_without_the_badge_a_lone_award_stays_unresolved(self):
        lines = [item for item in panel_lines() if item['text'] != '74']
        lines.append(line('+11', [251, 525, 322, 561], 100))
        facts = performance_panel_facts(lines, 'training_result', {})
        self.assertNotIn('composure', facts['awarded_performance_gains'])
        self.assertEqual(facts['performance_panel_provenance']['composure']['status'],
                         'unresolved_panel_value_conflict')

    def test_an_unknown_screen_never_makes_the_award_a_gain(self):
        lines = self.badge_lines()
        lines.append(line('+11', [251, 525, 322, 561], 100))
        facts = performance_panel_facts(lines, 'unknown', {})
        self.assertNotIn('awarded_performance_gains', facts)
        self.assertNotIn('composure', facts['performance_points'])

    def test_a_merged_line_under_the_floor_cut_short_of_the_crop_s_amount_is_not_a_conflict(self):
        # The detector read "9+1" at 73 where the crop of the same line's
        # right half reads 11: the line stopped one digit early.
        lines = self.badge_lines()
        lines.append(line('9+1', [225, 525, 314, 561], 73.282))
        regions = {'performance_panel_projected.composure': component('11', [268, 528, 312, 558], 99.79, 'projected')}
        evidence = _performance_panel_field(lines, 'composure', 544, regions=regions)
        self.assertEqual(evidence['status'], 'resolved_projected_under_badge')
        self.assertEqual(evidence['projected']['value'], 11)
        # An amount the merged line does not start is still a disagreement,
        # and so is a confident merged line.
        regions = {'performance_panel_projected.composure': component('21', [268, 528, 312, 558], 99.79, 'projected')}
        self.assertEqual(_performance_panel_field(lines, 'composure', 544, regions=regions)['status'],
                         'unresolved_panel_value_conflict')
        lines[-1]['confidence'] = 98
        regions = {'performance_panel_projected.composure': component('11', [268, 528, 312, 558], 99.79, 'projected')}
        self.assertEqual(_performance_panel_field(lines, 'composure', 544, regions=regions)['status'],
                         'unresolved_panel_value_conflict')


if __name__ == '__main__':
    unittest.main()
