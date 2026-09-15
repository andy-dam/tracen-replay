import copy
import unittest
from unittest.mock import patch

from tracen_replay.receipt_recovery import caption_identity, plan, scoped_observations, recover


def row(time, text, *, confidence=99, screen='event_outcome'):
    return dict(source_timestamp_ms=time, screen=screen, evidence=f'{time}.png',
                source_sha256='s' * 64,
                source_frame_sha256=f'{time:064x}',
                source_frame_id=f'frame-{time}',
                engine_fingerprint='e' * 64,
                model_sha256={'model.onnx': 'm' * 64},
                ocr={'neural': [dict(text=text, box=[310, 870, 550, 900], confidence=confidence)]})


def event(start, end, effects=()):
    return dict(id=f'event-{start}-{end}', kind='outcome', first_seen_ms=start, last_seen_ms=end, effects=list(effects), conflicting_readings=[])


class ReceiptRecoveryTests(unittest.TestCase):
    def test_recovered_amount_has_readable_alternate_proof_in_canonical_event(self):
        from tracen_replay.inspect_receipts import merge
        from tracen_replay.transactions import outcome_events
        original=row(1000,'Speed went up by')
        original.update(effects=[],facts={},stats={},evidence='primary.png')
        fresh=row(1000,'Speed went up by 5.')
        fresh.update(effects=[dict(kind='stat_change',field='speed',amount=5)],facts={},stats={},evidence='alternate.png')
        windows=plan([original],[event(900,1100)],2000)
        merged=merge([original],scoped_observations([original],[fresh],windows))
        self.assertEqual(merged[0]['evidence'],'primary.png')
        result=outcome_events(merged)[0]
        self.assertEqual(result['deltas'],{'speed':5})
        self.assertEqual(result['field_evidence']['stat_change|speed|'],['primary.png','alternate.png'])

    def test_reparse_and_zero_budget_leave_requests_pending_without_ocr(self):
        from tests.test_gameplay import workspace_temp
        for allow_ocr, budget in ((False, 20), (True, 0)):
            with workspace_temp() as root, patch('tracen_replay.vision.NeuralReader', side_effect=AssertionError('Unexpected OCR')):
                original = [row(1000, 'Speed went up by')]
                merged, metadata = recover('unused.mp4', root, {'sha256': 'a'*64, 'duration_ms': 2000},
                                            original, [event(900, 1100)], allow_ocr=allow_ocr, max_windows=budget)
                self.assertEqual(merged, original)
                self.assertEqual(len(metadata['pending_windows']), 1)
                self.assertEqual(metadata['processed_windows'], [])

    def test_numeric_probe_preserves_other_observations_and_keeps_numeric_conflicts(self):
        from tracen_replay.inspect_receipts import merge
        original = row(1000, 'Skill Pts went up by')
        original.update(stats={'values': {'speed': 100}}, effects=[dict(kind='friendship_change', name='Visible Name', amount=5)],
                        facts={'some_original_fact': True})
        fresh = row(1000, 'Skill Pts went up by 4.')
        fresh.update(stats={'values': {'speed': 999}}, effects=[dict(kind='stat_change', field='skill_points', amount=4),
                     dict(kind='friendship_change', name='Damaged Name', amount=5)],
                     facts={'some_original_fact': False, 'effect_candidates': [dict(kind='stat_change', field='skill_points', amount=9),
                             dict(kind='friendship_change', name='Another Damaged Name', amount=5)]})
        before = copy.deepcopy((original, fresh))
        windows = plan([original], [event(900, 1100)], 2000)
        scoped = scoped_observations([original], [fresh], windows)
        merged = merge([original], scoped)[0]
        self.assertEqual(merged['stats'], original['stats'])
        self.assertTrue(merged['facts']['some_original_fact'])
        self.assertEqual(merged['ocr'], original['ocr'])
        self.assertEqual([e.get('name') for e in merged['effects'] if e['kind'] == 'friendship_change'], ['Visible Name'])
        self.assertEqual(merged['facts']['effect_candidates'], [dict(kind='stat_change', field='skill_points', amount=9)])
        self.assertEqual((original, fresh), before)

    def test_probe_does_not_promote_unrequested_fields_or_new_state_readings(self):
        fresh = row(1000, 'Power went up by 5.')
        fresh.update(effects=[dict(kind='stat_change', field='power', amount=5)], facts={}, stats={'values': {'speed': 999}})
        self.assertEqual(scoped_observations([], [fresh], plan([row(1000, 'Speed went up by')], [], 2000)), [])
        scoped = scoped_observations([], [fresh], plan([fresh], [event(900, 1100)], 2000))
        self.assertEqual(scoped[0]['stats'], {})

    def test_same_field_from_another_or_unowned_occurrence_is_not_promoted(self):
        trigger = row(1000, 'Speed went up by')
        fresh = row(800, 'Speed went up by 10.')
        fresh.update(effects=[dict(kind='stat_change', field='speed', amount=10)], facts={}, stats={})
        windows = plan([trigger], [event(900, 1100)], 2000)
        self.assertEqual(scoped_observations([], [fresh], windows), [])
        fresh['source_timestamp_ms'] = 1000
        self.assertEqual(scoped_observations([], [fresh], plan([trigger], [], 2000)), [])
        self.assertTrue(scoped_observations([], [fresh], windows))

    def test_missing_amount_requests_prior_frames_without_guessing_number(self):
        readings = [row(1000, 'Passion went up by')]
        expected = copy.deepcopy(readings)
        windows = plan(readings, [event(900, 1100)], 2000)
        self.assertEqual((windows[0]['start_ms'], windows[0]['end_ms']), (250, 1250))
        self.assertEqual(windows[0]['triggers'][0]['field'], 'passion')
        self.assertNotIn('amount', windows[0]['triggers'][0])
        self.assertEqual(readings, expected)

    def test_present_effect_suppresses_redundant_probe_but_other_event_does_not(self):
        readings = [row(1000, 'Speed went up by')]
        effect = dict(kind='stat_change', field='speed', amount=5)
        self.assertEqual(plan(readings, [event(900, 1100, [effect])], 2000), [])
        self.assertTrue(plan(readings, [event(0, 500, [effect]), event(900, 1100)], 2000))
        disputed = event(900, 1100, [effect])
        disputed['conflicting_readings'] = [{'field': 'stat_change|speed|'}]
        self.assertTrue(plan(readings, [disputed], 2000))

    def test_caps_bonuses_dialogue_and_previews_do_not_trigger(self):
        for text in ('Passion cap went up by', 'Speed Bonus went up by 2.',
                     'If Speed went up by 10...', 'Would Energy recovered by 20 help?'):
            self.assertEqual(plan([row(1000, text)], [], 2000), [])
        for screen in ('training_preview', 'lesson_confirmation', 'dialogue', 'unknown'):
            self.assertEqual(plan([row(1000, 'Speed went up by', screen=screen)], [], 2000), [])

    def test_obstructed_text_requests_inspection_without_becoming_an_effect(self):
        observation = row(1000, 'Energy went down by 0.', confidence=0)
        observation['ocr']['neural'][0].update(overlay_occluded=True, pre_occlusion_confidence=98)
        windows = plan([observation], [], 2000)
        self.assertEqual(windows[0]['triggers'][0]['kind'], 'energy_change')
        self.assertEqual(caption_identity(row(0, 'Speed went up by', confidence=80)['ocr']['neural'][0]), None)

    def test_close_requests_merge_and_long_obstruction_is_bounded(self):
        readings = [row(t, 'Skill Pts went up by') for t in range(100, 10101, 250)]
        windows = plan(readings, [], 11000)
        self.assertGreater(len(windows), 1)
        self.assertTrue(all(0 <= w['start_ms'] < w['end_ms'] <= 11000 and w['end_ms']-w['start_ms'] <= 5000 for w in windows))
        self.assertEqual(sum(len(w['triggers']) for w in windows), len(readings))


if __name__ == '__main__':
    unittest.main()
