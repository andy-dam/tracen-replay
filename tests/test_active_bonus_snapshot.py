import unittest

from tracen_replay.transactions import active_bonus_snapshot


class ActiveBonusSnapshotTests(unittest.TestCase):
    fields=('friendship_training_effectiveness','specialty_priority','support_chain_event_frequency')

    def row(self,time,current,planned=None):
        return dict(source_timestamp_ms=time,evidence=f'frame-{time}.png',
                    facts=dict(current_concert_bonuses=current,planned_concert_bonuses=planned or {}))

    def test_source_pattern_preserves_single_frame_fields_without_confirmation(self):
        rows=[self.row(661750,dict(zip(self.fields,(10,10,0))))]
        rows.extend(self.row(t,dict(specialty_priority=10)) for t in (662000,662250,662500))
        result=active_bonus_snapshot(rows)
        self.assertEqual(result['values'],dict(specialty_priority=10))
        self.assertFalse(result['complete'])
        self.assertEqual(result['observations']['support_chain_event_frequency'][0]['value'],0)
        self.assertEqual(result['unresolved_fields']['friendship_training_effectiveness'],'insufficient_distinct_timestamps')

    def test_duplicate_frame_is_not_temporal_confirmation(self):
        row=self.row(1000,dict(zip(self.fields,(10,10,0))))
        self.assertEqual(active_bonus_snapshot([row,row])['values'],{})

    def test_two_distinct_matching_observations_confirm_zero(self):
        values=dict(zip(self.fields,(10,10,0)))
        result=active_bonus_snapshot([self.row(1000,values),self.row(1250,values)])
        self.assertEqual(result['values'],values)
        self.assertTrue(result['complete'])
        self.assertEqual(result['unresolved_fields'],{})

    def test_conflicts_and_planned_only_fields_remain_unconfirmed(self):
        rows=[self.row(1000,dict(specialty_priority=10),dict(friendship_training_effectiveness=20)),
              self.row(1250,dict(specialty_priority=15),dict(friendship_training_effectiveness=20))]
        result=active_bonus_snapshot(rows)
        self.assertEqual(result['values'],{})
        self.assertEqual(result['unresolved_fields']['specialty_priority'],'conflicting_observations')
        self.assertEqual(result['unresolved_fields']['friendship_training_effectiveness'],'not_observed')
        self.assertEqual(result['observations']['friendship_training_effectiveness'],[])
