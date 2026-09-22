"""Tests of ``tests.test_event_choice_adapter`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from tests import localdata
from tracen_replay.event_choice_adapter import build_choice_observations


class EventChoiceAdapterTests(unittest.TestCase):

















    @localdata.needs("development_third_recording_baseline", "neural", "part-010-frame-000285.json")
    def test_independent_source_cache_fixture_reaches_commitment_adapter(self):
        base = localdata.root("development_third_recording_baseline")
        readings = []
        for number in range(272, 286):
            raw = json.loads((base / "neural" / f"part-010-frame-{number:06d}.json").read_text(encoding="utf-8"))
            readings.append({"source_timestamp_ms": raw["source_timestamp_ms"],
                             "evidence": raw["evidence"], "screen": "unknown"})
        result = build_choice_observations(readings, base)
        self.assertEqual(result["committed_choice_count"], 1)
        self.assertEqual(result["committed_choices"][0]["selected_text"],
                         "Your dedication to the potential of Umamusume?")
