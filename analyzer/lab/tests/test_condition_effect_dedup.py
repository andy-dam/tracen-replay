"""Tests of ``tests.test_condition_effect_dedup`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from tests import localdata
from tracen_replay.condition_removal_banner import normalize_condition_removal_event
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import parse


class ConditionEffectDedupTests(unittest.TestCase):
    def test_source_event_collapses_fading_receipt_variants(self):
        paths = sorted(localdata.root(
            "development_third_recording_baseline", "neural"
        ).glob("part-001-frame-00044[5-9].json"))
        if len(paths) != 5:
            self.skipTest("independent-02 source sidecars are not available")
        rows = []
        for path in paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = parse(raw)
            parsed.update(
                source_timestamp_ms=raw["source_timestamp_ms"],
                evidence=raw["evidence"],
                context_title="At the Infirmary",
            )
            rows.append(parsed)

        event = outcome_events(rows)[0]
        before = [effect.get("name") for effect in event["effects"]
                  if effect.get("kind") == "condition_removed"]
        self.assertEqual(before, ["Night Owl"])

        normalized = normalize_condition_removal_event(
            event, {row["evidence"]: row for row in rows}
        )
        self.assertEqual(normalized, event)
        removals = [effect for effect in normalized["effects"]
                    if effect.get("kind") == "condition_removed"]
        self.assertEqual([effect["name"] for effect in removals], ["Night Owl"])
        self.assertFalse(any(effect.get("kind") == "condition_removal_conflict"
                             for effect in normalized["effects"]))
        variants = removals[0]["resolved_condition_name_variants"]
        self.assertEqual([item["observed_name"] for item in variants],
                         ["Night O", "Night O", "Night Ol"])
        self.assertIn("gameplay/part-001-frame-000448.png",
                      variants[1]["evidence"])
        self.assertIn("gameplay/part-001-frame-000449.png",
                      variants[2]["evidence"])
