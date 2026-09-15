import hashlib
import json
import copy
from pathlib import Path
import unittest

from PIL import Image

from tracen_replay.occluded_receipt_recovery import (
    OccludedReceiptRecoveryError,
    _adjacent_owner,
    _validate_inspection_cache,
    _validate_inspection_windows,
    _validate_replay_windows,
    plan,
    recover,
    scoped_observations,
)
from tracen_replay.receipt_occlusion import annotate
from tracen_replay.inspect_receipts import merge
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import NeuralReader, parse


SOURCE_FRAME = (
    Path(__file__).resolve().parents[1]
    / ".local/final-reliability-v1/diagnostic-scratch/friendship-155750-source/000016.jpg"
)
MODEL_DIR = Path(__file__).resolve().parents[1] / ".local/models/rapidocr"


def source_meta(timestamp, evidence):
    return dict(
        source_sha256="a" * 64,
        source_frame_sha256=hashlib.sha256(f"{timestamp}:{evidence}".encode()).hexdigest(),
        source_frame_id=evidence,
        engine_fingerprint="e" * 64,
        model_sha256={"model.onnx": "m" * 64},
    )


def blocked_line(box=(314, 828, 709, 863)):
    return dict(
        text="Friendship with Air Groove went up by 7.",
        box=list(box),
        confidence=98.846,
        overlay_boxes=[[485, 847, 502, 875]],
        animated_overlay_boxes=[[485, 847, 502, 875]],
        animated_overlay_occluded=True,
        recipient_name_occluded=False,
    )


def owner(*, event_id="outcome-1", start=155750, end=155750):
    return dict(
        id=event_id,
        kind="outcome",
        first_seen_ms=start,
        last_seen_ms=end,
        context_title="Incline",
        effects=[dict(kind="energy_change", amount=-20)],
    )


def base_row():
    return dict(
        source_timestamp_ms=155750,
        screen="event_outcome",
        context_title="Incline",
        evidence="base.png",
        stats={"values": {"speed": 100}},
        effects=[dict(kind="energy_change", amount=-20, raw_text="Energy went down by 20.")],
        facts={"occluded_receipt_lines": [blocked_line()]},
        ocr={"neural": []},
        **source_meta(155750, "base.png"),
    )


def clear_row(timestamp=155800, *, friendship_box=(315, 829, 708, 860), evidence="clear.png"):
    friendship_text = "Friendship with Air Groove went up by 7."
    energy_text = "Energy went down by 20."
    return dict(
        source_timestamp_ms=timestamp,
        screen="event_outcome",
        context_title="Incline",
        evidence=evidence,
        stats={"values": {"speed": 999}},
        effects=[
            dict(kind="energy_change", amount=-20, raw_text=energy_text, confidence=99),
            dict(kind="friendship_change", name="Air Groove", amount=7,
                 raw_text=friendship_text, confidence=98),
        ],
        facts={"unrelated_fact": "alternate"},
        ocr={"neural": [
            dict(text="Energy went down by 20.", confidence=99, box=[317, 807, 560, 834]),
            dict(text=friendship_text, confidence=98, box=list(friendship_box)),
        ]},
        **source_meta(timestamp, evidence),
    )


def identity_base_row(name="Symboli udolf", *, timestamp=239750, evidence="identity-base.png"):
    """One obstructed receipt anchor plus the degraded existing name read."""

    friendship_text = f"Friendship with {name} went up by 5."
    hint_text = "Gained 1 hint level(s) for Subdued End Closers."
    # This is the source overlay alignment retained by the normal receipt
    # occlusion path.  Its recipient span is derived by
    # receipt_occlusion.friendship_name_bounds; tests must not estimate it
    # from sentence length.
    friendship_alignment = dict(
        line_box=[316, 831, 747, 860],
        recognized_text=friendship_text,
        confidence=98.727,
        words=["Friendship", "with", *name.split(), "went", "up", "by", "5."],
        columns=[
            [2, 4, 5, 7, 9, 12, 14, 16, 18, 19],
            [23, 25, 27, 29],
            [32, 35, 37, 40, 43, 44, 46],
            [51, 54, 56, 58, 60],
            [63, 66, 68, 70],
            [73, 75],
            [79, 82],
            [85, 87],
        ],
        line_length=89,
    )
    return dict(
        source_timestamp_ms=timestamp,
        screen="event_outcome",
        context_title="A Hint for Growth",
        evidence=evidence,
        stats={},
        effects=[dict(
            kind="friendship_change", name=name, amount=5,
            raw_text=friendship_text, confidence=97.6,
        )],
        facts={"occluded_receipt_lines": [dict(
            text=hint_text,
            box=[318, 807, 768, 833],
            confidence=97.0,
            overlay_boxes=[[549, 854, 563, 872]],
            animated_overlay_boxes=[[549, 854, 563, 872]],
            animated_overlay_occluded=True,
            recipient_name_occluded=False,
        )], "receipt_overlay_evidence": {
            "alignments": [friendship_alignment],
        }},
        ocr={"neural": [dict(
            text=friendship_text,
            confidence=97.6,
            box=[316, 831, 747, 860],
        )]},
        **source_meta(timestamp, evidence),
    )


def identity_owner(*, event_id="identity-owner", start=239750, end=239750):
    return dict(
        id=event_id,
        kind="outcome",
        first_seen_ms=start,
        last_seen_ms=end,
        context_title="A Hint for Growth",
        effects=[],
    )


def identity_clear_row(
    *,
    name="Symboli Rudolf",
    timestamp=239717,
    evidence="identity-clear.png",
    second_name=None,
    context_title="A Hint for Growth",
):
    hint_text = "Gained 1 hint level(s) for Subdued End Closers."
    lines = [dict(text=hint_text, confidence=97.0, box=[316, 805, 768, 834])]
    effects = [dict(
        kind="skill_hint_change", name="Subdued End Closers", amount=1,
        raw_text=hint_text, confidence=97.0,
    )]
    friendship_text = f"Friendship with {name} went up by 5."
    lines.append(dict(text=friendship_text, confidence=99.265, box=[315, 829, 747, 861]))
    effects.append(dict(
        kind="friendship_change", name=name, amount=5,
        raw_text=friendship_text, confidence=99.265,
    ))
    if second_name is not None:
        second_text = f"Friendship with {second_name} went up by 5."
        lines.append(dict(text=second_text, confidence=99.1, box=[315, 829, 747, 861]))
        effects.append(dict(
            kind="friendship_change", name=second_name, amount=5,
            raw_text=second_text, confidence=99.1,
        ))
    return dict(
        source_timestamp_ms=timestamp,
        screen="event_outcome",
        context_title=context_title,
        evidence=evidence,
        stats={},
        effects=effects,
        facts={},
        ocr={"neural": lines},
        **source_meta(timestamp, evidence),
    )


class OccludedReceiptRecoveryTests(unittest.TestCase):
    def test_capped_planner_overlap_is_valid_and_does_not_duplicate_a_receipt(self):
        # A chain of nearby obstructions is longer than one allowed clip.
        # The planner must retain a little overlap rather than miss a source
        # trigger. This is the same shape encountered in a full fresh run.
        rows = []
        for timestamp in range(1000, 6251, 750):
            row = base_row()
            row.update(source_timestamp_ms=timestamp, evidence=f"base-{timestamp}.png")
            rows.append(row)
        windows = plan(rows, [], 10000)
        self.assertGreater(len(windows), 1)
        self.assertTrue(any(right['start_ms'] < left['end_ms']
                            for left, right in zip(windows, windows[1:])))
        self.assertTrue(all(window['end_ms'] - window['start_ms'] <= 5000 for window in windows))
        _validate_replay_windows(windows, 'processed_windows')
        inspected = [{key: window[key] for key in ('start_ms', 'end_ms', 'reason')}
                     | {'fps': 16} for window in windows]
        self.assertEqual(len(_validate_inspection_windows(inspected, 10000)), len(windows))
        with self.assertRaisesRegex(OccludedReceiptRecoveryError, 'duplicates'):
            _validate_inspection_windows([*inspected, inspected[0]], 10000)
        promoted = scoped_observations(rows, [clear_row(4800)], windows)
        self.assertEqual(len(promoted), 1)
        self.assertEqual([(effect['kind'], effect['amount']) for effect in promoted[0]['effects']],
                         [('friendship_change', 7)])

    def test_prepare_plan_binds_report_to_source_and_disables_ocr(self):
        from tests.test_gameplay import workspace_temp
        from unittest.mock import patch

        with workspace_temp() as root:
            source = root / "recording.mp4"
            source.write_bytes(b"source")
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            report = {
                "source": {"sha256": source_sha256, "duration_ms": 2000},
                "frames": [],
            }
            with patch("tracen_replay.full_recording.cached_readings", return_value=[]) as cached, \
                    patch("tracen_replay.occluded_receipt_recovery.recover",
                          return_value=([], {"prepared": True})) as run:
                metadata = __import__(
                    "tracen_replay.occluded_receipt_recovery",
                    fromlist=["prepare_plan"],
                ).prepare_plan(source, root, report)

            self.assertEqual(metadata, {"prepared": True})
            cached.assert_called_once_with(report, root.resolve())
            self.assertIs(run.call_args.kwargs["allow_ocr"], False)

    def test_prepare_plan_uses_pre_recovery_bundle_rows_when_supplied(self):
        from tests.test_gameplay import workspace_temp
        from unittest.mock import patch

        with workspace_temp() as root:
            source = root / "recording.mp4"
            source.write_bytes(b"source")
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            report = {
                "source": {"sha256": source_sha256, "duration_ms": 2000},
                "frames": [],
            }
            normalized_rows = [base_row()]
            normalized_events = [owner()]
            with patch("tracen_replay.full_recording.cached_readings",
                       side_effect=AssertionError("base-only planning was used")), \
                    patch("tracen_replay.occluded_receipt_recovery.recover",
                          return_value=([], {"prepared": True})) as run:
                metadata = __import__(
                    "tracen_replay.occluded_receipt_recovery",
                    fromlist=["prepare_plan"],
                ).prepare_plan(
                    source,
                    root,
                    report,
                    readings=normalized_rows,
                    events=normalized_events,
                )

            self.assertEqual(metadata, {"prepared": True})
            self.assertIs(run.call_args.args[3], normalized_rows)
            self.assertIs(run.call_args.args[4], normalized_events)
            self.assertIs(run.call_args.kwargs["allow_ocr"], False)

    def test_plan_is_label_free_and_expands_single_frame_owner(self):
        windows = plan([base_row()], [owner()], 200000)
        self.assertEqual(len(windows), 1)
        window = windows[0]
        self.assertEqual((window["start_ms"], window["end_ms"]), (155000, 156001))
        self.assertEqual(window["triggers"][0]["owner_ref"], "outcome-1")
        self.assertEqual(window["triggers"][0]["line_box"], [314, 828, 709, 863])
        self.assertNotIn("Friendship with Air Groove", json.dumps(window))
        self.assertNotIn('"amount"', json.dumps(window))

    def test_ownerless_source_context_can_bound_a_missing_event(self):
        row = base_row()
        row["effects"] = []
        windows = plan([row], [], 200000)
        self.assertEqual(len(windows), 1)
        trigger = windows[0]["triggers"][0]
        self.assertIsNone(trigger["owner_ref"])
        self.assertEqual(trigger["owner_context_title"], "Incline")
        promoted = scoped_observations([row], [clear_row()], windows)
        self.assertEqual(len(promoted), 1)
        self.assertEqual(
            [(effect["kind"], effect.get("name"), effect.get("amount"))
             for effect in promoted[0]["effects"]],
            [("friendship_change", "Air Groove", 7)],
        )
        self.assertEqual(
            promoted[0]["facts"]["occluded_receipt_recovery"]["owner_basis"],
            "source_row_context",
        )

    def test_ownerless_source_context_rejects_a_different_clear_context(self):
        row = base_row()
        row["effects"] = []
        windows = plan([row], [], 200000)
        clear = clear_row()
        clear["context_title"] = "Night Owl"
        self.assertEqual(scoped_observations([row], [clear], windows), [])

    def test_line_level_source_matching_accepts_a_bounded_ocr_spelling_variant(self):
        row = base_row()
        clear = clear_row()
        # The source-bound line remains the identity anchor.  A single dropped
        # glyph in a fresh reread should not make a physically matching receipt
        # disappear, while the parser still owns the resulting effect value.
        variant = "Friendship with Air Groov went up by 7."
        clear["effects"][1]["name"] = "Air Groov"
        clear["effects"][1]["raw_text"] = variant
        clear["ocr"]["neural"][1]["text"] = variant
        promoted = scoped_observations([row], [clear], plan([row], [owner()], 200000))
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["effects"][0]["name"], "Air Groov")

    def test_clear_line_amount_mismatch_is_not_accepted_by_sentence_similarity(self):
        row = base_row()
        clear = clear_row()
        # The source trigger has the observed amount 7, while the parsed clear
        # effect claims 8.  A fuzzy whole-sentence comparison alone would
        # accept this; semantic agreement must reject it.
        clear["effects"][1]["raw_text"] = "Friendship with Air Groove went up by 8."
        clear["effects"][1]["amount"] = 8
        self.assertEqual(
            scoped_observations([row], [clear], plan([row], [owner()], 200000)),
            [],
        )

    def test_clear_line_direction_mismatch_is_not_accepted_by_sentence_similarity(self):
        row = base_row()
        row["effects"] = []
        source_line = row["facts"]["occluded_receipt_lines"][0]
        source_line["text"] = "Energy went down by 20."
        source_line["box"] = [317, 807, 560, 834]
        source_line["overlay_boxes"] = [[485, 820, 502, 845]]
        clear = clear_row()
        clear["effects"] = [dict(
            kind="energy_change", amount=20,
            raw_text="Energy went up by 20.", confidence=99,
        )]
        clear["ocr"]["neural"] = [dict(
            text="Energy went up by 20.", confidence=99,
            box=[317, 807, 560, 834],
        )]
        self.assertEqual(
            scoped_observations([row], [clear], plan([row], [owner()], 200000)),
            [],
        )

    def test_mixed_owner_frame_vetoes_known_and_ownerless_lines(self):
        owned = base_row()
        ownerless = copy.deepcopy(base_row())
        ownerless["source_timestamp_ms"] = 155800
        ownerless["evidence"] = "ownerless.png"
        ownerless["effects"] = []
        ownerless_line = blocked_line(box=(317, 807, 560, 834))
        ownerless_line["text"] = "Energy went down by 20."
        ownerless["overlay_boxes"] = [[485, 820, 502, 845]]
        ownerless["animated_overlay_boxes"] = [[485, 820, 502, 845]]
        ownerless["facts"] = {"occluded_receipt_lines": [ownerless_line]}
        windows = plan([owned, ownerless], [owner()], 200000)
        promoted = scoped_observations(
            [owned, ownerless], [clear_row(timestamp=155800)], windows
        )
        self.assertEqual(promoted, [])

    def test_same_line_ambiguous_owner_vetoes_known_owner(self):
        known = base_row()
        known.update(source_timestamp_ms=1000, evidence="known.png")
        ambiguous = copy.deepcopy(known)
        ambiguous.update(source_timestamp_ms=1200, evidence="ambiguous.png", effects=[])
        events = [
            owner(event_id="owner-one", start=1000, end=1000),
            owner(event_id="owner-two", start=1200, end=1200),
            owner(event_id="owner-three", start=1200, end=1200),
        ]
        windows = plan(
            [known, ambiguous], events, 5000, pre_ms=750, post_ms=250
        )
        promoted = scoped_observations(
            [known, ambiguous], [clear_row(timestamp=1100)], windows
        )
        self.assertEqual(promoted, [])

    def test_clear_reread_can_fall_after_last_base_event_frame(self):
        promoted = scoped_observations(
            [base_row()],
            [clear_row()],
            plan([base_row()], [owner()], 200000),
        )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(
            [(effect["kind"], effect.get("name"), effect.get("amount"))
             for effect in promoted[0]["effects"]],
            [("friendship_change", "Air Groove", 7)],
        )
        # The clear frame is a new source timestamp, so its state map is
        # quarantined rather than copied into the canonical base row.
        self.assertEqual(promoted[0]["stats"], {})
        recovery = promoted[0]["facts"]["occluded_receipt_recovery"]
        self.assertEqual(recovery["requested_owner_ref"], "outcome-1")
        self.assertEqual(recovery["selected_line_boxes"], [[315, 829, 708, 860]])
        self.assertEqual(recovery["independent_frame_count"], 1)

    def test_adjacent_receipt_line_is_not_promoted(self):
        adjacent = clear_row(friendship_box=(314, 871, 709, 900))
        self.assertEqual(
            scoped_observations(
                [base_row()], [adjacent], plan([base_row()], [owner()], 200000)
            ),
            [],
        )

    def test_mutating_trigger_geometry_cannot_retarget_an_energy_effect(self):
        windows = plan([base_row()], [owner()], 200000)
        # This is the neighboring energy line from the clear reread.  A plan
        # edit must not be enough to move the source-bound trigger onto it.
        windows[0]["triggers"][0]["line_box"] = [317, 807, 560, 834]
        self.assertEqual(scoped_observations([base_row()], [clear_row()], windows), [])

    def test_same_context_wrong_recipient_does_not_pass_geometry_alone(self):
        windows = plan([base_row()], [owner()], 200000)
        wrong = clear_row()
        wrong_text = "Friendship with Symboli Rudolf went up by 5."
        wrong["effects"] = [dict(
            kind="friendship_change", name="Symboli Rudolf", amount=5,
            raw_text=wrong_text, confidence=99,
        )]
        wrong["ocr"]["neural"][1]["text"] = wrong_text
        self.assertEqual(scoped_observations([base_row()], [wrong], windows), [])

    def test_stable_owner_interval_survives_reassembly_id_renumbering(self):
        windows = plan([base_row()], [owner(event_id="outcome-0001")], 200000)
        # A merged replay bundle can insert a validated supplement before the
        # original event, changing its sequential display id.  The trigger's
        # source interval and context remain the ownership boundary.
        windows[0]["triggers"][0]["owner_ref"] = "outcome-0042"
        promoted = scoped_observations([base_row()], [clear_row()], windows)
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["facts"]["occluded_receipt_recovery"]["owner_basis"],
                         "outcome_event")

    def test_replay_plan_rejects_expanded_window_and_missing_source_binding(self):
        windows = plan([base_row()], [owner()], 200000)
        expanded = dict(windows[0], start_ms=0, end_ms=50000)
        with self.assertRaises(ValueError):
            _validate_replay_windows([expanded], "processed_windows")
        missing_binding = json.loads(json.dumps(windows))
        missing_binding[0]["triggers"][0].pop("source_line_text_sha256")
        with self.assertRaises(ValueError):
            _validate_replay_windows(missing_binding, "processed_windows")

    def test_cache_manifest_rejects_oversized_window_before_file_access(self):
        from tests.test_gameplay import workspace_temp

        with workspace_temp() as root:
            inspection = {
                "source_sha256": "a" * 64,
                "windows": [{
                    "start_ms": 0,
                    "end_ms": 50_000,
                    "fps": 16,
                    "reason": "source review",
                }],
                "readings": [],
            }
            with self.assertRaises(OccludedReceiptRecoveryError):
                _validate_inspection_cache(
                    inspection,
                    root,
                    "a" * 64,
                    60_000,
                )

    def test_duplicate_neural_variants_share_one_physical_source(self):
        clear = clear_row()
        clear["ocr"]["neural"].append(
            dict(text="Friendship with Air Groove went up by 7.", confidence=96,
                 box=[316, 830, 707, 861])
        )
        promoted = scoped_observations(
            [base_row()], [clear], plan([base_row()], [owner()], 200000)
        )
        self.assertEqual(len(promoted), 1)
        self.assertEqual(
            [(effect.get("name"), effect.get("amount")) for effect in promoted[0]["effects"]],
            [("Air Groove", 7)],
        )

    def test_pre_occlusion_confidence_cannot_certify_a_clear_reread(self):
        clear = clear_row()
        line = clear["ocr"]["neural"][1]
        line["confidence"] = 0
        line["pre_occlusion_confidence"] = 99
        self.assertEqual(
            scoped_observations(
                [base_row()], [clear], plan([base_row()], [owner()], 200000)
            ),
            [],
        )

    def test_ambiguous_owner_and_duplicate_base_timestamp_abstain(self):
        windows = plan(
            [base_row()],
            [owner(event_id="outcome-1"), owner(event_id="outcome-2")],
            200000,
        )
        self.assertIsNone(windows[0]["triggers"][0]["owner_ref"])
        self.assertEqual(scoped_observations([base_row()], [clear_row()], windows), [])

        duplicate = base_row()
        duplicate["evidence"] = "duplicate.png"
        clear = clear_row(timestamp=155750)
        self.assertEqual(
            scoped_observations([base_row(), duplicate], [clear],
                                plan([base_row()], [owner()], 200000)),
            [],
        )

    def test_unproven_or_malformed_overlay_does_not_create_work(self):
        row = base_row()
        row["facts"]["occluded_receipt_lines"][0]["overlay_boxes"] = [[0, 0, 10, 10]]
        self.assertEqual(plan([row], [owner()], 200000), [])
        row = base_row()
        row["facts"]["occluded_receipt_lines"][0]["box"] = [314, 828, 709, float("nan")]
        self.assertEqual(plan([row], [owner()], 200000), [])

    def test_existing_effect_suppresses_duplicate_resampling(self):
        row = base_row()
        row["effects"].append(dict(
            kind="friendship_change", name="Air Groove", amount=7,
            raw_text="Friendship with Air Groove went up by 7.", confidence=98,
        ))
        self.assertEqual(plan([row], [owner()], 200000), [])

    def test_nearby_clear_effect_on_same_source_line_suppresses_recovery_duplicate(self):
        blocked = base_row()
        already_clear = dict(
            source_timestamp_ms=155500,
            screen="event_outcome",
            context_title="Incline",
            evidence="already-clear.png",
            stats={},
            effects=[dict(
                kind="friendship_change", name="Air Groove", amount=7,
                raw_text="Friendship with Air Groove went up by 7.", confidence=98,
            )],
            facts={},
            ocr={"neural": [dict(
                text="Friendship with Air Groove went up by 7.", confidence=98,
                box=[315, 829, 708, 860],
            )]},
        )
        self.assertEqual(
            scoped_observations(
                [blocked, already_clear], [clear_row()],
                plan([blocked], [owner()], 200000),
            ),
            [],
        )

    def test_clean_adjacent_identity_resolves_obstructed_name_on_normal_route(self):
        blocked = identity_base_row()
        clear = identity_clear_row()
        windows = plan([blocked], [identity_owner()], 300000)
        promoted = scoped_observations([blocked], [clear], windows)
        self.assertEqual(len(promoted), 1)
        adjacent = [
            effect for effect in promoted[0]["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(len(adjacent), 1)
        proof = adjacent[0].get("source_bound_identity_proof")
        self.assertEqual(proof["basis"], "source_bound_clean_adjacent_receipt_line")
        self.assertEqual(proof["amount"], 5)
        self.assertEqual(proof["line_box"], [315, 829, 747, 861])
        rows = merge([blocked], promoted)
        event = outcome_events(rows)[0]
        friendships = [
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual([effect["name"] for effect in friendships], ["Symboli Rudolf"])
        self.assertNotIn("Symboli udolf", {effect.get("name") for effect in friendships})
        self.assertEqual(
            event["resolved_identity_readings"][0]["basis"],
            "source_bound_clean_same_occurrence",
        )

    def test_source_bound_anchor_overlay_fallback_is_restricted_and_re_id_stable(self):
        """The no-alignment legacy path still requires source-bound proof."""

        def build():
            blocked = identity_base_row()
            blocked["facts"]["receipt_overlay_evidence"]["alignments"] = []
            clear = identity_clear_row()
            promoted = scoped_observations(
                [blocked], [clear], plan([blocked], [identity_owner()], 300000)
            )
            self.assertEqual(len(promoted), 1)
            return blocked, promoted

        blocked, promoted = build()
        friendship = next(
            effect for effect in promoted[0]["effects"]
            if effect.get("kind") == "friendship_change"
        )
        proof = friendship["source_bound_identity_proof"]
        event = outcome_events(merge([blocked], promoted))[0]
        friendships = [
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(
            [(effect.get("name"), effect.get("amount")) for effect in friendships],
            [("Symboli Rudolf", 5)],
        )
        # Event IDs may be regenerated by assembly; the proof is tied to the
        # source owner interval and receipt evidence instead of this ID.
        self.assertNotEqual(event["id"], proof["owner_ref"])
        self.assertEqual(
            event["resolved_identity_readings"][0]["basis"],
            "source_bound_clean_same_occurrence",
        )

        blocked, promoted = build()
        promoted[0]["source_sha256"] = "0" * 64
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(event.get("resolved_identity_readings"))
        self.assertFalse([
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ])

        blocked, promoted = build()
        promoted[0]["facts"]["occluded_receipt_recovery"]["trigger_sources"][0][
            "owner_ref"
        ] = "forged-owner"
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(event.get("resolved_identity_readings"))
        self.assertFalse([
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ])

        blocked, promoted = build()
        blocked["facts"]["occluded_receipt_lines"][0]["overlay_boxes"] = [
            [600, 900, 614, 918]
        ]
        blocked["facts"]["occluded_receipt_lines"][0]["animated_overlay_boxes"] = [
            [600, 900, 614, 918]
        ]
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(event.get("resolved_identity_readings"))
        self.assertFalse([
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ])

    def test_non_recipient_overlay_cannot_replace_an_ordinary_clean_identity(self):
        # The trigger is visibly obstructed, but these overlays cover only a
        # neighboring hint line, the receipt prefix, or the amount.  The
        # existing friendship line is therefore a competing clean identity
        # and must remain quarantined with the reread.
        for label, overlay in (
            ("neighboring_hint", [549, 810, 563, 825]),
            ("prefix", [330, 844, 342, 859]),
            ("amount", [729, 844, 741, 859]),
        ):
            with self.subTest(label=label):
                blocked = identity_base_row(name="Beta Support")
                blocked["facts"]["occluded_receipt_lines"][0]["overlay_boxes"] = [overlay]
                blocked["facts"]["occluded_receipt_lines"][0]["animated_overlay_boxes"] = [overlay]
                blocked["ocr"]["neural"][0].update(
                    overlay_occluded=False,
                    overlay_boxes=[],
                )
                clear = identity_clear_row(name="Alpha Support")
                promoted = scoped_observations(
                    [blocked], [clear], plan([blocked], [identity_owner()], 300000)
                )
                self.assertEqual(len(promoted), 1)
                event = outcome_events(merge([blocked], promoted))[0]
                friendships = [
                    effect for effect in event["effects"]
                    if effect.get("kind") == "friendship_change"
                ]
                self.assertEqual(friendships, [])
                self.assertEqual(
                    {candidate["effect"]["name"]
                     for candidate in event["ambiguous_effect_candidates"]},
                    {"Alpha Support", "Beta Support"},
                )
                self.assertFalse(event.get("resolved_identity_readings"))

    def test_clean_adjacent_identity_survives_full_assemble_route(self):
        from unittest.mock import patch
        from tests.test_turn_ledger import report as report_fixture
        from tracen_replay.full_recording import assemble

        blocked = identity_base_row()
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        report = report_fixture()
        report["source"] = {"sha256": "a" * 64, "duration_ms": 300000}
        with patch("tracen_replay.full_recording.audit", return_value={}), \
                patch("tracen_replay.turn_ledger.build", return_value={}), \
                patch("tracen_replay.causal_accounting.build", return_value={}):
            assembled = assemble(report, merge([blocked], promoted))
        events = assembled["gameplay_tracking"]["events"]
        friendships = [
            effect for event in events for effect in event.get("effects", [])
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual([effect["name"] for effect in friendships], ["Symboli Rudolf"])
        self.assertNotIn("Symboli udolf", {
            effect.get("name") for effect in friendships
        })

    def test_adjacent_unknown_anchor_vetoes_known_owner(self):
        row = {"context_title": "A Hint for Growth"}
        known = {
            "source_timestamp_ms": 1000,
            "evidence": "known.png",
            "line_box": [316, 805, 768, 833],
            "owner_ref": "owner-one",
            "owner_start_ms": 1000,
            "owner_end_ms": 1000,
            "owner_context_title": "A Hint for Growth",
        }
        # This trigger is geometrically selected but has no recoverable owner
        # identity.  It must not be filtered out while the known trigger is
        # retained, because that would silently attribute the clear row to
        # owner-one.
        unknown = {
            "source_timestamp_ms": 1200,
            "evidence": "unknown.png",
            "line_box": [316, 805, 768, 833],
        }
        self.assertEqual(_adjacent_owner(row, [known, unknown]), (None, []))

    def test_forged_identity_owner_is_rejected_on_normal_event_route(self):
        blocked = identity_base_row()
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        friendship = next(
            effect for effect in promoted[0]["effects"]
            if effect.get("kind") == "friendship_change"
        )
        friendship["source_bound_identity_proof"]["owner_ref"] = "forged-owner"
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(event.get("resolved_identity_readings"))
        self.assertFalse([
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ])

    def test_mutated_recovery_owner_cannot_certify_identity_proof(self):
        blocked = identity_base_row()
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        promoted[0]["facts"]["occluded_receipt_recovery"]["requested_owner_ref"] = (
            "forged-owner"
        )
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(event.get("resolved_identity_readings"))
        self.assertFalse([
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ])

    def test_mutated_trigger_owner_cannot_certify_identity_proof(self):
        blocked = identity_base_row()
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        promoted[0]["facts"]["occluded_receipt_recovery"]["trigger_sources"][0][
            "owner_ref"
        ] = "forged-owner"
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(event.get("resolved_identity_readings"))
        self.assertFalse([
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ])

    def test_identity_proof_requires_event_field_evidence(self):
        from tracen_replay.receipt_names import _validated_source_identity_proof

        blocked = identity_base_row()
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        rows = merge([blocked], promoted)
        event = outcome_events(rows)[0]
        friendship = next(
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
            and effect.get("name") == "Symboli Rudolf"
        )
        field = "friendship_change||Symboli Rudolf"
        event["field_evidence"][field].remove(friendship["source_bound_identity_proof"]["evidence"])
        rows_by_evidence = {row["evidence"]: row for row in rows}
        self.assertIsNone(
            _validated_source_identity_proof(friendship, event, rows_by_evidence)
        )

    def test_zero_alignment_confidence_cannot_prove_recipient_obstruction(self):
        blocked = identity_base_row()
        blocked["facts"]["receipt_overlay_evidence"]["alignments"][0]["confidence"] = 0
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        event = outcome_events(merge([blocked], promoted))[0]
        friendships = [
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(friendships, [])
        self.assertFalse(event.get("resolved_identity_readings"))

    def test_competing_clean_adjacent_names_remain_ambiguous(self):
        blocked = identity_base_row()
        clear = identity_clear_row(name="Alpha Support", second_name="Beta Support")
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        self.assertEqual(len(promoted), 1)
        proof_names = {
            effect["name"] for effect in promoted[0]["effects"]
            if effect.get("source_bound_identity_proof")
        }
        self.assertEqual(proof_names, {"Alpha Support", "Beta Support"})
        event = outcome_events(merge([blocked], promoted))[0]
        friendships = [
            effect for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(friendships, [])
        self.assertEqual(
            {candidate["effect"]["name"] for candidate in event["ambiguous_effect_candidates"]},
            {"Alpha Support", "Beta Support", "Symboli udolf"},
        )
        self.assertFalse(event.get("resolved_identity_readings"))

    def test_ownerless_adjacent_identity_does_not_become_a_named_effect(self):
        blocked = identity_base_row()
        clear = identity_clear_row()
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [], 300000)
        )
        self.assertEqual(len(promoted), 1)
        self.assertFalse(any(
            effect.get("source_bound_identity_proof")
            for effect in promoted[0]["effects"]
        ))
        event = outcome_events(merge([blocked], promoted))[0]
        self.assertFalse(any(
            effect.get("name") == "Symboli Rudolf"
            for effect in event["effects"]
            if effect.get("kind") == "friendship_change"
        ))
        self.assertFalse(event.get("resolved_identity_readings"))

    def test_adjacent_identity_with_different_context_is_rejected(self):
        blocked = identity_base_row()
        clear = identity_clear_row(context_title="Different Event")
        promoted = scoped_observations(
            [blocked], [clear], plan([blocked], [identity_owner()], 300000)
        )
        self.assertEqual(promoted, [])

    def test_reparse_mode_keeps_unprocessed_windows_without_starting_ocr(self):
        from tests.test_gameplay import workspace_temp
        from unittest.mock import patch

        row = base_row()
        row["source_timestamp_ms"] = 1000
        row["facts"]["occluded_receipt_lines"][0]["box"] = [314, 828, 709, 863]
        event = owner(start=1000, end=1000)
        with workspace_temp() as root, patch(
            "tracen_replay.vision.NeuralReader",
            side_effect=AssertionError("unexpected OCR"),
        ):
            readings, metadata = recover(
                "missing-recording.mp4",
                root,
                {"sha256": "a" * 64, "duration_ms": 2000},
                [row],
                [event],
                allow_ocr=False,
                max_windows=0,
            )
        self.assertEqual(readings, [row])
        self.assertEqual(len(metadata["requested_windows"]), 1)
        self.assertEqual(metadata["processed_windows"], [])
        self.assertEqual(len(metadata["pending_windows"]), 1)

    def test_recover_rejects_unbound_source_identity(self):
        with self.assertRaises(OccludedReceiptRecoveryError):
            recover(
                "missing-recording.mp4", ".", {"sha256": "wrong", "duration_ms": 2000},
                [], [], allow_ocr=False,
            )

    def test_interrupted_inspection_reuses_only_recomputed_source_windows_without_ocr(self):
        from tests.test_gameplay import workspace_temp
        from unittest.mock import patch

        source = {'sha256': 'a' * 64, 'duration_ms': 200000}
        requests = plan([base_row()], [owner()], source['duration_ms'])
        for unexpected_window in (False, True):
            with self.subTest(unexpected_window=unexpected_window), workspace_temp() as root:
                folder = root / 'occluded-receipt-recovery'
                folder.mkdir()
                window = {key: requests[0][key] for key in ('start_ms', 'end_ms', 'reason')} | {'fps': 16}
                if unexpected_window:
                    window['start_ms'] += 1
                inspection = {'source_sha256': source['sha256'], 'windows': [window], 'readings': [clear_row()]}
                (folder / 'receipt-inspection.json').write_text(json.dumps(inspection), encoding='utf-8')
                # Isolate interruption handling from the separately tested
                # source-pixel verifier. Both branches have a valid cache;
                # only one was requested by the current source observations.
                with patch('tracen_replay.occluded_receipt_recovery._validate_inspection_cache',
                           return_value=({'fixture': {'model.onnx': 'b' * 64}}, [window])), \
                        patch('tracen_replay.inspect_training.reparse_inspection', return_value=[clear_row()]), \
                        patch('tracen_replay.vision.NeuralReader', side_effect=AssertionError('unexpected OCR')):
                    if unexpected_window:
                        with self.assertRaisesRegex(OccludedReceiptRecoveryError, 'outside the current source plan'):
                            recover('source.mp4', root, source, [base_row()], [owner()], allow_ocr=False)
                        self.assertFalse((folder / 'last-plan.json').exists())
                    else:
                        rows, metadata = recover('source.mp4', root, source, [base_row()], [owner()], allow_ocr=False)
                        self.assertTrue(metadata['resumed_unfinished_inspection'])
                        self.assertEqual(len(metadata['processed_windows']), 1)
                        self.assertEqual(metadata['pending_windows'], [])
                        self.assertEqual(metadata['new_frames'], 0)
                        self.assertTrue((folder / 'last-plan.json').is_file())
                        self.assertTrue(any(row['source_timestamp_ms'] == 155800 for row in rows))

    @unittest.skipUnless(
        SOURCE_FRAME.is_file() and any(MODEL_DIR.glob("*.onnx")),
        "The diagnostic source frame or OCR model is not available",
    )
    def test_actual_clear_source_frame_recovers_the_reported_friendship_gap(self):
        reader = NeuralReader(MODEL_DIR)
        with Image.open(SOURCE_FRAME) as image:
            pane = image.convert("RGB").crop((148, 0, 958, 1080))
            raw = reader.read(pane)
        raw.update(
            source_timestamp_ms=155800,
            source_frame_sha256=hashlib.sha256(SOURCE_FRAME.read_bytes()).hexdigest(),
            source_sha256="source-placeholder",
            evidence="friendship-155750-source/000016.png",
        )
        parsed = parse(annotate(raw, pane))
        clear = dict(parsed, source_timestamp_ms=155800,
                     evidence="friendship-155750-source/000016.png")
        effects = [
            effect for effect in parsed["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(
            [(effect.get("name"), effect.get("amount")) for effect in effects],
            [("Air Groove", 7)],
        )
        promoted = scoped_observations(
            [base_row()], [clear], plan([base_row()], [owner()], 200000)
        )
        self.assertEqual(
            [(effect.get("kind"), effect.get("name"), effect.get("amount"))
             for effect in promoted[0]["effects"]],
            [("friendship_change", "Air Groove", 7)],
        )


if __name__ == "__main__":
    unittest.main()
