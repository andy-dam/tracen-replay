import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from tests.test_gameplay import workspace_temp
from tests.test_full_recording import FakeReader
from tracen_replay.full_recording import (
    _generate_race_quantity_refinement,
    analyze_frames,
    cached_readings,
)
from tracen_replay.pipeline import PipelineError


class RaceQuantityPipelineTests(unittest.TestCase):
    def test_fresh_normal_route_requests_source_bound_fixed_quantity_sidecars(self):
        with workspace_temp() as root, patch(
                'tracen_replay.race_quantity_refinement.generate',
                return_value={'source_sha256': 'a' * 64, 'artifact_count': 2},
        ) as generate:
            result = _generate_race_quantity_refinement(
                Path(root),
                model_dir=Path('.local/models/rapidocr'),
            )

        self.assertEqual(result['artifact_count'], 2)
        generate.assert_called_once_with(
            Path(root),
            model_dir=Path('.local/models/rapidocr'),
            fixed_quantity_windows=True,
        )

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
