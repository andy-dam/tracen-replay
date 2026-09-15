import os
import subprocess
import sys
import unittest
from pathlib import Path

from tracen_replay.code_identity import code_digest, function_digest


def _sample(value):
    if value in {'alpha', 'beta', 'gamma', 'delta'}:
        return (value, 1.5, None, b'x')
    return [item for item in (value,) if item]


def _other(value):
    if value in {'alpha', 'beta', 'gamma', 'delta'}:
        return (value, 2.5, None, b'x')
    return [item for item in (value,) if item]


class CodeDigestTests(unittest.TestCase):
    def test_digest_is_unchanged_by_running_the_code(self):
        before = function_digest(_sample)
        for _ in range(50):
            _sample('alpha'); _sample('zeta')
        self.assertEqual(function_digest(_sample), before)

    def test_digest_sees_a_changed_constant_and_ignores_line_numbers(self):
        self.assertNotEqual(function_digest(_sample), function_digest(_other))
        moved = compile('\n\n\n' + 'def f(v):\n    return v + 1\n', 'a.py', 'exec')
        same = compile('def f(v):\n    return v + 1\n', 'zzz/b.py', 'exec')
        code_a = next(c for c in moved.co_consts if hasattr(c, 'co_code'))
        code_b = next(c for c in same.co_consts if hasattr(c, 'co_code'))
        self.assertEqual(code_digest(code_a).hexdigest(), code_digest(code_b).hexdigest())

    def test_digest_is_the_same_under_different_hash_seeds_and_processes(self):
        script = ('from tests.test_code_identity import _sample\n'
                  'from tracen_replay.code_identity import function_digest\n'
                  'print(function_digest(_sample).hex())\n')
        results = set()
        for seed in ('1', '2', '3'):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, env=env,
                                 cwd=str(Path(__file__).resolve().parents[1]), check=True)
            results.add(out.stdout.strip())
        results.add(function_digest(_sample).hex())
        self.assertEqual(len(results), 1, results)


class PackageDigestTests(unittest.TestCase):
    def test_package_digest_is_stable_and_follows_the_source(self):
        import tempfile
        from tracen_replay.code_identity import package_digest
        first = package_digest()
        self.assertEqual(len(first), 64)
        self.assertEqual(package_digest(), first)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'a.py').write_text('x = 1' + chr(10), encoding='utf-8')
            Path(tmp, 'b.txt').write_text('ignored', encoding='utf-8')
            one = package_digest(tmp)
            Path(tmp, 'a.py').write_text('x = 2' + chr(10), encoding='utf-8')
            self.assertNotEqual(package_digest(tmp), one)
            Path(tmp, 'a.py').write_text('x = 1' + chr(10), encoding='utf-8')
            self.assertEqual(package_digest(tmp), one)


@unittest.skipUnless(Path('.local/models/rapidocr').is_dir(), 'OCR models are not installed')
class ReaderFingerprintTests(unittest.TestCase):
    def test_fingerprint_survives_use_and_a_fresh_process(self):
        from PIL import Image
        from tracen_replay.vision import NeuralReader
        first = NeuralReader('.local/models/rapidocr')
        first.read(Image.new('RGB', (810, 1080), (30, 30, 30)))
        second = NeuralReader('.local/models/rapidocr')
        self.assertEqual(first.fingerprint, second.fingerprint)
        script = ('from tracen_replay.vision import NeuralReader\n'
                  "print(NeuralReader('.local/models/rapidocr').fingerprint)\n")
        out = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                             cwd=str(Path(__file__).resolve().parents[1]), check=True)
        self.assertEqual(out.stdout.strip().splitlines()[-1], first.fingerprint)


if __name__ == '__main__':
    unittest.main()
