import copy
import hashlib
import json
import unittest
from pathlib import Path

from scripts.build_final_source_adjudication import apply_adjudications, build


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / ".local" / "final-reliability-v1" / "source-references" / "independent-01.json"
RECORDING = ROOT / ".local" / "full-recording" / "independent-01"
BASELINE = ROOT / ".local" / "final-reliability-v1" / "before-frozen-semantic-grade-v2.json"
MODIFIER_ARTIFACT = ROOT / ".local" / "final-reliability-v1" / "source-adjudication-modifiers-v1.json"
PREVIEW_ARTIFACT = ROOT / ".local" / "final-reliability-v1" / "preview-phase-adjudication-proposals-v1.json"
RACE_ARTIFACT = ROOT / ".local" / "final-reliability-v1" / "race-item-adjudication" / "independent-01-t033-gold-omission.json"
ACTION_INTERVAL_ARTIFACT = ROOT / ".local" / "final-reliability-v1" / "semantic-action-interval-verdict-v1.json"


@unittest.skipUnless(REFERENCE.is_file() and RECORDING.is_dir() and BASELINE.is_file(),
                     "frozen source recording is not available")
class FinalSourceAdjudicationTests(unittest.TestCase):
    def _inputs(self):
        references = ROOT / ".local" / "final-reliability-v1" / "source-references"
        recording = ROOT / ".local" / "full-recording"
        freeze = ROOT / ".local" / "final-reliability-v1" / "reference-freeze-v2.json"
        artifact = build(references, recording, freeze)
        grade = json.loads(BASELINE.read_text(encoding="utf-8"))
        return artifact, grade, recording

    def test_adjudication_is_separate_and_preserves_frozen_source(self):
        before = hashlib.sha256(REFERENCE.read_bytes()).hexdigest()
        artifact = build(
            ROOT / ".local" / "final-reliability-v1" / "source-references",
            ROOT / ".local" / "full-recording",
            ROOT / ".local" / "final-reliability-v1" / "reference-freeze-v2.json",
        )
        after = hashlib.sha256(REFERENCE.read_bytes()).hexdigest()

        self.assertEqual(before, after)
        self.assertEqual(
            artifact["schema_version"],
            "tracen-replay/final-reliability-source-adjudication-v1",
        )
        self.assertTrue(all(artifact["policy"].values()))
        findings = {finding["finding_id"]: finding for finding in artifact["findings"]}
        t063 = findings["independent-01-turn-063/partial-glyph-result"]
        self.assertEqual(t063["adjudicated_interpretation"]["stable_result"],
                         {"speed": 12, "wit": 20, "skill_points": 14})
        self.assertFalse(t063["grade_action"]["production_effect_count_change"])
        t042 = findings["independent-01-turn-042/current-performance-overlay"]
        self.assertEqual(t042["adjudicated_interpretation"]["visible_current_values"],
                         {"vocal": 84, "composure": 100})
        self.assertEqual(t042["adjudicated_interpretation"]["visible_overlays"],
                         {"vocal": 15, "composure": 15})
        self.assertFalse(t042["grade_action"]["production_effect_count_change"])

    def test_application_reports_rows_without_changing_score_or_denominator(self):
        artifact, grade, recording = self._inputs()
        applied = apply_adjudications(grade, artifact, recording_dir=recording)

        self.assertEqual(len(applied["changed_rows"]), 4)
        self.assertEqual(len(applied["validated_source_rows"]), 9)
        self.assertTrue(applied["score_preservation"]["original_scores_preserved"])
        self.assertTrue(applied["score_preservation"]["original_denominator_preserved"])
        self.assertIsNone(applied["score_preservation"]["adjudicated_score"])
        t063 = next(row for row in applied["changed_rows"]
                    if row["source_ref"] == "t063-transient-speed-dense")
        self.assertEqual(t063["original_grade"]["status"], "missed")
        self.assertEqual(t063["score_effect"], "advisory_only")
        t042 = next(row for row in applied["changed_rows"]
                    if row["source_ref"] == "t042-state-after-performance")
        self.assertEqual(t042["original_grade"]["status"], "missed")

    def test_application_attaches_modifier_adjudication_without_regrading(self):
        if not MODIFIER_ARTIFACT.is_file():
            self.skipTest("modifier adjudication artifact is not available")
        artifact, grade, recording = self._inputs()
        modifier_artifact = json.loads(MODIFIER_ARTIFACT.read_text(encoding="utf-8"))
        applied = apply_adjudications(
            grade,
            artifact,
            recording_dir=recording,
            modifier_artifact=modifier_artifact,
            modifier_artifact_path=MODIFIER_ARTIFACT,
        )

        modifier = applied["modifier_adjudication"]
        self.assertEqual(len(modifier["rows"]), 9)
        self.assertEqual(len(modifier["unchanged_rows"]), 4)
        self.assertEqual(len(modifier["changed_rows"]), 5)
        self.assertEqual(
            modifier["summary"]["adjudication_counts"],
            {
                "source_supported_applied": 4,
                "source_supported_preview_only": 4,
                "cited_proof_mismatch_with_preview_context": 1,
                "rows_eligible_for_applied_effect_use": 4,
                "rows_not_eligible_for_applied_effect_use": 5,
            },
        )
        self.assertTrue(modifier["score_preservation"]["original_scores_preserved"])
        self.assertTrue(modifier["score_preservation"]["original_denominator_preserved"])
        self.assertIsNone(modifier["score_preservation"]["adjudicated_score"])
        self.assertEqual(applied["original_grade"]["status_counts"],
                         {"ambiguous": 4, "correct": 257, "partial": 18,
                          "missed": 196, "incorrect": 1})
        self.assertEqual(len(applied["original_grade"]["case_result_denominators"]), 24)
        t033_power = next(row for row in modifier["unchanged_rows"]
                          if row["source_ref"] == "t033-power-bonus")
        self.assertEqual(t033_power["original_grade"]["status"], "correct")
        t033_frequency = next(row for row in modifier["changed_rows"]
                              if row["source_ref"] == "t033-support-chain-frequency")
        self.assertEqual(t033_frequency["original_grade"]["status"], "missed")

    def test_modifier_application_rejects_changed_gold_or_proof(self):
        if not MODIFIER_ARTIFACT.is_file():
            self.skipTest("modifier adjudication artifact is not available")
        artifact, grade, recording = self._inputs()
        modifier_artifact = json.loads(MODIFIER_ARTIFACT.read_text(encoding="utf-8"))

        changed_gold = copy.deepcopy(modifier_artifact)
        changed_gold["cases"][0]["gold"]["payload"]["amount"] = 999
        with self.assertRaisesRegex(ValueError, "Frozen modifier gold row changed"):
            apply_adjudications(
                grade,
                artifact,
                recording_dir=recording,
                modifier_artifact=changed_gold,
            )

        changed_proof = copy.deepcopy(modifier_artifact)
        changed_proof["cases"][0]["cited_evidence"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Frozen modifier image hash mismatch"):
            apply_adjudications(
                grade,
                artifact,
                recording_dir=recording,
                modifier_artifact=changed_proof,
            )

    def test_modifier_application_binds_supplied_document_to_frozen_file(self):
        artifact, grade, recording = self._inputs()
        modifier_artifact = json.loads(MODIFIER_ARTIFACT.read_text(encoding="utf-8"))
        changed = copy.deepcopy(modifier_artifact)
        changed["purpose"] = "changed"
        with self.assertRaisesRegex(ValueError, "does not match its frozen file"):
            apply_adjudications(
                grade,
                artifact,
                recording_dir=recording,
                modifier_artifact=changed,
                modifier_artifact_path=MODIFIER_ARTIFACT,
            )

    def test_application_attaches_preview_race_and_action_adjudications(self):
        required = (PREVIEW_ARTIFACT, RACE_ARTIFACT, ACTION_INTERVAL_ARTIFACT)
        if not all(path.is_file() for path in required):
            self.skipTest("extended source adjudication artifacts are not available")
        artifact, grade, recording = self._inputs()
        applied = apply_adjudications(
            grade,
            artifact,
            recording_dir=recording,
            preview_artifact_path=PREVIEW_ARTIFACT,
            race_artifact_path=RACE_ARTIFACT,
            action_interval_artifact_path=ACTION_INTERVAL_ARTIFACT,
        )

        preview = applied["preview_phase_adjudication"]
        self.assertEqual(len(preview["rows"]), 19)
        self.assertEqual(len(preview["changed_rows"]), 8)
        self.assertEqual(len(preview["unchanged_rows"]), 11)
        self.assertEqual(len(preview["validated_control_rows"]), 11)
        self.assertTrue(all(row["score_effect"] == "advisory_only"
                            for row in preview["rows"]))
        self.assertEqual(
            {row["source_ref"] for row in preview["changed_rows"]},
            {
                "v1-turn-004-preview-speed", "v1-turn-004-preview-power",
                "v1-turn-004-preview-skill-points", "v1-turn-017-preview-dance",
                "v1-turn-017-preview-composure", "v1-turn-017-preview-speed",
                "v1-turn-017-preview-power", "v1-turn-017-preview-skill-points",
            },
        )

        race = applied["race_item_adjudication"]
        self.assertEqual(race["rows"][0]["frozen_expected_values"], [1, 400, 1])
        self.assertEqual(race["rows"][0]["producer_actual_values"], [1, 400, 1, 400])
        self.assertTrue(race["score_preservation"]["original_denominator_preserved"])

        action = applied["action_interval_adjudication"]
        self.assertEqual({row["source_ref"] for row in action["rows"]},
                         {"t066-outing-commit", "ind02-t035-rest"})
        self.assertTrue(all(row["source_phase"] == "committed"
                            and row["phase_application"]["matching_must_not_widen_source_interval"]
                            for row in action["rows"]))
        self.assertEqual(
            applied["original_grade"]["status_counts"],
            {"ambiguous": 4, "correct": 257, "partial": 18,
             "missed": 196, "incorrect": 1},
        )
        self.assertEqual(len(applied["original_grade"]["case_result_denominators"]), 24)

    def test_preview_application_rejects_changed_source_snapshot(self):
        if not PREVIEW_ARTIFACT.is_file():
            self.skipTest("preview adjudication artifact is not available")
        artifact, grade, recording = self._inputs()
        preview = json.loads(PREVIEW_ARTIFACT.read_text(encoding="utf-8"))
        changed = copy.deepcopy(preview)
        changed["findings"][0]["source_refs"][0]["source_row_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Frozen source row changed"):
            apply_adjudications(grade, artifact, recording_dir=recording,
                                preview_artifact=changed)

    def test_race_application_rejects_changed_producer_snapshot(self):
        if not RACE_ARTIFACT.is_file():
            self.skipTest("race adjudication artifact is not available")
        artifact, grade, recording = self._inputs()
        race = json.loads(RACE_ARTIFACT.read_text(encoding="utf-8"))
        changed = copy.deepcopy(race)
        changed["producer_source_evidence"]["snapshot_items"][3]["quantity"] = 1
        with self.assertRaisesRegex(ValueError, "Race producer item rows changed"):
            apply_adjudications(grade, artifact, recording_dir=recording,
                                race_artifact=changed)

    def test_action_application_rejects_changed_proof_timestamp(self):
        if not ACTION_INTERVAL_ARTIFACT.is_file():
            self.skipTest("action interval artifact is not available")
        artifact, grade, recording = self._inputs()
        action = json.loads(ACTION_INTERVAL_ARTIFACT.read_text(encoding="utf-8"))
        changed = copy.deepcopy(action)
        changed["cases"][0]["source_proof"]["gameplay_evidence"][1]["source_timestamp_ms"] += 250
        with self.assertRaisesRegex(ValueError, "Action evidence timestamp changed"):
            apply_adjudications(grade, artifact, recording_dir=recording,
                                action_interval_artifact=changed)

    def test_application_rejects_unknown_source_row(self):
        artifact, grade, recording = self._inputs()
        changed = copy.deepcopy(artifact)
        changed["findings"][0]["source_refs"][0]["source_ref"] = "unknown-source-row"
        with self.assertRaisesRegex(ValueError, "Unknown frozen source observation"):
            apply_adjudications(grade, changed, recording_dir=recording)

    def test_application_rejects_changed_gold_payload(self):
        artifact, grade, recording = self._inputs()
        changed = copy.deepcopy(artifact)
        changed["findings"][0]["source_refs"][0]["payload"]["amount"] = 999
        with self.assertRaisesRegex(ValueError, "Frozen source payload changed"):
            apply_adjudications(grade, changed, recording_dir=recording)

    def test_application_rejects_changed_image_proof(self):
        artifact, grade, recording = self._inputs()
        changed = copy.deepcopy(artifact)
        changed["findings"][0]["source_refs"][0]["evidence"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Preserved image proof changed"):
            apply_adjudications(grade, changed, recording_dir=recording)

    def test_application_rejects_missing_image_proof_entry(self):
        artifact, grade, recording = self._inputs()
        changed = copy.deepcopy(artifact)
        changed["findings"][0]["source_refs"][0]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "Frozen source image proof set changed"):
            apply_adjudications(grade, changed, recording_dir=recording)


if __name__ == "__main__":
    unittest.main()
