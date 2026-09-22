import copy
import unittest
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageOps

from tracen_replay.training_result_layout import detect_training_result_layout


def _line(text, box, confidence=99.0):
    return {"text": text, "box": list(box), "confidence": confidence}


def _result_lines():
    return [
        _line("Training", (152, 1, 228, 32)),
        _line("SUCCES", (352, 701, 711, 789)),
        _line("Stamina", (525, 796, 618, 823)),
        _line("95/1300", (532, 830, 651, 876)),
        _line("Guts", (345, 910, 403, 939)),
        _line("213/1500", (309, 949, 455, 990)),
        _line("Wit", (547, 910, 596, 940)),
        _line("335/1300", (511, 947, 649, 991)),
        _line("Skip", (520, 1034, 574, 1071)),
        _line("Quick", (671, 1037, 733, 1065)),
    ]


class TrainingResultLayoutTests(unittest.TestCase):
    def test_clipped_friendship_result_uses_banner_and_card_geometry(self):
        result = detect_training_result_layout(
            _result_lines(),
            header="Training",
            current_grid=False,
            result_grid=False,
        )

        self.assertEqual(result["status"], "recognized")
        self.assertTrue(result["result_grid"])
        self.assertEqual(
            [(row["field"], row["total"]["text"]) for row in result["field_geometry"]],
            [
                ("stamina", "95/1300"),
                ("guts", "213/1500"),
                ("wit", "335/1300"),
            ],
        )
        self.assertEqual(
            result["basis"],
            "same_frame_training_result_banner_and_card_geometry",
        )

    def test_existing_colour_signal_cannot_replace_source_layout_proof(self):
        result = detect_training_result_layout(
            [_line("Training", (152, 1, 228, 32))],
            header="Training",
            current_grid=False,
            result_grid=True,
        )

        self.assertFalse(result["result_grid"])
        self.assertEqual(result["status"], "rejected")

    def test_preview_failure_badge_is_not_an_applied_result_banner(self):
        lines = _result_lines()
        lines[1] = _line("Failure", (736, 769, 798, 794))

        result = detect_training_result_layout(
            lines,
            header="Training",
            current_grid=True,
            result_grid=False,
        )

        self.assertFalse(result["result_grid"])
        self.assertEqual(result["rejections"]["current_training_menu"], 1)

    def test_modal_or_outcome_word_without_card_layout_stays_unknown(self):
        result = detect_training_result_layout(
            [
                _line("Training", (152, 1, 228, 32)),
                _line("SUCCESS!", (368, 691, 724, 771)),
                _line("Speed went up by 35", (300, 829, 414, 893)),
            ],
            header="Training",
            current_grid=False,
            result_grid=False,
        )

        self.assertFalse(result["result_grid"])
        self.assertEqual(result["status"], "rejected")
        self.assertIn("insufficient_labeled_cards", result["rejections"])

    def test_amount_from_another_card_cannot_create_a_pair(self):
        lines = _result_lines()
        for line in lines:
            if "/" in line["text"]:
                line["box"] = [700, line["box"][1], 815, line["box"][3]]
        result = detect_training_result_layout(
            lines,
            header="Training",
            current_grid=False,
            result_grid=False,
        )

        self.assertFalse(result["result_grid"])


    def test_malformed_line_cannot_authorize_layout(self):
        lines = copy.deepcopy(_result_lines())
        lines.append(_line("Speed", [float("nan"), 796, 410, 823]))
        result = detect_training_result_layout(
            lines,
            header="Training",
            current_grid=False,
            result_grid=False,
        )

        self.assertTrue(result["result_grid"])
        self.assertGreaterEqual(result["rejections"]["line_geometry"], 1)

    def test_overflow_geometry_is_rejected_without_raising(self):
        lines = copy.deepcopy(_result_lines())
        lines.append(_line("Power", [10**10000, 796, 410, 823]))

        result = detect_training_result_layout(
            lines,
            header="Training",
            current_grid=False,
            result_grid=False,
        )

        self.assertTrue(result["result_grid"])
        self.assertGreaterEqual(result["rejections"]["line_geometry"], 1)

    def test_invalid_confidence_is_rejected_without_authorizing_metadata(self):
        lines = copy.deepcopy(_result_lines())
        lines[2]["confidence"] = 101
        lines[3]["confidence"] = float("nan")
        lines[4]["confidence"] = True

        result = detect_training_result_layout(
            lines,
            header="Training",
            current_grid=False,
            result_grid=False,
        )

        self.assertFalse(result["result_grid"])
        self.assertGreaterEqual(result["rejections"]["line_confidence"], 3)

    def test_neural_reader_promotes_pink_result_from_source_layout(self):
        # Exercise the normal reader branch with a detector result whose blue
        # probes are deliberately false.  The result flag must come from the
        # shared source-layout proof before result crops are requested.
        from unittest.mock import patch

        detector_lines = _result_lines()
        boxes = [
            np.asarray([
                [line["box"][0] - 148, line["box"][1]],
                [line["box"][2] - 148, line["box"][1]],
                [line["box"][2] - 148, line["box"][3]],
                [line["box"][0] - 148, line["box"][3]],
            ], dtype=float)
            for line in detector_lines
        ]

        class Engine:
            def __call__(self, _image, **_kwargs):
                return SimpleNamespace(
                    boxes=boxes,
                    txts=[line["text"] for line in detector_lines],
                    scores=[line["confidence"] / 100 for line in detector_lines],
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
        reader.fingerprint = "layout-test"
        pane = Image.new("RGB", (810, 1080), (30, 40, 50))

        with patch("tracen_replay.preview_recovery.recover_in_memory",
                   side_effect=lambda raw, _pane, reader: raw):
            raw = reader.read(pane)

        self.assertTrue(raw["result_grid"])
        self.assertFalse(raw["current_grid"])
        self.assertEqual(
            raw["training_result_layout"]["basis"],
            "same_frame_training_result_banner_and_card_geometry",
        )
        self.assertGreaterEqual(
            raw["training_result_layout"]["observed"]["paired_card_count"], 2,
        )

        # ``read_training`` receives the same detector lines after its fixed
        # result crops.  Its persisted flag must be updated after that
        # detector pass as well; otherwise the richer result proof and the
        # stored result-grid state disagree.
        training_raw = reader.read_training(pane)
        self.assertTrue(training_raw["result_grid"])
        self.assertFalse(training_raw["current_grid"])
        self.assertEqual(
            training_raw["training_result_layout"]["status"],
            "recognized",
        )


if __name__ == "__main__":
    unittest.main()
