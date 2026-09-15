import unittest
from tracen_replay.recording_verification import final_crosscheck


def row(screen,time,facts):
    return dict(screen=screen,source_timestamp_ms=time,facts=facts,evidence=f'frame-{time}.png')


class FinalCrosscheckTests(unittest.TestCase):
    attributes=dict(speed=1341,stamina=737,power=1029,guts=629,wit=1230)

    def test_rank_only_hub_preserves_numeric_summary_and_points_without_crosscheck(self):
        readings=[row('career_completion_hub',1000,dict(current_skill_points=8,final_attributes={})),
                  row('career_summary',2000,dict(final_attributes=self.attributes))]
        result=final_crosscheck(dict(readings=readings))
        self.assertFalse(result['complete_final_observations'])
        self.assertEqual(result['observed_values'],dict(self.attributes,skill_points=8))
        self.assertEqual(result['missing_observations'],['numeric_completion_hub_attributes'])
        self.assertIsNone(result['attributes_agree_between_hub_and_summary'])
        self.assertFalse(result['fully_verified'])
        self.assertNotIn('whole_career_stat_accounting',result)

    def test_zero_points_is_observed_while_missing_points_stays_absent(self):
        summaries=[row('career_summary',2000,dict(final_attributes=self.attributes))]
        absent=final_crosscheck(dict(readings=summaries))
        self.assertNotIn('skill_points',absent['observed_values'])
        self.assertIn('completion_hub_skill_points',absent['missing_observations'])
        zero=final_crosscheck(dict(readings=summaries+[row('career_completion_hub',1000,dict(current_skill_points=0))]))
        self.assertEqual(zero['observed_values']['skill_points'],0)

    def test_latest_observation_selected_by_time_not_input_order(self):
        readings=[row('career_completion_hub',1500,dict(current_skill_points=8)),
                  row('career_completion_hub',1000,dict(current_skill_points=100)),
                  row('career_summary',2000,dict(final_attributes=self.attributes))]
        result=final_crosscheck(dict(readings=readings))
        self.assertEqual(result['observed_values']['skill_points'],8)
        self.assertEqual(result['observation_timestamps_ms']['skill_points'],1500)

    def test_missing_both_screens_does_not_fabricate_final_values(self):
        result=final_crosscheck(dict(readings=[]))
        self.assertEqual(result['observed_values'],{})
        self.assertEqual(len(result['missing_observations']),3)

    def test_numeric_disagreement_still_fails_comparison(self):
        result=final_crosscheck(dict(readings=[
            row('career_completion_hub',1000,dict(current_skill_points=8,final_attributes=dict(self.attributes,speed=1300))),
            row('career_summary',2000,dict(final_attributes=self.attributes))]))
        self.assertTrue(result['complete_final_observations'])
        self.assertFalse(result['attributes_agree_between_hub_and_summary'])
        self.assertFalse(result['fully_verified'])
