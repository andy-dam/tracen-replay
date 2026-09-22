"""Tests of ``tests.test_occluded_receipt_recovery`` that need locally preserved evidence; they run only where it is."""
import hashlib
import unittest
from PIL import Image
from tracen_replay.occluded_receipt_recovery import plan, recover, scoped_observations
from tracen_replay.receipt_occlusion import annotate
from tracen_replay.vision import NeuralReader, parse
from tests.test_occluded_receipt_recovery import MODEL_DIR, SOURCE_FRAME, base_row, owner


class OccludedReceiptRecoveryTests(unittest.TestCase):








































    @unittest.skipUnless(
        SOURCE_FRAME.is_file() and any(MODEL_DIR.glob("*.onnx")),
        "The diagnostic source frame or OCR model is not available",
    )
    def test_actual_clear_source_frame_recovers_the_reported_friendship_gap(self):
        reader = NeuralReader(MODEL_DIR)
        with Image.open(SOURCE_FRAME) as image:
            pane = image.convert("RGB").crop((148, 0, 958, 1080))
            raw = reader.read(pane)
        raw.update(
            source_timestamp_ms=155800,
            source_frame_sha256=hashlib.sha256(SOURCE_FRAME.read_bytes()).hexdigest(),
            source_sha256="source-placeholder",
            evidence="friendship-155750-source/000016.png",
        )
        parsed = parse(annotate(raw, pane))
        clear = dict(parsed, source_timestamp_ms=155800,
                     evidence="friendship-155750-source/000016.png")
        effects = [
            effect for effect in parsed["effects"]
            if effect.get("kind") == "friendship_change"
        ]
        self.assertEqual(
            [(effect.get("name"), effect.get("amount")) for effect in effects],
            [("Air Groove", 7)],
        )
        promoted = scoped_observations(
            [base_row()], [clear], plan([base_row()], [owner()], 200000)
        )
        self.assertEqual(
            [(effect.get("kind"), effect.get("name"), effect.get("amount"))
             for effect in promoted[0]["effects"]],
            [("friendship_change", "Air Groove", 7)],
        )
