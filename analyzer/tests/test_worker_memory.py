"""An OCR worker collects garbage every so many frames."""
import unittest
from unittest import mock

from tracen_replay import worker_memory


class WorkerMemoryTests(unittest.TestCase):
    def test_garbage_is_collected_once_every_so_many_frames(self):
        with mock.patch.object(worker_memory, '_frames', 0), mock.patch.object(worker_memory.gc, 'collect') as collect:
            for _ in range(worker_memory.EVERY * 3 - 1):
                worker_memory.frame_done()
            self.assertEqual(collect.call_count, 2)
            worker_memory.frame_done()
            self.assertEqual(collect.call_count, 3)


if __name__ == '__main__':
    unittest.main()
