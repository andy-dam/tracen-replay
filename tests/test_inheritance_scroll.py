"""Focused source-bound tests for inheritance receipt scroll tracking."""

from copy import deepcopy
import unittest

from tracen_replay.inheritance_scroll import track


def effect(name="Stamina", **values):
    result = dict(
        kind="inheritance_spark",
        field=None,
        name=name,
        amount=None,
        direction=None,
        value=None,
        raw_text=f"{name} spark activated!",
        confidence=99,
    )
    result.update(values)
    return result


def ocr_line(text, center, *, confidence=99, overlay_occluded=False):
    return dict(
        text=text,
        box=[315, center - 10, 555, center + 10],
        confidence=confidence,
        overlay_occluded=overlay_occluded,
    )


def source_row(
    timestamp,
    evidence,
    target_effect,
    target_centers,
    *,
    anchor_centers=(800, 920),
    screen="event_outcome",
    context_title="inheritance",
    anchors=("Anchor A", "Anchor B"),
    extra_effects=(),
    target_overlay=False,
):
    lines = [
        ocr_line(target_effect["raw_text"], center, overlay_occluded=target_overlay)
        for center in target_centers
    ]
    lines.extend(ocr_line(text, center) for text, center in zip(anchors, anchor_centers))
    observed_effects = [deepcopy(target_effect) for _ in target_centers]
    observed_effects.extend(deepcopy(item) for item in extra_effects)
    return dict(
        source_timestamp_ms=timestamp,
        evidence=evidence,
        screen=screen,
        context_title=context_title,
        effects=observed_effects,
        ocr={"neural": lines},
    )


def source_event(target_effect, start=1000, end=2000):
    return dict(
        id="outcome-scroll",
        first_seen_ms=start,
        last_seen_ms=end,
        context_title="inheritance",
        effects=[deepcopy(target_effect)],
        field_evidence={
            f"{target_effect['kind']}||{target_effect['name']}": [],
        },
    )


def run(effect_value, rows, *, start=1000, end=2000):
    event = source_event(effect_value, start, end)
    event["field_evidence"][f"{effect_value['kind']}||{effect_value['name']}"] = list(rows)
    return track(event, effect_value, rows)


class InheritanceScrollTests(unittest.TestCase):
    def test_two_simultaneous_then_three_bottom_entry_through_anchored_scroll(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "b": source_row(1100, "b", target, [830, 870, 930], anchor_centers=(790, 910)),
            "c": source_row(1200, "c", target, [820, 860, 920], anchor_centers=(780, 900)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 3)
        self.assertIsNone(result["total_count"])
        self.assertFalse(result["count_complete"])
        selected = result["selected_track_evidence"]
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[2]["entry"]["basis"], "bottom_entry")
        self.assertEqual(selected[2]["entry"]["source_timestamp_ms"], 1100)
        self.assertEqual(len(selected[0]["continuity"]), 2)

    def test_top_exit_uses_receipt_relative_projection(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [820, 860], anchor_centers=(900, 940)),
            "b": source_row(1100, "b", target, [850], anchor_centers=(890, 930)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertEqual(result["breaks"], [])

    def test_interior_target_dropout_is_not_top_exit_when_another_line_is_above_it(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [820, 860], anchor_centers=(800, 940)),
            "b": source_row(1100, "b", target, [850], anchor_centers=(790, 930)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "interior_target_dropout"
                            for item in result["breaks"]))

    def test_many_to_one_target_geometry_is_rejected_before_greedy_matching(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 850]),
            "b": source_row(1100, "b", target, [845]),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "non_unique_target_geometry"
                            for item in result["breaks"]))

    def test_clear_gap_starts_a_new_standalone_snapshot(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840]),
            "b": source_row(1400, "b", target, [820, 850, 880]),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 3)
        self.assertEqual(sorted(segment["distinct_count"] for segment in result["segments"]), [1, 3])
        self.assertTrue(any(item["reason"] == "non_adjacent_source_rows"
                            for item in result["breaks"]))

    def test_repeated_frames_do_not_add_tracks(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "b": source_row(1100, "b", target, [840, 880]),
            "c": source_row(1200, "c", target, [840, 880]),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertEqual(len(result["selected_track_evidence"]), 2)
        self.assertTrue(all(not track_item["continuity"][0]["mode"] == "upward_scroll"
                            for track_item in result["selected_track_evidence"]))

    def test_skipped_source_frame_is_allowed_only_within_adjacency_bound(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "b": source_row(1250, "b", target, [830, 870], anchor_centers=(790, 910)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertEqual(result["breaks"], [])

        rows["b"]["source_timestamp_ms"] = 1251
        rows["c"] = source_row(1501, "c", target, [820, 860], anchor_centers=(780, 900))
        result = run(target, rows)
        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "non_adjacent_source_rows"
                            for item in result["breaks"]))

    def test_navigation_breaks_track(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "nav": source_row(1100, "nav", target, [], screen="training_preview"),
            "b": source_row(1200, "b", target, [830, 870], anchor_centers=(790, 910)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "conflicting_source_snapshot"
                            for item in result["breaks"]))

    def test_disconnected_segments_use_their_maximum_not_a_sum(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "nav": source_row(1100, "nav", target, [], screen="training_preview"),
            "b": source_row(1200, "b", target, [820, 850, 880]),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 3)
        self.assertEqual(sorted(segment["distinct_count"] for segment in result["segments"]), [2, 3])

    def test_reverse_motion_restarts_without_joining_segments(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "b": source_row(1100, "b", target, [830, 870], anchor_centers=(790, 910)),
            "c": source_row(1200, "c", target, [840, 880], anchor_centers=(800, 920)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "reverse_or_unbounded_motion"
                            for item in result["breaks"]))

    def test_incompatible_anchors_are_not_cherry_picked(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "b": source_row(1100, "b", target, [830, 870], anchor_centers=(790, 900)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "incompatible_anchor_motion"
                            for item in result["breaks"]))

    def test_same_timestamp_alternate_crops_are_not_unioned(self):
        target = effect()
        rows = {
            "a-left": source_row(1000, "a-left", target, [840, 880]),
            "a-right": source_row(1000, "a-right", target, [820, 900]),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 0)
        self.assertEqual(result["selected_track_evidence"], [])
        self.assertTrue(result["breaks"] == [])

    def test_malformed_or_occluded_target_and_payload_do_not_count(self):
        target = effect()
        occluded = {"a": source_row(1000, "a", target, [840], target_overlay=True)}
        self.assertEqual(run(target, occluded)["minimum_observed_count"], 0)

        wrong_payload = effect(amount=1)
        malformed = {"a": source_row(1000, "a", target, [840], extra_effects=[wrong_payload])}
        self.assertEqual(run(target, malformed)["minimum_observed_count"], 0)

        unreadable = {"a": source_row(1000, "a", target, [840])}
        unreadable["a"]["ocr"]["neural"][0]["box"] = [315, float("nan"), 555, 860]
        self.assertEqual(run(target, unreadable)["minimum_observed_count"], 0)

    def test_inspiration_effects_use_the_same_exact_payload_tracking(self):
        target = effect(kind="inheritance_inspiration", name="Uncatalogued Parent",
                        raw_text="Inspired by Uncatalogued Parent!")
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "b": source_row(1100, "b", target, [830, 870, 930], anchor_centers=(790, 910)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 3)

    def test_wrong_receipt_grammar_is_not_an_inspiration(self):
        target = effect(kind='inheritance_inspiration', name='Parent')
        rows = {'a': source_row(1000, 'a', target, [840, 880])}
        self.assertEqual(run(target, rows)['minimum_observed_count'], 0)

    def test_stationary_addition_has_only_simultaneous_evidence(self):
        target = effect()
        rows = {'a': source_row(1000, 'a', target, [850]),
                'b': source_row(1100, 'b', target, [850, 930])}
        result = run(target, rows)
        self.assertEqual(result['minimum_observed_count'], 2)
        self.assertTrue(any(item['reason'] == 'stationary_target_addition' for item in result['breaks']))
        self.assertTrue(all(item['entry']['basis'] == 'simultaneous_snapshot'
                            for segment in result['segments'] for item in segment['tracks']))

    def test_two_anchor_strings_in_one_physical_line_do_not_prove_scroll(self):
        target = effect()
        rows = {'a': source_row(1000, 'a', target, [800, 880], anchor_centers=(840, 840)),
                'b': source_row(1100, 'b', target, [840, 930], anchor_centers=(800, 800))}
        result = run(target, rows)
        self.assertEqual(result['minimum_observed_count'], 2)
        self.assertTrue(any(item['reason'] == 'overlapping_anchor_geometry' for item in result['breaks']))

    def test_event_bounds_are_required(self):
        target = effect()
        rows = {'a': source_row(1000, 'a', target, [840, 880])}
        for field in ('first_seen_ms', 'last_seen_ms'):
            event = source_event(target)
            event.pop(field)
            self.assertEqual(track(event, target, rows)['minimum_observed_count'], 0)
        rows['outside'] = source_row(5000, 'outside', target, [840, 880])
        self.assertEqual(track(source_event(target), target, rows)['source_observation_count'], 1)

    def test_interior_dropout_does_not_reappear_as_new_track(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "drop": source_row(1100, "drop", target, [830], anchor_centers=(790, 910)),
            "reappear": source_row(1200, "reappear", target, [820, 860], anchor_centers=(780, 900)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "interior_target_dropout"
                            for item in result["breaks"]))

    def test_unmatched_interior_target_is_not_a_bottom_entry(self):
        target = effect()
        rows = {
            "a": source_row(1000, "a", target, [840, 880]),
            "interior": source_row(1100, "interior", target, [830, 850, 870],
                                    anchor_centers=(790, 910)),
        }

        result = run(target, rows)

        self.assertEqual(result["minimum_observed_count"], 2)
        self.assertTrue(any(item["reason"] == "unanchored_interior_target"
                            for item in result["breaks"]))


if __name__ == "__main__":
    unittest.main()
