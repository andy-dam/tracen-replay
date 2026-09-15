import io
import json
import unittest
from unittest.mock import patch

from tests.test_analysis_job import _write_report
from tests.test_gameplay import workspace_temp
from tracen_replay import full_recording
from tracen_replay.analysis_job import main


class GuardedStageTests(unittest.TestCase):
    def test_a_failing_enrichment_stage_is_recorded_and_the_fallback_is_used(self):
        report = {}

        def boom():
            raise ValueError('turn state summary disagrees')

        self.assertIsNone(full_recording._guarded(report, 'causal_accounting', boom, fallback=None))
        self.assertEqual([f['stage'] for f in report['stage_failures']], ['causal_accounting'])
        self.assertIn('ValueError: turn state summary disagrees', report['stage_failures'][0]['error'])
        self.assertIn('Traceback', report['stage_failures'][0]['traceback'])
        self.assertEqual(full_recording._guarded(report, 'ok', lambda: 7, fallback=None), 7)
        self.assertEqual(len(report['stage_failures']), 1)

    def test_a_fatal_error_still_leaves_a_partial_report_on_disk(self):
        with workspace_temp() as root:
            full_recording._CURRENT.update(
                report={'source': {'sha256': 'x'},
                        'stage_failures': [{'stage': 'earlier', 'error': 'E: e', 'traceback': ''}]},
                output=root)
            try:
                raise RuntimeError('assembly exploded')
            except RuntimeError as exc:
                full_recording._write_partial_report(exc)
            partial = json.loads((root / 'report-partial.json').read_text(encoding='utf-8'))
            self.assertTrue(partial['partial'])
            self.assertEqual([f['stage'] for f in partial['stage_failures']], ['earlier', 'fatal'])
            self.assertIn('RuntimeError: assembly exploded', partial['stage_failures'][-1]['error'])

    def test_progress_lines_carry_wall_clock_and_memory(self):
        out = io.StringIO()
        with patch('sys.stdout', out):
            full_recording._progress('stage_done', name='probe')
        line = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual((line['stage'], line['name']), ('stage_done', 'probe'))
        self.assertIsInstance(line['wall_s'], float)
        if 'rss_mb' in line:
            self.assertGreater(line['rss_mb'], 0)


class JobBoundaryFailSoftTests(unittest.TestCase):
    def test_stage_failures_are_surfaced_in_the_job_result(self):
        with workspace_temp() as root:
            source = root / 'run.mp4'
            source.write_bytes(b'source')
            output = root / 'output'
            stdout = io.StringIO()

            def producer():
                path = _write_report(output, source)
                report = json.loads(path.read_text(encoding='utf-8'))
                report['stage_failures'] = [dict(stage='causal_accounting', error='ValueError: x', traceback='...')]
                report.pop('causal_accounting', None)
                path.write_text(json.dumps(report), encoding='utf-8')

            with patch('tracen_replay.analysis_job.full_recording.main', side_effect=producer), patch('sys.stdout', stdout):
                result = main([str(source), '--output', str(output)])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(payload['status'], 'completed_with_stage_failures')
            self.assertEqual(payload['stage_failures'], [dict(stage='causal_accounting', error='ValueError: x')])

    def test_prune_frames_deletes_images_after_validation_and_reports_it(self):
        with workspace_temp() as root:
            source = root / 'run.mp4'
            source.write_bytes(b'source')
            output = root / 'output'
            stdout = io.StringIO()

            def producer():
                _write_report(output, source)
                (output / 'part-000' / 'frames').mkdir(parents=True, exist_ok=True)
                (output / 'part-000' / 'frames' / '000001.jpg').write_bytes(b'jpg')
                (output / 'neural').mkdir(exist_ok=True)
                (output / 'neural' / 'part-000-frame-000001.json').write_text('{}', encoding='utf-8')

            with patch('tracen_replay.analysis_job.full_recording.main', side_effect=producer), patch('sys.stdout', stdout):
                result = main([str(source), '--output', str(output), '--prune-frames'])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(payload['status'], 'succeeded')
            self.assertGreaterEqual(payload['pruned_frames']['files'], 1)
            self.assertFalse(list(output.rglob('*.jpg')) + list(output.rglob('*.png')))
            self.assertTrue((output / 'neural' / 'part-000-frame-000001.json').is_file())
            self.assertTrue((output / 'report.json').is_file())


if __name__ == '__main__':
    unittest.main()
