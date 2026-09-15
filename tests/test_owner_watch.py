import subprocess
import sys
import threading
import unittest

from tracen_replay.analysis_job import JobInputError, watch_owner


class OwnerWatchTests(unittest.TestCase):
    def test_the_callback_fires_once_the_owner_process_is_gone(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        gone = threading.Event()
        try:
            watch_owner(owner.pid, on_gone=gone.set, interval=0.1)
            self.assertFalse(gone.wait(0.5), "the watch must not fire while the owner runs")
            owner.kill()
            owner.wait(10)
            self.assertTrue(gone.wait(5), "the watch must fire after the owner has died")
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(10)

    def test_a_dead_or_invalid_owner_is_rejected_up_front(self):
        owner = subprocess.Popen([sys.executable, "-c", "pass"])
        owner.wait(10)
        with self.assertRaises(JobInputError) as caught:
            watch_owner(owner.pid, on_gone=lambda: None, interval=0.1)
        self.assertEqual(caught.exception.code, "invalid_owner_pid")


if __name__ == "__main__":
    unittest.main()
