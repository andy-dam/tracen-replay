"""Tests of ``tests.test_lesson_offer_cost_projection`` that need locally preserved evidence; they run only where it is."""
import copy
import json
import shutil
import unittest
from PIL import Image
from tracen_replay.full_recording import cached_readings, parse_receipt_pixels
from tracen_replay.lesson_offer_adapter import (
    LessonOfferSourceError,
    adapt_lesson_offer_frame,
    merge_lesson_offer_cost_refinement,
    refine_lesson_offer_costs,
)
from tracen_replay.lesson_offer_refinement import fingerprint
from tests import localdata
from tests.test_gameplay import workspace_temp
from tests.test_lesson_offer_cost_projection import GAMEPLAY_PATH, RAW_PATH, SOURCE_FRAME_PATH, SOURCE_VALUES, _offer, _SourceCropReader, _VariantSourceCropReader


class LessonOfferCostProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = (RAW_PATH, GAMEPLAY_PATH, SOURCE_FRAME_PATH)
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest('Independent-02 lesson source fixture is unavailable')

    def source_row(self):
        return json.loads(RAW_PATH.read_text(encoding='utf-8'))

    def source_frame(self):
        return {
            'id': 'part-005-frame-000094',
            'source_timestamp_ms': 623250,
            'evidence': 'part-005/frames/000094.jpg',
        }

    def test_actual_group_basics_missing_slots_are_recovered_from_real_crops(self):
        raw = self.source_row()
        base = adapt_lesson_offer_frame(
            raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
        )
        self.assertEqual(
            [price['value'] for price in _offer(base, 'Group Lesson Basics')['prices']],
            [0, 15, 0, None, None],
        )

        reader = _SourceCropReader()
        merged, extra = refine_lesson_offer_costs(
            raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
            reader=reader,
        )
        self.assertEqual(len(reader.calls), 1)
        self.assertIsNotNone(extra)
        group = _offer(merged, 'Group Lesson Basics')
        self.assertEqual(
            [price['value'] for price in group['prices']],
            [0, 15, 0, 0, 0],
        )
        self.assertEqual(group['status'], 'complete')
        self.assertEqual(group['cost'], {
            'dance': 0, 'passion': 15, 'vocal': 0, 'visual': 0, 'composure': 0,
        })
        for price in group['prices']:
            self.assertEqual(price['status'], 'accepted')
            self.assertIn('source_refinement', price)
            self.assertEqual(price['source_refinement']['coordinate_space'], 'gameplay_pane')
        self.assertEqual(
            merged['cost_refinement_provenance']['source_frame_sha256'],
            raw['source_frame_sha256'],
        )
        self.assertFalse(merged['cost_refinement_provenance']['independent_observations'])

    def test_weak_prices_require_agreement_across_same_source_views(self):
        raw = self.source_row()
        reader = _VariantSourceCropReader()
        merged, extra = refine_lesson_offer_costs(
            raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
            reader=reader,
        )
        # One raw crop batch plus the two bounded preprocessing views.  The
        # second and third calls still read the same fifteen source slots;
        # neither a catalog value nor a balance is available to the reader.
        self.assertEqual(len(reader.calls), 3)
        group = _offer(merged, 'Group Lesson Basics')
        self.assertEqual(group['status'], 'complete')
        self.assertEqual([price['value'] for price in group['prices']], [0, 15, 0, 0, 0])
        for field in ('visual', 'composure'):
            price = next(item for item in group['prices'] if item['field'] == field)
            self.assertIn(price['preprocess'], ('gray_autocontrast', 'gray_autocontrast_3x'))
            readings = price['source_readings']
            self.assertEqual([reading['variant'] for reading in readings],
                             ['raw', 'gray_autocontrast', 'gray_autocontrast_3x'])
            self.assertEqual([reading['value'] for reading in readings], [None, 0, 0])
            self.assertGreaterEqual(sum(reading['value'] == 0 for reading in readings[1:]), 2)
        self.assertTrue(extra['cards'])

    def test_lone_letter_o_crop_projects_as_zero_cost(self):
        raw = self.source_row()
        base = adapt_lesson_offer_frame(raw, gameplay_path=GAMEPLAY_PATH,
                                        source_frame_path=SOURCE_FRAME_PATH)
        # The visual slot of Group Lesson Basics is the ninth crop in the
        # row.  Price slots hold digits only, so a lone recognizer ``O`` is
        # the zero glyph; the projection carries the recorded substitution.
        values = list(SOURCE_VALUES)
        values[8] = 'O'
        reader = _SourceCropReader(values=values)
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=reader,
        )
        merged = merge_lesson_offer_cost_refinement(
            base, extra['cards'],
            provenance=extra,
            raw=raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
        )
        group = _offer(merged, 'Group Lesson Basics')
        visual = group['prices'][3]
        self.assertEqual(visual['value'], 0)
        self.assertEqual(visual['status'], 'accepted')
        self.assertEqual(group['cost'].get('visual'), 0)

    def test_unknown_crop_does_not_turn_missing_price_into_zero(self):
        raw = self.source_row()
        base = adapt_lesson_offer_frame(raw, gameplay_path=GAMEPLAY_PATH,
                                        source_frame_path=SOURCE_FRAME_PATH)
        # A crop whose recognizer returns a non-digit letter other than O
        # stays ambiguous even though the crop and source hashes are valid.
        values = list(SOURCE_VALUES)
        values[8] = 'D'
        reader = _SourceCropReader(values=values)
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=reader,
        )
        merged = merge_lesson_offer_cost_refinement(
            base, extra['cards'],
            provenance=extra,
            raw=raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
        )
        group = _offer(merged, 'Group Lesson Basics')
        visual = group['prices'][3]
        self.assertIsNone(visual['value'])
        self.assertEqual(visual['status'], 'unknown')
        self.assertNotIn('visual', group['cost'])
        self.assertNotEqual(group['cost'].get('visual'), 0)

    def test_conflicting_direct_and_crop_prices_are_explicitly_unknown(self):
        raw = self.source_row()
        base = adapt_lesson_offer_frame(raw, gameplay_path=GAMEPLAY_PATH,
                                        source_frame_path=SOURCE_FRAME_PATH)
        reader = _SourceCropReader()
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=reader,
        )
        changed = copy.deepcopy(base)
        group = _offer(changed, 'Group Lesson Basics')
        group['prices'][3] = {
            'field': 'visual', 'value': 7, 'amount': 7, 'status': 'accepted',
            'unknown_reason': None, 'confidence': 99.0,
            'source_line': {'text': '7', 'confidence': 99.0, 'box': [700, 600, 720, 620]},
        }
        merged = merge_lesson_offer_cost_refinement(
            changed,
            extra['cards'],
            provenance=extra,
            raw=raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
        )
        visual = _offer(merged, 'Group Lesson Basics')['prices'][3]
        self.assertIsNone(visual['value'])
        self.assertEqual(visual['unknown_reason'], 'conflicting_source_cost')
        self.assertEqual(visual['source_alternatives']['base']['value'], 7)
        self.assertEqual(visual['source_alternatives']['refined']['value'], 0)
        self.assertNotIn('visual', _offer(merged, 'Group Lesson Basics')['cost'])

    def test_fresh_and_cached_paths_persist_one_rich_offer_envelope(self):
        raw = self.source_row()
        frame = self.source_frame()
        with workspace_temp() as root:
            (root / 'neural').mkdir()
            (root / 'gameplay').mkdir()
            (root / 'part-005/frames').mkdir(parents=True)
            shutil.copy2(RAW_PATH, root / 'neural/part-005-frame-000094.json')
            shutil.copy2(GAMEPLAY_PATH, root / 'gameplay/part-005-frame-000094.png')
            shutil.copy2(SOURCE_FRAME_PATH, root / 'part-005/frames/000094.jpg')

            row = parse_receipt_pixels(
                raw, root, frame,
                lesson_offer_reader=_SourceCropReader(),
            )
            sidecar = root / 'lesson-offer-refinement/part-005-frame-000094.json'
            self.assertTrue(sidecar.is_file())
            group = _offer(row['facts']['lesson_offer_preview'], 'Group Lesson Basics')
            self.assertEqual([price['value'] for price in group['prices']], [0, 15, 0, 0, 0])
            lessons = [item for item in row['facts']['preview_effects'] if item.get('kind') == 'lesson']
            self.assertEqual(len(lessons), 3)
            self.assertEqual(len({item['offer_id'] for item in lessons}), 3)
            sidecar_bytes = sidecar.read_bytes()

            cached = cached_readings({'frames': [frame]}, root)[0]
            cached_group = _offer(cached['facts']['lesson_offer_preview'], 'Group Lesson Basics')
            self.assertEqual([price['value'] for price in cached_group['prices']], [0, 15, 0, 0, 0])
            self.assertEqual(len(cached['facts']['lesson_offer_observations']), 3)
            self.assertEqual(sidecar.read_bytes(), sidecar_bytes)

    def test_sidecar_provenance_rejects_changed_source_frame(self):
        raw = self.source_row()
        reader = _SourceCropReader()
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=reader,
        )
        with workspace_temp() as root:
            evidence = root / 'gameplay.png'
            source_frame = root / 'source.jpg'
            shutil.copy2(GAMEPLAY_PATH, evidence)
            shutil.copy2(SOURCE_FRAME_PATH, source_frame)
            source_frame.write_bytes(b'changed source frame')
            from tracen_replay.lesson_offer_refinement import apply
            with self.assertRaisesRegex(ValueError, 'source-frame'):
                apply(
                    {'screen': 'lesson_selection'}, raw, extra, evidence,
                    source_frame_path=source_frame,
                )

    def test_retained_source_reading_is_recomputed_from_its_declared_variant(self):
        raw = self.source_row()
        reader = _VariantSourceCropReader()
        _, extra = refine_lesson_offer_costs(
            raw,
            gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH,
            reader=reader,
        )
        changed = copy.deepcopy(extra)
        group = next(card for card in changed['cards'] if card['title']['text'] == 'Group Lesson Basics')
        visual = group['prices'][3]
        self.assertIn('source_readings', visual)
        visual['source_readings'][1]['value'] = 7
        visual['source_readings'][1]['status'] = 'accepted'
        visual['source_readings'][1]['unknown_reason'] = None
        changed['cards_sha256'] = fingerprint(changed['cards'])
        from tracen_replay.lesson_offer_refinement import observe
        with self.assertRaisesRegex(ValueError, 'source crop reading value'):
            with Image.open(GAMEPLAY_PATH) as image:
                observe(
                    image.convert('RGB'), raw, changed,
                    source_frame_path=SOURCE_FRAME_PATH,
                )

    def test_direct_merge_rejects_swapped_price_crop_binding(self):
        raw = self.source_row()
        base = adapt_lesson_offer_frame(
            raw, gameplay_path=GAMEPLAY_PATH, source_frame_path=SOURCE_FRAME_PATH,
        )
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=_SourceCropReader(),
        )
        changed = copy.deepcopy(extra)
        group = next(card for card in changed['cards'] if card['title']['text'] == 'Group Lesson Basics')
        group['prices'][3], group['prices'][4] = group['prices'][4], group['prices'][3]
        changed['cards_sha256'] = fingerprint(changed['cards'])
        with self.assertRaisesRegex(LessonOfferSourceError, 'field order|crop binding|price crop geometry'):
            merge_lesson_offer_cost_refinement(
                base,
                changed['cards'],
                provenance=changed,
                raw=raw,
                gameplay_path=GAMEPLAY_PATH,
                source_frame_path=SOURCE_FRAME_PATH,
            )

    def test_direct_merge_rejects_self_rehashed_identity_and_selected_reading_changes(self):
        raw = self.source_row()
        base = adapt_lesson_offer_frame(
            raw, gameplay_path=GAMEPLAY_PATH, source_frame_path=SOURCE_FRAME_PATH,
        )
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=_SourceCropReader(),
        )

        cases = (
            'selected OCR amount',
            'selected OCR confidence',
            'title line identity',
            'cost label identity',
            'card uncertainty',
        )
        for case in cases:
            with self.subTest(case=case):
                changed = copy.deepcopy(extra)
                group = next(
                    card for card in changed['cards']
                    if card['title']['text'] == 'Group Lesson Basics'
                )
                if case == 'selected OCR amount':
                    price = group['prices'][3]
                    price.update(text='7', value=7, amount=7)
                elif case == 'selected OCR confidence':
                    group['prices'][3]['confidence'] = 98.0
                elif case == 'title line identity':
                    group['title_line_indices'] = [999]
                elif case == 'cost label identity':
                    group['cost_label_line_index'] = 999
                else:
                    group['unknown_reasons'] = ['visual_missing_digit']
                changed['cards_sha256'] = fingerprint(changed['cards'])
                with self.assertRaises(LessonOfferSourceError):
                    merge_lesson_offer_cost_refinement(
                        base,
                        changed['cards'],
                        provenance=changed,
                        raw=raw,
                        gameplay_path=GAMEPLAY_PATH,
                        source_frame_path=SOURCE_FRAME_PATH,
                    )

    def test_source_frame_hash_without_path_is_explicitly_unverified(self):
        raw = self.source_row()
        _, extra = refine_lesson_offer_costs(
            raw, gameplay_path=GAMEPLAY_PATH,
            source_frame_path=SOURCE_FRAME_PATH, reader=_SourceCropReader(),
        )
        from tracen_replay.lesson_offer_refinement import observe
        with Image.open(GAMEPLAY_PATH) as image:
            observed = observe(image.convert('RGB'), raw, extra)
        self.assertFalse(observed['source_frame_verified'])

    def test_legacy_sidecar_card_selection_is_reconciled_by_source_identity(self):
        """Historical crop sidecars retain the current source card identity."""

        legacy_base = localdata.root("development_third_recording_baseline")
        frame_id = 'part-004-frame-000165'
        raw_path = legacy_base / 'neural' / f'{frame_id}.json'
        gameplay_path = legacy_base / 'gameplay' / f'{frame_id}.png'
        source_path = legacy_base / 'part-004' / 'frames' / '000165.jpg'
        sidecar_path = legacy_base / 'lesson-offer-refinement' / f'{frame_id}.json'
        required = (raw_path, gameplay_path, source_path, sidecar_path)
        if not all(path.is_file() for path in required):
            self.skipTest('Legacy lesson sidecar fixture is unavailable')

        with workspace_temp() as root:
            (root / 'neural').mkdir()
            (root / 'gameplay').mkdir()
            (root / 'part-004' / 'frames').mkdir(parents=True)
            (root / 'lesson-offer-refinement').mkdir()
            shutil.copy2(raw_path, root / 'neural' / f'{frame_id}.json')
            shutil.copy2(gameplay_path, root / 'gameplay' / f'{frame_id}.png')
            shutil.copy2(source_path, root / 'part-004' / 'frames' / '000165.jpg')
            shutil.copy2(sidecar_path, root / 'lesson-offer-refinement' / f'{frame_id}.json')
            raw = json.loads(raw_path.read_text(encoding='utf-8'))
            frame = {
                'id': frame_id,
                'source_timestamp_ms': raw['source_timestamp_ms'],
                'evidence': 'part-004/frames/000165.jpg',
            }
            row = parse_receipt_pixels(
                raw,
                root,
                frame,
                raw,
                source_sha256=raw.get('source_sha256'),
                lesson_offer_reader=object(),
            )

        preview = row['facts']['lesson_offer_preview']
        self.assertEqual(
            [offer['name'] for offer in preview['offers']],
            ['Ring Ring Diary', 'Go This Way', 'Zero Is Where the Center Stands!'],
        )
        self.assertTrue(row['facts']['lesson_offer_refinement_provenance'])
        self.assertNotIn('lesson_offer_cost_refinement', row['facts'])
