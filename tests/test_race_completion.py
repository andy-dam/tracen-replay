import copy
import unittest

from tracen_replay.race_completion import annotate


def line(text="1st", confidence=99.5, box=(420, 570, 680, 780)):
    return {"text": text, "confidence": confidence, "box": list(box)}


def reading(timestamp, *, evidence=None, lines=None, screen="unknown", image=None):
    raw = {"neural": lines if lines is not None else [line()]}
    result = {
        "source_timestamp_ms": timestamp,
        "evidence": evidence or f"gameplay/{timestamp}.png",
        "screen": screen,
        "ocr": raw,
    }
    if image is not None:
        result["gameplay_sha256"] = image
    return result


def race(*, placing=1, first_seen_ms=723250, completed_action="race"):
    return {
        "id": "race-001",
        "first_seen_ms": first_seen_ms,
        "last_seen_ms": first_seen_ms + 250,
        "completed_action": completed_action,
        "placing": placing,
        "race_name": "Satsuki Sho",
        "evidence": [f"gameplay/{first_seen_ms}.png"],
    }


def bounded_source(first, second, *, extras=(), detail_time=723250):
    """Build an inspected 250 ms tail, changing only the case under test."""
    rows = [first, second, *extras]
    timestamps = {row["source_timestamp_ms"] for row in rows}
    for timestamp in range(715500, detail_time + 1, 250):
        if timestamp in timestamps:
            continue
        rows.append(
            reading(
                timestamp,
                lines=[],
                screen="race_result" if timestamp == detail_time else "unknown",
            )
        )
    rows.append(reading(detail_time, lines=[], screen="race_result"))
    return sorted(rows, key=lambda row: row["source_timestamp_ms"])


class RaceCompletionTests(unittest.TestCase):
    def test_repeated_centered_ordinal_enriches_without_changing_detail_time(self):
        races = [race()]
        readings = [reading(715500), reading(715750)] + [
            reading(
                timestamp,
                lines=[],
                screen="race_result" if timestamp == 723250 else "unknown",
            )
            for timestamp in range(716000, 723251, 250)
        ]
        original_races = copy.deepcopy(races)
        original_readings = copy.deepcopy(readings)

        result = annotate(races, readings)

        self.assertEqual(result[0]["first_seen_ms"], 723250)
        self.assertEqual(result[0]["completion_first_seen_ms"], 715500)
        self.assertEqual(result[0]["completion_last_seen_ms"], 715750)
        self.assertEqual(
            result[0]["completion_evidence"],
            ["gameplay/715500.png", "gameplay/715750.png"],
        )
        provenance = result[0]["completion_provenance"]
        self.assertEqual(provenance["method"], "large_centered_exact_ordinal_animation")
        self.assertEqual(provenance["ordinal_text"], "1st")
        self.assertEqual(provenance["placing"], 1)
        self.assertEqual(provenance["association"], "backward_from_existing_completed_race")
        self.assertEqual(len(provenance["observations"]), 2)
        self.assertEqual(races, original_races)
        self.assertEqual(readings, original_readings)

    def test_ordinal_must_match_existing_race_placing(self):
        result = annotate(
            [race(placing=2)],
            bounded_source(reading(715500), reading(715750)),
        )

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_same_timestamp_evidence_or_image_does_not_count_twice(self):
        cases = [
            [reading(715500), reading(715500, evidence="other.png")],
            [reading(715500), reading(715750, evidence="gameplay/715500.png")],
            [
                reading(715500, image="same-image"),
                reading(715750, image="same-image"),
            ],
        ]
        for readings in cases:
            with self.subTest(readings=readings):
                self.assertNotIn(
                    "completion_first_seen_ms",
                    annotate([race()], bounded_source(*readings))[0],
                )

    def test_conflicting_ordinals_at_one_timestamp_abstain(self):
        readings = bounded_source(
            reading(715500),
            reading(716000),
            extras=[reading(715750, lines=[line("1st"), line("2nd")])],
        )

        result = annotate([race()], readings)

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_small_static_result_rank_is_not_completion_animation(self):
        result = annotate(
            [race(first_seen_ms=723250)],
            bounded_source(
                reading(715500, lines=[line(box=(287, 554, 368, 626))]),
                reading(715750, lines=[line(box=(288, 561, 369, 628))]),
            ),
        )

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_static_large_result_rank_above_animation_band_is_not_completion(self):
        result = annotate(
            [race(first_seen_ms=723250)],
            bounded_source(
                reading(715500, lines=[line(box=(322, 185, 511, 330))]),
                reading(715750, lines=[line(box=(323, 191, 506, 325))]),
            ),
        )

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_forecast_and_cancelled_menu_evidence_are_not_completion(self):
        result = annotate(
            [race()],
            bounded_source(
                reading(715500),
                reading(715750),
                extras=[reading(716000, screen="race_list", lines=[])],
            ),
        )

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_low_confidence_or_non_exact_ordinal_is_unknown(self):
        readings = [
            reading(715250, lines=[line("st", 52.47)]),
            reading(715500, lines=[line("1st", 94.99)]),
            reading(715750, lines=[line("1 st", 99.9)]),
        ]

        result = annotate([race()], bounded_source(*readings[:2], extras=[readings[2]]))

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_missing_or_long_gap_does_not_bridge_sequences(self):
        readings = bounded_source(reading(715500), reading(716501))

        result = annotate([race()], readings)

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_detail_panel_must_follow_animation_within_bounded_lag(self):
        readings = bounded_source(reading(700000), reading(700250))

        result = annotate([race(first_seen_ms=723250)], readings)

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_nearest_valid_sequence_is_used_for_the_detail_panel(self):
        readings = bounded_source(
            reading(715500),
            reading(715750),
            extras=[reading(718000), reading(718250)],
        )

        result = annotate([race()], readings)

        self.assertEqual(result[0]["completion_first_seen_ms"], 718000)

    def test_previous_completed_race_bounds_backward_association(self):
        previous = race(first_seen_ms=2000)
        target = race(first_seen_ms=3500)
        readings = bounded_source(
            reading(1000),
            reading(1250),
            extras=[reading(2000, lines=[], screen="race_result")],
            detail_time=3500,
        )

        result = annotate([previous, target], readings)

        self.assertEqual(result[0]["completion_first_seen_ms"], 1000)
        self.assertNotIn("completion_first_seen_ms", result[1])

    def test_tail_requires_the_known_detail_screen(self):
        readings = bounded_source(reading(715500), reading(715750))
        readings[-1]["screen"] = "unknown"

        result = annotate([race()], readings)

        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_no_completed_race_means_animation_cannot_create_one(self):
        races = [race(completed_action=None)]

        result = annotate(races, bounded_source(reading(715500), reading(715750)))

        self.assertEqual(result, races)

    def test_unknown_placing_and_boundary_timestamp_remain_unconfirmed(self):
        unknown = race(placing=None)
        boundary = race()

        result = annotate(
            [unknown, boundary],
            bounded_source(reading(723250), reading(723500)),
        )

        self.assertNotIn("completion_first_seen_ms", result[0])
        self.assertNotIn("completion_first_seen_ms", result[1])

    def test_intervening_menu_or_conflicting_ordinal_breaks_sequence(self):
        readings = bounded_source(
            reading(715500),
            reading(716000),
            extras=[reading(715750, screen="menu", lines=[])],
        )
        result = annotate([race()], readings)
        self.assertNotIn("completion_first_seen_ms", result[0])

        readings = bounded_source(
            reading(715500),
            reading(716000),
            extras=[reading(715750, lines=[line("2nd")])],
        )
        result = annotate([race()], readings)
        self.assertNotIn("completion_first_seen_ms", result[0])

    def test_existing_completion_is_preserved(self):
        existing = race()
        existing.update(
            completion_first_seen_ms=710000,
            completion_evidence=["old.png"],
            completion_provenance={"method": "reviewed"},
        )

        result = annotate(
            [existing], bounded_source(reading(715500), reading(715750))
        )

        self.assertEqual(result[0], existing)


if __name__ == "__main__":
    unittest.main()
