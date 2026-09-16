"""The reader dataset builder labels crops from the run's own checkpoints."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.build_reader_dataset import BADGE_BOXES, COUNTER_BOXES, build, label_reading


def reading(time, screen, evidence, **facts):
    return dict(source_timestamp_ms=time, evidence=evidence, screen=screen, facts=facts)


def pane(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (810, 1080), (255, 255, 255)).save(path)


class ReaderDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='reader-dataset-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_run(self, name):
        root = self.tmp / name
        report = dict(source=dict(name=f'{name}.mp4', sha256='a' * 64), gameplay_tracking=dict(
            readings=[
                reading(1000, 'training_result', 'gameplay/part-000-frame-000004.png',
                        result_values=dict(speed=137, stamina=None, power=187, guts=112, wit=150, skill_points=120)),
                reading(1250, 'training_result', 'training-inspection/1000/frame-000001.png',
                        result_values=dict(speed=137, stamina=190, power=187, guts=112, wit=150, skill_points=120)),
                reading(5000, 'lesson_selection', 'gameplay/part-000-frame-000020.png',
                        performance_points=dict(dance=30, passion=25, vocal=27, visual=10, composure=38)),
            ],
            checkpoints=[dict(first_seen_ms=3000, status='ocr_consensus',
                              values=dict(speed=137, stamina=190, power=197, guts=112, wit=150, skill_points=120))],
            events=[dict(id='e1', kind='outcome', first_seen_ms=2000,
                         effects=[dict(kind='stat_change', field='power', amount=10)])],
            performance_accounting=dict(checkpoints=[dict(first_seen_ms=4500, last_seen_ms=5500,
                                                          values=dict(dance=30, passion=25, vocal=27, visual=10, composure=38))]),
        ))
        root.mkdir(parents=True)
        (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        pane(root / 'gameplay/part-000-frame-000004.png')
        pane(root / 'gameplay/part-000-frame-000020.png')
        return root

    def test_labels_follow_the_next_checkpoint(self):
        checkpoints = [dict(first_seen_ms=3000, values=dict(speed=137))]
        self.assertEqual(label_reading(1000, 'speed', 137, checkpoints, [], 'stat_change'), ('confirmed', 137))
        self.assertEqual(label_reading(1000, 'speed', 131, checkpoints, [], 'stat_change'), ('hard', 137))
        self.assertEqual(label_reading(1000, 'speed', None, checkpoints, [], 'stat_change'), ('hard', 137))
        blocked = [dict(first_seen_ms=2000, effects=[dict(kind='stat_change', field='speed', amount=5)])]
        self.assertEqual(label_reading(1000, 'speed', 137, checkpoints, blocked, 'stat_change'), ('unlabeled', None))
        self.assertEqual(label_reading(1000, 'stamina', 137, checkpoints, [], 'stat_change'), ('unlabeled', None))
        # A checkpoint long after the reading is another visit.
        self.assertEqual(label_reading(1000, 'speed', 137, [dict(first_seen_ms=500_000, values=dict(speed=137))], [], 'stat_change'), ('unlabeled', None))
        # A counter is judged by the visit it belongs to, not by a later one.
        visit = [dict(first_seen_ms=900, last_seen_ms=1200, values=dict(dance=30)), dict(first_seen_ms=3000, last_seen_ms=3100, values=dict(dance=20))]
        self.assertEqual(label_reading(1000, 'dance', 30, visit, [], 'performance_change', 'span'), ('confirmed', 30))
        self.assertEqual(label_reading(1000, 'dance', 39, visit, [], 'performance_change', 'span'), ('hard', 30))
        self.assertEqual(label_reading(2000, 'dance', 30, visit, [], 'performance_change', 'span'), ('unlabeled', None))

    def test_builds_crops_manifest_and_splits(self):
        first = self.make_run('run-a')
        second = self.make_run('run-b')
        out = self.tmp / 'dataset'
        manifest = build(out, [first, second], holdout=['run-b'], groups={'run-a': 'g1', 'run-b': 'g2'})
        rows = [json.loads(line) for line in (out / 'crops.jsonl').read_text(encoding='utf-8').splitlines()]
        by = {(r['run'], r['kind'], r['field']): r for r in rows}
        self.assertEqual(by[('run-a', 'stat_badge', 'speed')]['status'], 'confirmed')
        self.assertEqual(by[('run-a', 'stat_badge', 'stamina')], dict(by[('run-a', 'stat_badge', 'stamina')], status='hard', label=190, read=None))
        self.assertEqual(by[('run-a', 'stat_badge', 'power')]['status'], 'unlabeled')
        self.assertEqual(by[('run-a', 'performance_counter', 'dance')]['status'], 'confirmed')
        # Only the ordinary pass's full panes are cropped; an inspection frame is not.
        self.assertEqual(sum(r['run'] == 'run-a' for r in rows), 6 + 5)
        self.assertEqual({r['split'] for r in rows if r['run'] == 'run-b'}, {'holdout'})
        self.assertEqual([r['group'] for r in manifest['runs']], ['g1', 'g2'])
        badge = Image.open(out / by[('run-a', 'stat_badge', 'speed')]['crop'])
        box = BADGE_BOXES['speed']
        self.assertEqual(badge.size, (box[2] - box[0], box[3] - box[1]))
        counter = Image.open(out / by[('run-a', 'performance_counter', 'dance')]['crop'])
        box = COUNTER_BOXES['dance']
        self.assertEqual(counter.size, (box[2] - box[0], box[3] - box[1]))
        self.assertEqual(sum(row['crops'] for row in manifest['counts']), len(rows))


if __name__ == '__main__':
    unittest.main()
