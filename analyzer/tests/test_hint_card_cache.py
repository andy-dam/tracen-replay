from copy import deepcopy
import json
from importlib import import_module
import unittest
from unittest.mock import patch

from tracen_replay.hint_card_cache import (
    _ROW_HASH_VERSION_V1,
    _ROW_HASH_VERSION_V2,
    _source_manifest_hash,
    _span_hashes,
    _validate_candidate,
    load,
    prepare,
    refresh,
    save,
)
from tracen_replay.stat_state_details import read_goal_turns


SOURCE_SHA = "a" * 64


class HintCardCacheTests(unittest.TestCase):
    def setUp(self):
        # Reuse the source-chain fixture while keeping this suite independent
        # of the real ignored recording assets.
        fixture_class = import_module("tests.test_hint_card_identity").HintCardIdentityTests
        self.fixture = fixture_class("test_valid_source_chain_recovers_card_and_preserves_raw_names")
        self.fixture.setUp()
        self.root = self.fixture.root
        self.rows = self.fixture.rows
        for path in (self.root / "neural").glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["engine_fingerprint"] = "e" * 64
            path.write_text(json.dumps(raw), encoding="utf-8")
        self.candidate = self.fixture._patched_recover()[0]
        self.candidate["provenance"]["prefix_ocr_model_fingerprint"] = "d" * 64
        self.cache_path = self.root / "hint-card-recovery.json"
        with self._source_pixels():
            save(
                self.cache_path,
                [self.candidate],
                self.rows,
                self.root,
                source_sha256=SOURCE_SHA,
            )

    def tearDown(self):
        self.fixture.tearDown()

    def _source_pixels(self):
        def overlays(image):
            index = int(image.getpixel((0, 0))[0]) - 1
            return [[604 - index * 5, 854 - (index == 2) * 5, 617 - index * 5, 871 - (index == 2) * 4]]

        return self._patches(
            "tracen_replay.inventory_suffix.detect",
            side_effect=["single_circle", "single_circle", "single_circle"],
            extra=("tracen_replay.receipt_occlusion.overlay_boxes", overlays),
        )

    @staticmethod
    def _patches(target, *, side_effect, extra):
        class _Patches:
            def __enter__(self):
                self.detect = patch(target, side_effect=side_effect)
                self.overlay = patch(extra[0], side_effect=extra[1])
                self.detect.__enter__()
                self.overlay.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.overlay.__exit__(exc_type, exc, traceback)
                self.detect.__exit__(exc_type, exc, traceback)

        return _Patches()

    def _load(self, rows=None):
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            return load(
                self.rows if rows is None else rows,
                self.root,
                SOURCE_SHA,
                cache_path=self.cache_path,
            )

    def _assert_invalid(self, rows=None):
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(
                self.rows if rows is None else rows,
                self.root,
                SOURCE_SHA,
                cache_path=self.cache_path,
            )

    @staticmethod
    def _goal_lines(value):
        return [
            {"text": str(value), "confidence": 99.0, "box": [270, 45, 300, 75]},
            {"text": "turn(s)", "confidence": 99.0, "box": [310, 45, 370, 75]},
            {"text": "left", "confidence": 99.0, "box": [380, 45, 420, 75]},
        ]

    def _rows_with_goal_projection(self, value=5, *, include_projection=True):
        rows = deepcopy(self.rows)
        for row in rows:
            lines = row["ocr"]["neural"]
            lines.extend(self._goal_lines(value))
            if include_projection:
                parsed, proof = read_goal_turns(lines)
                self.assertEqual(parsed, value)
                row["stats"] = {
                    "calendar_text": "Classic Year Early Sep",
                    "turns_remaining_to_goal": parsed,
                    "turns_remaining_provenance": proof,
                }
            else:
                row["stats"] = {
                    "calendar_text": "Classic Year Early Sep",
                    "turns_remaining_to_goal": None,
                }
        return rows

    def _write_legacy_cache_for_rows(self, rows, *, representation=None):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        span = [
            row
            for row in rows
            if row["source_timestamp_ms"] in self.candidate["source_timestamps_ms"]
        ]
        with self._source_pixels():
            validation = _validate_candidate(
                self.candidate,
                rows,
                self.root,
                source_sha256=SOURCE_SHA,
                capture_manifest_sha256=payload["capture_manifest_sha256"],
            )
        self.assertIsNotNone(validation)
        expected_hashes = _span_hashes(
            span,
            version=_ROW_HASH_VERSION_V1,
            legacy_countdown=representation,
        )
        self.assertEqual(validation["span_hashes"], _span_hashes(span, version=_ROW_HASH_VERSION_V1))
        candidate = payload["candidates"][0]
        candidate["cache_provenance"]["span_rows"] = expected_hashes
        payload.pop("row_hash_version", None)
        candidate["cache_provenance"].pop("row_hash_version", None)
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_load_revalidates_cache_without_constructing_ocr(self):
        self.assertEqual(self._load(), [self.candidate])

    def test_particle_legacy_ranked_candidate_requires_source_pixel_card_proof(self):
        rows = deepcopy(self.rows)
        for row in rows:
            for line in row["facts"]["occluded_receipt_lines"]:
                line["animated_overlay_boxes"] = [[585, 870, 599, 888]]
                line["animated_overlay_occluded"] = True
        manifest_sha = _source_manifest_hash(self.root)
        self.assertIsNotNone(manifest_sha)
        with self._source_pixels():
            validation = _validate_candidate(
                self.candidate,
                rows,
                self.root,
                source_sha256=SOURCE_SHA,
                capture_manifest_sha256=manifest_sha,
                row_hash_version=_ROW_HASH_VERSION_V2,
            )
        self.assertIsNone(validation)

    def test_default_cache_path_is_model_free(self):
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            self.assertEqual(load(self.rows, self.root, SOURCE_SHA), [self.candidate])

    def test_missing_cache_is_empty(self):
        missing = self.root / "missing-cache.json"
        self.assertEqual(load(self.rows, self.root, SOURCE_SHA, cache_path=missing), [])

    def test_existing_cache_cannot_be_overwritten(self):
        with self._source_pixels():
            with self.assertRaises(FileExistsError):
                save(
                    self.cache_path,
                    [self.candidate],
                    self.rows,
                    self.root,
                    source_sha256=SOURCE_SHA,
                )

    def test_new_cache_records_v2_row_hash_and_preserves_current_rows(self):
        rows = self._rows_with_goal_projection()
        original = deepcopy(rows)
        output = self.root / "countdown-v2-cache.json"
        with self._source_pixels():
            payload = save(
                output,
                [self.candidate],
                rows,
                self.root,
                source_sha256=SOURCE_SHA,
            )
        self.assertEqual(payload["row_hash_version"], _ROW_HASH_VERSION_V2)
        self.assertEqual(
            payload["candidates"][0]["cache_provenance"]["row_hash_version"],
            _ROW_HASH_VERSION_V2,
        )
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            self.assertEqual(load(rows, self.root, SOURCE_SHA, cache_path=output), [self.candidate])
        self.assertEqual(rows, original)

    def test_v2_rejects_unknown_row_hash_version(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["row_hash_version"] = "source-row-v99"
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_v2_rejects_unknown_candidate_row_hash_version(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["cache_provenance"]["row_hash_version"] = "source-row-v99"
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_v2_rejects_countdown_without_matching_immutable_ocr_proof(self):
        rows = self._rows_with_goal_projection()
        rows[0]["stats"]["turns_remaining_to_goal"] += 1
        self._assert_invalid(rows)

    def test_v2_rejects_forged_countdown_provenance(self):
        rows = self._rows_with_goal_projection()
        rows[0]["stats"]["turns_remaining_provenance"]["value"] += 1
        self._assert_invalid(rows)

    def test_v2_rejects_changed_goal_ocr_even_when_projection_is_recomputed(self):
        rows = self._rows_with_goal_projection()
        lines = rows[0]["ocr"]["neural"]
        lines[-3]["text"] = "6"
        parsed, proof = read_goal_turns(lines)
        self.assertEqual(parsed, 6)
        rows[0]["stats"]["turns_remaining_to_goal"] = parsed
        rows[0]["stats"]["turns_remaining_provenance"] = proof
        self._assert_invalid(rows)

    def test_v2_keeps_unrelated_stats_source_bound(self):
        rows = self._rows_with_goal_projection()
        rows[0]["stats"]["calendar_text"] = "Classic Year Late Sep"
        self._assert_invalid(rows)

    def test_legacy_cache_migrates_only_none_countdown_representation(self):
        legacy_rows = self._rows_with_goal_projection(include_projection=False)
        self._write_legacy_cache_for_rows(legacy_rows)
        current_rows = self._rows_with_goal_projection()
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            self.assertEqual(
                load(current_rows, self.root, SOURCE_SHA, cache_path=self.cache_path),
                [self.candidate],
            )

    def test_legacy_cache_migrates_only_absent_countdown_representation(self):
        legacy_rows = self._rows_with_goal_projection(include_projection=False)
        self._write_legacy_cache_for_rows(legacy_rows, representation="absent")
        current_rows = self._rows_with_goal_projection()
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            self.assertEqual(
                load(current_rows, self.root, SOURCE_SHA, cache_path=self.cache_path),
                [self.candidate],
            )

    def test_legacy_migration_rejects_nonnull_countdown_not_proven_by_ocr(self):
        legacy_rows = self._rows_with_goal_projection(include_projection=False)
        self._write_legacy_cache_for_rows(legacy_rows)
        current_rows = self._rows_with_goal_projection()
        current_rows[0]["stats"]["turns_remaining_to_goal"] += 1
        self._assert_invalid(current_rows)

    def test_changed_intermediate_row_invalidates_cache(self):
        rows = deepcopy(self.rows)
        rows[1]["context_title"] = "Different event"
        rows[1]["context_title_candidate"] = "Different event"
        self._assert_invalid(rows)

    def test_changed_prefix_amount_invalidates_cache(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][0]["prefix_amount_proof"]["amount"] = 3
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_prefix_confidence_must_be_a_bounded_percentage(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][0]["prefix_amount_proof"]["confidence"] = 101
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_changed_card_geometry_invalidates_cache(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][1]["card"]["box"][0] += 10
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_changed_source_png_invalidates_cache(self):
        path = self.root / self.rows[1]["evidence"]
        original = path.read_bytes()
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        self._assert_invalid()

    def test_changed_raw_model_observation_invalidates_cache(self):
        path = self.root / "neural/part-000-frame-000001.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["engine_fingerprint"] = "changed-engine"
        path.write_text(json.dumps(raw), encoding="utf-8")
        self._assert_invalid()

    def test_foreign_source_is_empty(self):
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            with self.assertRaises(ValueError):
                load(self.rows, self.root, "b" * 64, cache_path=self.cache_path)

    def test_malformed_present_cache_is_a_fatal_replay_error(self):
        self.cache_path.write_text("not json", encoding="utf-8")
        self._assert_invalid()

    def test_prepare_writes_the_explicit_new_artifact(self):
        output = self.root / "prepared-cache.json"
        report = {
            "source": {"sha256": SOURCE_SHA},
            "gameplay_tracking": {"readings": self.rows},
        }
        with (
            self._source_pixels(),
            patch("tracen_replay.hint_card_identity.recover", return_value=[self.candidate]),
        ):
            payload = prepare(report, self.root, output)
        self.assertEqual(payload["schema"], "tracen-replay/hint-card-recovery-cache-v1")
        self.assertEqual(payload["candidate_count"], 1)
        self.assertTrue(output.is_file())
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            self.assertEqual(load(self.rows, self.root, SOURCE_SHA, cache_path=output), [self.candidate])


class SingleLineHintCardCacheTests(unittest.TestCase):
    def setUp(self):
        fixture_class = import_module("tests.test_hint_card_identity").HintCardIdentityTests
        self.fixture = fixture_class("test_single_line_receipt_recovers_card_with_unknown_suffix")
        self.fixture.setUp()
        self.root = self.fixture.root
        self.rows = self.fixture._single_line_rows()
        for path in (self.root / "neural").glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["engine_fingerprint"] = "e" * 64
            path.write_text(json.dumps(raw), encoding="utf-8")
        with patch(
            "tracen_replay.inventory_suffix.detect",
            side_effect=[None, None],
        ):
            self.candidate = self.fixture._patched_recover(
                self.rows,
                detect_side_effect=[None, None],
            )[0]
        self.candidate["provenance"]["prefix_ocr_model_fingerprint"] = "d" * 64
        self.cache_path = self.root / "single-line-hint-card-recovery.json"
        with self._source_pixels():
            save(
                self.cache_path,
                [self.candidate],
                self.rows,
                self.root,
                source_sha256=SOURCE_SHA,
            )
        self.valid_cache = self.cache_path.read_text(encoding="utf-8")

    def tearDown(self):
        self.fixture.tearDown()

    def _source_pixels(self, suffixes=None):
        def overlays(image):
            index = int(image.getpixel((0, 0))[0]) - 1
            return [[604 - index * 5, 854, 617 - index * 5, 871]]

        return self._patches(
            suffixes=[None, None] if suffixes is None else suffixes,
            overlays=overlays,
        )

    @staticmethod
    def _patches(*, suffixes, overlays):
        class _Patches:
            def __enter__(self):
                self.detect = patch("tracen_replay.inventory_suffix.detect", side_effect=suffixes)
                self.overlay = patch("tracen_replay.receipt_occlusion.overlay_boxes", side_effect=overlays)
                self.detect.__enter__()
                self.overlay.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.overlay.__exit__(exc_type, exc, traceback)
                self.detect.__exit__(exc_type, exc, traceback)

        return _Patches()

    def _load(self, rows=None, *, suffixes=None):
        with self._source_pixels(suffixes=suffixes), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            return load(
                self.rows if rows is None else rows,
                self.root,
                SOURCE_SHA,
                cache_path=self.cache_path,
            )

    def _assert_invalid(self, mutate):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        mutate(payload["candidates"][0])
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(self.rows, self.root, SOURCE_SHA, cache_path=self.cache_path)

    def test_single_line_cache_replays_unknown_rank_without_ocr(self):
        loaded = self._load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["observation_kind"], "single_line_hint_receipt")
        self.assertEqual(loaded[0]["identity_proof"]["rank_state"], "undetermined")
        self.assertIsNone(loaded[0]["identity_proof"]["suffix"])

    def test_parser_derived_choice_preview_and_status_facts_do_not_stale_cache(self):
        # A newer parser may attach observational facts to a row after this
        # cache was prepared.  They do not replace the source card/receipt
        # proof and must not change the persisted source-row fingerprint.
        rows = deepcopy(self.rows)
        for row in rows:
            row.setdefault("facts", {}).update(
                choice_observation={"selection_verified": False},
                preview_option=None,
                preview_overlay_effects=[],
                preview_overlay_evidence={},
                preview_overlay_proven=False,
                preview_modifier_effects=[],
                preview_modifier_proven=False,
                rejected_counts={"missing_training_header_or_option": 1},
                status_badges=[{"kind": "mood_status", "value": "great"}],
            )
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ):
            loaded = load(rows, self.root, SOURCE_SHA, cache_path=self.cache_path)
        self.assertEqual(loaded, [self.candidate])

    def _rows_with_particle_metadata(self, *, boxes, occluded):
        rows = deepcopy(self.rows)
        for row in rows:
            facts = row["facts"]
            for line in facts["occluded_receipt_lines"]:
                line["animated_overlay_boxes"] = deepcopy(boxes)
                line["animated_overlay_occluded"] = occluded
            facts["receipt_overlay_evidence"]["animated_overlay_boxes"] = deepcopy(boxes)
        return rows

    def test_neutral_particle_metadata_does_not_stale_legacy_cache(self):
        rows = self._rows_with_particle_metadata(boxes=[], occluded=False)
        loaded = self._load(rows)
        self.assertEqual(loaded, [self.candidate])

    def test_nonempty_particle_metadata_invalidates_legacy_cache(self):
        rows = self._rows_with_particle_metadata(
            boxes=[[585, 870, 599, 888]],
            occluded=True,
        )
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(rows, self.root, SOURCE_SHA, cache_path=self.cache_path)

    def test_particle_single_line_candidate_requires_source_pixel_card_proof(self):
        # The private cursor-only refresh view also applies to single-line
        # candidates.  Without this check, a matching Meta Only card/receipt
        # and raw OCR edit could pass while the gameplay pixels stay fixed.
        rows = self._rows_with_particle_metadata(
            boxes=[[585, 870, 599, 888]],
            occluded=True,
        )
        manifest_sha = _source_manifest_hash(self.root)
        self.assertIsNotNone(manifest_sha)
        with patch(
            "tracen_replay.inventory_suffix.detect",
            side_effect=[None, None],
        ):
            validation = _validate_candidate(
                self.candidate,
                rows,
                self.root,
                source_sha256=SOURCE_SHA,
                capture_manifest_sha256=manifest_sha,
                source_row_view="receipt-occlusion-cursor-only-v1",
                row_hash_version=_ROW_HASH_VERSION_V2,
            )
        self.assertIsNone(validation)

    def test_true_particle_metadata_invalidates_legacy_cache(self):
        rows = self._rows_with_particle_metadata(boxes=[], occluded=True)
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(rows, self.root, SOURCE_SHA, cache_path=self.cache_path)

    def test_source_refresh_preserves_particle_metadata_and_reloads_without_ocr(self):
        rows = self._rows_with_particle_metadata(
            boxes=[[585, 870, 599, 888]],
            occluded=True,
        )
        original_cache = self.cache_path.read_bytes()
        output = self.root / "refreshed-hint-card-recovery.json"

        def source_card_proofs(candidate, _readings, proof_root, *, source_sha256):
            from hashlib import sha256
            from PIL import Image

            proofs = []
            for observation in candidate["observations"]:
                image_path = proof_root / observation["evidence"]
                with Image.open(image_path) as opened:
                    image = opened.convert("RGB")
                    left, top, right, bottom = (
                        int(value) for value in observation["card"]["box"]
                    )
                    crop = image.crop((left - 148, top, right - 148, bottom))
                proofs.append(
                    {
                        "timestamp_ms": observation["timestamp_ms"],
                        "evidence": observation["evidence"],
                        "card_text": candidate["name"],
                        "recognized_text": candidate["name"],
                        "confidence": 99.0,
                        "crop_box": list(observation["card"]["box"]),
                        "pixel_rgb_sha256": sha256(crop.tobytes()).hexdigest(),
                        "model_fingerprint": "d" * 64,
                        "basis": "source_bound_card_identity_crop_ocr",
                    }
                )
            return proofs

        with self._source_pixels(), patch(
            "tracen_replay.hint_card_identity.recover",
            return_value=[deepcopy(self.candidate)],
        ), patch(
            "tracen_replay.hint_card_cache._source_card_identity_proofs",
            side_effect=source_card_proofs,
        ):
            payload = refresh(
                self.cache_path,
                rows,
                self.root,
                output,
                source_sha256=SOURCE_SHA,
            )
        self.assertEqual(self.cache_path.read_bytes(), original_cache)
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(
            payload["candidates"][0]["cache_provenance"]["source_row_view"],
            "receipt-occlusion-cursor-only-v1",
        )
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("refreshed cache load must not construct OCR"),
        ):
            loaded = load(rows, self.root, SOURCE_SHA, cache_path=output)
        self.assertEqual(loaded, [self.candidate])

        tampered = deepcopy(rows)
        tampered[0]["facts"]["occluded_receipt_lines"][0][
            "animated_overlay_boxes"
        ][0][0] += 1
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("tampered cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(tampered, self.root, SOURCE_SHA, cache_path=output)

    def test_unallowlisted_fact_change_still_invalidates_cache(self):
        rows = deepcopy(self.rows)
        rows[0].setdefault("facts", {})["unallowlisted_parser_fact"] = {"value": 1}
        with self._source_pixels(), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(rows, self.root, SOURCE_SHA, cache_path=self.cache_path)

    def test_changed_suffix_interpretation_invalidates_unknown_rank_cache(self):
        with self._source_pixels(suffixes=["single_circle", None]), patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("cache load must not construct OCR"),
        ), self.assertRaises(ValueError):
            load(self.rows, self.root, SOURCE_SHA, cache_path=self.cache_path)

    def test_tampered_single_line_suffix_state_or_name_invalidates_cache(self):
        self._assert_invalid(
            lambda candidate: candidate["observations"][0]["card"].update(
                suffix="single_circle", suffix_state="present"
            )
        )
        self.cache_path.write_text(self.valid_cache, encoding="utf-8")
        self._assert_invalid(
            lambda candidate: candidate["observations"][1]["receipt"].update(
                raw_name="Other Skill"
            )
        )

    def test_tampered_single_line_prefix_text_or_name_invalidates_cache(self):
        self._assert_invalid(
            lambda candidate: candidate["observations"][0]["prefix_amount_proof"].update(
                recognized_text="Gained 2 hint level(s) for Other Skill",
                raw_name="Other Skill",
            )
        )
        self.cache_path.write_text(self.valid_cache, encoding="utf-8")
        self._assert_invalid(
            lambda candidate: candidate["observations"][0]["prefix_amount_proof"].update(
                raw_name="Other Skill"
            )
        )

    def test_tampered_single_line_prefix_proof_hash_or_model_invalidates_cache(self):
        self._assert_invalid(
            lambda candidate: candidate["observations"][0]["prefix_amount_proof"].update(
                pixel_rgb_sha256="f" * 64
            )
        )
        self.cache_path.write_text(self.valid_cache, encoding="utf-8")
        self._assert_invalid(
            lambda candidate: candidate["observations"][0]["prefix_amount_proof"].update(
                model_fingerprint="f" * 64
            )
        )


class WrappedHintCardCacheTests(unittest.TestCase):
    def setUp(self):
        fixture_class = import_module("tests.test_hint_card_identity").HintCardIdentityTests
        self.fixture = fixture_class("test_wrapped_receipt_recovers_with_unknown_rank_and_context")
        self.fixture.setUp()
        self.root = self.fixture.root
        self.rows = self.fixture._make_wrapped_rows()
        for path in (self.root / "neural").glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["engine_fingerprint"] = "e" * 64
            path.write_text(json.dumps(raw), encoding="utf-8")
        self.candidate = self.fixture._patched_wrapped_recover(self.rows)[0]
        self.cache_path = self.root / "wrapped-hint-card-recovery.json"
        with patch(
            "tracen_replay.inventory_suffix.detect",
            side_effect=[None, None],
        ):
            save(
                self.cache_path,
                [self.candidate],
                self.rows,
                self.root,
                source_sha256=SOURCE_SHA,
            )

    def tearDown(self):
        self.fixture.tearDown()

    def _load(self, rows=None, *, suffixes=None):
        suffixes = [None, None] if suffixes is None else suffixes
        with (
            patch("tracen_replay.inventory_suffix.detect", side_effect=suffixes),
            patch(
                "tracen_replay.vision.NeuralReader",
                side_effect=AssertionError("cache load must not construct OCR"),
            ),
        ):
            return load(
                self.rows if rows is None else rows,
                self.root,
                SOURCE_SHA,
                cache_path=self.cache_path,
            )

    def _assert_invalid(self, rows=None):
        with (
            patch("tracen_replay.inventory_suffix.detect", side_effect=[None, None]),
            patch(
                "tracen_replay.vision.NeuralReader",
                side_effect=AssertionError("cache load must not construct OCR"),
            ),
            self.assertRaises(ValueError),
        ):
            load(
                self.rows if rows is None else rows,
                self.root,
                SOURCE_SHA,
                cache_path=self.cache_path,
            )

    def test_wrapped_cache_replays_without_ocr_and_preserves_unknown_rank(self):
        loaded = self._load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "Wrapped Skill")
        self.assertEqual(loaded[0]["identity_proof"]["rank_state"], "undetermined")
        self.assertIsNone(loaded[0]["identity_proof"]["suffix"])
        self.assertEqual(
            loaded[0]["observations"][0]["receipt_parts"]["prefix"]["text"],
            "Gained 2 hint l:ve Wrapped",
        )

    def test_particle_wrapped_candidate_requires_source_pixel_card_proof(self):
        # A cursor-free wrapped row can otherwise be made to agree with a
        # forged card/receipt/raw OCR label while its gameplay pixels stay
        # unchanged.  The source-pixel witness is mandatory whenever the
        # particle detector adds a non-neutral annotation.
        rows = deepcopy(self.rows)
        for row in rows:
            row["facts"] = {
                "occluded_receipt_lines": [
                    {
                        "animated_overlay_boxes": [[585, 870, 599, 888]],
                        "animated_overlay_occluded": True,
                    }
                ]
            }
        manifest_sha = _source_manifest_hash(self.root)
        self.assertIsNotNone(manifest_sha)
        with patch(
            "tracen_replay.inventory_suffix.detect",
            side_effect=[None, None],
        ):
            validation = _validate_candidate(
                self.candidate,
                rows,
                self.root,
                source_sha256=SOURCE_SHA,
                capture_manifest_sha256=manifest_sha,
                row_hash_version=_ROW_HASH_VERSION_V2,
            )
        self.assertIsNone(validation)

    def test_undetermined_rank_is_not_promoted_by_replay_detector(self):
        loaded = self._load(suffixes=["single_circle", "single_circle"])
        self.assertEqual(loaded[0]["identity_proof"]["rank_state"], "undetermined")
        self.assertIsNone(loaded[0]["identity_proof"]["suffix"])

    def test_tampered_amount_crop_hash_invalidates_cache(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][0]["prefix_amount_proof"][
            "pixel_rgb_sha256"
        ] = "a" * 64
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_tampered_amount_crop_geometry_invalidates_cache(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][0]["prefix_amount_proof"][
            "crop_box"
        ][0] += 1
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_amount_proof_model_binding_invalidates_cache(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][0]["prefix_amount_proof"][
            "model_fingerprint"
        ] = "f" * 64
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()

    def test_unknown_rank_state_or_suffix_is_rejected(self):
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        payload["candidates"][0]["observations"][0]["card"]["suffix_state"] = "absent_proven"
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self._assert_invalid()


if __name__ == "__main__":
    unittest.main()
