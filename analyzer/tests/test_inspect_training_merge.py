import unittest

from tracen_replay.inspect_training import merge
from tracen_replay.source_state_observations import build_observations


FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points')


def row(timestamp=1000, *, facts=None, screen='training_result', evidence=None,
        **extra):
    value = dict(
        source_timestamp_ms=timestamp,
        evidence=evidence or f'{timestamp}.png',
        screen=screen,
        stats={'training_preview': False},
        facts=facts or {},
        effects=[],
        ocr={'neural': []},
    )
    value.update(extra)
    return value


class TrainingInspectionMergeTests(unittest.TestCase):
    def test_partial_inspection_preserves_source_validated_result_fields(self):
        base = row(
            facts={
                'result_values': {
                    'speed': 599, 'stamina': 433, 'power': 499,
                    'guts': 325, 'wit': 576, 'skill_points': 1027,
                },
                'stat_caps': {
                    'speed': 1600, 'stamina': 1348, 'power': 1355,
                    'guts': 1500, 'wit': 1300,
                },
                'result_snapshot_refinement': {
                    'speed': {'text': '599/1600', 'basis': 'source-bound'},
                    'wit': {'text': '576/1300', 'basis': 'source-bound'},
                },
            },
            numeric_cap_refinement={
                'fields': {
                    'result_total.speed': {'value': 599, 'cap': 1600},
                    'result_total.wit': {'value': 576, 'cap': 1300},
                },
                'applied_fields': ['result_total.speed', 'result_total.wit'],
            },
        )
        supplement = row(
            facts={
                'result_values': {'stamina': 433, 'power': 499},
                'stat_caps': {'stamina': 1348, 'power': 1355},
            },
            evidence='training-inspection/frame.png',
        )

        got = merge([base], [supplement])[0]

        self.assertEqual(got['facts']['result_values']['speed'], 599)
        self.assertEqual(got['facts']['result_values']['wit'], 576)
        self.assertEqual(got['facts']['stat_caps']['speed'], 1600)
        self.assertEqual(got['facts']['stat_caps']['wit'], 1300)
        self.assertEqual(
            got['facts']['inspection_merge_provenance']['result_values']['speed']['origin'],
            'base',
        )
        self.assertIn('result_total.speed', got['numeric_cap_refinement']['applied_fields'])
        self.assertEqual(got['inspection_conflicts'], {})

    def test_explicit_result_value_conflict_is_unresolved_and_prunes_numeric_proof(self):
        base = row(
            facts={
                'result_values': {'speed': 599, 'stamina': 433},
                'stat_caps': {'speed': 1600, 'stamina': 1348},
                'result_value_candidates': {'speed': 599, 'stamina': 433},
                'result_snapshot_refinement': {
                    'speed': {'text': '599/1600', 'basis': 'source-bound'},
                },
            },
            numeric_cap_refinement={
                'fields': {'result_total.speed': {'value': 599, 'cap': 1600}},
                'applied_fields': ['result_total.speed'],
            },
        )
        supplement = row(
            facts={'result_values': {'speed': 600}, 'stat_caps': {'speed': 1600}},
            evidence='training-inspection/conflicting-frame.png',
        )

        got = merge([base], [supplement])[0]
        facts = got['facts']

        self.assertNotIn('speed', facts.get('result_values', {}))
        self.assertNotIn('speed', facts.get('result_value_candidates', {}))
        self.assertNotIn('speed', facts.get('result_snapshot_refinement', {}))
        self.assertNotIn('speed', facts.get('inspection_merge_provenance', {}).get('stat_caps', {}))
        self.assertNotIn('result_total.speed', got.get('numeric_cap_refinement', {}).get('fields', {}))
        self.assertEqual(got['inspection_conflicts']['result_values.speed'], [599, 600])
        self.assertEqual(
            facts['inspection_conflict_provenance']['result_values']['speed']['status'],
            'unresolved',
        )
        observations = build_observations([got])
        stats = next(item for item in observations if item['payload']['channel'] == 'stats')
        self.assertNotIn('speed', stats['payload']['values'])

    def test_later_same_frame_view_cannot_resurrect_a_quarantined_value(self):
        base = row(facts={
            'result_values': {'speed': 599},
            'stat_caps': {'speed': 1600},
            'training_gains': {'speed': 5},
        })
        first = row(
            facts={
                'result_values': {'speed': 600},
                'stat_caps': {'speed': 1700},
                'training_gains': {'speed': 6},
            },
            evidence='inspection-first.png',
        )
        third = row(
            facts={
                'result_values': {'speed': 599},
                'stat_caps': {'speed': 1600},
                'training_gains': {'speed': 5},
            },
            evidence='inspection-third.png',
        )

        got = merge([base], [first, third])[0]

        self.assertNotIn('speed', got['facts'].get('result_values', {}))
        self.assertNotIn('speed', got['facts'].get('stat_caps', {}))
        self.assertNotIn('speed', got['facts'].get('training_gains', {}))
        self.assertEqual(got['inspection_conflicts']['result_values.speed'], [599, 600])
        self.assertEqual(got['inspection_conflicts']['stat_caps.speed'], [1600, 1700])
        self.assertEqual(got['inspection_conflicts']['speed'], [5, 6])
        self.assertIn(
            599,
            got['facts']['inspection_conflict_provenance']['result_values']['speed']
            ['additional_candidates'],
        )

    def test_paired_cap_stays_quarantined_after_numerator_conflict(self):
        base = row(facts={
            'result_values': {'speed': 599},
            'stat_caps': {'speed': 1600},
        })
        conflicting = row(facts={
            'result_values': {'speed': 600},
            'stat_caps': {'speed': 1600},
        }, evidence='inspection-conflict.png')
        later = row(facts={
            'result_values': {'speed': 599},
            'stat_caps': {'speed': 1600},
        }, evidence='inspection-later.png')

        got = merge([base], [conflicting, later])[0]

        self.assertNotIn('speed', got['facts'].get('result_values', {}))
        self.assertNotIn('speed', got['facts'].get('stat_caps', {}))
        self.assertEqual(
            got['facts']['inspection_conflict_provenance']['stat_caps']['speed']['reason'],
            'paired_result_value_conflict',
        )

    def test_duplicate_base_timestamp_is_retained_and_quarantined(self):
        first = row(facts={'result_values': {'speed': 599}}, evidence='base-a.png')
        second = row(facts={'result_values': {'speed': 600}}, evidence='base-b.png')
        supplement = row(
            facts={'result_values': {'speed': 601}}, evidence='inspection.png'
        )

        got = merge([first, second], [supplement])

        self.assertEqual(len(got), 2)
        self.assertEqual([item['evidence'] for item in got], ['base-a.png', 'base-b.png'])
        self.assertTrue(all(
            item['inspection_merge_rejections'][0]['reason'] == 'duplicate_base_timestamp'
            for item in got
        ))
        self.assertEqual(got[0]['facts']['result_values'], {'speed': 599})
        self.assertEqual(got[1]['facts']['result_values'], {'speed': 600})

    def test_explicit_cap_conflict_quarantines_ratio_proof_but_keeps_equal_value(self):
        base = row(
            facts={
                'result_values': {'speed': 599},
                'stat_caps': {'speed': 1600},
                'result_value_candidates': {'speed': 599},
                'result_snapshot_refinement': {
                    'speed': {'text': '599/1600', 'basis': 'source-bound'},
                },
            },
        )
        supplement = row(
            facts={'result_values': {'speed': 599}, 'stat_caps': {'speed': 1700}},
            evidence='training-inspection/cap-conflict.png',
        )

        got = merge([base], [supplement])[0]
        facts = got['facts']

        self.assertEqual(facts['result_values']['speed'], 599)
        self.assertNotIn('speed', facts.get('stat_caps', {}))
        self.assertNotIn('speed', facts.get('result_value_candidates', {}))
        self.assertNotIn('speed', facts.get('result_snapshot_refinement', {}))
        self.assertEqual(got['inspection_conflicts']['stat_caps.speed'], [1600, 1700])

    def test_source_identity_mismatch_is_rejected_without_state_merge(self):
        base = row(facts={'result_values': {'speed': 599}}, source_sha256='recording-a')
        supplement = row(
            facts={'result_values': {'speed': 600, 'wit': 576}},
            source_sha256='recording-b',
            evidence='other-recording.png',
        )

        got = merge([base], [supplement])[0]

        self.assertEqual(got['facts']['result_values'], {'speed': 599})
        self.assertEqual(
            got['inspection_merge_rejections'][0]['reason'],
            'source_identity_mismatch',
        )

    def test_preview_result_phase_mismatch_is_rejected(self):
        base = row(facts={'result_values': {'speed': 599}})
        supplement = row(
            facts={'result_values': {'speed': 600}},
            stats={'training_preview': True},
            evidence='preview.png',
        )

        got = merge([base], [supplement])[0]

        self.assertEqual(got['facts']['result_values'], {'speed': 599})
        self.assertEqual(
            got['inspection_merge_rejections'][0]['reason'],
            'training_phase_mismatch',
        )

    def test_null_partial_fields_do_not_erase_base_state(self):
        base = row(facts={
            'result_values': {'speed': 599, 'wit': 576},
            'stat_caps': {'speed': 1600, 'wit': 1300},
        })
        supplement = row(facts={
            'result_values': {'speed': None, 'wit': None},
            'stat_caps': {'speed': None},
        })

        got = merge([base], [supplement])[0]

        self.assertEqual(got['facts']['result_values'], base['facts']['result_values'])
        self.assertEqual(got['facts']['stat_caps'], base['facts']['stat_caps'])

    def test_distinct_timestamp_remains_a_separate_observation(self):
        base = row(100, facts={'result_values': {'speed': 599}})
        supplement = row(200, facts={'result_values': {'speed': 600}})

        got = merge([base], [supplement])

        self.assertEqual([item['source_timestamp_ms'] for item in got], [100, 200])
        self.assertEqual(got[0]['facts']['result_values'], {'speed': 599})
        self.assertEqual(got[1]['facts']['result_values'], {'speed': 600})
        self.assertEqual(
            got[1]['facts']['inspection_merge_provenance']['result_values']['speed']['origin'],
            'supplemental',
        )


if __name__ == '__main__':
    unittest.main()
