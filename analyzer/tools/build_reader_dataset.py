"""Build a crop dataset for a learned result-card reader from finished runs.

The analyzer already knows what a training result card should show. The
card has a box per stat: the value and its cap ("190/1341"), a plain value
for skill points, or, while the card animates, the training's gain as a
"+N" overlay on top of the box, or nothing yet while the card slides in.
The stat bar the run observed before the training and the one after it
bracket the card; with the stat receipts read in between, they give the
value before the training, the value after it, and so the gain, none of
which depends on what the reader made of the card. Every box of every
training result frame is cut and written with what it shows:

- ``badge``: the reader's value equals the value before or after the
  training (a card shows either, depending on the moment); the target is
  ``value/``, the value and the slash that marks a stat box, since the cap
  after it is often half covered and the accounting never needs it; skill
  points have no cap, and their target is the plain ``value``;
- ``gain``: the reader's overlay equals the bracketed gain; the target is
  ``+gain``;
- ``blank``: the box holds no ink at all; the target is empty;
- ``unknown``: anything else (the big animated digits, a covered badge, a
  value the reader missed). No target, but the before, after and gain are
  kept, so a reader can still be judged on the box.

The lesson menu's performance counters are a second kind, whose target is
the consensus of the menu visit the frame belongs to.

Runs are the unit of splitting: ``--holdout`` names runs that never feed
training, ``--group`` tags each run with the recorder it came from, and the
manifest records both so a model is only ever judged on recordings and
recorders it did not learn from.

    python -m tools.build_reader_dataset --output DIR --run [NAME=]ROOT [--run ...]
        [--video NAME=RECORDING ...] [--holdout NAME ...] [--group NAME=GROUP ...]
        [--kinds result_box performance_counter]

A run root is a directory holding ``report.json``. Its frames are read from
the images its readings point at (the ordinary pass's ``gameplay/`` panes and
the card's high-rate rereads under ``training-inspection/``), or, for a run
given ``--video``, decoded from its source recording at each reading's
timestamp, which also covers a run whose working data was pruned.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

# Pane-space boxes, as the recognizer crops them (gameplay.py subtracts the
# pane's left edge, 148, from the screen x). Result card boxes sit in two
# rows of three, skill points last; lesson-menu counters sit in one row
# across the top.
PANE_LEFT = 148
STAT_FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit')
CURRENCIES = ('dance', 'passion', 'vocal', 'visual', 'composure')
BADGE_BOXES = {field: ((322, 518, 714)[i % 3], 834 if i < 3 else 952) for i, field in enumerate(STAT_FIELDS)}
BADGE_BOXES = {field: (x, y, x + 126, y + 42) for field, (x, y) in BADGE_BOXES.items()}
SKILL_BOX = (708, 952, 810, 990)
RESULT_BOXES = {**BADGE_BOXES, 'skill_points': SKILL_BOX}
COUNTER_BOXES = {CURRENCIES[0]: (345, 91, 389, 119)}
COUNTER_BOXES.update({CURRENCIES[i]: (344 + 104 * i, 88, 394 + 104 * i, 123) for i in range(1, 5)})

# A stat bar observed further than this from the card belongs to another turn.
MAX_CHECKPOINT_GAP_MS = 120_000
# The result frames between two bracketing stat bars must fit one card.
MAX_CARD_SPAN_MS = 20_000
# A gain outside this range means the bracket missed something (a purchase,
# an unread receipt), so the card gets no values from it.
MAX_GAIN = 150
# A box is blank when less than this share of its pixels is ink: dark and
# not blue, which covers the badges' brown digits and the overlays' red
# outline but not the pastel or blue backgrounds behind the card.
BLANK_INK_SHARE = 0.002


# The analyzer's result boxes start exactly where a three-digit value begins,
# so a four-digit value, the card's bounce or a zooming "+N" overlay pushes
# digits outside them. The learned reader cuts wider boxes: this much more on
# the left, top, right and bottom, in pane pixels.
READER_MARGIN = (30, 8, 6, 8)


def pane_box(box, margin=(0, 0, 0, 0)):
    left, top, right, bottom = margin
    return (box[0] - PANE_LEFT - left, box[1] - top, box[2] - PANE_LEFT + right, box[3] + bottom)


def ink_share(image):
    """The share of a crop's pixels that are dark and not blue."""
    rgb = np.asarray(image.convert('RGB')).astype(np.int32)
    luminance = 299 * rgb[..., 0] + 587 * rgb[..., 1] + 114 * rgb[..., 2]
    return float(((luminance < 140_000) & (rgb[..., 0] >= rgb[..., 2])).mean())


def stat_receipts(events):
    """``(time, field, amount)`` for every stat receipt outside a training, in time order."""
    out = []
    for event in events:
        time = event.get('first_seen_ms')
        if event.get('kind') == 'training' or not isinstance(time, int):
            continue
        for effect in event.get('effects') or []:
            if effect.get('kind') == 'stat_change' and effect.get('field') in RESULT_BOXES and isinstance(effect.get('amount'), int):
                out.append((time, effect['field'], effect['amount']))
    return sorted(out)


def bracket(checkpoints, time):
    """The last stat bar ending before ``time`` and the first starting after it, each within the gap."""
    before = [c for c in checkpoints if c['last_seen_ms'] <= time and time - c['last_seen_ms'] <= MAX_CHECKPOINT_GAP_MS]
    after = [c for c in checkpoints if c['first_seen_ms'] >= time and c['first_seen_ms'] - time <= MAX_CHECKPOINT_GAP_MS]
    return (before[-1] if before else None), (after[0] if after else None)


def card_values(previous, following, receipts, field, time):
    """The field's value before and after the training shown at ``time``, and its gain.

    The stat bar before the card plus the receipts read between it and the
    card is the value before; the stat bar after the card less the receipts
    read between the card and it is the value after. Returns three Nones
    when either bar lacks the field or the gain is implausible.
    """
    before = (previous.get('values') or {}).get(field)
    after = (following.get('values') or {}).get(field)
    if not isinstance(before, int) or not isinstance(after, int):
        return None, None, None
    before += sum(amount for t, f, amount in receipts if f == field and previous['last_seen_ms'] < t <= time)
    after -= sum(amount for t, f, amount in receipts if f == field and time < t <= following['first_seen_ms'])
    if not 0 <= after - before <= MAX_GAIN:
        return None, None, None
    return before, after, after - before


def classify(field, value, gain_read, before, after, gain, ink):
    """What a result box shows, and its target text; see the module docstring."""
    if value is None and gain_read is None and ink < BLANK_INK_SHARE:
        return 'blank', ''
    if before is None:
        return 'unknown', None
    if value is not None and gain_read is None and value in (before, after):
        return 'badge', str(value) if field == 'skill_points' else f'{value}/'
    if gain_read is not None and value is None and gain and gain_read == gain:
        return 'gain', f'+{gain_read}'
    return 'unknown', None


class VideoPanes:
    """Gameplay panes decoded from a run's source recording at its readings' timestamps.

    Asked for times in increasing order, it decodes forward and seeks only
    when the next time is behind or more than a few seconds ahead, so a card's
    frames cost one seek. A frame is the one within half a frame interval of
    the time; the pane is the same 810x1080 cut the analyzer reads.
    """
    def __init__(self, path, size_bytes=None):
        import cv2
        path = Path(path)
        if size_bytes is not None and path.stat().st_size != size_bytes:
            raise ValueError(f'{path}: {path.stat().st_size} bytes, but the report was made from {size_bytes}')
        self.cv2 = cv2
        self.capture = cv2.VideoCapture(str(path))
        if not self.capture.isOpened():
            raise ValueError(f'{path}: cannot be opened')
        self.tolerance = 500 / (self.capture.get(cv2.CAP_PROP_FPS) or 30) + 1
        self.current = None

    def pane(self, time):
        cv2 = self.cv2
        if self.current is None or time < self.current[0] - self.tolerance or time > self.current[0] + 3000:
            self.capture.set(cv2.CAP_PROP_POS_MSEC, max(0, time - 1000))
            self.current = None
        while self.current is None or self.current[0] < time - self.tolerance:
            ok, frame = self.capture.read()
            if not ok:
                return None
            self.current = (self.capture.get(cv2.CAP_PROP_POS_MSEC), frame)
        if abs(self.current[0] - time) > self.tolerance:
            return None
        rgb = cv2.cvtColor(self.current[1], cv2.COLOR_BGR2RGB)
        if rgb.shape[:2] != (1080, 1920):
            rgb = cv2.resize(rgb, (1920, 1080), interpolation=cv2.INTER_AREA)
        return Image.fromarray(np.ascontiguousarray(rgb[:, PANE_LEFT:PANE_LEFT + 810]))


def load_run(root):
    root = Path(root)
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    tracking = report['gameplay_tracking']

    def normalized(items):
        out = []
        for c in items:
            if isinstance(c.get('first_seen_ms'), int) and isinstance(c.get('values'), dict):
                out.append(dict(c, last_seen_ms=c.get('last_seen_ms') if isinstance(c.get('last_seen_ms'), int) else c['first_seen_ms']))
        return sorted(out, key=lambda c: c['first_seen_ms'])
    stats = normalized(c for c in tracking.get('checkpoints', []) if c.get('status') in (None, 'ocr_consensus', 'verified'))
    counters = normalized((tracking.get('performance_accounting') or {}).get('checkpoints', []))
    return report, tracking, stats, counters


def build(output, runs, holdout=(), groups=None, videos=None, kinds=('result_box', 'performance_counter')):
    """Write the dataset under ``output`` and return its manifest.

    ``runs`` are run roots, or ``(name, root)`` pairs. A run named in
    ``videos`` has its frames decoded from that recording instead of read
    from its root, so any training result reading counts, whatever reread
    wrote its frame.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    groups = dict(groups or {})
    videos = dict(videos or {})
    holdout = set(holdout)
    rows = []
    manifest_runs = []
    counts = Counter()
    for run in runs:
        name, root = run if isinstance(run, tuple) else (None, run)
        root = Path(root)
        name = name or (root.name if root.name != 'run' else root.parent.name)
        report, tracking, stats, counter_checkpoints = load_run(root)
        split = 'holdout' if name in holdout else 'train'
        receipts = stat_receipts(tracking.get('events', []))
        readings = sorted((r for r in tracking.get('readings', []) if isinstance(r.get('evidence'), str)
                           and isinstance(r.get('source_timestamp_ms'), int)), key=lambda r: r['source_timestamp_ms'])
        video = VideoPanes(videos[name], report.get('source', {}).get('size_bytes')) if name in videos else None
        results = [r for r in readings if r.get('screen') == 'training_result' and 'result_box' in kinds
                   and (video is not None or r['evidence'].startswith('gameplay/') or r['evidence'].startswith('training-inspection/'))]
        result_ids = {id(r) for r in results}
        result_times = [r['source_timestamp_ms'] for r in results]
        run_counts = Counter()
        for reading in readings:
            screen = reading.get('screen')
            if id(reading) in result_ids:
                boxes, kind = RESULT_BOXES, 'result_box'
            elif screen == 'lesson_selection' and 'performance_counter' in kinds and reading['evidence'].startswith('gameplay/'):
                boxes, kind = COUNTER_BOXES, 'performance_counter'
            else:
                continue
            facts = reading.get('facts') or {}
            time = reading['source_timestamp_ms']
            if video is not None:
                pane = video.pane(time)
                if pane is None:
                    run_counts['missing_frame'] += 1
                    continue
            else:
                pane_path = root / reading['evidence']
                if not pane_path.is_file():
                    run_counts['missing_pane'] += 1
                    continue
                with Image.open(pane_path) as image:
                    pane = image.convert('RGB')
                if pane.size != (810, 1080):
                    raise ValueError(f'{pane_path}: expected an 810x1080 gameplay pane, got {pane.size}')
            if kind == 'result_box':
                previous, following = bracket(stats, time)
                if previous is not None and following is not None:
                    span = [t for t in result_times if previous['last_seen_ms'] <= t <= following['first_seen_ms']]
                    if max(span) - min(span) > MAX_CARD_SPAN_MS:
                        previous = following = None
                visit = f"{name}:{previous['first_seen_ms']}" if previous is not None and following is not None else None
            else:
                spanning = [c for c in counter_checkpoints if c['first_seen_ms'] <= time <= c['last_seen_ms']]
                visit = f"{name}:{spanning[0]['first_seen_ms']}" if spanning else None
            for field, box in boxes.items():
                margin = READER_MARGIN if kind == 'result_box' else (0, 0, 0, 0)
                crop = pane.crop(pane_box(box, margin))
                box = (box[0] - margin[0], box[1] - margin[1], box[2] + margin[2], box[3] + margin[3])
                # The whole evidence path names the crop, so a pass frame and a
                # reread frame of the same number never collide.
                stem = re.sub(r'[^A-Za-z0-9]+', '-', reading['evidence'].rsplit('.', 1)[0])
                crop_rel = Path(name) / kind / field / f'{stem}.png'
                (output / crop_rel).parent.mkdir(parents=True, exist_ok=True)
                crop.save(output / crop_rel)
                row = dict(run=name, split=split, group=groups.get(name), kind=kind, field=field, crop=crop_rel.as_posix(),
                           frame=reading['evidence'], source_timestamp_ms=time, box=list(box), visit=visit)
                if kind == 'result_box':
                    value = (facts.get('result_values') or {}).get(field)
                    cap = (facts.get('stat_caps') or {}).get(field)
                    gain_read = (facts.get('training_gains') or {}).get(field)
                    value = value if isinstance(value, int) else None
                    cap = cap if isinstance(cap, int) else None
                    gain_read = gain_read if isinstance(gain_read, int) else None
                    before = after = gain = None
                    if visit is not None:
                        before, after, gain = card_values(previous, following, receipts, field, time)
                    ink = ink_share(crop)
                    content, target = classify(field, value, gain_read, before, after, gain, ink)
                    row.update(before=before, after=after, gain=gain, read_value=value, read_cap=cap, read_gain=gain_read,
                               ink=round(ink, 4), content=content, target=target)
                else:
                    value = (facts.get('performance_points') or {}).get(field)
                    value = value if isinstance(value, int) else None
                    expected = (spanning[0]['values'].get(field) if spanning else None)
                    confirmed = isinstance(expected, int) and value == expected
                    row.update(expected=expected if isinstance(expected, int) else None, read_value=value,
                               content='counter' if confirmed else 'unknown', target=str(value) if confirmed else None)
                rows.append(row)
                run_counts[(kind, row['content'])] += 1
                counts[(split, kind, row['content'])] += 1
        manifest_runs.append(dict(run=name, root=str(root), video=str(videos[name]) if name in videos else None,
                                  split=split, group=groups.get(name), source_sha256=report.get('source', {}).get('sha256'),
                                  counts={('/'.join(key) if isinstance(key, tuple) else key): n for key, n in sorted(run_counts.items(), key=str)}))
    manifest = dict(
        built_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        crops=len(rows),
        runs=manifest_runs,
        counts=[dict(split=s, kind=k, content=c, crops=n) for (s, k, c), n in sorted(counts.items())],
        label_rule='result_box: badge (target value/, or value for skill points) when the read value equals the value before or after '
                   'the training bracketed by the stat bars and receipts, gain when the read overlay equals the bracketed gain, '
                   'blank when the box holds no ink, unknown otherwise; performance_counter: the consensus of the menu visit.',
    )
    with (output / 'crops.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--output', type=Path, required=True, help='dataset directory to create')
    parser.add_argument('--run', action='append', required=True, help='[NAME=]ROOT: a run root holding report.json')
    parser.add_argument('--video', action='append', default=[], help="NAME=RECORDING: decode the run's frames from its recording")
    parser.add_argument('--holdout', action='append', default=[], help='run name that never feeds training')
    parser.add_argument('--group', action='append', default=[], help='NAME=GROUP: the recorder a run came from')
    parser.add_argument('--kinds', nargs='+', default=['result_box', 'performance_counter'], choices=['result_box', 'performance_counter'])
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f'Refusing to write into a non-empty directory: {args.output}')

    def pair(item):
        # NAME=ROOT, where NAME has no path separator; a bare ROOT is named after its directory.
        name, sep, rest = item.partition('=')
        return (name, Path(rest)) if sep and not re.search(r'[\\/:]', name) else (None, Path(item))
    runs = [pair(item) for item in args.run]
    groups = dict(item.split('=', 1) for item in args.group)
    videos = {name: Path(path) for name, path in (item.split('=', 1) for item in args.video)}
    manifest = build(args.output, runs, args.holdout, groups, videos, tuple(args.kinds))
    for row in manifest['counts']:
        print(f"{row['split']:8} {row['kind']:20} {row['content']:8} {row['crops']:6}")
    print(f"{manifest['crops']} crops from {len(manifest['runs'])} runs -> {args.output}")


if __name__ == '__main__':
    main()
