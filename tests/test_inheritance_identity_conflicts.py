"""Source-backed ambiguity tests for inheritance inspiration names."""

import copy
import unittest

from tracen_replay.receipt_names import flag_inheritance_identity_conflicts


def ocr_line(text, box, confidence=99):
    return dict(text=text, box=list(box), confidence=confidence)


def inspiration(name, *, field=None):
    return dict(
        kind="inheritance_inspiration",
        field=field,
        name=name,
        amount=None,
        direction=None,
        value=None,
        raw_text=f"Inspired by {name}!",
        confidence=99,
    )


def source_row(timestamp, evidence, target_text, target_box, *, anchors=(),
               screen="event_outcome", title=None, target_confidence=99):
    lines = [ocr_line(target_text, target_box, target_confidence)]
    lines.extend(ocr_line(text, box, confidence) for text, box, confidence in anchors)
    return dict(
        source_timestamp_ms=timestamp,
        evidence=evidence,
        screen=screen,
        context_title=title,
        context_title_candidate=None,
        ocr=dict(neural=lines),
    )


def event_for(effects, proofs):
    return dict(
        effects=effects,
        field_evidence={
            f"inheritance_inspiration||{effect['name']}": [proofs[effect['name']]]
            for effect in effects
        },
        conflicting_readings=[],
    )


class InheritanceIdentityConflictTests(unittest.TestCase):
    def stationary_case(self, *, right_timestamp=1250, right_box=None,
                        right_screen="event_outcome", right_title=None,
                        right_confidence=99, right_name="Seiun S"):
        left = inspiration("Seiun Sky")
        right = inspiration(right_name)
        right_box = right_box or [316, 804, 552, 836]
        rows = {
            "clear.png": source_row(
                1000, "clear.png", left["raw_text"], [317, 806, 532, 836]
            ),
            "variant.png": source_row(
                right_timestamp, "variant.png", right["raw_text"], right_box,
                screen=right_screen, title=right_title,
                target_confidence=right_confidence,
            ),
        }
        return [left, right], rows

    def test_stationary_same_slot_remains_unclassified(self):
        effects, rows = self.stationary_case()
        event = event_for(effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"})
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)

        self.assertEqual(event, before)

    def test_upward_scroll_requires_a_unique_exact_anchor(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99.91),),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(("Power spark activated!", [317, 879, 538, 905], 97.02),),
            ),
        }
        event = event_for([left, right], proofs)
        flag_inheritance_identity_conflicts(event, rows)

        self.assertEqual(event["effects"], [])
        self.assertEqual(event["conflicting_readings"][0]["continuity"]["mode"], "upward_scroll")
        self.assertEqual(event["conflicting_readings"][0]["continuity"]["anchor"]["text"],
                         "Power spark activated!")

    def test_multiple_consistent_unique_anchors_are_retained(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(
                    ("Power spark activated!", [317, 908, 538, 934], 99),
                    ("Stamina spark activated!", [317, 936, 538, 962], 99),
                ),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(
                    ("Power spark activated!", [317, 879, 538, 905], 99),
                    ("Stamina spark activated!", [317, 907, 538, 933], 99),
                ),
            ),
        }
        event = event_for([left, right], proofs)
        flag_inheritance_identity_conflicts(event, rows)

        continuity = event["conflicting_readings"][0]["continuity"]
        self.assertEqual(
            [anchor["text"] for anchor in continuity["anchors"]],
            ["Power spark activated!", "Stamina spark activated!"],
        )

    def test_contradictory_moving_anchor_vetoes_matching_anchor(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(
                    ("Power spark activated!", [317, 908, 538, 934], 99),
                    ("Stamina spark activated!", [317, 936, 538, 962], 99),
                ),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(
                    ("Power spark activated!", [317, 879, 538, 905], 99),
                    # This exact shared line moves by a different amount, so
                    # it cannot be silently ignored as a second anchor.
                    ("Stamina spark activated!", [317, 890, 538, 916], 99),
                ),
            ),
        }
        event = event_for([left, right], proofs)
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_scroll_to_different_slot_is_not_a_track(self):
        effects, rows = self.stationary_case(
            right_box=[316, 804, 700, 836]
        )
        # The names occupy different horizontal slots even though their
        # vertical centers happen to be close.
        event = event_for(effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"})
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_moving_target_geometry_must_stay_stable(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99),),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 700, 836],
                anchors=(("Power spark activated!", [317, 879, 538, 905], 99),),
            ),
        }
        event = event_for([left, right], proofs)
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_stationary_slot_with_scrolling_anchor_is_not_a_track(self):
        effects, rows = self.stationary_case()
        rows["clear.png"]["ocr"]["neural"].append(
            ocr_line("Power spark activated!", [317, 850, 538, 876])
        )
        rows["variant.png"]["ocr"]["neural"].append(
            ocr_line("Power spark activated!", [317, 826, 538, 852])
        )
        event = event_for(effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"})
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_neighboring_weak_inspiration_line_blocks_generic_anchor(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99),),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(("Power spark activated!", [317, 879, 538, 905], 99),),
            ),
        }
        rows["bourbon.png"]["ocr"]["neural"].append(
            ocr_line("Inspired by Maruzensky!", [316, 839, 597, 871], 94)
        )
        event = event_for([left, right], proofs)
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_upward_scroll_without_anchor_is_not_a_track(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863]
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836]
            ),
        }
        event = event_for([left, right], proofs)
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_anchor_motion_must_match_target_and_relative_slot(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99),),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(("Power spark activated!", [317, 893, 538, 919], 99),),
            ),
        }
        event = event_for([left, right], proofs)
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_duplicate_anchor_is_not_unique(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        proofs = {left["name"]: "sourbon.png", right["name"]: "bourbon.png"}
        anchor_first = ("Power spark activated!", [317, 908, 538, 934], 99)
        anchor_second = ("Power spark activated!", [317, 879, 538, 905], 99)
        rows = {
            "sourbon.png": source_row(
                1100500, "sourbon.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(anchor_first, anchor_first),
            ),
            "bourbon.png": source_row(
                1100750, "bourbon.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(anchor_second, anchor_second),
            ),
        }
        event = event_for([left, right], proofs)
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_missing_frame_or_boundary_abstains(self):
        mutations = ("gap", "screen", "title", "low_confidence")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                effects, rows = self.stationary_case(
                    right_timestamp=1500 if mutation == "gap" else 1250,
                    right_screen="training_preview" if mutation == "screen" else "event_outcome",
                    right_title="Different receipt" if mutation == "title" else None,
                    right_confidence=94 if mutation == "low_confidence" else 99,
                )
                event = event_for(
                    effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"}
                )
                before = copy.deepcopy(event)
                flag_inheritance_identity_conflicts(event, rows)
                self.assertEqual(event, before)

    def test_simultaneously_visible_names_remain_distinct(self):
        left = inspiration("Seiun Sky")
        right = inspiration("Mejiro Ryan")
        proof = "both.png"
        rows = {
            proof: source_row(
                1000, proof, left["raw_text"], [317, 806, 532, 836],
                anchors=((right["raw_text"], [318, 879, 553, 906], 99),),
            )
        }
        event = event_for([left, right], {left["name"]: proof, right["name"]: proof})
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_simultaneous_names_in_any_linked_observation_block_pair(self):
        effects, rows = self.stationary_case()
        rows["both.png"] = source_row(
            1500, "both.png", effects[0]["raw_text"], [317, 806, 532, 836],
            anchors=((effects[1]["raw_text"], [318, 879, 553, 906], 99),),
        )
        event = event_for(effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"})
        event["field_evidence"][f"inheritance_inspiration||{effects[0]['name']}"].append("both.png")
        event["field_evidence"][f"inheritance_inspiration||{effects[1]['name']}"].append("both.png")
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_existing_non_name_candidate_does_not_crash_or_get_rewritten(self):
        effects, rows = self.stationary_case()
        rows["clear.png"]["ocr"]["neural"][0]["box"] = [317, 836, 596, 863]
        rows["variant.png"]["ocr"]["neural"][0]["box"] = [316, 804, 597, 836]
        rows["clear.png"]["ocr"]["neural"].append(
            ocr_line("Power spark activated!", [317, 908, 538, 934])
        )
        rows["variant.png"]["ocr"]["neural"].append(
            ocr_line("Power spark activated!", [317, 879, 538, 905])
        )
        event = event_for(effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"})
        numeric = dict(effect=dict(kind="stat_change", field="power", amount=5), reason="other")
        event["ambiguous_effect_candidates"] = [numeric]
        flag_inheritance_identity_conflicts(event, rows)
        self.assertIs(event["ambiguous_effect_candidates"][0], numeric)
        self.assertEqual(
            {candidate["effect"]["name"] for candidate in event["ambiguous_effect_candidates"][1:]},
            {"Seiun Sky", "Seiun S"},
        )

    def test_contiguous_pair_windows_reinforce_one_track(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        rows = {
            "a0.png": source_row(
                1000, "a0.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99),),
            ),
            "b0.png": source_row(
                1250, "b0.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(("Power spark activated!", [317, 879, 538, 905], 99),),
            ),
            "a1.png": source_row(
                1500, "a1.png", left["raw_text"], [316, 772, 597, 804],
                anchors=(("Power spark activated!", [317, 847, 538, 873], 99),),
            ),
        }
        event = event_for([left, right], {left["name"]: "a0.png", right["name"]: "b0.png"})
        event["field_evidence"][f"inheritance_inspiration||{left['name']}"] = [
            "a0.png", "a1.png"
        ]
        flag_inheritance_identity_conflicts(event, rows)

        self.assertEqual(event["effects"], [])
        continuity = event["conflicting_readings"][0]["continuity"]
        self.assertEqual(continuity["evidence_pairs"], [
            ["a0.png", "b0.png"], ["b0.png", "a1.png"]
        ])

    def test_disconnected_pair_windows_do_not_merge(self):
        left = inspiration("Mihono Sourbon")
        right = inspiration("Mihono Bourbon")
        rows = {
            "a0.png": source_row(
                1000, "a0.png", left["raw_text"], [318, 836, 596, 863],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99),),
            ),
            "b0.png": source_row(
                1250, "b0.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(("Power spark activated!", [317, 879, 538, 905], 99),),
            ),
            "a1.png": source_row(
                2000, "a1.png", left["raw_text"], [316, 836, 597, 868],
                anchors=(("Power spark activated!", [317, 908, 538, 934], 99),),
            ),
            "b1.png": source_row(
                2250, "b1.png", right["raw_text"], [316, 804, 597, 836],
                anchors=(("Power spark activated!", [317, 879, 538, 905], 99),),
            ),
        }
        event = event_for([left, right], {left["name"]: "a0.png", right["name"]: "b0.png"})
        event["field_evidence"][f"inheritance_inspiration||{left['name']}"] = [
            "a0.png", "a1.png"
        ]
        event["field_evidence"][f"inheritance_inspiration||{right['name']}"] = [
            "b0.png", "b1.png"
        ]
        before = copy.deepcopy(event)
        flag_inheritance_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_different_payload_and_unaccepted_observation_are_untouched(self):
        for mutation in ("payload", "unaccepted"):
            with self.subTest(mutation=mutation):
                effects, rows = self.stationary_case(
                    right_confidence=94 if mutation == "unaccepted" else 99
                )
                if mutation == "payload":
                    effects[1]["value"] = "different"
                event = event_for(
                    effects, {effects[0]["name"]: "clear.png", effects[1]["name"]: "variant.png"}
                )
                before = copy.deepcopy(event)
                flag_inheritance_identity_conflicts(event, rows)
                self.assertEqual(event, before)


if __name__ == "__main__":
    unittest.main()
