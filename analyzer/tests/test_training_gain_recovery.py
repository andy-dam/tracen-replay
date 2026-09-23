import copy
import json
import unittest
from unittest.mock import patch
from tests.test_gameplay import workspace_temp
from tracen_replay.training_gain_recovery import plan,promote,recover


def row(t,gains):return dict(source_timestamp_ms=t,evidence=f'{t}.png',screen='training_result',facts={'training_gains':gains})


class TrainingGainRecoveryTests(unittest.TestCase):
    def test_plan_uses_conflicting_badges_without_balance_input(self):
        rows=[row(100,{'speed':1}),row(150,{'speed':13})]
        event=dict(id='training',kind='training',first_seen_ms=90,last_seen_ms=400,conflicting_readings={'speed':[1,13]})
        self.assertEqual(plan(rows,[event])[0],dict(start_ms=90,end_ms=250,owner_id='training',fields=['speed'],reason='conflicting_observed_training_badge_digits'))
        self.assertEqual(plan(rows,[dict(event,kind='outcome')]),[])

    def test_a_gain_read_on_one_frame_that_was_not_accepted_requests_a_reread(self):
        rows=[row(100,{'speed':1,'wit':6})]
        event=dict(id='training',kind='training',first_seen_ms=90,last_seen_ms=400,deltas={'speed':1},conflicting_readings={})
        self.assertEqual(plan(rows,[event])[0],dict(start_ms=90,end_ms=200,owner_id='training',fields=['wit'],reason='single_frame_training_gain'))
        # Accepted on the event, or seen on two frames, it needs no reread.
        self.assertEqual(plan(rows,[dict(event,deltas={'speed':1,'wit':6})]),[])
        self.assertEqual(plan([row(100,{'wit':6}),row(150,{'wit':6})],[event]),[])
        # Filled from the state instead, it was not read: the reread is owed.
        derived=dict(event,deltas={'speed':1,'wit':6},result_state_derived_fields=['wit'])
        self.assertEqual([(w['fields'],w['reason']) for w in plan(rows,[derived])],[(['wit'],'single_frame_training_gain')])

    def test_a_chosen_training_whose_result_was_never_sampled_is_reread_before_the_next_screen(self):
        # The player skipped the card between two sampled frames: the event
        # is the frame after the choice, then the animation, then the next
        # recognized screen. The card showed in the moments before it.
        rows=[dict(source_timestamp_ms=t,evidence=f'{t}.png',screen='unknown',facts={}) for t in (1000,1250,1500,1750)]
        rows.append(dict(source_timestamp_ms=4000,evidence='4000.png',screen='event_outcome',facts={}))
        event=dict(id='training',kind='training',training_option='wit',first_seen_ms=1000,last_seen_ms=1000,
                   deltas={'wit':44},conflicting_readings={})
        got=plan(rows,[event])
        self.assertEqual([(w['start_ms'],w['end_ms'],w['reason'],w['training_option']) for w in got],
                         [(2500,4000,'training_without_result_frames','wit')])
        # No option chosen, or no recognized screen within ten seconds: no reread.
        self.assertEqual(plan(rows,[dict(event,training_option=None)]),[])
        self.assertEqual(plan(rows[:4]+[dict(rows[4],source_timestamp_ms=11001)],[event]),[])

    def test_a_result_with_no_accepted_gain_keeps_its_whole_interval_reread(self):
        # One declined single-frame reading must not narrow the bounded reread
        # that a result with no accepted badge at all is owed: the other
        # badges may sit outside the one frame.
        result=dict(row(1000,{'wit':6}),training_option='speed')
        result['facts'].update(result_values={'speed':334,'skill_points':299},training_outcome='success')
        event=dict(id='training',kind='training',training_option='speed',first_seen_ms=900,last_seen_ms=1400,deltas={},conflicting_readings={})
        got=plan([result],[event])
        self.assertEqual([(w['start_ms'],w['end_ms'],w['reason']) for w in got],[(400,1900,'committed_result_missing_signed_gain_observation')])
        self.assertEqual(got[0]['fields'],['guts','power','skill_points','speed','stamina','wit'])
        # With another gain accepted, the single frame owns its own narrow reread.
        self.assertEqual(plan([result],[dict(event,deltas={'speed':1})])[0]['reason'],'single_frame_training_gain')
    def test_a_card_left_on_screen_longer_than_the_span_is_reread_from_its_first_frames(self):
        result=dict(row(1000,{}),training_option='speed')
        result['facts'].update(result_values={'speed':334},training_outcome='success')
        event=dict(id='training',kind='training',training_option='speed',first_seen_ms=900,last_seen_ms=2400,deltas={},conflicting_readings={})
        self.assertEqual([(w['start_ms'],w['end_ms'],w['reason']) for w in plan([result],[event])],
                         [(400,1900,'committed_result_missing_signed_gain_observation')])

    def test_a_performance_row_the_committed_card_left_unread_gets_the_bounded_reread(self):
        # The stat badges were read and accepted; the Dance row's award was
        # cut or merged under the floor on every sampled frame. The reread
        # projects that row from frames the accepted badges bind to this card.
        result=dict(row(1000,{'wit':6}),training_option='wit')
        result['facts'].update(result_values={'wit':334,'skill_points':299},training_outcome='success')
        event=dict(id='training',kind='training',training_option='wit',first_seen_ms=900,last_seen_ms=1400,
                   deltas={'wit':6},conflicting_readings={},performance_rows_unread=['dance'])
        got=plan([result],[event])
        self.assertEqual(got,[dict(start_ms=400,end_ms=1900,owner_id='training',fields=['wit'],performance_fields=['dance'],
                                   training_option='wit',source_result_projection=True,
                                   reason='performance_row_unread_on_committed_result')])
        # Every row read, no reread; no accepted badge at all, the whole-card reread as before.
        self.assertEqual(plan([result],[dict(event,performance_rows_unread=[])]),[])
        self.assertEqual([w['reason'] for w in plan([result],[dict(event,deltas={})])],
                         ['committed_result_missing_signed_gain_observation'])

    def test_a_card_seen_only_as_a_candidate_with_no_gain_read_gets_the_bounded_reread(self):
        # The player skipped through the card: one frame, its banner unread,
        # no badge accepted. The high-rate reread sees what the sparse pass fell between.
        card=dict(row(1000,{}),screen='training_result_candidate',training_option='wit')
        event=dict(id='training',kind='training',training_option='wit',first_seen_ms=1000,last_seen_ms=1000,deltas={},conflicting_readings={})
        got=plan([card],[event])
        self.assertEqual([(w['start_ms'],w['end_ms'],w['reason'],w['fields']) for w in got],
                         [(500,1500,'result_seen_without_any_signed_gain',['guts','power','skill_points','speed','stamina','wit'])])
        # With a gain accepted, or no card frame at all, no such reread.
        self.assertEqual(plan([card],[dict(event,deltas={'wit':22})]),[])
        self.assertEqual(plan([],[event]),[])
        # An event that outlasts the span (its banner stayed up) is reread from its first moments.
        self.assertEqual([(w['start_ms'],w['end_ms']) for w in plan([card],[dict(event,last_seen_ms=2200)])],[(500,2000)])

    def test_a_card_that_showed_before_the_result_frames_is_reread_at_its_own_moment(self):
        # The card came and went in a quarter second, caught once mid
        # animation: unclassified, its badges under the banner, but the stat
        # names along its row read through. The event is dated by the empty
        # transition frames 1.5 s later; that window would miss the card.
        def glimpse(t, labels=('Speed', 'Skill Pts'), screen='unknown', **extra):
            lines=[dict(text=text, confidence=96, box=[289+466*i, 747, 360+466*i, 764]) for i, text in enumerate(labels)]
            lines.append(dict(text='+11', confidence=100, box=[755, 678, 831, 720]))
            return dict(source_timestamp_ms=t, evidence=f'{t}.png', screen=screen, facts={}, stats={}, ocr={'neural': lines}, **extra)
        empty=dict(row(2229500,{}),training_option='guts');empty['facts'].update(result_values={'skill_points':None})
        event=dict(id='training',kind='training',training_option='guts',first_seen_ms=2229500,last_seen_ms=2229750,deltas={},conflicting_readings={})
        got=plan([glimpse(2228000),empty],[event])
        self.assertEqual([(w['start_ms'],w['end_ms'],w['reason']) for w in got],
                         [(2229000,2230250,'result_seen_without_any_signed_gain'),(2227750,2229000,'card_shown_before_result_frames')])
        self.assertEqual((got[1]['owner_id'],got[1]['fields'],got[1]['training_option']),('training',got[0]['fields'],'guts'))
        # A committed result with a gain missing gets the same second window
        # only while it has no accepted gain at all.
        committed=dict(empty);committed['facts']=dict(empty['facts'],result_values={'guts':334,'skill_points':299},training_outcome='success')
        self.assertEqual([w['reason'] for w in plan([glimpse(2228000),committed],[event])],
                         ['committed_result_missing_signed_gain_observation','card_shown_before_result_frames'])
        self.assertNotIn('card_shown_before_result_frames',
                         [w['reason'] for w in plan([glimpse(2228000),committed],[dict(event,deltas={'guts':32})])])
        # One name, a preview frame, a preview after the glimpse, or a glimpse
        # too long before: no second window.
        for rows in ([glimpse(2228000,labels=('Speed',)),empty],
                     [glimpse(2228000,screen='training_preview'),empty],
                     [glimpse(2228000),glimpse(2228500,screen='training_preview'),empty],
                     [glimpse(2226500),empty]):
            with self.subTest(rows=[r['source_timestamp_ms'] for r in rows]):
                self.assertEqual([w['reason'] for w in plan(rows,[event])],['result_seen_without_any_signed_gain'])

    def test_same_timestamp_unrequested_fields_and_other_occurrences_are_excluded(self):
        original=[row(100,{'speed':1})];before=copy.deepcopy(original)
        fresh=[row(100,{'speed':13}),row(117,{'speed':13,'wit':999}),row(300,{'speed':13})]
        windows=[dict(start_ms=90,end_ms=250,owner_id='training',fields=['speed'])]
        result=promote(original,fresh,windows)
        self.assertEqual(result[0],original[0]);self.assertEqual(original,before)
        self.assertEqual(len(result),2);self.assertEqual(result[1]['facts']['training_gains'],{'speed':13})
        self.assertEqual(result[1]['stats'],{});self.assertEqual(result[1]['effects'],[])
        self.assertIsNone(result[1]['training_option'])
        self.assertEqual(promote(original,fresh,windows*2),original)

    def test_no_ocr_and_zero_budget_leave_uncached_windows_pending(self):
        rows=[row(100,{'speed':1}),row(150,{'speed':13})]
        events=[dict(id='training',kind='training',first_seen_ms=90,last_seen_ms=400,
                     conflicting_readings={'speed':[1,13]})]
        for options in ({'allow_ocr':False},{'max_windows':0},{'max_duration_ms':0}):
            with self.subTest(options=options),workspace_temp() as root:
                with patch('tracen_replay.vision.NeuralReader',side_effect=AssertionError('Unexpected OCR')), \
                     patch('tracen_replay.inspect_receipts.inspect',side_effect=AssertionError('Unexpected capture')):
                    result,metadata=recover(root/'source.mp4',root,{'sha256':'source'},rows,events,**options)
                self.assertEqual(result,rows)
                self.assertEqual(metadata['pending_windows'],plan(rows,events))
                self.assertEqual(metadata['processed_windows'],[])
                self.assertEqual(metadata['new_frames'],0)

    def test_a_window_a_paused_run_read_counts_toward_the_budget(self):
        # Two trainings to reread and a budget of one window: the window read
        # before a pause is the budget, as it is for a run straight through.
        rows=[row(100,{'speed':1}),row(150,{'speed':13}),row(9100,{'speed':1}),row(9150,{'speed':13})]
        events=[dict(id=f'training-{start}',kind='training',first_seen_ms=start,last_seen_ms=start+310,
                     conflicting_readings={'speed':[1,13]}) for start in (90,9090)]
        first,second=plan(rows,events)
        with workspace_temp() as root:
            directory=root/'training-gain-recovery';directory.mkdir()
            (directory/'receipt-inspection.json').write_text(json.dumps(dict(
                source_sha256='source',windows=[dict(start_ms=first['start_ms'],end_ms=first['end_ms'],fps=60)],readings=[])),
                encoding='utf-8')
            with patch('tracen_replay.inspect_receipts.inspect'), \
                 patch('tracen_replay.dense_inspection_pool.prepare_windows',return_value=0), \
                 patch('tracen_replay.vision.NeuralReader',side_effect=AssertionError('Unexpected OCR')):
                _,metadata=recover(root/'source.mp4',root,{'sha256':'source'},rows,events,max_windows=1)
        self.assertEqual(metadata['processed_windows'],[first])
        self.assertEqual(metadata['pending_windows'],[second])

    def test_changed_source_cache_is_rejected_before_probe(self):
        rows=[row(100,{'speed':1})]
        events=[dict(id='training',kind='training',first_seen_ms=90,last_seen_ms=400,
                     conflicting_readings={'speed':[1,13]})]
        with workspace_temp() as root:
            directory=root/'training-gain-recovery';directory.mkdir()
            (directory/'capture.json').write_text(json.dumps({'source':{'sha256':'old'}}),encoding='utf-8')
            with patch('tracen_replay.inspect_receipts.inspect',side_effect=AssertionError('Unexpected capture')):
                with self.assertRaisesRegex(ValueError,'source changed'):
                    recover(root/'source.mp4',root,{'sha256':'new'},rows,events)
