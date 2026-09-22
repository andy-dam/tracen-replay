"""Tests of ``tests.test_code_identity`` that need locally preserved evidence; they run only where it is."""
import subprocess
import sys
import unittest
from pathlib import Path
from tests import localdata


@unittest.skipUnless(localdata.MODEL_DIR.is_dir(), 'OCR models are not installed')
class ReaderFingerprintTests(unittest.TestCase):
    def test_fingerprint_survives_use_and_a_fresh_process(self):
        from PIL import Image
        from tracen_replay.vision import NeuralReader
        first = NeuralReader(localdata.MODEL_DIR)
        first.read(Image.new('RGB', (810, 1080), (30, 30, 30)))
        second = NeuralReader(localdata.MODEL_DIR)
        self.assertEqual(first.fingerprint, second.fingerprint)
        script = ('from tracen_replay.vision import NeuralReader\n'
                  f"print(NeuralReader({str(localdata.MODEL_DIR)!r}).fingerprint)\n")
        out = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                             cwd=str(Path(__file__).resolve().parents[1]), check=True)
        self.assertEqual(out.stdout.strip().splitlines()[-1], first.fingerprint)
