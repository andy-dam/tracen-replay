import unittest
from copy import deepcopy

from tests.test_report_contract import valid_report
from tracen_replay.report_contract import ReportContractError, validate

from tracen_replay.source_state_observations import build_observations


def row(time, values, performance=None):
    return dict(source_timestamp_ms=time,evidence=f'{time}.png',screen='unknown',
                stats={'values':values,'calendar_text':'Classic Year Early May'},
                facts={'performance_points':performance})


class SourceStateObservationTests(unittest.TestCase):
    def test_does_not_merge_complementary_partial_states(self):
        result=build_observations([row(0,{'speed':100,'stamina':None}),row(250,{'speed':None,'stamina':200})])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['payload']['values'],{'speed':100,'stamina':None})
        self.assertEqual(result[1]['payload']['values'],{'speed':None,'stamina':200})

    def test_preserves_zero_and_rejects_boolean_values(self):
        result=build_observations([row(0,{'skill_points':0,'speed':True})])
        self.assertEqual(result[0]['payload']['values'],{'skill_points':0,'speed':None})

    def test_groups_only_identical_snapshots_with_local_proofs(self):
        result=build_observations([row(0,{'speed':100}),row(250,{'speed':100}),row(2000,{'speed':100})])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['evidence'],['0.png','250.png'])
        self.assertEqual(result[0]['end_ms'],250)
        self.assertFalse(result[0]['reconciliation_endpoint_inferred'])

    def test_current_values_do_not_borrow_projected_or_runner_scoped_values(self):
        source=row(0,None)
        source['facts'].update(projected_performance_points={'dance':99},
                               race_runner_attributes={'speed':123},final_attributes={'speed':456})
        self.assertEqual(build_observations([source]),[])

    def test_training_result_totals_are_an_explicit_stats_snapshot(self):
        source = row(0, None)
        source['screen'] = 'training_result'
        source['facts'].update(
            result_values={'speed': 212, 'stamina': 221, 'power': 267,
                           'guts': 160, 'wit': 240, 'skill_points': 337},
            training_gains={'speed': 999},
            result_value_candidates={'speed': 999},
        )
        result = build_observations([source])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['payload']['channel'], 'stats')
        self.assertEqual(result[0]['payload']['values'], source['facts']['result_values'])
        self.assertEqual(result[0]['observation_basis'], 'facts.result_values')
        self.assertEqual(result[0]['source_observations'][0]['value_source'], 'facts.result_values')

    def test_result_totals_preserve_partial_unknowns_without_candidates(self):
        source = row(0, None)
        source['screen'] = 'training_result'
        source['facts'].update(
            result_values={'stamina': 623, 'power': 822, 'guts': 491,
                           'wit': 959, 'skill_points': 1969},
            result_value_candidates={'speed': 125, 'stamina': 623},
        )
        result = build_observations([source])
        self.assertEqual(result[0]['payload']['values'], source['facts']['result_values'])
        self.assertNotIn('speed', result[0]['payload']['values'])

    def test_result_candidates_or_gains_alone_do_not_create_a_snapshot(self):
        source = row(0, None)
        source['screen'] = 'training_result'
        source['facts'].update(
            result_value_candidates={'speed': 212},
            training_gains={'speed': 12},
        )
        self.assertEqual(build_observations([source]), [])

    def test_current_grid_is_not_composed_with_result_totals(self):
        source = row(0, {'speed': 100, 'stamina': None})
        source['screen'] = 'training_result'
        source['facts']['result_values'] = {'speed': 101, 'stamina': 200, 'power': 300}
        result = build_observations([source])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['payload']['values'], {'speed': 100, 'stamina': None})
        self.assertEqual(result[0]['observation_basis'], 'stats.values')

    def test_result_totals_do_not_supply_performance_channel(self):
        source = row(0, None)
        source['screen'] = 'training_result'
        source['facts']['result_values'] = {'dance': 99, 'speed': 100}
        # The result total is a stats-layout fact; performance points require
        # their own typed ``facts.performance_points`` field.
        result = build_observations([source])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['payload']['channel'], 'stats')
        self.assertEqual(result[0]['payload']['values'], {'speed': 100})

    def test_result_totals_are_screen_gated(self):
        source = row(0, None)
        source['facts']['result_values'] = {'speed': 212}
        self.assertEqual(build_observations([source]), [])

    def test_contract_accepts_explicit_result_total_snapshot(self):
        original = valid_report()
        source = row(250, None)
        source['screen'] = 'training_result'
        source['facts']['result_values'] = {'speed': 212, 'skill_points': 337}
        original['gameplay_tracking']['readings'] = [source]
        original['gameplay_tracking']['state_observations'] = build_observations([source])
        self.assertIs(validate(original, require_gameplay=True), original)

    def test_channels_remain_separate_and_missing_frames_break_groups(self):
        result=build_observations([row(0,{'speed':100},{'dance':5}),row(100,None),row(250,{'speed':100})])
        self.assertEqual(len(result),3)
        self.assertEqual(result[1]['payload']['channel'],'performance')
        self.assertTrue(all(r['phase']=='observed' and not r['cross_frame_values_merged'] for r in result))

    def test_contract_rejects_changed_values_phase_time_and_evidence(self):
        original = valid_report()
        gameplay = original['gameplay_tracking']
        gameplay['readings'] = [row(250, {'speed': 100})]
        gameplay['state_observations'] = build_observations(gameplay['readings'])
        validate(original, require_gameplay=True)
        for key, value in [('phase', 'applied'), ('start_ms', 0), ('evidence', ['other.png'])]:
            report = deepcopy(original)
            report['gameplay_tracking']['state_observations'][0][key] = value
            with self.assertRaisesRegex(ReportContractError, 'source state readings'):
                validate(report, require_gameplay=True)
        report = deepcopy(original)
        report['gameplay_tracking']['state_observations'][0]['payload']['values']['speed'] = 101
        with self.assertRaisesRegex(ReportContractError, 'source state readings'):
            validate(report, require_gameplay=True)
        del original['gameplay_tracking']['state_observations']
        validate(original, require_gameplay=True)


if __name__ == '__main__':
    unittest.main()
