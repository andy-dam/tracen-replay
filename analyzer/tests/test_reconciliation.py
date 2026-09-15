import unittest

from tracen_replay.reconcile import FIELDS, account, distinct_changes, stable_checkpoints, preview_segments, reconcile_changes
from tracen_replay.stats import parse_log
from tracen_replay.accounting_view import render_accounting


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

    def test_baseline_and_repeated_changes_disclose_identity_uncertainty(self):
        events=[dict(deltas={'speed':10}),dict(deltas={'speed':10}),dict(deltas={'wit':5})]
        result=reconcile_changes(events,[dict(deltas={'wit':5})])
        self.assertEqual(result['events'],[events[0]])
        self.assertEqual([d['decision'] for d in result['decisions']],['merge_partial_or_identical','exclude_baseline_match'])
        self.assertFalse(result['identity_verified'])

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

    def test_partial_scrolled_blocks_do_not_double_count(self):
        events=[dict(deltas={'speed':15}),dict(deltas={'speed':15,'power':5}),dict(deltas={'skill_points':10})]
        baseline=[dict(deltas={'wit':10,'skill_points':10})]
        self.assertEqual(distinct_changes(events,baseline),[events[1]])

    def test_previews_and_friendship_do_not_become_stat_gains(self):
        lines=[dict(text=t,confidence=99) for t in ['Speed +13','Possible outcome: Speed went up by 13.','Friendship with Speed Star went up by 5.']]
        self.assertEqual(parse_log(lines),[])

    def test_training_action_requires_explicit_log_heading(self):
        lines=[dict(text='Training Stamina Lvl 1',confidence=90,training_heading='stamina'),dict(text='Stamina went up by 20.',confidence=90),dict(text='Guts went up by 5.',confidence=90)]
        result=parse_log(lines)
        self.assertEqual(result[0]['training_option'],'stamina')
        self.assertEqual(result[0]['event_type'],'logged_training_result')
        self.assertEqual(result[0]['deltas'],{'stamina':20,'guts':5})
        self.assertIsNone(parse_log(lines[1:])[0]['training_option'])

    def test_outcomes_resolve_arithmetic_without_inventing_events(self):
        before=dict(id='a',last_seen_ms=0,values=reading(0)['values'])
        after=dict(id='b',first_seen_ms=1000,values=reading(0,wit=106,skill_points=105)['values'])
        events=parse_log([dict(text='Wit went up by 6.',confidence=95),dict(text='Skill Pts went up by 5.',confidence=95)])
        self.assertEqual(account(before,after,events)['status'],'balanced')
        self.assertFalse(account(before,after,events)['complete_event_history'])


if __name__ == '__main__':
    unittest.main()
