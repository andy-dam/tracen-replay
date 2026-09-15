import json
import unittest
from pathlib import Path

from tracen_replay.identity import track_occurrences, interval_occurrences, track_outcomes, attach_outcomes, refine_intervals
from tracen_replay.reconcile import FIELDS, account
from tracen_replay.stats import parse_log


def snapshot(time,*texts):
    return dict(source_timestamp_ms=time,evidence=f'frames/{time}.jpg',
                lines=[dict(text=text,confidence=95,training_heading=None) for text in texts])


class IdentityTests(unittest.TestCase):
    def test_missing_heading_does_not_join_separate_cards(self):
        lines=[dict(text='Power went up by 10.',confidence=95,top=100,bottom=120),
               dict(text='Skill Pts went up by 10.',confidence=95,top=126,bottom=146),
               dict(text='Stamina went up by 8.',confidence=95,top=240,bottom=260)]
        blocks=parse_log(lines)
        self.assertEqual([b['deltas'] for b in blocks],[{'power':10,'skill_points':10},{'stamina':8}])

    def test_cropped_heading_retains_matching_named_footer_context(self):
        rows=[snapshot(0,'Training Stamina Lvl 1','Stamina went up by 17.',
                       'Friendship with Light Hello went up by 4.'),
              snapshot(250,'Stamina went up by 17.','Friendship with Light Hello went up by 4.')]
        ledger=track_occurrences(rows,parse_log)
        self.assertEqual(len(ledger['occurrences']),1)

    def test_delayed_log_requires_matching_narrative_not_just_gains(self):
        narrative='She dominates the race pulling off an impressive victory'
        outcomes=[snapshot(100,narrative),snapshot(350,'Power went up by 5.')]
        before=dict(id='a',last_seen_ms=1000,values={f:100 for f in FIELDS})
        after=dict(id='b',first_seen_ms=3000,values={f:100 for f in FIELDS})
        for context,expected in [(narrative,'balanced'),('A different event with an entirely different outcome','unresolved')]:
            with self.subTest(context=context):
                logs=[snapshot(2000,context,'Power went up by 5.')]
                intervals,_,_=refine_intervals([before,after],logs,outcomes,[account(before,after,[])],parse_log)
                self.assertEqual(intervals[0]['status'],expected)
                if expected=='balanced':
                    excluded=intervals[0]['excluded_historical_occurrences'][0]
                    self.assertEqual(excluded['context_evidence'],'frames/100.jpg')
                    self.assertFalse(excluded['identity_verified'])

    def test_real_delayed_power_log_matches_earlier_main_narrative(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/delayed-outcome-ocr.json').read_text(encoding='utf-8'))
        before,after=fixture['checkpoints']
        intervals,_,_=refine_intervals([before,after],fixture['log_readings'],fixture['outcome_readings'],
                                     [account(before,after,[])],parse_log)
        interval=intervals[0]
        self.assertEqual(interval['status'],'balanced')
        self.assertEqual(interval['supported_change']['power'],0)
        self.assertEqual(interval['supported_change']['wit'],13)
        excluded=interval['excluded_historical_occurrences']
        self.assertEqual(len(excluded),1)
        self.assertEqual(excluded[0]['observed_at_ms'],189000)
        self.assertIn('context_evidence',excluded[0])

    def test_old_matching_narrative_is_not_unconditionally_excluded(self):
        narrative='She dominates the race pulling off an impressive victory'
        before=dict(id='a',last_seen_ms=20000,values={f:100 for f in FIELDS})
        after=dict(id='b',first_seen_ms=23000,values={f:100 for f in FIELDS})
        intervals,_,_=refine_intervals([before,after],[snapshot(22000,narrative,'Power went up by 5.')],
                                     [snapshot(100,narrative),snapshot(350,'Power went up by 5.')],
                                     [account(before,after,[])],parse_log)
        self.assertEqual(len(intervals[0]['events']),1)
        self.assertEqual(intervals[0]['excluded_historical_occurrences'],[])

    def test_card_border_artifact_keeps_raw_text_but_parses_delta(self):
        block=parse_log(snapshot(0,'Stamina went up by 5. |')['lines'])[0]
        self.assertEqual(block['deltas'],{'stamina':5})
        self.assertEqual(block['text'],'Stamina went up by 5. |')
        self.assertEqual(parse_log(snapshot(0,'Possible: Stamina went up by 5. |')['lines']),[])
        self.assertEqual(parse_log(snapshot(0,'Stamina went up by 5. maybe')['lines']),[])

    def test_scrolled_partial_block_keeps_occurrence(self):
        rows=[snapshot(0,'A distinctive support event title','Speed went up by 10.'),
              snapshot(250,'A distinctive support event title','Speed went up by 10.','Power went up by 5.')]
        ledger=track_occurrences(rows,parse_log)
        self.assertEqual(len(ledger['occurrences']),1)
        occurrence=ledger['occurrences'][0]
        self.assertEqual(occurrence['deltas'],{'speed':10,'power':5})
        self.assertEqual(occurrence['observations'][0]['deltas'],{'speed':10})

    def test_identical_gains_with_different_context_are_distinct(self):
        rows=[snapshot(0,'First distinctive support event','Speed went up by 10.','Energy went down by 20.'),
              snapshot(250,'Second different support event','Speed went up by 10.','Energy went down by 20.')]
        ledger=track_occurrences(rows,parse_log)
        self.assertEqual(len(ledger['occurrences']),2)
        events,_=interval_occurrences(ledger,dict(last_seen_ms=-1),dict(first_seen_ms=250))
        self.assertEqual(sum(e['deltas']['speed'] for e in events),20)

    def test_two_equal_visible_entries_are_not_collapsed(self):
        rows=[snapshot(0,'First distinctive support event','Speed went up by 10.'),
              snapshot(250,'First distinctive support event','Speed went up by 10.',
                       'Second different support event','Speed went up by 10.')]
        ledger=track_occurrences(rows,parse_log)
        self.assertEqual(len(ledger['occurrences']),2)
        self.assertEqual([o['first_seen_ms'] for o in ledger['occurrences']],[0,250])
        events,excluded=interval_occurrences(ledger,dict(last_seen_ms=0),dict(first_seen_ms=250))
        self.assertEqual(sum(e['deltas']['speed'] for e in events),10)
        self.assertEqual(len(excluded),1)

    def test_delta_only_match_does_not_claim_identity(self):
        ledger=track_occurrences([snapshot(0,'Speed went up by 10.'),snapshot(250,'Speed went up by 10.')],parse_log)
        self.assertEqual(len(ledger['occurrences']),2)
        self.assertTrue(all(o['identity_status']=='unanchored' for o in ledger['occurrences']))

    def test_context_return_after_gap_is_explicitly_unverified(self):
        rows=[snapshot(0,'A distinctive support event title','Speed went up by 10.'),snapshot(250),
              snapshot(5000,'A distinctive support event title','Speed went up by 10.')]
        ledger=track_occurrences(rows,parse_log)
        self.assertEqual(len(ledger['occurrences']),1)
        occurrence=ledger['occurrences'][0]
        self.assertEqual(occurrence['identity_status'],'context_reappearance')
        self.assertFalse(occurrence['identity_verified'])
        self.assertIsNone(occurrence['event_time_ms'])

    def test_real_historical_stamina_entry_is_excluded(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/historical-log-ocr.json').read_text(encoding='utf-8'))
        ledger=track_occurrences(fixture['snapshots'],parse_log)
        stamina=[o for o in ledger['occurrences'] if o['deltas']=={'stamina':5}]
        self.assertEqual(len(stamina),1)
        self.assertLess(stamina[0]['first_seen_ms'],153250)
        events,excluded=interval_occurrences(ledger,dict(last_seen_ms=153250),dict(first_seen_ms=156000))
        self.assertTrue(any(e['occurrence_id']==stamina[0]['id'] for e in excluded))
        self.assertFalse(any(e['deltas']=={'stamina':5} for e in events))
        self.assertEqual(sum(e['deltas'].get('stamina',0) for e in events),8)

    def test_future_fuller_reading_does_not_leak_into_interval(self):
        ledger=track_occurrences([snapshot(100,'A distinctive support event title','Speed went up by 10.'),
                                  snapshot(350,'A distinctive support event title','Speed went up by 10.','Power went up by 5.')],parse_log)
        events,_=interval_occurrences(ledger,dict(last_seen_ms=0),dict(first_seen_ms=200))
        self.assertEqual(events[0]['deltas'],{'speed':10})
        self.assertEqual(events[0]['evidence'],'frames/100.jpg')
        self.assertEqual(events[0]['observed_at_ms'],100)

    def test_repeated_main_outcomes_separated_by_blank_remain_distinct(self):
        episodes=track_outcomes([snapshot(100,'Speed went up by 10.'),snapshot(350),snapshot(600,'Speed went up by 10.')],parse_log)
        self.assertEqual(len(episodes),2)
        events=[]
        attach_outcomes(events,episodes,0,1000)
        self.assertEqual(sum(e['deltas']['speed'] for e in events),20)

    def test_outcome_visibility_does_not_become_click_timestamp(self):
        episodes=track_outcomes([snapshot(100,'Speed went up by 10.'),snapshot(350,'Speed went up by 10.')],parse_log)
        events=[dict(id='log-1',origin='ocr_log_occurrence',deltas={'speed':10},observed_at_ms=500,outcome_observations=[])]
        associations=attach_outcomes(events,episodes,0,1000)
        self.assertEqual(len(events),1)
        self.assertEqual(associations[0]['first_seen_ms'],100)
        self.assertEqual(associations[0]['last_seen_ms'],350)
        self.assertIsNone(associations[0]['action_time_ms'])
        self.assertFalse(associations[0]['identity_verified'])

    def test_equal_log_candidates_leave_outcome_assignment_ambiguous(self):
        episodes=track_outcomes([snapshot(100,'Speed went up by 10.')],parse_log)
        events=[dict(id=f'log-{i}',origin='ocr_log_occurrence',deltas={'speed':10},observed_at_ms=i*500,outcome_observations=[]) for i in (1,2)]
        associations=attach_outcomes(events,episodes,0,1500)
        self.assertEqual(len(events),2)
        self.assertEqual(associations[0]['status'],'ambiguous_multiple_log_matches')
        self.assertTrue(all(not e['outcome_observations'] for e in events))


if __name__=='__main__':
    unittest.main()
