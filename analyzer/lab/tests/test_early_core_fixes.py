"""Tests of ``tests.test_early_core_fixes`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from tracen_replay.animated_performance import candidates
from tracen_replay.event_choice_commitment import reconstruct_committed_choices
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import NeuralReader, parse
from tests import localdata
from tracen_replay.event_choice_adapter import same_frame_choice_observation
from tests.test_early_core_fixes import line


class EarlyComposureTests(unittest.TestCase):
    def _source_like_lines(self, *, caption="Composure went by 10.", label="Me Composure"):
        return [
            line("+10", (650, 455, 794, 528)),
            line(label, (609, 366, 832, 412)),
            line(caption, (315, 917, 578, 953), 95),
        ]



    def test_actual_fourth_composure_frames_reach_normal_transactions(self):
        root = localdata.root("fourth_recording_untouched_baseline")
        sidecars = [root / "neural" / f"part-005-frame-{number:06d}.json"
                    for number in (146, 147)]
        if not all(path.is_file() for path in sidecars):
            self.skipTest("untouched fourth source sidecars unavailable")
        readings = []
        for path in sidecars:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = parse(raw)
            readings.append(dict(
                parsed,
                source_timestamp_ms=raw["source_timestamp_ms"],
                evidence=raw["evidence"],
            ))
        events = outcome_events(readings)
        self.assertEqual(len(events), 1)
        composure = [effect for effect in events[0]["effects"]
                     if effect.get("kind") == "performance_change"
                     and effect.get("field") == "composure"]
        self.assertEqual(len(composure), 1)
        self.assertEqual(composure[0]["amount"], 10)
        self.assertEqual(
            events[0]["field_evidence"]["performance_change|composure|"],
            [
                "gameplay/part-005-frame-000146.png",
                "gameplay/part-005-frame-000147.png",
            ],
        )


class EarlyChoiceCommitmentTests(unittest.TestCase):
    OPTIONS = [
        ("You're just going to power through it?", 606),
        ("Sounds like you're writing a pretty complicated thesis.", 716),
    ]









    @unittest.skipUnless(
        localdata.available("fourth_recording_source_controls_early", "native")
        and localdata.MODEL_DIR.is_dir(),
        "early source images or the pinned OCR model are unavailable",
    )
    def test_actual_native_choice_transition_uses_green_collapse_witness(self):
        root = localdata.root("fourth_recording_source_controls_early")
        reader = NeuralReader(localdata.MODEL_DIR)
        specs = [
            ("native/window-002/frames/frame-000092.png", 330933),
            ("native/window-002/frames/frame-000093.png", 331000),
            ("native/window-002-choice-native/frames/frame-000017.png", 331267),
        ]
        rows = []
        for relative, timestamp in specs:
            image_path = root / relative
            with open(image_path, "rb") as handle:
                from PIL import Image
                with Image.open(handle) as image:
                    pane = image.convert("RGB")
            raw = reader.read(pane)
            observation = same_frame_choice_observation(
                pane, raw["lines"], source_timestamp_ms=timestamp, evidence=relative
            )
            self.assertIsNotNone(observation)
            rows.append(observation)
        self.assertEqual(len(rows[-1]["selected_card_candidates"]), 1)
        self.assertLess(len(rows[-1]["offered_card_slots"]), 2)
        result = reconstruct_committed_choices(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selected_index"], 0)
        self.assertEqual(result[0]["selected_text"], self.OPTIONS[0][0])
        self.assertEqual(result[0]["selection_basis"],
                         "selected_card_highlight_and_transition")

    @unittest.skipUnless(
        localdata.available("fourth_recording_source_controls_early", "native")
        and localdata.MODEL_DIR.is_dir(),
        "early source images or the pinned OCR model are unavailable",
    )
    def test_actual_native_hover_without_selection_witness_stays_uncommitted(self):
        root = localdata.root("fourth_recording_source_controls_early")
        reader = NeuralReader(localdata.MODEL_DIR)
        specs = [
            ("native/window-003-choice-native/frames/frame-000012.png", 639933),
            ("native/window-003-choice-native/frames/frame-000013.png", 640000),
            ("native/window-003-choice-native/frames/frame-000014.png", 640067),
        ]
        rows = []
        for relative, timestamp in specs:
            image_path = root / relative
            with open(image_path, "rb") as handle:
                from PIL import Image
                with Image.open(handle) as image:
                    pane = image.convert("RGB")
            raw = reader.read(pane)
            observation = same_frame_choice_observation(
                pane, raw["lines"], source_timestamp_ms=timestamp, evidence=relative
            )
            self.assertIsNotNone(observation)
            rows.append(observation)
        # The source frame has a yellow hover card and two remaining white
        # cards, but no parser-owned selected green transition.  The gold
        # label alone cannot be promoted as a click.
        self.assertEqual(rows[-1]["selected_card_candidates"], [])
        audit = {}
        self.assertEqual(reconstruct_committed_choices(rows, audit=audit), [])
        self.assertEqual(audit["menus"][0]["status"], "selection_unobserved")
