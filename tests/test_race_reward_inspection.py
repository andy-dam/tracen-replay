import copy
import hashlib
import json
import shutil
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from PIL import Image

from tracen_replay.race_reward_inspection import load, register, merge_reward_rows, inspect, refine_base_rows
from tracen_replay.transactions import races
from tests.test_gameplay import workspace_temp


def quantity(value, box=(310, 855, 380, 887)):
    return dict(quantity=value, name=None, box=list(box))


def row(time, items=(), fans=100):
    return dict(screen='race_result', source_timestamp_ms=time, evidence=f'{time}.png',
                facts=dict(fans=fans, fans_gained=10, visible_item_quantities=list(items)),
                ocr={'neural': []})


def source_reward_row(time, quantities, *, fans, fans_gained, evidence, include_headers=True):
    """Build a source-shaped payout row with no item-name inference."""
    boxes = [
        (318, 695, 386, 734),
        (425, 699, 490, 727),
        (533, 695, 614, 734),
        (308, 855, 388, 897),
        (418, 855, 503, 897),
    ]
    item_count = 3 if len(quantities) >= 5 else max(0, len(quantities) - 1)
    lines = [dict(text=f'Fans {fans:,} (+{fans_gained:,})', confidence=99,
                  box=[420, 539, 630, 566])]
    if include_headers:
        lines.extend([
            dict(text='Items', confidence=99, box=[281, 591, 328, 612]),
        ])
    for index, value in enumerate(quantities):
        box_index = index if index < item_count else 3 + index - item_count
        lines.append(dict(text=f'x{value}', confidence=99, box=list(boxes[box_index])))
        if index == item_count and include_headers:
            lines.append(dict(text='Bonus', confidence=99, box=[271, 750, 335, 779]))
    if include_headers and len(quantities) <= 2:
        lines.append(dict(text='Bonus', confidence=99, box=[271, 750, 335, 779]))
    # The source payout screen can contain repeated values in separate rows;
    # keep their boxes distinct so the parser does not collapse them.
    return dict(
        screen='race_result', source_timestamp_ms=time, evidence=evidence,
        facts=dict(fans=fans, fans_gained=fans_gained,
                   visible_item_quantities=[
                       quantity(value, boxes[index if index < item_count else 3 + index - item_count])
                       for index, value in enumerate(quantities)
                   ]),
        ocr={'neural': lines},
    )


class RaceRewardMergeTests(unittest.TestCase):
    def test_source_positive_w1_aoi_adds_all_directly_visible_quantities(self):
        base = [
            row(921500, [quantity(800, (425, 699, 490, 727))], fans=34034),
            row(921750, [quantity(800, (425, 699, 490, 727))], fans=34034),
        ]
        for base_row in base:
            base_row['facts']['fans_gained'] = 6162
        extra = [
            source_reward_row(921500, [2, 800, 40], fans=34034, fans_gained=6162,
                               evidence='source-w1-921500.png'),
            source_reward_row(921750, [2, 800, 40], fans=34034, fans_gained=6162,
                               evidence='source-w1-921750.png'),
        ]

        refined = refine_base_rows(base, extra)
        result = races(refined)[0]['visible_item_reward_snapshots']
        self.assertEqual([item['quantity'] for item in result[0]['items']], [2, 800, 40])
        self.assertEqual([item['section'] for item in result[0]['items']], ['items', 'items', 'bonus'])
        self.assertTrue(all(item['name'] is None for item in result[0]['items']))
        self.assertFalse(result[0]['list_complete'])

    def test_source_positive_w1_nonaligned_inspection_frames_form_one_snapshot(self):
        base = [
            row(921250, [quantity(800, (425, 699, 490, 727))], fans=34034),
            row(921500, [quantity(800, (425, 699, 490, 727))], fans=34034),
            row(921750, [quantity(800, (425, 699, 490, 727))], fans=34034),
        ]
        for base_row in base:
            base_row['facts']['fans_gained'] = 6162
        inspection = [
            source_reward_row(921333, [2, 800, 40], fans=34034, fans_gained=6162,
                               evidence='source-w1-921333.png'),
            source_reward_row(921416, [2, 800, 40], fans=34034, fans_gained=6162,
                               evidence='source-w1-921416.png'),
        ]

        snapshot = races(base, inspection)[0]['visible_item_reward_snapshots']
        self.assertEqual([item['quantity'] for item in snapshot[0]['items']], [2, 800, 40])
        self.assertEqual(snapshot[0]['evidence'],
                         ['source-w1-921333.png', 'source-w1-921416.png'])

    def test_source_positive_w2_mile_keeps_repeated_quantities_in_both_sections(self):
        base = [
            row(1239500, [quantity(800, (425, 699, 489, 727)),
                          quantity(800, (312, 863, 378, 890))], fans=75401),
            row(1239750, [quantity(800, (425, 699, 489, 727)),
                          quantity(800, (312, 863, 378, 890))], fans=75401),
        ]
        for base_row in base:
            base_row['facts']['fans_gained'] = 18681
        extra = [
            source_reward_row(1239500, [2, 800, 2, 800, 40], fans=75401, fans_gained=18681,
                               evidence='source-w2-1239500.png'),
            source_reward_row(1239750, [2, 800, 2, 800, 40], fans=75401, fans_gained=18681,
                               evidence='source-w2-1239750.png'),
        ]

        refined = refine_base_rows(base, extra)
        snapshot = races(refined)[0]['visible_item_reward_snapshots'][0]
        self.assertEqual([item['quantity'] for item in snapshot['items']], [2, 800, 2, 800, 40])
        self.assertEqual([item['section'] for item in snapshot['items']],
                         ['items', 'items', 'items', 'bonus', 'bonus'])
        self.assertFalse(snapshot['identity_verified'])
        self.assertFalse(snapshot['list_complete'])

    def test_same_frame_recovery_imports_only_missing_reward_headers(self):
        base = row(921500, [quantity(800, (425, 699, 490, 727))], fans=34034)
        base['facts']['fans_gained'] = 6162
        base['ocr'] = {'neural': [dict(text='unrelated', confidence=99, box=[10, 10, 20, 20])]}
        extra = source_reward_row(921500, [2, 800, 40], fans=34034, fans_gained=6162,
                                  evidence='source-w1-header-proof.png')

        merged = refine_base_rows([base], [extra])[0]
        headers = [line['text'] for line in merged['ocr']['neural']]
        self.assertEqual(headers, ['unrelated', 'Items', 'Bonus'])

    def test_source_conflict_abstains_instead_of_selecting_one_payout_value(self):
        base = source_reward_row(1239500, [2, 800, 2, 800, 40], fans=75401, fans_gained=18681,
                                 evidence='source-w2-base.png')
        conflict = source_reward_row(1239500, [2, 700, 2, 800, 40], fans=75401, fans_gained=18681,
                                     evidence='source-w2-conflict.png')

        merged = merge_reward_rows([base], [conflict])[0]
        self.assertEqual(merged['facts']['visible_item_quantities'], [])
        self.assertTrue(merged['quantity_inspection_conflict'])

    def test_out_of_frame_quantity_proof_is_rejected(self):
        base = row(921500, [quantity(800, (425, 699, 490, 727))], fans=34034)
        base['facts']['fans_gained'] = 6162
        malformed = source_reward_row(921500, [2, 800, 40], fans=34034, fans_gained=6162,
                                      evidence='source-w1-out-of-frame.png')
        malformed['facts']['visible_item_quantities'][0]['box'] = [-1, 695, 10, 734]

        merged = merge_reward_rows([base], [malformed])[0]
        self.assertEqual(merged['facts']['visible_item_quantities'], [])
        self.assertTrue(merged['quantity_inspection_conflict'])

    def test_preview_and_other_race_cannot_supply_reward_quantities(self):
        base = row(921500, [quantity(800, (425, 699, 490, 727))], fans=34034)
        base['facts']['fans_gained'] = 6162
        preview = source_reward_row(921500, [2, 800, 40], fans=34034, fans_gained=6162,
                                    evidence='source-w1-preview.png')
        preview['screen'] = 'race_preview'
        self.assertEqual(refine_base_rows([base], [preview]), [base])

        other_race = source_reward_row(921500, [2, 800, 40], fans=99999, fans_gained=1,
                                       evidence='source-other-race.png')
        self.assertEqual(refine_base_rows([base], [other_race]), [base])

        merged = merge_reward_rows([base], [preview])
        self.assertEqual([item['quantity'] for item in merged[0]['facts']['visible_item_quantities']], [800])

    def test_dense_samples_confirm_rewards_without_new_races_or_action_times(self):
        base = [row(0), row(250), row(2000, fans=200), row(2250, fans=200)]
        extra = [row(100, [quantity(1)]), row(200, [quantity(1)]), row(4000, [quantity(9)])]
        before = copy.deepcopy(base)
        result = races(base, extra)
        self.assertEqual([(r['first_seen_ms'], r['last_seen_ms']) for r in result], [(0, 250), (2000, 2250)])
        self.assertEqual(result[0]['visible_item_reward_snapshots'][0]['items'][0]['quantity'], 1)
        self.assertEqual(result[1]['visible_item_reward_snapshots'], [])
        self.assertEqual(base, before)

    def test_duplicate_pts_and_missing_frames_do_not_create_confirmation(self):
        base = [row(0), row(250)]
        sample = row(100, [quantity(1)])
        self.assertEqual(races(base, [sample, copy.deepcopy(sample)])[0]['visible_item_reward_snapshots'], [])
        other = row(200, [quantity(1)])
        for middle in [dict(row(150), screen='unknown'), row(150, [quantity(1)], fans=999)]:
            self.assertEqual(races(base, [sample, middle, other])[0]['visible_item_reward_snapshots'], [])

    def test_same_frame_partial_readings_merge_once_and_keep_proofs(self):
        old = row(250, [quantity(600, (425, 862, 490, 890))])
        extra = row(250, [quantity(1), quantity(600, (425, 862, 490, 890))])
        extra['evidence'] = 'native.png'
        result = merge_reward_rows([old], [extra, extra])
        self.assertEqual(len(result), 1)
        self.assertEqual([x['quantity'] for x in result[0]['facts']['visible_item_quantities']], [1, 600])
        self.assertEqual(result[0]['evidence'], '250.png')
        self.assertIn('native.png', result[0]['quantity_inspection_evidence'])
        shifted = row(250, [quantity(1, (301, 850, 389, 892))])
        self.assertEqual(len(merge_reward_rows([row(250, [quantity(1)])], [shifted])[0]['facts']['visible_item_quantities']), 1)

    def test_same_frame_recovery_keeps_repeated_quantities_in_distinct_sections(self):
        """A recovered leading badge must not collapse either repeated 400."""
        items_box = (425, 699, 489, 727)
        bonus_box = (310, 862, 376, 890)
        base = row(250, [quantity(400, items_box), quantity(400, bonus_box)])
        base['ocr']['neural'] = [
            dict(text='Items', confidence=99, box=[278, 591, 328, 612]),
            dict(text='Bonus', confidence=99, box=[270, 750, 334, 779]),
        ]
        recovered = row(250, [
            quantity(1, (318, 695, 386, 734)),
            quantity(400, items_box),
            quantity(400, bonus_box),
        ])
        recovered['evidence'] = 'bounded.png'
        recovered['ocr'] = copy.deepcopy(base['ocr'])

        merged = merge_reward_rows([base], [recovered])[0]
        self.assertEqual(
            [entry['quantity'] for entry in merged['facts']['visible_item_quantities']],
            [1, 400, 400],
        )
        from tracen_replay.race_reward_sections import annotate
        self.assertEqual(
            [entry['section'] for entry in annotate(merged)],
            ['items', 'items', 'bonus'],
        )

    def test_same_frame_conflict_abstains_and_cannot_be_restored_by_repetition(self):
        base = row(250, [quantity(1)])
        conflict = row(250, [quantity(2)])
        result = merge_reward_rows([base], [conflict, base, base])
        self.assertEqual(result[0]['facts']['visible_item_quantities'], [])
        self.assertTrue(result[0]['quantity_inspection_conflict'])

    def test_base_refinement_preserves_other_mechanics_and_original_quantity_proof(self):
        base = row(250, [quantity(600, (425, 862, 490, 890))])
        base.update(stats={'values': {'speed': 100}}, effects=[{'kind': 'example'}])
        before = copy.deepcopy(base)
        extra = row(250, [quantity(1), quantity(600, (425, 862, 490, 890))])
        extra['evidence'] = 'native.png'
        result = refine_base_rows([base], [extra, row(333, [quantity(9)])])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['stats'], base['stats'])
        self.assertEqual(result[0]['effects'], base['effects'])
        self.assertEqual(result[0]['facts']['base_visible_item_quantities'], base['facts']['visible_item_quantities'])
        self.assertEqual([x['quantity'] for x in result[0]['facts']['visible_item_quantities']], [1, 600])
        self.assertEqual(base, before)
        self.assertEqual(refine_base_rows([base], [row(250, [quantity(9)], fans=999)]), [base])


class RaceRewardEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(workspace_temp()))
        self.source = dict(sha256='a' * 64, duration_ms=5000, timeline_origin_seconds=0,
                           width=1920, height=1080)
        self.write(self.root / 'capture.json', {'source': self.source, 'frames': []})
        self.window = self.root / 'window'
        for folder in ['frames', 'gameplay', 'neural']:
            (self.window / folder).mkdir(parents=True)
        frame = Image.new('RGB', (1920, 1080), 'white')
        frame.save(self.window / 'frames/f.png')
        pane = frame.crop((148, 0, 958, 1080))
        pane.save(self.window / 'gameplay/f.png')
        self.raw = dict(lines=[{'text': 'Fans 100 (+10)', 'box': [270, 700, 600, 730], 'confidence': 99}],
                        regions={}, header='', result_grid=False, current_grid=False,
                        source_timestamp_ms=1000, evidence='gameplay/f.png',
                        source_frame_sha256=self.digest(self.window / 'frames/f.png'),
                        gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest(),
                        engine_fingerprint='fixture', model_sha256={'fixture': 'b' * 64})
        self.write(self.window / 'neural/f.json', self.raw)
        self.capture = dict(source=self.source, scope={'start_ms': 1000, 'end_ms': 1250},
                            frames=[dict(id='f', source_timestamp_ms=1000, source_pts=1000,
                                         time_base='1/1000', evidence='frames/f.png')])
        self.write(self.window / 'capture.json', self.capture)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_source_pixels_and_pts_checked_and_only_race_facts_returned(self):
        register(self.root, 'window')
        metadata, rows = load(self.root, self.source['sha256'])
        self.assertEqual(metadata['verified_frames'], 1)
        self.assertEqual(rows[0]['evidence'], 'window/gameplay/f.png')
        self.assertEqual(set(rows[0]['facts']), {'fans', 'fans_gained', 'visible_item_quantities'})
        self.assertNotIn('stats', rows[0])
        self.assertNotIn('effects', rows[0])

    def test_registered_capture_changes_and_source_mismatch_fail_closed(self):
        register(self.root, 'window')
        with self.assertRaises(ValueError):
            load(self.root, 'c' * 64)
        self.capture['scope']['end_ms'] = 1300
        self.write(self.window / 'capture.json', self.capture)
        with self.assertRaisesRegex(ValueError, 'capture changed'):
            load(self.root, self.source['sha256'])

    def test_modified_crop_or_ocr_pixels_rejected(self):
        register(self.root, 'window')
        original = copy.deepcopy(self.raw)
        self.raw['lines'][0]['text'] = 'Fans 999 (+99)'
        self.write(self.window / 'neural/f.json', self.raw)
        with self.assertRaisesRegex(ValueError, 'original OCR JSON changed'):
            load(self.root, self.source['sha256'])
        self.write(self.window / 'neural/f.json', original)
        Image.new('RGB', (810, 1080), 'black').save(self.window / 'gameplay/f.png')
        with self.assertRaisesRegex(ValueError, 'proof differs'):
            load(self.root, self.source['sha256'])

    def test_invalid_pts_duplicate_ids_and_path_escape_are_rejected(self):
        for problem in ('pts', 'duplicate', 'escape', 'source', 'unbounded', 'identity'):
            with self.subTest(problem=problem):
                capture = copy.deepcopy(self.capture)
                if problem == 'pts': capture['frames'][0]['source_pts'] = 999
                if problem == 'duplicate': capture['frames'].append(copy.deepcopy(capture['frames'][0]))
                if problem == 'escape': capture['frames'][0]['evidence'] = '../capture.json'
                if problem == 'source': capture['source']['sha256'] = 'c' * 64
                if problem == 'unbounded': capture['scope']['end_ms'] = 6001
                if problem == 'identity': capture['frames'][0]['id'] = '../sibling'
                self.write(self.window / 'capture.json', capture)
                with self.assertRaises(ValueError): register(self.root, 'window')
        with self.assertRaises(ValueError): register(self.root, '../escape')

    def test_malformed_provenance_is_a_failed_audit_instead_of_a_crash(self):
        from tracen_replay.verify_evidence import verify
        source_path = self.root / 'source.bin'
        source_path.write_bytes(b'fixture recording')
        self.source['sha256'] = self.digest(source_path)
        self.write(self.root / 'capture.json', {'source': self.source, 'frames': []})
        self.write(self.window / 'capture.json', self.capture)
        register(self.root, 'window')
        self.capture['frames'][0]['time_base'] = []
        self.write(self.window / 'capture.json', self.capture)
        path = self.root / 'race-reward-inspection.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['windows'][0]['capture_sha256'] = self.digest(self.window / 'capture.json')
        self.write(path, manifest)
        with patch('builtins.print'):
            result = verify(self.root, source_path)
        self.assertFalse(result['evidence_integrity_verified'])
        self.assertEqual(result['errors'][0]['reason'], 'Malformed race inspection provenance.')

    def test_bounded_capture_registers_and_reuses_valid_ocr_cache(self):
        source_path = self.root / 'source.bin'
        source_path.write_bytes(b'fixture recording')
        self.source['sha256'] = self.digest(source_path)
        self.write(self.root / 'capture.json', {'source': self.source, 'frames': []})
        def decode(source, directory, *args):
            shutil.copyfile(self.window / 'frames/f.png', directory / 'f.png')
            return copy.deepcopy(self.capture['frames'])
        reader = Mock()
        reader.read.return_value = copy.deepcopy(self.raw)
        with patch('tracen_replay.pipeline.decode_frames', side_effect=decode) as decoder, \
             patch('tracen_replay.vision.NeuralReader', return_value=reader) as factory, \
             patch('tracen_replay.race_quantity_refinement.generate', return_value={}) as generator:
            first = inspect(source_path, self.root, 1000, 1250)
            second = inspect(source_path, self.root, 1000, 1250)
        self.assertEqual(first, second)
        self.assertTrue(first['registered'])
        self.assertEqual(decoder.call_count, 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(reader.read.call_count, 1)
        self.assertEqual(generator.call_count, 2)
        self.assertTrue(all(call.kwargs['fixed_quantity_windows'] for call in generator.call_args_list))
        metadata, _ = load(self.root, self.source['sha256'])
        self.assertEqual(metadata['verified_frames'], 1)

    def test_inspection_rejects_invalid_bounds_before_capture(self):
        for args in [(0, 5001, 60), (1000, 1250, 61), (True, 1250, 60)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                inspect(self.root / 'missing-video', self.root, *args)

    def test_evidence_audit_includes_registered_race_windows(self):
        from tracen_replay.verify_evidence import verify
        source_path = self.root / 'source.bin'
        source_path.write_bytes(b'fixture recording')
        self.source['sha256'] = self.digest(source_path)
        self.write(self.root / 'capture.json', {'source': self.source, 'frames': []})
        self.write(self.window / 'capture.json', self.capture)
        register(self.root, 'window')
        with patch('builtins.print'):
            result = verify(self.root, source_path)
        self.assertTrue(result['evidence_integrity_verified'])
        self.assertEqual(result['verified_race_reward_inspection_frames'], 1)
        self.assertIn('race-reward-inspection.json', result['inspection_manifest_sha256'])
        self.raw['gameplay_sha256'] = 'c' * 64
        self.write(self.window / 'neural/f.json', self.raw)
        with patch('builtins.print'):
            result = verify(self.root, source_path)
        self.assertFalse(result['evidence_integrity_verified'])
        self.assertEqual(result['errors'][0]['path'], 'race-reward-inspection.json')
