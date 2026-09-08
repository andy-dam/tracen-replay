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
