import unittest

from tracen_replay.vision import (_performance_panel_component_requests,
                                  parse, performance_panel_facts)
from tests.test_performance_panel_recovery import panel_lines


def line(text, box, confidence=99):
    return dict(text=text, confidence=confidence, box=list(box))


class CurrentProjectedPanelGeometryTests(unittest.TestCase):
    def test_low_confidence_merged_composure_remains_unresolved(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56+19', confidence=85.603,
                        box=[208, 515, 314, 553])

        facts = performance_panel_facts(lines, 'training_preview', {})

        self.assertNotIn('composure', facts['performance_points'])
        self.assertNotIn('composure', facts['projected_performance_gains'])
        evidence = facts['performance_panel_provenance']['composure']
        self.assertEqual(evidence['status'], 'unresolved_low_confidence_merged_panel_value')
        self.assertEqual(evidence['raw_observations'][0]['text'], '56+19')
        self.assertEqual(evidence['raw_observations'][0]['box'], [208, 515, 314, 553])

    def test_high_confidence_merged_composure_splits_by_one_panel_geometry(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56+19', confidence=99,
                        box=[208, 515, 314, 553])

        facts = performance_panel_facts(lines, 'training_result', {})

        self.assertEqual(facts['performance_points']['composure'], 56)
        self.assertEqual(facts['awarded_performance_gains']['composure'], 19)
        evidence = facts['performance_panel_provenance']['composure']
        self.assertEqual(evidence['status'], 'resolved_merged_panel_value')
        self.assertEqual(evidence['current']['value'], 56)
        self.assertEqual(evidence['projected']['value'], 19)
        self.assertEqual(evidence['current']['observation']['box'], [208, 515, 314, 553])
        self.assertEqual(evidence['projected']['observation']['box'], [208, 515, 314, 553])

    def test_separate_current_and_projection_stay_separate(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56', confidence=99,
                        box=[208, 515, 248, 553])
        lines.append(line('+19', [258, 515, 302, 553]))

        facts = performance_panel_facts(lines, 'training_result', {})

        self.assertEqual(facts['performance_points']['composure'], 56)
        self.assertEqual(facts['awarded_performance_gains']['composure'], 19)
        evidence = facts['performance_panel_provenance']['composure']
        self.assertEqual(evidence['status'], 'resolved_separate_panel_values')
        self.assertEqual(evidence['current']['value'], 56)
        self.assertEqual(evidence['projected']['value'], 19)
        self.assertNotEqual(evidence['current']['value'] + evidence['projected']['value'],
                            facts['performance_points']['composure'])

    def test_unknown_screen_keeps_current_balance_but_never_promotes_projection(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56', confidence=99,
                        box=[208, 515, 248, 553])
        lines.append(line('+19', [258, 515, 302, 553]))

        facts = performance_panel_facts(lines, 'unknown', {})

        self.assertEqual(facts['performance_points']['composure'], 56)
        self.assertNotIn('projected_performance_gains', facts)
        self.assertNotIn('awarded_performance_gains', facts)

    def test_unknown_screen_does_not_split_a_high_confidence_merged_line(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56+19', confidence=99,
                        box=[208, 515, 314, 553])

        facts = performance_panel_facts(lines, 'unknown', {})

        self.assertNotIn('composure', facts['performance_points'])
        self.assertEqual(facts['performance_points']['visual'], 19)

    def test_projection_from_another_row_is_not_reassigned_to_composure(self):
        lines = panel_lines()
        lines.append(line('+19', [350, 515, 394, 553]))

        facts = performance_panel_facts(lines, 'training_result', {})

        self.assertNotIn('composure', facts['awarded_performance_gains'])
        self.assertIsNone(facts['performance_panel_provenance']['composure']['projected'])

    def test_explicitly_excluded_raw_panel_line_stays_diagnostic(self):
        lines = panel_lines()
        visual = next(item for item in lines if item['text'] == '19')
        visual.update(text='31+19', confidence=99.904,
                      box=[207, 460, 314, 498], input_eligible=False,
                      role='noisy_amount_candidate_excluded')

        facts = performance_panel_facts(lines, 'training_preview', {})

        self.assertNotIn('visual', facts['performance_points'])
        self.assertNotIn('visual', facts['projected_performance_gains'])
        evidence = facts['performance_panel_provenance']['visual']
        self.assertEqual(evidence['status'], 'unresolved_excluded_panel_value_observation')
        self.assertFalse(evidence['raw_observations'][0]['input_eligible'])
        self.assertEqual(evidence['raw_observations'][0]['role'],
                         'noisy_amount_candidate_excluded')

    def test_localized_components_resolve_low_confidence_merged_row(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56+19', confidence=85.603,
                         box=[208, 515, 314, 553], input_eligible=False,
                         role='merged_current_projection_observation')
        regions = {
            'performance_panel_current.composure': line(
                '56', [215, 518, 260, 550], 98.78) | {
                    'role': 'panel_current_component', 'component': 'current',
                    'input_eligible': True,
                    'geometry_basis': 'same_row_merged_panel_line',
                },
            'performance_panel_projected.composure': line(
                '19', [259, 518, 312, 550], 99.85) | {
                    'role': 'panel_projected_component', 'component': 'projected',
                    'input_eligible': True,
                    'geometry_basis': 'same_row_merged_panel_line',
                },
        }

        facts = performance_panel_facts(lines, 'training_preview', {}, regions)

        self.assertEqual(facts['performance_points']['composure'], 56)
        self.assertEqual(facts['projected_performance_gains']['composure'], 19)
        evidence = facts['performance_panel_provenance']['composure']
        self.assertEqual(evidence['status'], 'resolved_separate_component_panel_values')
        self.assertEqual(evidence['basis'], 'same_row_component_crops')
        self.assertEqual(evidence['current']['observation']['component'], 'current')
        self.assertEqual(evidence['projected']['observation']['component'], 'projected')

    def test_disagreeing_component_crops_remain_unresolved(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56+19', confidence=85.603,
                         box=[208, 515, 314, 553], input_eligible=False,
                         role='merged_current_projection_observation')
        regions = {
            'performance_panel_current.composure': line(
                '55', [215, 518, 260, 550], 99) | {
                    'role': 'panel_current_component', 'component': 'current',
                    'input_eligible': True,
                },
            'performance_panel_projected.composure': line(
                '19', [259, 518, 312, 550], 99) | {
                    'role': 'panel_projected_component', 'component': 'projected',
                    'input_eligible': True,
                },
        }

        facts = performance_panel_facts(lines, 'training_result', {}, regions)

        self.assertNotIn('composure', facts['performance_points'])
        self.assertNotIn('composure', facts['awarded_performance_gains'])
        self.assertEqual(facts['performance_panel_provenance']['composure']['status'],
                         'unresolved_panel_value_conflict')

    def test_component_crop_requests_follow_merged_line_geometry(self):
        lines = [line('56+19', [208, 515, 314, 553], 85.603)]
        requests = _performance_panel_component_requests(lines)

        self.assertEqual([name for name, _box, _meta in requests], [
            'performance_panel_current.composure',
            'performance_panel_projected.composure',
        ])
        current = requests[0]
        projected = requests[1]
        self.assertLessEqual(current[1][2], projected[1][0] + 1)
        self.assertEqual(current[2]['parent_observation']['text'], '56+19')
        self.assertEqual(projected[2]['component'], 'projected')

    def test_component_request_ignores_explicitly_excluded_merged_line(self):
        lines = [line('56+19', [208, 515, 314, 553], 99) | {
            'input_eligible': False, 'role': 'merged_current_projection_observation',
        }]

        self.assertEqual(_performance_panel_component_requests(lines), [])

    def test_parse_consumes_component_regions_without_combining_values(self):
        lines = panel_lines()
        composure = next(item for item in lines if item['text'] == '74')
        composure.update(text='56+19', confidence=85.603,
                         box=[208, 515, 314, 553], input_eligible=False,
                         role='merged_current_projection_observation')
        regions = {
            'performance_panel_current.composure': line(
                '56', [215, 518, 260, 550], 98.78) | {
                    'role': 'panel_current_component', 'component': 'current',
                    'input_eligible': True,
                },
            'performance_panel_projected.composure': line(
                '19', [259, 518, 312, 550], 99.85) | {
                    'role': 'panel_projected_component', 'component': 'projected',
                    'input_eligible': True,
                },
        }
        lines.append(line('Failure', [736, 771, 798, 796]))
        raw = dict(lines=lines, regions=regions, header='Training',
                   current_grid=True, result_grid=False)

        parsed = parse(raw)

        self.assertEqual(parsed['screen'], 'training_preview')
        self.assertEqual(parsed['facts']['performance_points']['composure'], 56)
        self.assertEqual(parsed['facts']['projected_performance_gains']['composure'], 19)
        self.assertNotIn(75, parsed['facts']['performance_points'].values())


if __name__ == '__main__':
    unittest.main()
