"""Tests of ``tests.test_event_choice_commitment`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from PIL import Image
from tests import localdata
from tracen_replay.choice_evidence import observe
from tracen_replay.event_choice_commitment import reconstruct_committed_choices


class EventChoiceCommitmentTests(unittest.TestCase):















    @localdata.needs("development_third_recording_baseline", "gameplay", "part-010-frame-000285.png")
    def test_independent_source_expression_of_conviction_is_promoted(self):
        base = localdata.root("development_third_recording_baseline")
        rows = []
        for number in range(272, 286):
            image_path = base / "gameplay" / f"part-010-frame-{number:06d}.png"
            raw_path = base / "neural" / f"part-010-frame-{number:06d}.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            observation = observe(
                Image.open(image_path).convert("RGB"), raw["lines"], include_slots=True
            )
            observation.update(
                source_timestamp_ms=raw["source_timestamp_ms"],
                evidence=raw["evidence"],
            )
            rows.append(observation)
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selected_text"],
                         "Your dedication to the potential of Umamusume?")
        self.assertEqual(result[0]["selection_observed_ms"], 1_271_000)
        observed_by_time = {
            row["source_timestamp_ms"]: row
            for row in rows
        }
        # The source shows Connecting at both 1,268,250 ms and 1,268,500 ms,
        # while both cards remain visible.  Neither frame carries the
        # selected-state pixel witness; the later activation frame does.
        for timestamp in (1_268_250, 1_268_500):
            self.assertEqual(len(observed_by_time[timestamp]["offered_card_candidates"]), 2)
            self.assertEqual(observed_by_time[timestamp]["selection_mark_pairs"], [])
        self.assertEqual(len(observed_by_time[1_271_000]["selection_mark_pairs"]), 1)
