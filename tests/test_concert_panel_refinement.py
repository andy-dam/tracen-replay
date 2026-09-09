import copy
import hashlib
import json
from pathlib import Path
import unittest

from tracen_replay.concert_panel_refinement import apply, build, consensus
from tracen_replay.refine_contrast import fingerprint as canonical_fingerprint
from tracen_replay.vision import parse
from tests.test_gameplay import workspace_temp


class ConcertPanelRefinementTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(Path("tests/fixtures/concert-panels.json").read_text(encoding="utf-8"))[0]["raw"]
        self.fixture = json.loads(Path("tests/fixtures/concert-panel-refinement-v1.json").read_text(encoding="utf-8"))

    def _observations(self):
        return copy.deepcopy(self.fixture["observations"])

    def _materialize_proofs(self, root, observations):
        """Create a small, internally linked source bundle for apply tests."""

        manifest = []
        for observation in observations:
            crop = root / observation["evidence"]
            crop.parent.mkdir(parents=True, exist_ok=True)
            crop.write_bytes(f"crop-{observation['timestamp_ms']}".encode())
            observation["evidence_sha256"] = hashlib.sha256(crop.read_bytes()).hexdigest()

            source_frame = root / observation["source_frame_evidence"]
            source_frame.parent.mkdir(parents=True, exist_ok=True)
            source_frame.write_bytes(f"decoded-{observation['timestamp_ms']}".encode())
            observation["source_frame_sha256"] = hashlib.sha256(source_frame.read_bytes()).hexdigest()

            panel = copy.deepcopy(self.raw)
            panel["source_timestamp_ms"] = observation["timestamp_ms"]
            panel["source_frame_sha256"] = observation["source_frame_sha256"]
            panel_path = root / observation["panel_raw_evidence"]
            panel_path.parent.mkdir(parents=True, exist_ok=True)
            panel_path.write_text(json.dumps(panel, ensure_ascii=False), encoding="utf-8")
            observation["panel_raw_sha256"] = hashlib.sha256(panel_path.read_bytes()).hexdigest()

            manifest.append(
                {
                    "id": observation["source_manifest_row_id"],
                    "source_timestamp_ms": observation["timestamp_ms"],
                    "source_pts": observation["source_pts"],
                    "time_base": observation["time_base"],
                    "evidence": f"part-005/frames/{source_frame.name}",
                }
            )

        manifest_path = root / "frames.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        for observation in observations:
            observation["source_manifest_sha256"] = manifest_hash
        return observations

    def test_fingerprint_matches_shared_audit_convention(self):
        from tracen_replay.concert_panel_refinement import fingerprint as refinement_fingerprint

        self.assertEqual(refinement_fingerprint({"text": "♪"}), canonical_fingerprint({"text": "♪"}))

    def test_temporal_consensus_ignores_low_confidence_first_frame(self):
        result = consensus(self._observations())
        self.assertEqual(result["value"], 0)
        self.assertEqual(result["independent_frame_count"], 3)
        self.assertEqual(result["first_timestamp_ms"], 662000)
        self.assertEqual(result["last_timestamp_ms"], 662500)
        self.assertEqual(result["timestamp_span_ms"], 500)
        self.assertEqual(result["maximum_gap_ms"], 250)

    def test_letter_o_is_never_normalized_to_zero(self):
        observations = self._observations()
        for observation in observations:
            observation["text"] = "Lvl O"
            observation["confidence"] = 95
        self.assertIsNone(consensus(observations))

    def test_stable_slot_pixels_can_repeat_across_distinct_source_frames(self):
        observations = self._observations()
        for observation in observations:
            observation["evidence_sha256"] = "same-stable-slot-pixels"
        result = consensus(observations)
        self.assertEqual(result["independent_frame_count"], 3)
        # Reusing one decoded source image is still insufficient support.
        for observation in observations:
            observation["source_frame_evidence"] = "same-source.jpg"
        self.assertIsNone(consensus(observations))

    def test_conflicting_numeric_readings_at_one_timestamp_abstain(self):
        observations = self._observations()
        conflict = copy.deepcopy(observations[1])
        conflict["text"] = "Lvl 1"
        conflict["confidence"] = 95
        conflict["variant"] = "alternate"
        conflict["evidence"] = "conflict.png"
        conflict["evidence_sha256"] = "conflict-hash"
        observations.append(conflict)
        self.assertIsNone(consensus(observations))

    def test_unrelated_far_apart_timestamps_do_not_form_support(self):
        observations = self._observations()
        observations[2]["timestamp_ms"] = 900000
        self.assertIsNone(consensus(observations))

    def test_support_sequence_must_stay_near_its_base_panel(self):
        observations = self._observations()
        for observation in observations:
            observation["timestamp_ms"] += 120000
        self.assertIsNone(consensus(observations, base_timestamp_ms=self.raw["source_timestamp_ms"]))

    def test_panel_anchor_is_required_for_each_support_frame(self):
        observations = self._observations()
        observations[1]["panel_anchors"] = []
        self.assertIsNone(consensus(observations))

    def test_apply_rejects_crop_that_hides_visible_planned_change(self):
        with workspace_temp() as root:
            base = root / "panel.png"
            base.write_bytes(b"base-panel-proof")
            raw = copy.deepcopy(self.raw)
            raw["lines"] = [
                dict(line, text="Lvl 0 > Lvl 3") if line.get("text") == "Lvl O" else line
                for line in raw["lines"]
            ]
            with self.assertRaisesRegex(ValueError, "single unchanged"):
                build(raw, self._observations(), base)

    def test_apply_retains_raw_line_and_exposes_refined_unchanged_value(self):
        with workspace_temp() as root:
            base = root / "panel.png"
            base.write_bytes(b"base-panel-proof")
            observations = self._materialize_proofs(root, self._observations())
            refinement = build(self.raw, observations, base, model_sha256={"rec": "test"}, observation_root=root)
            applied = apply(self.raw, refinement, base, observation_root=root)
            self.assertEqual(sum(line.get("text") == "Lvl O" for line in applied["lines"]), 1)
            refined = parse(applied)
            self.assertEqual(refined["facts"]["current_concert_bonuses"]["support_chain_event_frequency"], 0)
            self.assertEqual(refined["facts"]["planned_concert_bonuses"]["support_chain_event_frequency"], 0)
            evidence = refined["facts"]["concert_bonus_evidence"]["support_chain_event_frequency"]
            self.assertEqual(evidence["text"], "Lvl 0")
            self.assertEqual(evidence["refinement"], "concert_support_level")
            self.assertEqual(evidence['refinement_source_timestamps_ms'],[662000,662250,662500])
            from tracen_replay.transactions import active_bonus_snapshot
            snapshot=active_bonus_snapshot([dict(refined,source_timestamp_ms=self.raw['source_timestamp_ms'],evidence=self.raw['evidence'])])
            self.assertEqual(snapshot['values']['support_chain_event_frequency'],0)

    def test_apply_rejects_tampered_source_proof(self):
        with workspace_temp() as root:
            base = root / "panel.png"
            base.write_bytes(b"base-panel-proof")
            observations = self._materialize_proofs(root, self._observations())
            refinement = build(self.raw, observations, base, observation_root=root)
            (root / observations[1]["evidence"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                apply(self.raw, refinement, base, observation_root=root)

    def test_apply_rejects_support_from_another_concert(self):
        with workspace_temp() as root:
            base = root / "panel.png"
            base.write_bytes(b"base-panel-proof")
            observations = self._materialize_proofs(root, self._observations())
            refinement = build(self.raw, observations, base, observation_root=root)
            for item in refinement["observations"]:
                panel_path = root / item["panel_raw_evidence"]
                panel = json.loads(panel_path.read_text(encoding="utf-8"))
                for line in panel["lines"]:
                    if line.get("text") == item["panel_context"]:
                        line["text"] = "3rd Concert"
                panel_path.write_text(json.dumps(panel), encoding="utf-8")
                item["panel_raw_sha256"] = hashlib.sha256(panel_path.read_bytes()).hexdigest()
                item["panel_context"] = "3rd Concert"
            with self.assertRaisesRegex(ValueError, "support context"):
                apply(self.raw, refinement, base, observation_root=root)

    def test_apply_rejects_pts_provenance_drift(self):
        with workspace_temp() as root:
            base = root / "panel.png"
            base.write_bytes(b"base-panel-proof")
            observations = self._materialize_proofs(root, self._observations())
            refinement = build(self.raw, observations, base, observation_root=root)
            refinement["observations"][1]["source_pts"] += 1
            with self.assertRaisesRegex(ValueError, "PTS manifest linkage changed"):
                apply(self.raw, refinement, base, observation_root=root)


if __name__ == "__main__":
    unittest.main()
