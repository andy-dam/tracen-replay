import copy
import hashlib
import json
import shutil
import unittest
import uuid

from PIL import Image

from tests import localdata


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RaceIdentityRefinementTests(unittest.TestCase):
    def setUp(self):
        self.test_root = localdata.local('test-runs').resolve()
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(self.cleanup)
        (self.root / 'neural').mkdir()
        self.model = {'model.onnx': 'a' * 64}
        self.raws, self.observations, frames = [], [], []
        for index, timestamp in enumerate((0, 250)):
            image = Image.new('RGB', (1920, 1080), 'white')
            image.putpixel((0, 0), (index, 0, 0))
            source = self.root / f'f{index}.png'
            image.save(source)
            pane = image.crop((148, 0, 958, 1080))
            proof = self.root / f'p{index}.png'
            pane.save(proof)
            lines = [
                dict(text='Example Final', confidence=94.6, box=[328, 427, 486, 453]),
                dict(text='Fans 12000 (+3000)', confidence=99, box=[340, 680, 640, 730]),
                dict(text='Example Turf 2000m (Medium) Left', confidence=99, box=[277, 461, 595, 492]),
                dict(text='2nd', confidence=99, box=[330, 200, 430, 260]),
            ]
            raw = dict(lines=lines, regions={}, header='', current_grid=False, result_grid=False,
                       source_timestamp_ms=timestamp, source_frame_sha256=digest(source.read_bytes()),
                       gameplay_sha256=digest(pane.tobytes()), model_sha256=self.model,
                       engine_fingerprint='b' * 64, evidence=proof.name)
            self.write(f'neural/f{index}.json', raw)
            frames.append(dict(id=f'f{index}', source_timestamp_ms=timestamp, evidence=source.name))
            views = []
            for padding in (4, 10, 20):
                box = [328 - 148 - padding, 421, 486 - 148 + padding, 459]
                views.append(dict(index=0, original=copy.deepcopy(lines[0]), crop_box=box,
                                  crop_pixel_sha256=digest(pane.crop(box).tobytes()),
                                  text='Example Final', confidence=98,
                                  model_sha256=copy.deepcopy(self.model), engine_fingerprint='d' * 64))
            self.raws.append(raw)
            self.observations.append(dict(frame_id=f'f{index}', views=views))
        self.capture = dict(source={'sha256': 'c' * 64}, frames=frames)
        self.write('capture.json', self.capture)

    def cleanup(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.test_root))
        shutil.rmtree(self.root)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
