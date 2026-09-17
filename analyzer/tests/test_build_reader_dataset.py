"""The reader dataset labels each result box by what it shows, from the stat bars around the card."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.build_reader_dataset import (BADGE_BOXES, COUNTER_BOXES, PANE_LEFT, READER_MARGIN, SKILL_BOX, VideoPanes,
                                        build, card_values, classify, ink_share)


def reading(time, screen, evidence, **facts):
    return dict(source_timestamp_ms=time, evidence=evidence, screen=screen, facts=facts)


def pane(path, inked=()):
    """A white 810x1080 pane with brown ink drawn in the named result boxes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (810, 1080), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for field in inked:
        box = SKILL_BOX if field == 'skill_points' else BADGE_BOXES[field]
        draw.rectangle((box[0] - PANE_LEFT + 10, box[1] + 10, box[0] - PANE_LEFT + 40, box[1] + 30), fill=(107, 74, 46))
    image.save(path)


class CardValueTests(unittest.TestCase):
    def test_receipts_on_either_side_of_the_card_are_taken_out(self):
        before_bar = dict(first_seen_ms=0, last_seen_ms=500, values=dict(power=300))
        after_bar = dict(first_seen_ms=5000, last_seen_ms=5500, values=dict(power=330))
        receipts = [(1000, 'power', 4), (4000, 'power', 6), (4000, 'speed', 9)]
        self.assertEqual(card_values(before_bar, after_bar, receipts, 'power', 2000), (304, 324, 20))
        self.assertEqual(card_values(before_bar, after_bar, receipts, 'speed', 2000), (None, None, None))
        # A negative or huge gain means the bracket missed something.
        self.assertEqual(card_values(before_bar, dict(after_bar, values=dict(power=250)), [], 'power', 2000), (None, None, None))

    def test_what_a_box_shows(self):
        # A stat box's target is its value and the slash; the cap after it is left out.
        self.assertEqual(classify('speed', 115, None, 100, 115, 15, 0.2), ('badge', '115/'))
        self.assertEqual(classify('speed', 100, None, 100, 115, 15, 0.2), ('badge', '100/'))
        self.assertEqual(classify('skill_points', 127, None, 120, 127, 7, 0.2), ('badge', '127'))
        self.assertEqual(classify('skill_points', None, 7, 120, 127, 7, 0.2), ('gain', '+7'))
        self.assertEqual(classify('speed', None, 9, 100, 115, 15, 0.2), ('unknown', None))
        self.assertEqual(classify('speed', 108, None, 100, 115, 15, 0.2), ('unknown', None))
        self.assertEqual(classify('speed', None, None, None, None, None, 0.0), ('blank', ''))
        self.assertEqual(classify('speed', None, None, None, None, None, 0.2), ('unknown', None))
        self.assertEqual(classify('speed', 115, None, None, None, None, 0.2), ('unknown', None))

    def test_ink_is_dark_and_not_blue(self):
        self.assertEqual(ink_share(Image.new('RGB', (10, 10), (255, 255, 255))), 0.0)
        self.assertEqual(ink_share(Image.new('RGB', (10, 10), (40, 90, 215))), 0.0)
        self.assertEqual(ink_share(Image.new('RGB', (10, 10), (107, 74, 46))), 1.0)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='reader-dataset-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_run(self, name):
        root = self.tmp / name
        values_before = dict(speed=100, stamina=200, power=300, guts=50, wit=60, skill_points=120)
        values_after = dict(speed=115, stamina=200, power=305, guts=50, wit=60, skill_points=127)
        report = dict(source=dict(name=f'{name}.mp4', sha256='a' * 64), gameplay_tracking=dict(
            readings=[
                reading(2000, 'training_result', 'gameplay/part-000-frame-000008.png',
                        result_values=dict(speed=115, stamina=200), stat_caps=dict(speed=1600, stamina=1300), training_gains={}),
                reading(2100, 'training_result', 'training-inspection/2000/frame-000001.png',
                        result_values=dict(speed=100), stat_caps=dict(speed=1600), training_gains=dict(skill_points=7)),
                reading(2200, 'training_result', 'training-inspection/2000/frame-000002.png',
                        result_values=dict(speed=115), stat_caps=dict(speed=1600), training_gains={}),
                reading(6000, 'lesson_selection', 'gameplay/part-000-frame-000020.png',
                        performance_points=dict(dance=30, passion=None, vocal=27, visual=10, composure=38)),
            ],
            checkpoints=[dict(first_seen_ms=0, last_seen_ms=500, status='ocr_consensus', values=values_before),
                         dict(first_seen_ms=5000, last_seen_ms=5500, status='ocr_consensus', values=values_after)],
            events=[dict(id='e1', kind='outcome', first_seen_ms=4000, effects=[dict(kind='stat_change', field='power', amount=5)])],
            performance_accounting=dict(checkpoints=[dict(first_seen_ms=5800, last_seen_ms=6200,
                                                          values=dict(dance=30, passion=25, vocal=27, visual=10, composure=38))]),
        ))
        root.mkdir(parents=True)
        (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        pane(root / 'gameplay/part-000-frame-000008.png', inked=('speed', 'stamina'))
        pane(root / 'training-inspection/2000/frame-000001.png', inked=('speed', 'skill_points'))
        pane(root / 'training-inspection/2000/frame-000002.png', inked=('speed',))
        pane(root / 'gameplay/part-000-frame-000020.png')
        return root

    def test_boxes_are_labeled_by_what_they_show(self):
        first = self.make_run('run-a')
        second = self.make_run('run-b')
        out = self.tmp / 'dataset'
        manifest = build(out, [first, second], holdout=['run-b'], groups={'run-a': 'g1', 'run-b': 'g2'})
        rows = [json.loads(line) for line in (out / 'crops.jsonl').read_text(encoding='utf-8').splitlines()]
        by = {(r['run'], r['frame'], r['field']): r for r in rows}
        pass_frame, reread = 'gameplay/part-000-frame-000008.png', 'training-inspection/2000/frame-000001.png'
        self.assertEqual((by[('run-a', pass_frame, 'speed')]['content'], by[('run-a', pass_frame, 'speed')]['target']), ('badge', '115/'))
        self.assertEqual((by[('run-a', reread, 'speed')]['content'], by[('run-a', reread, 'speed')]['target']), ('badge', '100/'))
        self.assertEqual((by[('run-a', pass_frame, 'stamina')]['target'], by[('run-a', pass_frame, 'stamina')]['read_cap']), ('200/', 1300))
        self.assertEqual((by[('run-a', reread, 'skill_points')]['content'], by[('run-a', reread, 'skill_points')]['target']), ('gain', '+7'))
        self.assertEqual((by[('run-a', pass_frame, 'power')]['content'], by[('run-a', pass_frame, 'power')]['target']), ('blank', ''))
        power = by[('run-a', pass_frame, 'power')]
        self.assertEqual((power['before'], power['after'], power['gain']), (300, 300, 0))
        self.assertEqual(by[('run-a', pass_frame, 'speed')]['visit'], by[('run-a', reread, 'speed')]['visit'])
        counter = by[('run-a', 'gameplay/part-000-frame-000020.png', 'dance')]
        self.assertEqual((counter['kind'], counter['content'], counter['target']), ('performance_counter', 'counter', '30'))
        self.assertEqual(by[('run-a', 'gameplay/part-000-frame-000020.png', 'passion')]['content'], 'unknown')
        self.assertEqual({r['split'] for r in rows if r['run'] == 'run-b'}, {'holdout'})
        self.assertEqual([r['group'] for r in manifest['runs']], ['g1', 'g2'])
        with Image.open(out / by[('run-a', pass_frame, 'speed')]['crop']) as crop:
            # The learned reader's box is the analyzer's, widened on every side.
            box = BADGE_BOXES['speed']
            left, top, right, bottom = READER_MARGIN
            self.assertEqual(crop.size, (box[2] - box[0] + left + right, box[3] - box[1] + top + bottom))
            self.assertEqual(by[('run-a', pass_frame, 'speed')]['box'], [box[0] - left, box[1] - top, box[2] + right, box[3] + bottom])
        with Image.open(out / counter['crop']) as counter_crop:
            box = COUNTER_BOXES['dance']
            self.assertEqual(counter_crop.size, (box[2] - box[0], box[3] - box[1]))
        self.assertEqual(sum(row['crops'] for row in manifest['counts']), len(rows))
        self.assertEqual(len({r['crop'] for r in rows}), len(rows))

    def test_panes_decoded_from_the_recording(self):
        import cv2
        import numpy as np
        path = self.tmp / 'recording.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (320, 180))
        for index in range(30):
            writer.write(np.full((180, 320, 3), index * 8, dtype=np.uint8))
        writer.release()
        panes = VideoPanes(path)
        first = panes.pane(1000)
        self.assertEqual(first.size, (810, 1080))
        self.assertAlmostEqual(float(np.asarray(first).mean()), 80, delta=6)
        self.assertAlmostEqual(float(np.asarray(panes.pane(2500)).mean()), 200, delta=6)
        # Going back seeks; a time past the end has no frame.
        self.assertAlmostEqual(float(np.asarray(panes.pane(500)).mean()), 40, delta=6)
        self.assertIsNone(panes.pane(9000))
        with self.assertRaises(ValueError):
            VideoPanes(path, size_bytes=path.stat().st_size + 1)

    def test_a_run_can_take_its_frames_from_its_recording(self):
        import cv2
        import numpy as np
        root = self.make_run('run-v')
        for image in root.rglob('*.png'):
            image.unlink()
        path = self.tmp / 'recording.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 20, (320, 180))
        for _ in range(140):
            writer.write(np.full((180, 320, 3), 255, dtype=np.uint8))
        writer.release()
        out = self.tmp / 'dataset-v'
        manifest = build(out, [('named', root)], videos={'named': path}, kinds=('result_box',))
        rows = [json.loads(line) for line in (out / 'crops.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual({r['run'] for r in rows}, {'named'})
        self.assertEqual({r['kind'] for r in rows}, {'result_box'})
        # Every result reading gets its six boxes, including the rereads' frames.
        self.assertEqual(len(rows), 3 * 6)
        self.assertEqual(manifest['runs'][0]['video'], str(path))


if __name__ == '__main__':
    unittest.main()
