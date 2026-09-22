import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageOps

from tests import localdata
from tracen_replay.current_state_layout import detect_current_state_layout


REPO = Path(__file__).resolve().parents[2]
SOURCE_CASES = (
    localdata.root("fourth_recording_retest_older", "neural/part-015-frame-000201.json"),
    localdata.root("fourth_recording_retest_older", "neural/part-015-frame-000257.json"),
    localdata.root("fourth_recording_retest_older", "neural/part-017-frame-000407.json"),
    localdata.root("fourth_recording_retest_older", "neural/part-018-frame-000040.json"),
)
SHOGI_SOURCE = localdata.root(
    "fourth_recording_retest_older", "neural/part-015-frame-000246.json"
)


def line(text, box, confidence=99.0):
    return {"text": text, "box": list(box), "confidence": confidence}


def current_lines(*, header="Career", wit_value="1023", wit_confidence=99.0):
    rows = [
        ("Speed", 320, "1372", "/1623"),
        ("Stamina", 425, "256", "/1300"),
        ("Power", 520, "967", "/1400"),
        ("Guts", 618, "409", "/1500"),
        ("Wit", 710, wit_value, "/1300"),
    ]
    result = [line(header, (156, 5, 217, 29))] if header else []
    for label, center_x, value, cap in rows:
        result.extend([
            line(label, (center_x - 35, 697, center_x + 35, 722)),
            line(value, (center_x - 25, 720, center_x + 35, 748),
                 wit_confidence if label == "Wit" else 99.0),
            line(cap, (center_x - 30, 742, center_x + 35, 766)),
        ])
    result.extend([
        line("Skill Pts", (754, 692, 834, 722)),
        line("1991", (753, 722, 834, 760)),
    ])
    return result


class CurrentStateLayoutTests(unittest.TestCase):
    def test_complete_current_bar_is_source_anchored(self):
        result = detect_current_state_layout(
            current_lines(), header="Career", current_grid=False,
        )

        self.assertEqual(result["status"], "recognized")
        self.assertTrue(result["current_grid"])
        self.assertEqual(result["basis"],
                         "same_frame_current_stat_bar_labels_values_and_caps")
        self.assertEqual(result["observed"]["stat_row_count"], 5)
        self.assertFalse(result["observed"]["colour_signal"])
        self.assertEqual(result["header_status"], "same_frame_header")

    def test_complete_bar_can_prove_state_when_header_was_missed(self):
        result = detect_current_state_layout(
            current_lines(header=""), header="", current_grid=False,
        )

        self.assertTrue(result["current_grid"])
        self.assertEqual(result["header"], None)
        self.assertEqual(result["header_status"], "header_not_visible")

    def test_rank_touched_value_can_open_fixed_crop_but_is_not_decoded(self):
        result = detect_current_state_layout(
            current_lines(wit_value="U41246", wit_confidence=84.364),
            header="Career",
            current_grid=False,
        )

        self.assertTrue(result["current_grid"])
        wit = next(row for row in result["field_geometry"] if row["field"] == "wit")
        self.assertEqual(wit["value"]["text"], "U41246")

    def test_applied_result_proof_vetoes_current_state(self):
        result = detect_current_state_layout(
            current_lines(),
            header="Career",
            result_layout={"result_grid": True},
        )

        self.assertFalse(result["current_grid"])
        self.assertEqual(result["rejections"]["applied_result_layout"], 1)

    def test_a_near_miss_label_in_its_own_column_at_lower_confidence_is_the_label(self):
        # White text on a pink or orange strip comes back as "Sil Pts",
        # "SSpeed" or "peed" in the sixties to eighties; the column says
        # which label it is.
        for skill, speed in (("Sil Pts", "SSpeed"), ("Shill Pts", "peed"), ("Skill Pts", "Speed")):
            lines = current_lines()
            for entry in lines:
                if entry["text"] == "Skill Pts":
                    entry.update(text=skill, confidence=72.0)
                if entry["text"] == "Speed":
                    entry.update(text=speed, confidence=68.0)
            with self.subTest(skill=skill, speed=speed):
                result = detect_current_state_layout(lines, header="Career")
                self.assertTrue(result["current_grid"], result["rejections"])
                self.assertEqual(result["observed"]["stat_row_count"], 5)

    def test_a_near_miss_needs_its_column_and_some_confidence(self):
        lines = current_lines()
        for entry in lines:
            if entry["text"] == "Skill Pts":
                entry.update(text="Sil Pts", confidence=55.0)
        self.assertFalse(detect_current_state_layout(lines, header="Career")["current_grid"])
        lines = current_lines()
        for entry in lines:
            if entry["text"] == "Skill Pts":
                entry.update(text="Sil Pts", confidence=80.0, box=[600, 692, 680, 722])
        self.assertFalse(detect_current_state_layout(lines, header="Career")["current_grid"])

    def test_two_labels_read_as_one_box_are_split(self):
        lines = [entry for entry in current_lines() if entry["text"] not in ("Stamina", "Power")]
        lines.append(line("StaminaPower", (391, 695, 551, 721)))
        result = detect_current_state_layout(lines, header="Career")
        self.assertTrue(result["current_grid"], result["rejections"])
        fields = {row["field"]: row["label"] for row in result["field_geometry"]}
        self.assertEqual(fields["stamina"]["merged_from"], "StaminaPower")
        self.assertLess(fields["stamina"]["box"][2], fields["power"]["box"][0] + 1)

    def test_four_capped_rows_prove_the_panel_when_one_cap_is_unreadable(self):
        # The wit cap comes back with the grade glyph stuck to it.
        lines = current_lines()
        for entry in lines:
            if entry["text"] == "/1300" and entry["box"][0] > 650:
                entry["text"] = "UG/1301"
        result = detect_current_state_layout(lines, header="Career")
        self.assertTrue(result["current_grid"], result["rejections"])
        self.assertEqual(result["observed"]["capped_stat_row_count"], 4)
        self.assertEqual(result["rejections"], {"missing_wit_cap": 1})
        # Three capped rows do not.
        for entry in lines:
            if entry["text"] == "/1500":
                entry["text"] = "UG/1500"
        result = detect_current_state_layout(lines, header="Career")
        self.assertFalse(result["current_grid"])
        self.assertIn("insufficient_capped_stat_rows", result["rejections"])

    def test_the_strip_probe_takes_any_theme_colour_and_no_grey(self):
        from tracen_replay.vision import STRIP_PROBE_BOXES, STRIP_PROBE_MIN, _strip_saturation

        def strip(rgb, text=None):
            crop = np.full((19, 35, 3), rgb, dtype="uint8")
            if text is not None:
                crop[6:13, 8:27] = text
            return crop

        # The three strips seen so far: blue (B), pink (A), orange (C), with
        # white label text over a third of the box.
        for colour in ((135, 170, 218), (217, 159, 219), (241, 163, 101)):
            with self.subTest(colour=colour):
                self.assertGreater(_strip_saturation(strip(colour, (255, 255, 255))), STRIP_PROBE_MIN)
        # A white panel, a grey one, a dark scene: no strip.
        for colour in ((250, 250, 250), (128, 128, 128), (30, 40, 50)):
            with self.subTest(colour=colour):
                self.assertLess(_strip_saturation(strip(colour)), STRIP_PROBE_MIN)
        self.assertEqual(len(STRIP_PROBE_BOXES), 5)
        self.assertTrue(all(box[1] == 700 and box[3] == 719 for box in STRIP_PROBE_BOXES))

    def test_a_five_digit_wit_token_is_a_glyph_on_a_value(self):
        # The grade glyph read as a digit: "11218" for 1218. Geometry only;
        # the fixed crop still decides the number.
        lines = current_lines(wit_value="11218", wit_confidence=88.0)
        result = detect_current_state_layout(lines, header="Career")
        self.assertTrue(result["current_grid"], result["rejections"])

    def test_partial_or_unrelated_rows_do_not_authorize_current_state(self):
        lines = [line("Career", (156, 5, 217, 29))]
        lines.extend([
            line("Speed", (285, 697, 360, 722)),
            line("1372", (304, 720, 366, 748)),
            line("/1623", (305, 742, 365, 766)),
            line("Skill Pts", (754, 692, 834, 722)),
            line("1991", (753, 722, 834, 760)),
        ])

        result = detect_current_state_layout(lines, header="Career")

        self.assertFalse(result["current_grid"])
        self.assertIn("incomplete_current_panel", result["rejections"])


    def test_malformed_geometry_and_confidence_fail_closed(self):
        lines = copy.deepcopy(current_lines())
        lines[1]["box"] = [10**10000, 697, 360, 722]
        lines[2]["confidence"] = 101

        result = detect_current_state_layout(lines, header="Career")

        self.assertFalse(result["current_grid"])
        self.assertGreaterEqual(result["rejections"].get("line_geometry", 0), 1)
        self.assertGreaterEqual(result["rejections"].get("line_confidence", 0), 1)


    def test_neural_reader_promotes_translucent_current_bar_before_crops(self):
        from unittest.mock import patch

        detector_lines = current_lines()
        boxes = [
            np.asarray([
                [entry["box"][0] - 148, entry["box"][1]],
                [entry["box"][2] - 148, entry["box"][1]],
                [entry["box"][2] - 148, entry["box"][3]],
                [entry["box"][0] - 148, entry["box"][3]],
            ], dtype=float)
            for entry in detector_lines
        ]

        class Engine:
            def __call__(self, _image, **_kwargs):
                return SimpleNamespace(
                    boxes=boxes,
                    txts=[entry["text"] for entry in detector_lines],
                    scores=[entry["confidence"] / 100 for entry in detector_lines],
                )

            def text_rec(self, request):
                return SimpleNamespace(
                    txts=["?"] * len(request.img),
                    scores=[0.99] * len(request.img),
                )

        from tracen_replay.vision import NeuralReader

        reader = object.__new__(NeuralReader)
        reader.np = np
        reader.Image = Image
        reader.ImageOps = ImageOps
        reader.TextRecInput = lambda *, img: SimpleNamespace(img=img)
        reader.engine = Engine()
        reader.models = {}
        reader.fingerprint = "current-layout-test"
        pane = Image.new("RGB", (810, 1080), (30, 40, 50))

        with patch("tracen_replay.preview_recovery.recover_in_memory",
                   side_effect=lambda raw, _pane, reader: raw):
            raw = reader.read(pane)

        self.assertTrue(raw["current_grid"])
        self.assertEqual(
            raw["current_state_layout"]["basis"],
            "same_frame_current_stat_bar_labels_values_and_caps",
        )
        self.assertEqual(raw["current_state_layout"]["observed"]["stat_row_count"], 5)


if __name__ == "__main__":
    unittest.main()
