import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tracen_replay.receipt_sampling import (
    ReceiptSamplingError,
    build_plan,
    create_plan,
    execute_plan,
)


SOURCE_SHA = 'a' * 64
REPORT_SHA = 'b' * 64
CAPTURE_SHA = 'c' * 64
INSPECTION_SHA = 'd' * 64


def report(*, duration=10_000, readings=None, candidates=None, events=None,
           intervals=None, performance_intervals=None):
    return dict(
        source=dict(sha256=SOURCE_SHA, duration_ms=duration),
        gameplay_tracking=dict(
            auxiliary_log_used=False,
            readings=readings or [],
            unparsed_receipt_candidates=candidates or [],
            events=events or [],
            intervals=intervals or [],
            performance_accounting=dict(intervals=performance_intervals or []),
        ),
    )


def capture(duration=10_000):
    return dict(source=dict(sha256=SOURCE_SHA, duration_ms=duration))


def point_reading(timestamp, *, screen='event_outcome', text='Energy went down by 8.'):
    return dict(source_timestamp_ms=timestamp, evidence=f'frame-{timestamp}.png', screen=screen,
                facts=dict(occluded_receipt_lines=[dict(text=text)]))


class ReceiptSamplingTests(unittest.TestCase):
    def symbol_event(self):
        return dict(kind='outcome', first_seen_ms=2000, last_seen_ms=2500,
                    evidence='receipt.png', effects=[
                        dict(kind='skill_hint_change', name='Test Skill ○', amount=3,
                             original_text='Gained hint for Test Skill O.',
                             visual_symbol_observation=dict(method='strict_terminal_ring_geometry')),
                        dict(kind='skill_hint_change', name='Test Skill O', amount=3,
                             raw_text='Gained hint for Test Skill O.')])

    def test_unresolved_symbol_pair_requests_pixels_without_supplying_labels(self):
        data = report(events=[self.symbol_event()])
        original = copy.deepcopy(data)
        plan = self.make_plan(data)
        self.assertEqual(len(plan['windows']), 1)
        self.assertEqual([t['kind'] for t in plan['targets']], ['unresolved_hint_symbol'])
        self.assertEqual(plan['targets'][0]['anchor_start_ms'], 2000)
        self.assertNotIn('Test Skill', json.dumps(plan))
        self.assertNotIn('amount', json.dumps(plan))
        self.assertEqual(data, original)

    def test_distinct_or_malformed_hints_do_not_trigger_symbol_recovery(self):
        mutations = [
            (0, 'visual_symbol_observation', None),
            (0, 'visual_symbol_observation', 'unverified'),
            (0, 'visual_symbol_observation', {'method': 'guessed'}),
            (0, 'original_text', None),
            (1, 'name', 'Test Skill ○'),
            (1, 'name', None),
            (1, 'raw_text', 'Different receipt.'),
            (1, 'amount', 2),
            (1, 'amount', None),
            (1, 'amount', True),
        ]
        for index, key, value in mutations:
            with self.subTest(index=index, key=key, value=value):
                event = self.symbol_event()
                event['effects'][index][key] = value
                self.assertEqual(self.make_plan(report(events=[event]))['targets'], [])

    def test_symbol_recovery_respects_optional_panels_and_prior_inspection(self):
        for kind in ('training', 'career_summary', 'concert_info'):
            event = self.symbol_event()
            event['kind'] = kind
            plan = self.make_plan(report(events=[event]))
            self.assertEqual(plan['targets'], [])
            self.assertEqual(plan['windows'], [])
        covered = dict(source_sha256=SOURCE_SHA,
                       windows=[dict(start_ms=1500, end_ms=3000, fps=30)])
        plan = self.make_plan(report(events=[self.symbol_event()]),
                              existing_inspection=covered,
                              existing_inspection_sha256=INSPECTION_SHA)
        self.assertEqual(plan['windows'], [])
        self.assertEqual(plan['deferred'][0]['reason'], 'already_inspected_at_sufficient_fps')

    def make_plan(self, data, **kwargs):
        return build_plan(data, capture(data['source']['duration_ms']),
                          source_sha256=SOURCE_SHA, report_sha256=REPORT_SHA,
                          capture_sha256=CAPTURE_SHA, **kwargs)

    def test_empty_and_optional_panel_observations_do_not_create_receipt_work(self):
        empty = self.make_plan(report())
        self.assertEqual(empty['targets'], [])
        self.assertEqual(empty['windows'], [])
        self.assertEqual(empty['deferred'], [])

        optional = report(readings=[point_reading(100, screen='concert_info')],
                          candidates=[dict(first_seen_ms=200, last_seen_ms=200,
                                           screen='owned_skill_inventory', evidence=['panel.png'])])
        plan = self.make_plan(optional)
        self.assertEqual(plan['targets'], [])
        self.assertEqual(plan['windows'], [])
        self.assertEqual(plan['summary']['skipped_optional_targets'], 2)
        self.assertEqual({row['reason'] for row in plan['deferred']},
                         {'optional_panel_not_receipt_target'})

    def test_boundaries_merge_nearby_targets_and_split_long_windows(self):
        data = report(
            readings=[point_reading(10), point_reading(80)],
            candidates=[dict(first_seen_ms=2_000, last_seen_ms=7_000, evidence=['candidate.png'])],
        )
        plan = self.make_plan(data, pad_ms=100, merge_gap_ms=100, max_window_ms=1_000,
                              max_footage_ms=20_000, max_frames=10_000)
        self.assertTrue(plan['windows'])
        self.assertTrue(all(0 <= w['start_ms'] < w['end_ms'] <= 10_000 for w in plan['windows']))
        self.assertTrue(all(w['end_ms'] - w['start_ms'] <= 1_000 for w in plan['windows']))
        merged_first = [w for w in plan['windows'] if 'target-0001' in w['target_ids']]
        self.assertEqual(len(merged_first), 1)
        self.assertEqual(set(merged_first[0]['target_ids']), {'target-0001', 'target-0002'})
        self.assertEqual(merged_first[0]['start_ms'], 0)
        self.assertGreaterEqual(len(plan['windows']), 6)

    def test_padded_windows_merge_before_budget_selection(self):
        data = report(candidates=[
            dict(first_seen_ms=1_000, last_seen_ms=1_000, evidence=['first.png']),
            dict(first_seen_ms=1_502, last_seen_ms=1_502, evidence=['second.png']),
        ])
        plan = self.make_plan(data, pad_ms=500, merge_gap_ms=250,
                              max_footage_ms=10_000, max_frames=10_000)
        self.assertEqual(len(plan['windows']), 1)
        self.assertEqual(plan['windows'][0]['start_ms'], 500)
        self.assertEqual(plan['windows'][0]['end_ms'], 2_003)
        self.assertEqual(set(plan['windows'][0]['target_ids']),
                         {'target-0001', 'target-0002'})

    def test_unresolved_stat_overlap_is_prioritized_before_budget(self):
        data = report(
            candidates=[
                dict(first_seen_ms=1_000, last_seen_ms=1_000, evidence=['early.png']),
                dict(first_seen_ms=8_000, last_seen_ms=8_000, evidence=['late.png']),
            ],
            intervals=[dict(start_ms=7_800, end_ms=8_200, status='unresolved',
                            unexplained_change={'speed': 9999})],
        )
        plan = self.make_plan(data, pad_ms=100, max_footage_ms=250, max_frames=100)
        self.assertEqual(len(plan['windows']), 1)
        selected = plan['windows'][0]
        selected_target = next(t for t in plan['targets'] if t['id'] in selected['target_ids'])
        self.assertEqual(selected_target['anchor_start_ms'], 8_000)
        self.assertTrue(selected_target['priority']['overlaps_unresolved_interval'])
        self.assertTrue(any(row['reason'] == 'footage_budget_exhausted' for row in plan['deferred']))
        self.assertNotIn('9999', json.dumps(plan))

    def test_sufficient_existing_coverage_is_skipped_but_low_fps_is_not(self):
        data = report(candidates=[dict(first_seen_ms=500, last_seen_ms=500, evidence=['receipt.png'])])
        covered = dict(source_sha256=SOURCE_SHA,
                       windows=[dict(start_ms=0, end_ms=1_000, fps=16)])
        plan = self.make_plan(data, existing_inspection=covered,
                              existing_inspection_sha256=INSPECTION_SHA, pad_ms=100)
        self.assertEqual(plan['windows'], [])
        self.assertEqual(plan['deferred'][0]['reason'], 'already_inspected_at_sufficient_fps')

        low_fps = dict(source_sha256=SOURCE_SHA,
                       windows=[dict(start_ms=0, end_ms=1_000, fps=8)])
        plan = self.make_plan(data, existing_inspection=low_fps,
                              existing_inspection_sha256=INSPECTION_SHA, pad_ms=100)
        self.assertEqual(len(plan['windows']), 1)
        self.assertEqual(plan['deferred'], [])

    def test_requested_sampling_must_meet_its_own_reuse_threshold(self):
        with self.assertRaisesRegex(ReceiptSamplingError, 'fps must meet min_inspection_fps'):
            self.make_plan(report(), fps=8, min_inspection_fps=16)

    def test_conflict_target_keeps_uncertainty_without_expected_labels(self):
        data = report(
            events=[dict(id='event-1', kind='outcome', first_seen_ms=2_000, last_seen_ms=2_100,
                         evidence=['conflict.png'],
                         conflicting_readings=[dict(field='energy_change', values=[-8, -18])])],
        )
        plan = self.make_plan(data, pad_ms=200)
        self.assertEqual(len(plan['targets']), 1)
        self.assertEqual(plan['targets'][0]['kind'], 'conflicting_receipt_readings')
        # The plan's own creation date is not a leaked value: on the 18th of
        # a month it would otherwise spell one.
        written = json.dumps({key: value for key, value in plan.items() if key != 'created_at_utc'})
        self.assertNotIn('energy_change', written)
        self.assertNotIn('-18', written)
        self.assertEqual(plan['targets'][0]['reason'],
                         'source event retained conflicting receipt readings')

    def test_training_conflicts_are_deferred_from_receipt_sampling(self):
        data = report(events=[dict(id='training-1', kind='training', first_seen_ms=2_000,
                                   last_seen_ms=2_100, evidence=['training.png'],
                                   conflicting_readings=[dict(field='speed', values=[1, 2])])])
        plan = self.make_plan(data)
        self.assertEqual(plan['targets'], [])
        self.assertEqual(plan['windows'], [])
        self.assertEqual(plan['deferred'][0]['reason'], 'non_receipt_event_conflict')

    def test_auxiliary_log_reports_are_rejected(self):
        data = report()
        data['gameplay_tracking']['auxiliary_log_used'] = True
        with self.assertRaisesRegex(ReceiptSamplingError, 'auxiliary_log_used must be false'):
            self.make_plan(data)

    def test_create_and_execute_are_source_bound_and_leave_report_unchanged(self):
        with workspace_temp() as root:
            source = root / 'recording.mp4'
            source.write_bytes(b'video bytes')
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            capture_path = root / 'capture.json'
            capture_path.write_text(json.dumps({'source': {'sha256': source_sha, 'duration_ms': 2_000}}),
                                     encoding='utf-8')
            report_path = root / 'report.json'
            report_data = dict(source={'sha256': source_sha, 'duration_ms': 2_000},
                               gameplay_tracking=dict(
                                   auxiliary_log_used=False, readings=[],
                                   unparsed_receipt_candidates=[dict(first_seen_ms=1_000,
                                                                      last_seen_ms=1_000,
                                                                      evidence=['receipt.png'])],
                                   events=[], intervals=[], performance_accounting={'intervals': []}))
            report_path.write_text(json.dumps(report_data), encoding='utf-8')
            before = report_path.read_bytes()
            plan_path = root / 'receipt-sampling-plan.json'
            created, plan = create_plan(source, report_path, output=plan_path, pad_ms=100)
            self.assertEqual(created, plan_path.resolve())
            def fake_inspect(source_arg, root_arg, start, end, fps):
                inspection_path = root_arg / 'receipt-inspection.json'
                if inspection_path.exists():
                    inspection = json.loads(inspection_path.read_text(encoding='utf-8'))
                else:
                    inspection = dict(source_sha256=source_sha, windows=[], readings=[])
                inspection['windows'].append(dict(start_ms=start, end_ms=end, fps=fps,
                                                  reason='unparsed_receipt_review'))
                inspection_path.write_text(json.dumps(inspection), encoding='utf-8')

            with patch('tracen_replay.inspect_receipts.inspect', side_effect=fake_inspect) as inspect:
                result = execute_plan(plan_path)
            inspect.assert_called_once_with(source.resolve(), root.resolve(), 900, 1_101, 16)
            self.assertEqual(result['selected_window_count'], 1)
            self.assertTrue(result['report_unchanged'])
            self.assertEqual(report_path.read_bytes(), before)
            self.assertEqual(json.loads((root / 'receipt-inspection.json').read_text())['windows'],
                             [dict(start_ms=900, end_ms=1_101, fps=16,
                                   reason='unparsed_receipt_review')])

    def test_execution_recomputes_window_costs_against_budget(self):
        with workspace_temp() as root:
            source = root / 'recording.mp4'
            source.write_bytes(b'video bytes')
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / 'capture.json').write_text(
                json.dumps({'source': {'sha256': source_sha, 'duration_ms': 2_000}}), encoding='utf-8')
            report_path = root / 'report.json'
            data = dict(source={'sha256': source_sha, 'duration_ms': 2_000},
                        gameplay_tracking=dict(auxiliary_log_used=False, readings=[],
                                               unparsed_receipt_candidates=[dict(first_seen_ms=1_500,
                                                                                  last_seen_ms=1_500,
                                                                                  evidence=['receipt.png'])],
                                               events=[], intervals=[], performance_accounting={'intervals': []}))
            report_path.write_text(json.dumps(data), encoding='utf-8')
            plan_path, _ = create_plan(source, report_path, output=root / 'plan.json',
                                       pad_ms=100, max_footage_ms=201)
            tampered = json.loads(plan_path.read_text(encoding='utf-8'))
            tampered['windows'][0]['start_ms'] = 900
            tampered['windows'][0]['end_ms'] = 1_900
            tampered['windows'][0]['estimated_footage_ms'] = 1_000
            tampered['windows'][0]['estimated_frames'] = 17
            tampered['summary']['selected_footage_ms'] = 1_000
            tampered['summary']['estimated_frames'] = 17
            tampered_path = root / 'tampered-plan.json'
            tampered_path.write_text(json.dumps(tampered), encoding='utf-8')
            with self.assertRaisesRegex(ReceiptSamplingError, 'exceed the declared footage budget'):
                execute_plan(tampered_path)

            frame_plan_path, _ = create_plan(source, report_path, output=root / 'frame-plan.json',
                                             pad_ms=100, max_footage_ms=5_000, max_frames=5)
            frame_tampered = json.loads(frame_plan_path.read_text(encoding='utf-8'))
            frame_tampered['windows'][0]['start_ms'] = 900
            frame_tampered['windows'][0]['end_ms'] = 1_400
            frame_tampered['windows'][0]['estimated_footage_ms'] = 500
            frame_tampered['windows'][0]['estimated_frames'] = 9
            frame_tampered['summary']['selected_footage_ms'] = 500
            frame_tampered['summary']['estimated_frames'] = 9
            frame_tampered_path = root / 'frame-tampered-plan.json'
            frame_tampered_path.write_text(json.dumps(frame_tampered), encoding='utf-8')
            with self.assertRaisesRegex(ReceiptSamplingError, 'exceed the declared frame budget'):
                execute_plan(frame_tampered_path)

    def test_candidate_report_can_bind_to_explicit_evidence_root(self):
        with workspace_temp() as root:
            source = root / 'recording.mp4'
            source.write_bytes(b'video bytes')
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / 'capture.json').write_text(
                json.dumps({'source': {'sha256': source_sha, 'duration_ms': 2_000}}), encoding='utf-8')
            candidate_dir = root / 'candidate'
            candidate_dir.mkdir()
            report_path = candidate_dir / 'candidate-report.json'
            data = dict(source={'sha256': source_sha, 'duration_ms': 2_000},
                        gameplay_tracking=dict(auxiliary_log_used=False, readings=[],
                                               unparsed_receipt_candidates=[dict(first_seen_ms=1_000,
                                                                                  last_seen_ms=1_000,
                                                                                  evidence=['receipt.png'])],
                                               events=[], intervals=[], performance_accounting={'intervals': []}))
            report_path.write_text(json.dumps(data), encoding='utf-8')
            plan_path, plan = create_plan(source, report_path, output=candidate_dir / 'plan.json',
                                          pad_ms=100, run_root=root)
            self.assertEqual(Path(plan['paths']['run_root']), root.resolve())
            self.assertEqual(Path(plan['paths']['capture']), (root / 'capture.json').resolve())
            self.assertEqual(Path(plan['paths']['report']), report_path.resolve())

            def fake_inspect(source_arg, root_arg, start, end, fps):
                inspection_path = root_arg / 'receipt-inspection.json'
                inspection_path.write_text(json.dumps(dict(
                    source_sha256=source_sha, windows=[dict(start_ms=start, end_ms=end, fps=fps)],
                    readings=[])), encoding='utf-8')

            with patch('tracen_replay.inspect_receipts.inspect', side_effect=fake_inspect) as inspect:
                execute_plan(plan_path)
            inspect.assert_called_once_with(source.resolve(), root.resolve(), 900, 1_101, 16)

    def test_execution_rejects_stale_report_and_existing_inspection(self):
        with workspace_temp() as root:
            source = root / 'recording.mp4'
            source.write_bytes(b'video bytes')
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / 'capture.json').write_text(
                json.dumps({'source': {'sha256': source_sha, 'duration_ms': 2_000}}), encoding='utf-8')
            report_path = root / 'report.json'
            report_data = dict(source={'sha256': source_sha, 'duration_ms': 2_000},
                               gameplay_tracking=dict(auxiliary_log_used=False, readings=[],
                                                      unparsed_receipt_candidates=[], events=[],
                                                      intervals=[], performance_accounting={'intervals': []}))
            report_path.write_text(json.dumps(report_data), encoding='utf-8')
            plan_path, _ = create_plan(source, report_path, output=root / 'plan.json')
            report_path.write_text(json.dumps(dict(report_data, changed=True)), encoding='utf-8')
            with self.assertRaisesRegex(ReceiptSamplingError, 'report identity is stale'):
                execute_plan(plan_path)

            # A newly created plan binds the absence of receipt-inspection.json;
            # creating it afterward must make execution fail closed as stale.
            report_path.write_text(json.dumps(report_data), encoding='utf-8')
            plan_path, _ = create_plan(source, report_path, output=root / 'plan-2.json')
            (root / 'receipt-inspection.json').write_text(
                json.dumps({'source_sha256': source_sha, 'windows': []}), encoding='utf-8')
            with self.assertRaisesRegex(ReceiptSamplingError, 'inspection identity changed'):
                execute_plan(plan_path)

    def test_plan_output_refuses_overwrite_and_invalid_capture_identity(self):
        with workspace_temp() as root:
            source = root / 'recording.mp4'
            source.write_bytes(b'video bytes')
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            (root / 'capture.json').write_text(
                json.dumps({'source': {'sha256': source_sha, 'duration_ms': 1_000}}), encoding='utf-8')
            report_path = root / 'report.json'
            data = dict(source={'sha256': source_sha, 'duration_ms': 1_000},
                        gameplay_tracking=dict(auxiliary_log_used=False, readings=[],
                                               unparsed_receipt_candidates=[], events=[], intervals=[],
                                               performance_accounting={'intervals': []}))
            report_path.write_text(json.dumps(data), encoding='utf-8')
            output = root / 'plan.json'
            create_plan(source, report_path, output=output)
            with self.assertRaisesRegex(ReceiptSamplingError, 'refusing to overwrite'):
                create_plan(source, report_path, output=output)

            (root / 'capture.json').write_text(
                json.dumps({'source': {'sha256': 'e' * 64, 'duration_ms': 1_000}}), encoding='utf-8')
            with self.assertRaisesRegex(ReceiptSamplingError, 'identities do not match'):
                create_plan(source, report_path, output=root / 'plan-2.json')


if __name__ == '__main__':
    unittest.main()
