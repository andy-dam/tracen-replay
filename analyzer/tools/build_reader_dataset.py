"""Build a crop dataset for a learned badge and counter reader from finished runs.

The analyzer already labels the crops it reads: a stat badge on a training
result card shows a value that the next stat-bar consensus either confirms or
contradicts, and a performance counter on the lesson menu likewise meets the
next performance checkpoint. This tool walks run roots that still hold their
gameplay panes, cuts the fixed crops the reader sees, and writes each with the
value the run's own evidence settled on:

- ``confirmed``: the reader's value equals the checkpoint that vouches for
  the moment (for a lesson-menu counter, the consensus of the visit the frame
  belongs to; for a result-card badge, the next stat bar within two minutes
  with no receipt for that field in between);
- ``hard``: the reader read nothing, or its value differs from that checkpoint
  (the checkpoint value is the label);
- ``unlabeled``: no checkpoint vouches for the moment, so the crop has an
  image but no trusted value.

Nothing is inferred beyond that arithmetic. Runs are the unit of splitting:
``--holdout`` names runs that never feed training, ``--group`` tags each run
with the recorder it came from, and the manifest records both so a model is
only ever judged on recordings and recorders it did not learn from.

    python -m tools.build_reader_dataset --output DIR --run ROOT [--run ROOT ...]
        [--holdout NAME ...] [--group NAME=GROUP ...]

A run root is a directory holding ``report.json`` and the ``gameplay/`` panes
(810x1080 crops of the game pane) the report's readings point at. A pruned
run can be given its panes back with ``analysis_job --reparse-only
--rehydrate-frames`` first.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

# Pane-space boxes, as the recognizer crops them (gameplay.py subtracts the
# pane's left edge, 148, from the screen x). Result card badges sit in two
# rows of three; skill points have their own box. Lesson-menu counters sit
# in one row across the top.
PANE_LEFT = 148
STAT_FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit')
CURRENCIES = ('dance', 'passion', 'vocal', 'visual', 'composure')
BADGE_BOXES = {field: ((322, 518, 714)[i % 3], 834 if i < 3 else 952) for i, field in enumerate(STAT_FIELDS)}
BADGE_BOXES = {field: (x, y, x + 126, y + 42) for field, (x, y) in BADGE_BOXES.items()}
SKILL_BOX = (708, 952, 810, 990)
COUNTER_BOXES = {CURRENCIES[0]: (345, 91, 389, 119)}
COUNTER_BOXES.update({CURRENCIES[i]: (344 + 104 * i, 88, 394 + 104 * i, 123) for i in range(1, 5)})

# A checkpoint further than this after the reading is another visit, not the
# state the card showed.
MAX_CHECKPOINT_GAP_MS = 120_000

KINDS = {
    # screen, facts key, boxes, checkpoint list, receipt kind that changes the field, labeling mode
    'stat_badge': ('training_result', 'result_values', {**BADGE_BOXES, 'skill_points': SKILL_BOX}, 'checkpoints', 'stat_change', 'next'),
    'performance_counter': ('lesson_selection', 'performance_points', COUNTER_BOXES, 'performance_checkpoints', 'performance_change', 'span'),
}


def pane_box(box):
    return (box[0] - PANE_LEFT, box[1], box[2] - PANE_LEFT, box[3])


def _changes_between(events, kind, field, start, end):
    """Receipt amounts for ``field`` first seen after ``start`` and at or before ``end``."""
    out = []
    for event in events:
        time = event.get('first_seen_ms')
        if not isinstance(time, int) or not start < time <= end:
            continue
        for effect in event.get('effects', []) or []:
            if effect.get('kind') == kind and effect.get('field') == field and isinstance(effect.get('amount'), int):
                out.append(effect['amount'])
    return out


def label_reading(time, field, read, checkpoints, events, receipt_kind, mode='next'):
    """Settle one crop's label against the checkpoint that vouches for the field.

    A checkpoint is a consensus over a visit to a screen that shows the
    value. In ``span`` mode the crop came from such a screen, so the
    checkpoint whose visit contains the reading is its label (the lesson
    menu's counters). In ``next`` mode the crop came from a card no
    checkpoint is built from, so the next checkpoint within two minutes is
    the label, provided no receipt changed the field in between (the result
    card's badges against the next stat bar).

    Returns ``(status, label)``: ``confirmed`` when the read value equals the
    checkpoint; ``hard`` with the checkpoint's value when the reader missed
    or misread it; ``unlabeled`` when no checkpoint can vouch for the moment.
    """
    def shows(c):
        return isinstance(c.get('first_seen_ms'), int) and isinstance((c.get('values') or {}).get(field), int)
    if mode == 'span':
        spanning = [c for c in checkpoints if shows(c) and c['first_seen_ms'] <= time <= c.get('last_seen_ms', c['first_seen_ms'])]
        if not spanning:
            return 'unlabeled', None
        checkpoint = spanning[0]
    else:
        following = [c for c in checkpoints if shows(c) and time <= c['first_seen_ms'] <= time + MAX_CHECKPOINT_GAP_MS]
        if not following:
            return 'unlabeled', None
        checkpoint = min(following, key=lambda c: c['first_seen_ms'])
        if _changes_between(events, receipt_kind, field, time, checkpoint['first_seen_ms']):
            return 'unlabeled', None
    expected = checkpoint['values'][field]
    if read == expected:
        return 'confirmed', expected
    return 'hard', expected


def load_run(root):
    root = Path(root)
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    tracking = report['gameplay_tracking']
    checkpoints = {
        'checkpoints': [c for c in tracking.get('checkpoints', []) if c.get('status') in (None, 'ocr_consensus', 'verified')],
        'performance_checkpoints': list((tracking.get('performance_accounting') or {}).get('checkpoints', [])),
    }
    return report, tracking, checkpoints


def build(output, runs, holdout=(), groups=None):
    """Write the dataset under ``output`` and return its manifest."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    groups = dict(groups or {})
    holdout = set(holdout)
    rows = []
    manifest_runs = []
    counts = Counter()
    for root in runs:
        root = Path(root)
        name = root.name if root.name != 'run' else root.parent.name
        report, tracking, checkpoints = load_run(root)
        split = 'holdout' if name in holdout else 'train'
        run_counts = Counter()
        seen = set()
        for reading in tracking.get('readings', []):
            evidence = reading.get('evidence')
            if not isinstance(evidence, str) or not evidence.startswith('gameplay/'):
                continue  # only the ordinary pass's panes are a fixed, full-pane crop
            for kind, (screen, facts_key, boxes, checkpoint_key, receipt_kind, mode) in KINDS.items():
                if reading.get('screen') != screen:
                    continue
                values = (reading.get('facts') or {}).get(facts_key)
                if not isinstance(values, dict):
                    continue
                pane_path = root / evidence
                if not pane_path.is_file():
                    run_counts['missing_pane'] += 1
                    continue
                pane = None
                for field, box in boxes.items():
                    key = (evidence, kind, field)
                    if key in seen:
                        continue
                    seen.add(key)
                    read = values.get(field) if isinstance(values.get(field), int) else None
                    status, label = label_reading(reading['source_timestamp_ms'], field, read,
                                                  checkpoints[checkpoint_key], tracking.get('events', []), receipt_kind, mode)
                    if pane is None:
                        pane = Image.open(pane_path).convert('RGB')
                        if pane.size != (810, 1080):
                            raise ValueError(f'{pane_path}: expected an 810x1080 gameplay pane, got {pane.size}')
                    stem = re.sub(r'[^A-Za-z0-9]+', '-', Path(evidence).stem)
                    crop_rel = Path(name) / kind / field / f'{stem}.png'
                    crop_path = output / crop_rel
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    pane.crop(pane_box(box)).save(crop_path)
                    rows.append(dict(run=name, split=split, group=groups.get(name), kind=kind, field=field,
                                     crop=crop_rel.as_posix(), frame=evidence, source_timestamp_ms=reading['source_timestamp_ms'],
                                     box=list(box), read=read, label=label, status=status))
                    run_counts[status] += 1
                    counts[(split, kind, status)] += 1
        manifest_runs.append(dict(run=name, root=str(root), split=split, group=groups.get(name),
                                  source_sha256=report.get('source', {}).get('sha256'), counts=dict(run_counts)))
    manifest = dict(
        built_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        crops=len(rows),
        runs=manifest_runs,
        counts=[dict(split=s, kind=k, status=st, crops=n) for (s, k, st), n in sorted(counts.items())],
        label_rule='confirmed: the read value equals the checkpoint that vouches for the moment (a counter: the consensus of its own visit; '
                   'a badge: the next stat bar within two minutes with no receipt for the field in between); '
                   'hard: the reader missed or misread it, the checkpoint is the label; unlabeled: no checkpoint vouches for the moment.',
    )
    with (output / 'crops.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--output', type=Path, required=True, help='dataset directory to create')
    parser.add_argument('--run', type=Path, action='append', required=True, help='run root holding report.json and gameplay/')
    parser.add_argument('--holdout', action='append', default=[], help='run name that never feeds training')
    parser.add_argument('--group', action='append', default=[], help='NAME=GROUP: the recorder a run came from')
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f'Refusing to write into a non-empty directory: {args.output}')
    groups = dict(item.split('=', 1) for item in args.group)
    manifest = build(args.output, args.run, args.holdout, groups)
    for row in manifest['counts']:
        print(f"{row['split']:8} {row['kind']:20} {row['status']:10} {row['crops']:6}")
    print(f"{manifest['crops']} crops from {len(manifest['runs'])} runs -> {args.output}")


if __name__ == '__main__':
    main()
