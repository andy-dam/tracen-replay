import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import analyze_frames, cached_readings
from tracen_replay.pipeline import PipelineError
from tracen_replay.recording_verification import audit
from tracen_replay.action_evaluate import evaluate


class FakeReader:
    Image=Image
    fingerprint='test-engine'
    inputs=[]
    def __init__(self,*args):pass
    def read(self,pane):
        if pane.size!=(810,1080):raise AssertionError('Auxiliary pixels reached recognition.')
        self.inputs.append(hashlib.sha256(pane.tobytes()).hexdigest())
        return dict(lines=[],regions={},header='',current_grid=False,result_grid=False,
                    model_sha256={'test':'model'},engine_fingerprint=self.fingerprint,gameplay_sha256=self.inputs[-1])


class FullRecordingTests(unittest.TestCase):
    def test_cli_loads_hint_cache_and_does_not_publish_invalid_evidence(self):
        from contextlib import ExitStack
        from tracen_replay.full_recording import main
        for invalid in (False, True):
            with self.subTest(invalid=invalid), workspace_temp() as root, ExitStack() as stack:
                report={'source':{'sha256':'a'*64}}
                rows=[{'evidence':'source.png','source_timestamp_ms':1000}]
                candidates=[{'identity':'prepared source evidence'}]
                stack.enter_context(patch('sys.argv',['full_recording','source.mp4','--output',str(root),'--reparse-only']))
                stack.enter_context(patch('tracen_replay.full_recording.capture',return_value=report))
                stack.enter_context(patch('tracen_replay.full_recording.cached_readings',return_value=rows))
                stack.enter_context(patch('tracen_replay.full_recording.analyze_frames',side_effect=AssertionError('OCR during replay')))
                stack.enter_context(patch('tracen_replay.inspect_choices.load',return_value=({},[])))
                stack.enter_context(patch('tracen_replay.race_reward_inspection.load',return_value=({},[])))
                loader=stack.enter_context(patch('tracen_replay.hint_card_cache.load',return_value=candidates,
                    side_effect=ValueError('stale source proof') if invalid else None))
                assemble=stack.enter_context(patch('tracen_replay.full_recording.assemble',return_value=report))
                stack.enter_context(patch('tracen_replay.full_recording.validate_output'))
                save=stack.enter_context(patch('tracen_replay.full_recording.save_json'))
                stack.enter_context(patch('tracen_replay.full_recording.render',return_value='verified report'))
                stack.enter_context(patch('builtins.print'))
                if invalid:
                    with self.assertRaisesRegex(PipelineError,'Hint-card cache evidence invalid: stale source proof'):
                        main()
                    assemble.assert_not_called();save.assert_not_called()
                    self.assertFalse((root/'index.html').exists())
                else:
                    main()
                    assemble.assert_called_once_with(report,rows,[],[],candidates)
                    save.assert_called_once_with(root/'report.json',report)
                loader.assert_called_once_with(rows,root,'a'*64)

    def test_neural_input_is_independent_of_auxiliary_pixels_and_cache_rejects_tampering(self):
        with workspace_temp() as root:
            frame=dict(id='one',evidence='frame.png',source_timestamp_ms=0)
            report=dict(frames=[frame]);FakeReader.inputs=[]
            with patch('tracen_replay.full_recording.NeuralReader',FakeReader):
                for background in ('black','red'):
                    image=Image.new('RGB',(1920,1080),background)
                    image.paste(Image.new('RGB',(810,1080),'white'),(148,0));image.save(root/'frame.png')
                    analyze_frames(report,root,workers=1)
            self.assertEqual(len(FakeReader.inputs),2)
            self.assertEqual(FakeReader.inputs[0],FakeReader.inputs[1])
            self.assertEqual(len(cached_readings(report,root)),1)
            (root/'frame.png').write_bytes(b'changed')
            with self.assertRaisesRegex(PipelineError,'source evidence mismatch'):cached_readings(report,root)

    def test_missing_ocr_is_not_complete(self):
        with workspace_temp() as root:
            report=dict(frames=[dict(id='absent',evidence='absent.png',source_timestamp_ms=0)])
            self.assertEqual(cached_readings(report,root,allow_partial=True),[])
            with self.assertRaisesRegex(PipelineError,'Missing OCR'):cached_readings(report,root)

    def test_source_coverage_checks_pts_and_missing_samples(self):
        frames=[dict(source_timestamp_ms=t,source_pts=t*60//1000,time_base='1/60') for t in (0,250,500,750)]
        report=dict(source=dict(sha256='test',duration_ms=1000),sampling=dict(requested_fps=4),frames=frames,
            gameplay_tracking=dict(readings=[dict(source_timestamp_ms=f['source_timestamp_ms'],screen='unknown') for f in frames],intervals=[]))
        result=audit(report)
        self.assertTrue(result['full_source_processed']);self.assertFalse(result['fully_verified']);self.assertFalse(result['go_ready'])
        report['frames'][1]['source_pts']=0
        self.assertIn('source_pts_timestamp_mismatch',audit(report)['source_coverage_errors'])
        report['gameplay_tracking']['readings'].pop()
        self.assertIn('unprocessed_base_frames',audit(report)['source_coverage_errors'])

    def test_reviewed_negative_action_scope_detects_false_completions(self):
        reference=dict(source_sha256='one',start_ms=100,end_ms=200,kinds=['race'],scope='race entry only',
            actions=[],no_completed_actions=True,independently_reviewed=True)
        report=dict(source=dict(sha256='one'),gameplay_tracking=dict(auxiliary_log_used=False,
            turn_action_receipts=[dict(kind='race',source_timestamp_ms=200)]))
        result=evaluate(reference,report)
        self.assertTrue(result['passed']);self.assertIsNone(result['recall']);self.assertIsNone(result['precision'])
        report['gameplay_tracking']['turn_action_receipts'].append(dict(kind='race',source_timestamp_ms=150))
        result=evaluate(reference,report)
        self.assertFalse(result['passed']);self.assertEqual(len(result['extra']),1)
        self.assertEqual(result['precision'],0);self.assertIsNone(result['recall'])

    def test_empty_action_labels_require_explicit_review(self):
        reference=dict(source_sha256='one',start_ms=100,end_ms=200,kinds=['race'],scope='unknown',actions=[])
        report=dict(source=dict(sha256='one'),gameplay_tracking=dict(auxiliary_log_used=False,turn_action_receipts=[]))
        for changes in ({},{'no_completed_actions':True},{'independently_reviewed':True},
                        {'no_completed_actions':True,'independently_reviewed':True,'end_ms':100},
                        {'no_completed_actions':True,'independently_reviewed':True,'actions':[dict(kind='race',start_ms=110,end_ms=150)]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):evaluate(dict(reference,**changes),report)

    def test_duplicate_action_receipts_reduce_precision(self):
        reference=dict(source_sha256='one',start_ms=0,end_ms=2000,kinds=['training'],scope='training only',
            actions=[dict(kind='training',training_option='speed',start_ms=500,end_ms=1000)])
        action=dict(kind='training',training_option='speed',source_timestamp_ms=750)
        report=dict(source=dict(sha256='one'),gameplay_tracking=dict(auxiliary_log_used=False,turn_action_receipts=[action,dict(action,source_timestamp_ms=800)]))
        result=evaluate(reference,report)
        self.assertEqual(result['recall'],1);self.assertEqual(result['precision'],.5);self.assertFalse(result['passed'])
        report['source']['sha256']='two'
        with self.assertRaises(ValueError):evaluate(reference,report)


if __name__=='__main__':unittest.main()
