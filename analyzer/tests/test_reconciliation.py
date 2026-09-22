import unittest

from tracen_replay.reconcile import FIELDS, account, stable_checkpoints, preview_segments


def reading(time, option=None, **changes):
    return dict(source_timestamp_ms=time, evidence=f'frames/{time}.jpg',
                values={f: changes.get(f,100) for f in FIELDS}, preview_option=option,
                training_preview=option is not None)


class ReconciliationTests(unittest.TestCase):
    def test_equal_totals_after_visibility_gap_remain_separate(self):
        rows = [reading(t) for t in (0,250,500)] + [dict(source_timestamp_ms=750, values=None)] + [reading(t) for t in (1000,1250,1500)]
        checkpoints = stable_checkpoints(rows)
        self.assertEqual(len(checkpoints),2)
        self.assertEqual(checkpoints[0]['last_seen_ms'],500)
        self.assertEqual(checkpoints[1]['first_seen_ms'],1000)
        interval = account(*checkpoints, [])
        self.assertEqual(interval['status'],'balanced')
        self.assertFalse(interval['turn_boundary_verified'])
        self.assertFalse(interval['event_assignment_verified'])

    def test_returning_to_preview_does_not_select_it(self):
        rows=[reading(0,'speed'),reading(250,'speed'),reading(500,'wit'),reading(750,'speed')]
        previews=preview_segments(rows)
        self.assertEqual([p['option'] for p in previews],['speed','wit','speed'])
        self.assertTrue(all(p['completed_action'] is None for p in previews))
        self.assertEqual(previews[0]['last_seen_ms'],250)

    def test_browsing_options_does_not_create_completed_actions(self):
        rows = [reading(0,'speed'),reading(250,'stamina'),reading(500,'wit')]
        checkpoints = stable_checkpoints(rows)
        self.assertEqual(len(checkpoints),1)
        self.assertNotIn('completed_action', checkpoints[0])
        self.assertEqual(checkpoints[0]['values']['skill_points'],100)

    def test_brief_or_incomplete_readings_are_not_checkpoints(self):
        rows = [reading(0),reading(250),dict(source_timestamp_ms=500,values=None)]
        self.assertEqual(stable_checkpoints(rows), [])
        rows = [reading(0,skill_points=None),reading(250,skill_points=None),reading(500,skill_points=None)]
        self.assertEqual(stable_checkpoints(rows), [])

    def test_ocr_change_requires_new_consensus(self):
        rows=[reading(t) for t in (0,250,500)]+[reading(750,speed=999)]+[reading(t,speed=120) for t in (1000,1250,1500)]
        self.assertEqual([c['values']['speed'] for c in stable_checkpoints(rows)],[100,120])

    def test_missing_event_stays_unexplained(self):
        before=dict(id='a',last_seen_ms=0,values=reading(0)['values'])
        after=dict(id='b',first_seen_ms=1000,values=reading(0,speed=125,skill_points=80)['values'])
        events=[dict(deltas={'speed':20,'skill_points':-20})]
        result=account(before,after,events)
        self.assertEqual(result['unexplained_change']['speed'],5)
        self.assertEqual(result['unexplained_change']['skill_points'],0)
        self.assertEqual(result['status'],'unresolved')
        self.assertFalse(result['complete_event_history'])

    def test_outcomes_resolve_arithmetic_without_inventing_events(self):
        before=dict(id='a',last_seen_ms=0,values=reading(0)['values'])
        after=dict(id='b',first_seen_ms=1000,values=reading(0,wit=106,skill_points=105)['values'])
        events=[dict(deltas={'wit':6,'skill_points':5})]
        self.assertEqual(account(before,after,events)['status'],'balanced')
        self.assertFalse(account(before,after,events)['complete_event_history'])


if __name__ == '__main__':
    unittest.main()
