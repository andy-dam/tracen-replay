import copy
import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image

from tracen_replay.evaluation_adapters import report_document
from tracen_replay.inspect_receipts import merge
from tracen_replay.inspect_training import reparse_inspection
from tracen_replay.occluded_receipt_recovery import scoped_observations
from tests import localdata
from tracen_replay.transactions import outcome_events


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = localdata.root("third_recording_hint_recovery_report", "report.json")
PREPARED_ROOTS = (
    localdata.root("prepared_snapshot_final", "independent-02"),
    localdata.root("prepared_snapshot_late", "independent-02"),
)


def _prepared_root():
    return next(
        (
            path
            for path in PREPARED_ROOTS
            if (path / "occluded-receipt-recovery/receipt-inspection.json").is_file()
        ),
        None,
    )


@unittest.skipUnless(
    REPORT_PATH.is_file() and _prepared_root() is not None,
    "preserved independent-02 source bundle is unavailable",
)
class SourceBoundFriendshipProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = _prepared_root()
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        cls.inspection_root = cls.prepared / "occluded-receipt-recovery"
        cls.inspection = json.loads(
            (cls.inspection_root / "receipt-inspection.json").read_text(
                encoding="utf-8"
            )
        )
        cls.target = next(
            row
            for row in cls.inspection["readings"]
            if row.get("source_timestamp_ms") == 239717
            and row.get("evidence", "").endswith("frame-000023.png")
        )
        cls.anchor = next(
            row
            for row in cls.report["gameplay_tracking"]["readings"]
            if row.get("source_timestamp_ms") == 239750
            and row.get("evidence")
            == "initial-baseline/gameplay/part-001-frame-000480.png"
        )
        cls.windows = cls.report["occluded_receipt_recovery"]["processed_windows"]

    def _promoted(self):
        reread = reparse_inspection(
            {
                "source_sha256": self.inspection["source_sha256"],
                "readings": [self.target],
            },
            self.inspection_root,
        )
        base = [copy.deepcopy(self.anchor)]
        promoted = scoped_observations(base, reread, self.windows)
        self.assertEqual(len(promoted), 1)
        return base, promoted

    @staticmethod
    def _event_rows(rows):
        merged = merge(rows[0], rows[1])
        events = outcome_events(merged)
        friendships = [
            effect
            for event in events
            for effect in event.get("effects", [])
            if effect.get("kind") == "friendship_change"
        ]
        return merged, events, friendships

    def test_actual_source_clean_name_reaches_event_and_report_document_once(self):
        base, promoted = self._promoted()
        merged, events, friendships = self._event_rows((base, promoted))
        self.assertEqual(
            [(effect.get("name"), effect.get("amount")) for effect in friendships],
            [("Symboli Rudolf", 5)],
        )
        target_events = [
            event for event in events
            if any(
                effect.get("kind") == "friendship_change"
                and effect.get("name") == "Symboli Rudolf"
                for effect in event.get("effects", [])
            )
        ]
        self.assertEqual(len(target_events), 1)
        self.assertNotEqual(
            target_events[0]["id"],
            friendships[0]["source_bound_identity_proof"]["owner_ref"],
        )

        document = report_document(
            {
                "source": {"sha256": self.report["source"]["sha256"]},
                "gameplay_tracking": {
                    "readings": merged,
                    "events": events,
                    "turn_action_receipts": [],
                    "dialogue_choices": [],
                    "races": [],
                    "checkpoints": [],
                    "performance_accounting": {"checkpoints": []},
                    "lesson_purchases": [],
                    "skill_purchases": [],
                },
            }
        )
        projected = [
            row
            for row in document["observations"]
            if row.get("payload", {}).get("kind") == "friendship_change"
        ]
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["payload"]["name"], "Symboli Rudolf")
        self.assertEqual(projected[0]["payload"]["amount"], 5)

        # Repeat the final projection with the preserved report's original
        # event graph.  Only the affected outcome is replaced by the rebuilt
        # event; training/action references remain intact.
        full_report = copy.deepcopy(self.report)
        full_report["gameplay_tracking"]["readings"] = merge(
            self.report["gameplay_tracking"]["readings"], promoted
        )
        full_events = copy.deepcopy(self.report["gameplay_tracking"]["events"])
        target_indices = [
            index for index, event in enumerate(full_events)
            if event.get("first_seen_ms") == self.anchor["source_timestamp_ms"]
            and event.get("last_seen_ms") == self.anchor["source_timestamp_ms"]
            and any(
                effect.get("kind") == "friendship_change"
                and effect.get("name") == "Symboli udolf"
                for effect in event.get("effects", [])
            )
        ]
        self.assertEqual(len(target_indices), 1)
        full_events[target_indices[0]] = target_events[0]
        full_report["gameplay_tracking"]["events"] = full_events
        full_document = report_document(full_report)
        # The same source receipt line block also carries the real hint
        # ("Gained 1 hint level(s) for Subdued End Closers."), which the
        # rebuilt event legitimately retains ahead of the friendship effect.
        # Locate the friendship effect by kind instead of assuming index 0.
        friendship_indices = [
            index
            for index, effect in enumerate(target_events[0]["effects"])
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(len(friendship_indices), 1)
        source_ref = (
            f"/gameplay_tracking/events/{target_indices[0]}"
            f"/effects/{friendship_indices[0]}"
        )
        full_projected = [
            row for row in full_document["observations"]
            if row.get("source_ref") == source_ref
        ]
        self.assertEqual(len(full_projected), 1)
        self.assertEqual(full_projected[0]["payload"]["kind"], "friendship_change")
        self.assertEqual(full_projected[0]["payload"]["name"], "Symboli Rudolf")
        self.assertEqual(full_projected[0]["payload"]["amount"], 5)
        event_prefix = f"/gameplay_tracking/events/{target_indices[0]}/effects/"
        event_projected = [
            row for row in full_document["observations"]
            if str(row.get("source_ref", "")).startswith(event_prefix)
        ]
        self.assertEqual(
            sorted(
                (row["payload"].get("kind"), row["payload"].get("name"), row["payload"].get("amount"))
                for row in event_projected
            ),
            [
                ("friendship_change", "Symboli Rudolf", 5),
                ("skill_hint_change", "Subdued End Closers", 1),
            ],
        )
        self.assertEqual(
            [
                row["payload"].get("name")
                for row in full_document["observations"]
                if row.get("payload", {}).get("kind") == "friendship_change"
                and row.get("source_ref", "").startswith(
                    f"/gameplay_tracking/events/{target_indices[0]}/"
                )
            ],
            ["Symboli Rudolf"],
        )

    def test_fresh_cached_baseline_anchor_matches_gameplay_frame(self):
        cache_path = (
            self.prepared
            / "initial-baseline/neural/part-001-frame-000480.json"
        )
        gameplay_path = (
            self.prepared
            / "initial-baseline/gameplay/part-001-frame-000480.png"
        )
        source_frame_path = self.prepared / "initial-baseline/part-001/frames/000480.jpg"
        self.assertTrue(cache_path.is_file())
        self.assertTrue(gameplay_path.is_file())
        self.assertTrue(source_frame_path.is_file())
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cached["source_timestamp_ms"], 239750)
        self.assertEqual(cached["evidence"], "gameplay/part-001-frame-000480.png")
        self.assertEqual(
            cached["source_frame_sha256"],
            hashlib.sha256(source_frame_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            cached["gameplay_sha256"],
            hashlib.sha256(
                Image.open(gameplay_path).convert("RGB").tobytes()
            ).hexdigest(),
        )
        lines = [
            line for line in cached.get("lines", [])
            if line.get("text") == "Friendship with Symboli udolf went up by 5."
        ]
        self.assertEqual(len(lines), 1)
        self.assertGreaterEqual(lines[0]["confidence"], 95)
        self.assertEqual(lines[0]["box"], [316, 831, 747, 860])

    def test_wrong_source_namespace_cannot_resolve_clean_name(self):
        base, promoted = self._promoted()
        promoted[0]["source_sha256"] = "0" * 64
        _, events, friendships = self._event_rows((base, promoted))
        self.assertEqual(friendships, [])
        self.assertFalse(any(event.get("resolved_identity_readings") for event in events))

    def test_wrong_owner_interval_cannot_resolve_clean_name(self):
        base, promoted = self._promoted()
        recovery = promoted[0]["facts"]["occluded_receipt_recovery"]
        recovery["trigger_sources"][0]["owner_ref"] = "forged-owner"
        _, events, friendships = self._event_rows((base, promoted))
        self.assertEqual(friendships, [])
        self.assertFalse(any(event.get("resolved_identity_readings") for event in events))

    def test_adjacent_different_receipt_geometry_cannot_resolve_clean_name(self):
        base, unmodified = self._promoted()
        unmodified_hint = next(
            effect
            for effect in unmodified[0]["effects"]
            if effect.get("kind") == "skill_hint_change"
        )
        reread = reparse_inspection(
            {
                "source_sha256": self.inspection["source_sha256"],
                "readings": [self.target],
            },
            self.inspection_root,
        )
        row = reread[0]
        effect = next(
            effect
            for effect in row["effects"]
            if effect.get("kind") == "friendship_change"
        )
        # Move the clean observation to the separate hint slot while retaining
        # all other source metadata.  Recovery must reject this physical
        # mismatch instead of using adjacent text as identity.
        different_box = [316, 805, 768, 834]
        row["ocr"]["neural"] = [
            line
            for line in row["ocr"]["neural"]
            if line.get("box") != [315, 829, 747, 861]
        ]
        row["ocr"]["neural"].append(
            {
                "text": effect["raw_text"],
                "confidence": effect["confidence"],
                "box": different_box,
            }
        )
        row["effects"] = [
            effect if item.get("kind") == "friendship_change" else item
            for item in row["effects"]
        ]
        promoted = scoped_observations(base, reread, self.windows)
        # The forged friendship line must not be promoted from adjacent
        # geometry, and nothing may resolve the clean name.  The genuine
        # hint on the untouched physical line is still legitimate evidence
        # and must survive exactly as it did without the forgery.
        promoted_effects = [
            effect for row in promoted for effect in row.get("effects", [])
        ]
        self.assertEqual(
            [effect for effect in promoted_effects if effect.get("kind") == "friendship_change"],
            [],
        )
        # Raw OCR lines are retained as-is; the clean name must not appear in
        # any promoted effect or recovery fact.
        for row in promoted:
            self.assertNotIn(
                "Symboli Rudolf",
                json.dumps(
                    {"effects": row.get("effects"), "facts": row.get("facts")},
                    sort_keys=True,
                ),
            )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted_effects, [unmodified_hint])
        self.assertEqual(
            promoted[0]["facts"]["occluded_receipt_recovery"]["effect_signatures"],
            [["skill_hint_change", None, "Subdued End Closers", 1, None, None]],
        )
        _, events, friendships = self._event_rows((base, promoted))
        # Without a same-line clean observation, the anchor's own damaged
        # reading stays exactly as observed: nothing resolves the clean name.
        self.assertEqual(
            [(effect.get("name"), effect.get("amount")) for effect in friendships],
            [("Symboli udolf", 5)],
        )
        self.assertFalse(
            any(effect.get("source_bound_identity_proof") for effect in friendships)
        )
        self.assertFalse(any(event.get("resolved_identity_readings") for event in events))


if __name__ == "__main__":
    unittest.main()
