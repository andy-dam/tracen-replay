"""The reader dataset builder labels crops from the run's own checkpoints."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.build_reader_dataset import BADGE_BOXES, COUNTER_BOXES, build, demote_animating, label_gain, label_reading


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

    def test_gain_overlays_are_labeled_from_the_training_event(self):
        events = [dict(id='t1', kind='training', first_seen_ms=1000, last_seen_ms=1500, deltas=dict(speed=15, skill_points=7))]
        self.assertEqual(label_gain(1200, 'speed', 15, events), ('confirmed', 15))
        self.assertEqual(label_gain(1200, 'speed', 18, events), ('hard', 15))
        self.assertEqual(label_gain(1200, 'speed', None, events), ('hard', 15))
        self.assertEqual(label_gain(1200, 'guts', None, events), ('confirmed', 0))
        self.assertEqual(label_gain(1200, 'guts', 9, events), ('hard', 0))
        self.assertEqual(label_gain(9000, 'speed', 15, events), ('unlabeled', None))

    def test_frames_beside_a_confirmed_read_are_animating_not_hard(self):
        def r(kind, field, time, status):
            return dict(run='x', kind=kind, field=field, source_timestamp_ms=time, status=status)
        rows = [r('stat_badge', 'speed', 1000, 'hard'), r('stat_badge', 'speed', 1250, 'confirmed'), r('stat_badge', 'speed', 1500, 'hard'),
                r('stat_badge', 'speed', 60000, 'hard'), r('stat_badge', 'speed', 60250, 'hard'),
                r('stat_badge', 'guts', 1000, 'hard'), r('gain_overlay', 'speed', 1000, 'hard'), r('gain_overlay', 'speed', 1100, 'confirmed')]
        demote_animating(rows)
        self.assertEqual([x['status'] for x in rows], ['animating', 'confirmed', 'animating', 'hard', 'hard', 'hard', 'animating', 'confirmed'])

    def test_inspection_frames_feed_the_gain_overlay_kind(self):
        root = self.make_run('run-c')
        report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
        report['gameplay_tracking']['events'].append(dict(id='t1', kind='training', first_seen_ms=1000, last_seen_ms=1300, deltas=dict(speed=15)))
        report['gameplay_tracking']['readings'] += [
            reading(1100, 'training_result', 'training-inspection/1000/frame-000001.png', training_gains=dict(speed=15)),
            reading(1150, 'training_result', 'training-inspection/1000/frame-000002.png', training_gains=dict()),
        ]
        (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        pane(root / 'training-inspection/1000/frame-000001.png')
        pane(root / 'training-inspection/1000/frame-000002.png')
        out = self.tmp / 'dataset-c'
        build(out, [root])
        rows = [json.loads(line) for line in (out / 'crops.jsonl').read_text(encoding='utf-8').splitlines()]
        gains = {(r['frame'], r['field']): r for r in rows if r['kind'] == 'gain_overlay'}
        self.assertEqual(gains[('training-inspection/1000/frame-000001.png', 'speed')]['status'], 'confirmed')
        self.assertEqual(gains[('training-inspection/1000/frame-000001.png', 'guts')], dict(gains[('training-inspection/1000/frame-000001.png', 'guts')], status='confirmed', label=0, read=None))
        # The second frame read no gain beside a frame that did: the overlay was moving.
        self.assertEqual(gains[('training-inspection/1000/frame-000002.png', 'speed')]['status'], 'animating')
        self.assertTrue((out / gains[('training-inspection/1000/frame-000002.png', 'speed')]['crop']).is_file())
        # Badges are still cut only from the ordinary pass's panes.
        self.assertEqual({r['frame'] for r in rows if r['kind'] == 'stat_badge'}, {'gameplay/part-000-frame-000004.png'})

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
