"""The learned result-card reader is part of the analyzer and travels with every copy of it.

The file is ignored by the repository's rule for model weights unless it is
named as an exception, and once it went missing from every bundle and image
for two days without a test noticing: each training gain the badge reader
missed was worked out from the difference between turns instead. This test
fails the moment the checkout lacks the reader or holds another file under
its name.
"""
import hashlib
import unittest
from pathlib import Path

READER = Path(__file__).resolve().parents[1] / "tracen_replay" / "data" / "reader.onnx"
# reader-final-v2, the reader trained on every recording; change it here
# together with the file.
SHA256 = "9890edf0143e46e18d05aa7ac0b0e78b0fe011dc8f1d6eea593091918f13e824"


class ReaderShips(unittest.TestCase):
    def test_the_reader_is_in_the_checkout(self):
        self.assertTrue(READER.is_file(), f"{READER} is missing: the bundles and images copy the analyzer from here")
        self.assertGreater(READER.stat().st_size, 1_000_000)
        self.assertEqual(hashlib.sha256(READER.read_bytes()).hexdigest(), SHA256)


if __name__ == "__main__":
    unittest.main()
