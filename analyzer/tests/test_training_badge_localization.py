import copy
import hashlib
import unittest
from types import SimpleNamespace

import numpy as np
from PIL import Image

from tracen_replay.crop_provenance import resolve_gain_regions
from tracen_replay.training_badge_localization import (
    SCHEMA,
    VERSION,
    GAIN_BOXES,
    WIDE_GAIN_BOXES,
    attach,
    discover_training_badge_crops,
    localize_training_badges,
    gameplay_fingerprint,
)
from tracen_replay.training_gain_resolution import resolve_source_clipped_gain
from tracen_replay.vision import NeuralReader


class TrainingBadgeLocalizationTests(unittest.TestCase):
    def _pane(self, *, fields=("speed",)):
        pixels = np.zeros((1080, 810, 3), dtype=np.uint8)
        for field in fields:
            left, top, right, bottom = WIDE_GAIN_BOXES[field]
            # A connected gold/orange badge-shaped region.  The component is
            # intentionally inside the normal field geometry but is not an
            # amount supplied by the fixture; the recognizer callback below
            # is the only test-side source of text.
            x0 = int((left + right) / 2 - 26)
            x1 = x0 + 52
            y0 = top + 31
            y1 = y0 + 29
            pixels[y0:y1, x0 - 148:x1 - 148] = (240, 100, 30)
        return Image.fromarray(pixels, mode="RGB")

    @staticmethod
    def _metadata(pane, **extra):
        value = {"gameplay_sha256": gameplay_fingerprint(pane)}
        value.update(extra)
        return value

    @staticmethod
    def _candidate(family, field, text, confidence, box, **extra):
        value = {
            "text": text,
            "raw_text": text,
            "confidence": confidence,
            "box": list(box),
            "role": "amount_crop_candidate",
            "source_role": "amount_crop_candidate",
            "input_eligible": True,
        }
        value.update(extra)
        return value

    def test_localizer_discovers_source_component_without_amount_input(self):
        pane = self._pane()
        discovery = discover_training_badge_crops(
            pane, header="Training", result_grid=True,
        )
        self.assertEqual(discovery["status"], "candidate")
        self.assertEqual([item["field"] for item in discovery["observations"]], ["speed"])
        self.assertEqual(discovery["observations"][0]["box"], [324, 837, 388, 878])
        self.assertFalse(any("amount" in item for item in discovery["observations"]))

        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata=self._metadata(pane, source_timestamp_ms=10, evidence="frame.png"),
        )
        self.assertEqual(localized["status"], "localized")
        observation = localized["observations"][0]
        self.assertEqual(observation["amount"], 7)
        self.assertEqual(observation["crop_family"], "localized_gain")
        self.assertTrue(observation["source_pixel_verified"])
        self.assertEqual(observation["source_observation_basis"], "source_pixel_localized_training_badge")
        self.assertRegex(observation["pixel_rgb_sha256"], r"^[0-9a-f]{64}$")

    def test_gate_rejects_preview_and_unproven_result_grid(self):
        pane = self._pane()
        for kwargs, reason in (
            ({"preview": True}, "preview_screen"),
            ({"screen": "training_preview"}, "preview_or_menu_screen"),
            ({"result_grid": False}, "training_result_grid_not_proven"),
            ({"header": "Lessons"}, "training_header_not_proven"),
        ):
            params = dict(header="Training", result_grid=True)
            params.update(kwargs)
            result = discover_training_badge_crops(pane, **params)
            self.assertEqual(result["status"], "gated")
            self.assertIn(reason, result["rejections"].values())

    def test_ambiguous_components_and_blocked_overlay_abstain(self):
        pane = self._pane()
        pixels = np.asarray(pane).copy()
        # Add a second qualifying component with a gap larger than the
        # grouping tolerance.  Both centers still fall inside speed's field.
        pixels[843:872, 400 - 148:452 - 148] = (240, 100, 30)
        ambiguous = discover_training_badge_crops(
            Image.fromarray(pixels, mode="RGB"), header="Training", result_grid=True,
        )
        self.assertEqual(ambiguous["rejections"].get("speed"), "ambiguous_components")

        blocked = discover_training_badge_crops(
            pane,
            header="Training",
            result_grid=True,
            blocked_boxes=[(320, 840, 390, 880)],
        )
        self.assertEqual(
            blocked["rejections"].get("speed"),
            "badge_component_occluded_by_source_overlay",
        )

    def test_unsigned_clipped_or_weak_ocr_never_becomes_localized(self):
        pane = self._pane()
        for recognized in (("+7:", 0.999), ("+7", 0.96)):
            result = localize_training_badges(
                pane,
                recognize=lambda crops, recognized=recognized: [recognized] * len(crops),
                header="Training",
                result_grid=True,
                source_metadata=self._metadata(pane, source_timestamp_ms=10, evidence="frame.png"),
            )
            self.assertEqual(result["status"], "unresolved")
            self.assertFalse(result["observations"])

    def test_attach_rejects_duplicate_field_and_preserves_existing_region(self):
        pane = self._pane()
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata=self._metadata(pane),
        )
        raw = {"gameplay_sha256": gameplay_fingerprint(pane), "regions": {}}
        attached = attach(raw, localized)
        self.assertIn("localized_gain.speed", attached["regions"])
        duplicate = copy.deepcopy(localized)
        duplicate["observations"].append(copy.deepcopy(duplicate["observations"][0]))
        with self.assertRaises(ValueError):
            attach(raw, duplicate)
        with self.assertRaises(ValueError):
            attach(attached, localized)

    def test_pixel_and_source_geometry_proof_is_retained_by_resolver(self):
        pane = self._pane()
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata=self._metadata(pane),
        )["observations"][0]
        regions = {
            "gain.speed": self._candidate("gain", "speed", "+7", 99.5, GAIN_BOXES["speed"]),
            "wide_gain.speed": self._candidate(
                "wide_gain", "speed", "+700", 94.0, WIDE_GAIN_BOXES["speed"],
            ),
            "localized_gain.speed": localized,
        }
        result = resolve_gain_regions(regions, "speed")
        self.assertEqual(result["canonical_amount"], 7)
        self.assertEqual(result["canonical_basis"], "source_crop_pixel_localized_training_badge_agreement")
        self.assertEqual(result["conflict_state"], "resolved_source_pixel_localized")
        self.assertEqual(result["canonical_candidate"]["crop_family"], "localized_gain")
        self.assertEqual(result["candidate_amounts"], [7, 700])

    def test_localized_badge_requires_tight_agreement_and_keeps_clipped_control_unresolved(self):
        pane = self._pane()
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata=self._metadata(pane),
        )["observations"][0]
        regions = {"localized_gain.speed": localized}
        result = resolve_gain_regions(regions, "speed")
        self.assertIsNone(result["canonical_amount"])
        self.assertEqual(result["conflict_state"], "unresolved_localized_without_tight_agreement")

        clipped = copy.deepcopy(localized)
        clipped["text"] = clipped["raw_text"] = "+6"
        clipped["amount"] = 6
        regions = {
            "gain.speed": self._candidate("gain", "speed", "+65", 99.5, GAIN_BOXES["speed"]),
            "localized_gain.speed": clipped,
        }
        result = resolve_gain_regions(regions, "speed")
        self.assertIsNone(result["canonical_amount"])
        self.assertEqual(result["conflict_state"], "unresolved_localized_tight_conflict")

    def test_sidecar_proof_retains_decoded_crop_identity(self):
        pane = self._pane()
        metadata = self._metadata(
            pane,
            source_timestamp_ms=10,
            evidence="gameplay.png",
        )
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata=metadata,
        )
        raw = {
            "source_timestamp_ms": 10,
            "evidence": "gameplay.png",
            "gameplay_sha256": gameplay_fingerprint(pane),
            "regions": {},
        }
        attached = attach(raw, localized)
        sidecar = dict(localized)
        sidecar["schema_version"] = SCHEMA
        sidecar["version"] = VERSION
        sidecar["metadata"] = dict(metadata)
        # The persisted crop hash is an independent source identity.  A
        # self-rehashed or changed crop must therefore not be interchangeable
        # with the attached proof, even before a filesystem loader is called.
        self.assertIn("localized_gain.speed", attached["regions"])
        mutated = copy.deepcopy(sidecar)
        mutated["observations"][0]["pixel_rgb_sha256"] = hashlib.sha256(b"changed").hexdigest()
        self.assertNotEqual(
            mutated["observations"][0]["pixel_rgb_sha256"],
            localized["observations"][0]["pixel_rgb_sha256"],
        )

    def test_reader_helper_uses_same_source_geometry_and_bgr_engine_contract(self):
        pane = self._pane()

        class Engine:
            def __init__(self):
                self.received = None

            def text_rec(self, request):
                self.received = request.img
                return SimpleNamespace(txts=["+7"] * len(request.img), scores=[0.999] * len(request.img))

        reader = object.__new__(NeuralReader)
        reader.engine = Engine()
        reader.TextRecInput = lambda *, img: SimpleNamespace(img=img)
        result = reader._localize_training_badges(
            pane, header="Training", result_grid=True,
        )
        self.assertEqual(result["status"], "localized")
        self.assertTrue(reader.engine.received)
        # Source localizer discovers an RGB crop; RapidOCR receives BGR just
        # like the ordinary fixed crop path.  The source proof hash remains
        # computed from the original RGB crop before this conversion.
        self.assertEqual(tuple(reader.engine.received[0][10, 10]), (30, 100, 240))


class TrainingBadgePhaseResolutionTests(unittest.TestCase):
    def _candidate(self, family, field, text, confidence, box, **extra):
        result = {
            "text": text,
            "raw_text": text,
            "confidence": confidence,
            "box": list(box),
            "role": "amount_crop_candidate",
            "source_role": "amount_crop_candidate",
            "input_eligible": True,
        }
        result.update(extra)
        return result

    def _row(self, regions, timestamp=100, option="speed"):
        resolution = resolve_gain_regions(regions, "speed")
        return {
            "screen": "training_result",
            "training_option": option,
            "source_timestamp_ms": timestamp,
            "evidence": f"frame-{timestamp}.png",
            "facts": {"training_gain_crop_provenance": {"speed": resolution}},
        }

    def test_localized_conflict_resolves_broad_contamination_in_committed_phase(self):
        from tracen_replay.training_badge_localization import localize_training_badges

        pane = Image.fromarray(np.zeros((1080, 810, 3), dtype=np.uint8), mode="RGB")
        pixels = np.asarray(pane).copy()
        pixels[843:872, 182:234] = (240, 100, 30)
        pane = Image.fromarray(pixels, mode="RGB")
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata={"gameplay_sha256": gameplay_fingerprint(pane)},
        )["observations"][0]
        regions = {
            "gain.speed": self._candidate("gain", "speed", "+7", 99.5, (300, 832, 414, 890)),
            "wide_gain.speed": self._candidate("wide_gain", "speed", "+700", 94.0, (250, 812, 462, 909)),
            "localized_gain.speed": localized,
        }
        row = self._row(regions)
        result = resolve_source_clipped_gain([row], "speed", phase_key="speed:100:100")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["accepted_amount"], 7)
        self.assertEqual(
            result["basis"],
            "source_pixel_localized_training_badge_with_tight_agreement",
        )
        self.assertEqual(len(result["accepted_observations"]), 2)

    def test_localized_badge_ignores_explicitly_noncanonical_cross_frame_diagnostics(self):
        """A weak wider/result view remains audit data after localized proof."""
        from tracen_replay.training_badge_localization import localize_training_badges

        pane = Image.fromarray(np.zeros((1080, 810, 3), dtype=np.uint8), mode="RGB")
        pixels = np.asarray(pane).copy()
        pixels[843:872, 182:234] = (240, 100, 30)
        pane = Image.fromarray(pixels, mode="RGB")
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata={"gameplay_sha256": gameplay_fingerprint(pane)},
        )["observations"][0]
        first = self._row({
            "gain.speed": self._candidate(
                "gain", "speed", "+7", 99.5, (300, 832, 414, 890),
            ),
            "localized_gain.speed": localized,
        }, timestamp=100)
        # The wider +71 is below the canonical wide-crop quality floor.  The
        # +72 result overlay is explicitly excluded from amount input.  Both
        # must stay visible in raw provenance while the independently proven
        # localized +7 is accepted.
        second = self._row({
            "wide_gain.speed": self._candidate(
                "wide_gain", "speed", "+71", 85.0, (250, 812, 462, 909),
            ),
            "result.speed": self._candidate(
                "result", "speed", "+72", 99.0, (270, 812, 450, 909),
                role="result_crop_diagnostic_excluded",
                source_role="result_crop_diagnostic_excluded",
                input_eligible=False,
            ),
        }, timestamp=133)

        result = resolve_source_clipped_gain(
            [first, second], "speed", phase_key="speed:100:133",
        )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["accepted_amount"], 7)
        self.assertEqual(result["observed_amounts"], [7, 71, 72])
        self.assertEqual(
            {item["amount"] for item in result["observations"]},
            {7, 71, 72},
        )

    def test_transactions_project_localized_amount_only_after_phase_and_tight_proof(self):
        from tracen_replay.training_badge_localization import localize_training_badges
        from tracen_replay.transactions import training_events

        pane = Image.fromarray(np.zeros((1080, 810, 3), dtype=np.uint8), mode="RGB")
        pixels = np.asarray(pane).copy()
        pixels[843:872, 182:234] = (240, 100, 30)
        pane = Image.fromarray(pixels, mode="RGB")
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+7", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata={"gameplay_sha256": gameplay_fingerprint(pane)},
        )["observations"][0]
        regions = {
            "gain.speed": self._candidate("gain", "speed", "+7", 99.5, (300, 832, 414, 890)),
            "wide_gain.speed": self._candidate("wide_gain", "speed", "+700", 94.0, (250, 812, 462, 909)),
            "localized_gain.speed": localized,
        }
        row = self._row(regions)
        row["facts"]["training_gains"] = {"speed": 7}
        events = training_events([row])
        self.assertEqual(events[0]["deltas"].get("speed"), 7)
        self.assertEqual(
            events[0]["source_clipped_gain_resolutions"]["speed"]["basis"],
            "source_pixel_localized_training_badge_with_tight_agreement",
        )

    def test_localized_preview_or_wrong_tight_value_never_promotes_amount(self):
        from tracen_replay.training_badge_localization import localize_training_badges

        pane = Image.fromarray(np.zeros((1080, 810, 3), dtype=np.uint8), mode="RGB")
        pixels = np.asarray(pane).copy()
        pixels[843:872, 182:234] = (240, 100, 30)
        pane = Image.fromarray(pixels, mode="RGB")
        localized = localize_training_badges(
            pane,
            recognize=lambda crops: [("+6", 0.999)] * len(crops),
            header="Training",
            result_grid=True,
            source_metadata={"gameplay_sha256": gameplay_fingerprint(pane)},
        )["observations"][0]
        regions = {
            "gain.speed": self._candidate("gain", "speed", "+65", 99.5, (300, 832, 414, 890)),
            "localized_gain.speed": localized,
        }
        row = self._row(regions)
        result = resolve_source_clipped_gain([row], "speed", phase_key="speed:100:100")
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "localized_badge_tight_conflict")

        row["screen"] = "training_preview"
        result = resolve_source_clipped_gain([row], "speed", phase_key="speed:100:100")
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "no_committed_result_phase_rows")


if __name__ == "__main__":
    unittest.main()
