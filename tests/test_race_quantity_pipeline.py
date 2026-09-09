import json
import unittest
from unittest.mock import patch

from PIL import Image
from tests.test_gameplay import workspace_temp
from tests.test_full_recording import FakeReader
from tracen_replay.full_recording import analyze_frames, cached_readings
from tracen_replay.pipeline import PipelineError


class RaceQuantityPipelineTests(unittest.TestCase):
    def test_cached_pipeline_rejects_unbound_refinement_artifact(self):
        with workspace_temp() as root:
            Image.new('RGB', (1920, 1080), 'white').save(root/'frame.png')
            report = dict(frames=[dict(id='one', evidence='frame.png', source_timestamp_ms=0)])
            with patch('tracen_replay.full_recording.NeuralReader', FakeReader):
                analyze_frames(report, root, workers=1)
            directory = root/'race-quantity-refinement'
            directory.mkdir()
            (directory/'one.json').write_text(json.dumps(dict(minimum_confidence=97, slots=[])),
                                             encoding='utf-8')
            with self.assertRaisesRegex(PipelineError, 'Race quantity refinement evidence invalid'):
                cached_readings(report, root)


if __name__ == '__main__':
    unittest.main()
