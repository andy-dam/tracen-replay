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
