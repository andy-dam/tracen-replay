"""A proof written while its frame is read is the same file an ordinary save writes."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tracen_replay.proof_writer import save_while


class ProofWriterTests(unittest.TestCase):
    def test_the_file_is_byte_for_byte_an_ordinary_save_once_joined(self):
        rng = np.random.default_rng(7)
        pane = Image.fromarray(rng.integers(0, 256, (1080, 810, 3), dtype=np.uint8), 'RGB')
        with tempfile.TemporaryDirectory() as tmp:
            ordinary, background = Path(tmp) / 'ordinary.png', Path(tmp) / 'background.png'
            pane.save(ordinary)
            wait = save_while(pane, background)
            wait()
            self.assertEqual(hashlib.sha256(background.read_bytes()).hexdigest(),
                             hashlib.sha256(ordinary.read_bytes()).hexdigest())

    def test_a_failed_write_raises_where_the_caller_waits(self):
        pane = Image.new('RGB', (8, 8))
        with tempfile.TemporaryDirectory() as tmp:
            wait = save_while(pane, Path(tmp) / 'missing' / 'proof.png')
            with self.assertRaises(OSError):
                wait()


if __name__ == '__main__':
    unittest.main()
