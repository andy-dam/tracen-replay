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
