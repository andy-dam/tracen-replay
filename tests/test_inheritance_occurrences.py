import copy
import math
import unittest

from tracen_replay.inheritance_occurrences import summarize


def effect(kind="inheritance_spark", name="Stamina", **values):
    raw = (
        f"{name} spark activated!"
        if kind == "inheritance_spark"
        else f"Inspired by {name}!"
    )
    result = dict(
        kind=kind,
        field=None,
        name=name,
        amount=None,
        direction=None,
        value=None,
        raw_text=raw,
        confidence=99,
    )
    result.update(values)
    return result


def line(text="Stamina spark activated!", box=(315, 800, 555, 830), confidence=99):
    return {"text": text, "box": list(box), "confidence": confidence}


def row(timestamp, evidence, lines, *, row_effects=None, screen="event_outcome"):
    result = {
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "screen": screen,
        "ocr": {"neural": list(lines)},
    }
    if row_effects is not None:
        result["effects"] = copy.deepcopy(row_effects)
    return result


def key(item):
    return f"{item['kind']}||{item.get('name', '')}"


def event_for(effects, proofs):
    return {
        "id": "outcome-1",
        "first_seen_ms": 0,
        "last_seen_ms": 10_000,
        "effects": effects,
        "field_evidence": {
            key(item): list(paths) for item, paths in zip(effects, proofs)
        },
    }


class InheritanceOccurrenceTests(unittest.TestCase):
    def test_maximum_simultaneous_disjoint_lines_is_preserved(self):
        stamina = effect()
        rows = {
            "a.png": row(
                1000,
                "a.png",
                [
                    line(box=(315, 800, 555, 830)),
                    line(box=(315, 900, 555, 930)),
                    line(box=(316, 802, 556, 832)),  # duplicate OCR channel
                ],
                row_effects=[stamina],
            ),
            "b.png": row(
                1250,
                "b.png",
                [line(box=(315, 805, 555, 835))],
                row_effects=[stamina],
            ),
        }

        result = summarize(event_for([stamina], [["a.png", "b.png"]]), rows)
        item = result["by_key"]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 2)
        self.assertEqual(len(item["observations"]), 3)
        self.assertIsNone(item["total_count"])
        self.assertFalse(item["count_complete"])
        self.assertEqual(
            [(o["source_timestamp_ms"], o["line_index"]) for o in item["observations"]],
            [(1000, 0), (1000, 1), (1250, 0)],
        )

    def test_repeated_frames_do_not_sum_into_occurrence_count(self):
        stamina = effect()
        rows = {
            "one.png": row(1000, "one.png", [line()], row_effects=[stamina]),
            "two.png": row(1250, "two.png", [line()], row_effects=[stamina]),
            "three.png": row(1500, "three.png", [line()], row_effects=[stamina]),
        }

        item = summarize(event_for([stamina], [["one.png", "two.png", "three.png"]]), rows)[
            "by_key"
        ]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 1)
        self.assertEqual(len(item["observations"]), 3)
        self.assertIsNone(item["total_count"])

    def test_overlapping_same_frame_lines_are_deduplicated(self):
        stamina = effect()
        rows = {
            "overlay.png": row(
                1000,
                "overlay.png",
                [
                    line(box=(315, 800, 555, 830), confidence=96),
                    line(box=(316, 802, 556, 832), confidence=99),
                    line(box=(315, 900, 555, 930)),
                ],
                row_effects=[stamina],
            )
        }

        item = summarize(event_for([stamina], [["overlay.png"]]), rows)["by_key"][
            "inheritance_spark||Stamina"
        ]

        self.assertEqual(item["minimum_observed_count"], 2)
        self.assertEqual(len(item["observations"]), 2)
        self.assertEqual(item["observations"][0]["line_index"], 1)

    def test_partial_overlap_same_line_replicas_are_not_disjoint(self):
        stamina = effect()
        rows = {
            "partial.png": row(
                1000,
                "partial.png",
                [
                    line(box=(315, 800, 555, 830), confidence=99),
                    # 48% of the smaller box overlaps: enough to be the same
                    # line in a shifted crop, but below the old 50% heuristic.
                    line(box=(440, 800, 680, 830), confidence=98),
                ],
                row_effects=[stamina],
            )
        }

        item = summarize(event_for([stamina], [["partial.png"]]), rows)["by_key"][
            "inheritance_spark||Stamina"
        ]

        self.assertEqual(item["minimum_observed_count"], 1)
        self.assertEqual(len(item["observations"]), 1)

    def test_alternate_crops_at_one_timestamp_are_not_unioned(self):
        stamina = effect()
        rows = {
            "left-crop.png": row(
                1000,
                "left-crop.png",
                [line(box=(315, 800, 555, 830))],
                row_effects=[stamina],
            ),
            "right-crop.png": row(
                1000,
                "right-crop.png",
                [line(box=(600, 800, 840, 830))],
                row_effects=[stamina],
            ),
        }

        item = summarize(
            event_for([stamina], [["left-crop.png", "right-crop.png"]]), rows
        )["by_key"]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 1)
        self.assertEqual(len(item["observations"]), 1)
        self.assertEqual(len(item["frame_counts"]), 2)
        self.assertEqual(
            {(frame["evidence"], frame["minimum_observed_count"]) for frame in item["frame_counts"]},
            {("left-crop.png", 1), ("right-crop.png", 1)},
        )

    def test_static_cards_and_numeric_stat_caps_outside_allowed_receipt_are_ignored(self):
        stamina = effect()
        rows = {
            "card.png": row(
                1000,
                "card.png",
                [line(box=(315, 600, 555, 630))],
                row_effects=[stamina],
            ),
            "cap.png": row(
                1250,
                "cap.png",
                [line("Stamina cap went up by 7.", box=(315, 850, 555, 880))],
                row_effects=[stamina],
            ),
        }

        item = summarize(event_for([stamina], [["card.png", "cap.png"]]), rows)["by_key"][
            "inheritance_spark||Stamina"
        ]

        self.assertEqual(item["minimum_observed_count"], 0)
        self.assertEqual(item["observations"], [])

    def test_low_confidence_and_malformed_or_truncated_lines_stay_unknown(self):
        stamina = effect()
        rows = {
            "low.png": row(1000, "low.png", [line(confidence=94.99)], row_effects=[stamina]),
            "truncated.png": row(
                1250,
                "truncated.png",
                [line("Stamina spark activatd!")],
                row_effects=[stamina],
            ),
            "malformed.png": row(
                1500,
                "malformed.png",
                [line(box=[315, math.nan, 555, 830])],
                row_effects=[stamina],
            ),
        }

        item = summarize(
            event_for([stamina], [["low.png", "truncated.png", "malformed.png"]]),
            rows,
        )["by_key"]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 0)
        self.assertEqual(item["observations"], [])

    def test_evidence_path_and_row_evidence_must_bind_exactly(self):
        stamina = effect()
        rows = {
            "proof.png": row(
                1000,
                "different.png",
                [line()],
                row_effects=[stamina],
            ),
            "unlisted.png": row(
                1250,
                "unlisted.png",
                [line()],
                row_effects=[stamina],
            ),
        }

        item = summarize(
            event_for([stamina], [["proof.png", "missing.png"]]),
            rows,
        )["by_key"]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 0)
        self.assertEqual(item["observations"], [])

    def test_missing_row_effects_do_not_count_matching_ocr(self):
        stamina = effect()
        rows = {"source.png": row(1000, "source.png", [line()])}

        item = summarize(event_for([stamina], [["source.png"]]), rows)["by_key"][
            "inheritance_spark||Stamina"
        ]

        self.assertEqual(item["minimum_observed_count"], 0)
        self.assertEqual(item["observations"], [])

    def test_row_effect_without_exact_raw_text_does_not_count(self):
        stamina = effect()
        observed = dict(stamina)
        observed.pop("raw_text")
        rows = {
            "source.png": row(
                1000,
                "source.png",
                [line()],
                row_effects=[observed],
            )
        }

        item = summarize(event_for([stamina], [["source.png"]]), rows)["by_key"][
            "inheritance_spark||Stamina"
        ]

        self.assertEqual(item["minimum_observed_count"], 0)
        self.assertEqual(item["observations"], [])

    def test_duplicate_timestamp_and_evidence_are_not_new_lines(self):
        stamina = effect()
        rows = {
            "a.png": row(1000, "a.png", [line()], row_effects=[stamina]),
            "b.png": row(1000, "b.png", [line()], row_effects=[stamina]),
        }

        item = summarize(event_for([stamina], [["a.png", "a.png", "b.png"]]), rows)[
            "by_key"
        ]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 1)
        self.assertEqual(len(item["observations"]), 1)
        self.assertEqual(item["observations"][0]["evidence"], "a.png")

    def test_event_bounds_exclude_proofs_outside_outcome(self):
        stamina = effect()
        event = event_for(
            [stamina], [["before.png", "at-start.png", "at-end.png", "after.png"]]
        )
        event.update(first_seen_ms=1000, last_seen_ms=1250)
        rows = {
            "before.png": row(750, "before.png", [line()], row_effects=[stamina]),
            "at-start.png": row(1000, "at-start.png", [line()], row_effects=[stamina]),
            "at-end.png": row(1250, "at-end.png", [line()], row_effects=[stamina]),
            "after.png": row(1500, "after.png", [line()], row_effects=[stamina]),
        }

        item = summarize(event, rows)["by_key"]["inheritance_spark||Stamina"]

        self.assertEqual(item["minimum_observed_count"], 1)
        self.assertEqual(
            {observation["source_timestamp_ms"] for observation in item["observations"]},
            {1000, 1250},
        )

    def test_conflicting_payload_variants_remain_uncertain(self):
        first = effect(amount=1)
        second = effect(amount=2)
        rows = {"source.png": row(1000, "source.png", [line()], row_effects=[first, second])}

        item = summarize(
            event_for([first, second], [["source.png"], ["source.png"]]),
            rows,
        )["by_key"]["inheritance_spark||Stamina"]

        self.assertTrue(item["uncertain"])
        self.assertTrue(item["conflicting_payloads"])
        self.assertEqual(len(item["variants"]), 2)
        self.assertEqual(
            {variant["payload"]["amount"] for variant in item["variants"]},
            {1, 2},
        )
        self.assertTrue(all(variant["minimum_observed_count"] == 1 for variant in item["variants"]))

    def test_retained_payload_with_identity_or_value_conflict_stays_uncertain(self):
        stamina = effect()
        event = event_for([stamina], [["source.png"]])
        event["conflicting_readings"] = [
            {
                "field": "inheritance_spark||Stamina",
                "reason": "changing_effect_value",
            }
        ]
        rows = {
            "source.png": row(1000, "source.png", [line()], row_effects=[stamina])
        }

        item = summarize(event, rows)["by_key"]["inheritance_spark||Stamina"]

        self.assertTrue(item["uncertain"])
        self.assertIn("changing_effect_value", item["uncertainty_reasons"])

    def test_occupancy_conflict_alone_does_not_mark_identity_uncertain(self):
        stamina = effect()
        event = event_for([stamina], [["source.png"]])
        event["conflicting_readings"] = [
            {
                "field": "inheritance_spark||Stamina",
                "reason": "multiple_same_field_lines",
            }
        ]
        rows = {
            "source.png": row(1000, "source.png", [line()], row_effects=[stamina])
        }

        item = summarize(event, rows)["by_key"]["inheritance_spark||Stamina"]

        self.assertFalse(item["uncertain"])

    def test_inspiration_is_supported_without_using_a_name_catalog(self):
        inspiration = effect(
            kind="inheritance_inspiration",
            name="Uncatalogued Parent",
        )
        rows = {
            "inspiration.png": row(
                1000,
                "inspiration.png",
                [line("Inspired by Uncatalogued Parent!")],
                row_effects=[inspiration],
            )
        }

        item = summarize(event_for([inspiration], [["inspiration.png"]]), rows)["by_key"][
            "inheritance_inspiration||Uncatalogued Parent"
        ]

        self.assertEqual(item["minimum_observed_count"], 1)
        self.assertEqual(item["observations"][0]["payload"]["name"], "Uncatalogued Parent")

    def test_non_inheritance_effects_are_not_summarized(self):
        stat = dict(
            kind="stat_change",
            field="stamina",
            amount=7,
            raw_text="Stamina went up by 7.",
        )
        rows = {
            "stat.png": row(
                1000,
                "stat.png",
                [line("Stamina went up by 7.")],
                row_effects=[stat],
            )
        }

        result = summarize(event_for([stat], [["stat.png"]]), rows)

        self.assertEqual(result["by_key"], {})

    def test_result_is_pure_and_missing_inputs_are_safe(self):
        stamina = effect()
        event = event_for([stamina], [["source.png"]])
        rows = {"source.png": row(1000, "source.png", [line()], row_effects=[stamina])}
        original_event = copy.deepcopy(event)
        original_rows = copy.deepcopy(rows)

        summarize(event, rows)

        self.assertEqual(event, original_event)
        self.assertEqual(rows, original_rows)
        self.assertEqual(summarize(None, None)["by_key"], {})


if __name__ == "__main__":
    unittest.main()
