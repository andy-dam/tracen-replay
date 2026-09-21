"""Standard output carries the terminal JSON object and nothing else.

A finished analysis on a Mac failed with "worker stdout is not a JSON object:
invalid character 'E'": something other than the interpreter's own printing
had written to standard output. This runs the reservation in a real process
and writes to standard output the three ways that escape a redirected
``sys.stdout``: straight to file descriptor 1, from a child process, and
from ``print`` after the reservation.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

PROGRAM = r"""
import os, subprocess, sys
from tracen_replay import analysis_job
analysis_job._reserve_stdout()
os.write(1, b"E5RT: a native library wrote this\n")
subprocess.run([sys.executable, "-c", "print('a child process wrote this')"], check=True)
print("the interpreter printed this")
analysis_job._emit({"schema_version": "test", "status": "succeeded"})
"""


class ReservedStdoutTests(unittest.TestCase):
    def test_only_the_terminal_object_reaches_stdout(self):
        analyzer = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", PROGRAM], cwd=analyzer, capture_output=True,
                                text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertEqual(json.loads(result.stdout), {"schema_version": "test", "status": "succeeded"})
        self.assertEqual(len(result.stdout.strip().splitlines()), 1, result.stdout)
        for noise in ("E5RT: a native library wrote this", "a child process wrote this", "the interpreter printed this"):
            self.assertIn(noise, result.stderr)

    def test_the_version_query_still_answers_on_stdout(self):
        analyzer = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "tracen_replay.analysis_job", "--worker-version"],
                                cwd=analyzer, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("worker_version", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
