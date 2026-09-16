"""The current reader's accuracy and coverage on a reader dataset, per field.

Reads the ``crops.jsonl`` written by ``build_reader_dataset`` and reports, for
every split, kind and field, how the reader that produced the run did on the
crops whose label is known:

- ``labeled``: crops with a confirmed or checkpoint-settled value;
- ``coverage``: the share of those where the reader accepted a value at all;
- ``accuracy``: the share of accepted values that equal the label;
- ``exact``: the share of all labeled crops read correctly (coverage times
  accuracy), which is the number a learned reader has to beat;
- ``visits`` and ``visits read``: the same by visit rather than by frame,
  since a card is sampled on several frames and one correct read of it is
  what the accounting needs.

    python -m tools.reader_baseline DATASET [--markdown OUT.md]

The table is the baseline the first learned reader is compared against; it
is written to the local records, never to the repository.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

FIELD_ORDER = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points', 'dance', 'passion', 'vocal', 'visual', 'composure')


# Crops of one field closer together than this belong to one visit of the
# screen: the same card or menu sampled frame after frame.
VISIT_GAP_MS = 1500


def visits(rows):
    """Group labeled rows of one run, kind and field into visits by time."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.get('run'), row['split'], row['kind'], row['field'])].append(row)
    out = []
    for key, group in grouped.items():
        group.sort(key=lambda r: r.get('source_timestamp_ms') or 0)
        current = []
        for row in group:
            if current and (row.get('source_timestamp_ms') or 0) - (current[-1].get('source_timestamp_ms') or 0) > VISIT_GAP_MS:
                out.append((key, current))
                current = []
            current.append(row)
        if current:
            out.append((key, current))
    return out


def summarize(rows):
    """Per (split, kind, field) counts over the labeled rows, in a fixed order.

    Frames are counted one by one (``labeled``, ``read``, ``correct``) and
    again by visit (``cards``, ``cards_read``): a result card is sampled on
    several frames, many of them mid-animation, so the per-frame coverage
    understates how often the value was read at all. A visit counts as read
    when at least one of its frames was read correctly.
    """
    labeled = [r for r in rows if r.get('status') in ('confirmed', 'hard') and r.get('label') is not None]
    cells = defaultdict(lambda: dict(labeled=0, read=0, correct=0, cards=0, cards_read=0))
    for row in labeled:
        cell = cells[(row['split'], row['kind'], row['field'])]
        cell['labeled'] += 1
        if row.get('read') is not None:
            cell['read'] += 1
            if row['read'] == row['label']:
                cell['correct'] += 1
    for (run, split, kind, field), group in visits(labeled):
        cell = cells[(split, kind, field)]
        cell['cards'] += 1
        if any(r.get('read') is not None and r['read'] == r['label'] for r in group):
            cell['cards_read'] += 1
    out = []
    for key in sorted(cells, key=lambda k: (k[0], k[1], FIELD_ORDER.index(k[2]) if k[2] in FIELD_ORDER else 99, k[2])):
        cell = cells[key]
        out.append(dict(split=key[0], kind=key[1], field=key[2], **cell,
                        coverage=cell['read'] / cell['labeled'] if cell['labeled'] else None,
                        accuracy=cell['correct'] / cell['read'] if cell['read'] else None,
                        exact=cell['correct'] / cell['labeled'] if cell['labeled'] else None,
                        card_exact=cell['cards_read'] / cell['cards'] if cell['cards'] else None))
    return out


def totals(table):
    """One row per (split, kind) summing the fields."""
    sums = defaultdict(lambda: dict(labeled=0, read=0, correct=0, cards=0, cards_read=0))
    for row in table:
        cell = sums[(row['split'], row['kind'])]
        for k in ('labeled', 'read', 'correct', 'cards', 'cards_read'):
            cell[k] += row[k]
    return [dict(split=s, kind=k, field='all', **c,
                 coverage=c['read'] / c['labeled'] if c['labeled'] else None,
                 accuracy=c['correct'] / c['read'] if c['read'] else None,
                 exact=c['correct'] / c['labeled'] if c['labeled'] else None,
                 card_exact=c['cards_read'] / c['cards'] if c['cards'] else None)
            for (s, k), c in sorted(sums.items())]


def pct(value):
    return '—' if value is None else f'{100 * value:.1f}%'


def markdown(table, manifest=None):
    lines = []
    if manifest:
        lines.append(f"Dataset built {manifest.get('built_at')} from {len(manifest.get('runs', []))} runs, {manifest.get('crops')} crops.")
        lines.append('')
    lines.append('| split | kind | field | labeled frames | coverage | accuracy | exact | visits | visits read |')
    lines.append('|---|---|---|---:|---:|---:|---:|---:|---:|')
    for row in table:
        lines.append(f"| {row['split']} | {row['kind']} | {row['field']} | {row['labeled']} | {pct(row['coverage'])} | {pct(row['accuracy'])} | {pct(row['exact'])} | {row['cards']} | {pct(row['card_exact'])} |")
    lines.append('')
    lines.append('Per frame: coverage is the share of labeled frames where the reader accepted a value, accuracy the share of accepted values equal to the label, exact the share of all labeled frames read correctly. '
                 'Per visit (frames of one field closer than 1.5 s): visits read is the share of visits with at least one correct read.')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('dataset', type=Path, help='directory holding crops.jsonl and manifest.json')
    parser.add_argument('--markdown', type=Path, help='write the table here as well as printing it')
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in (args.dataset / 'crops.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    manifest_path = args.dataset / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else None
    table = summarize(rows)
    text = markdown(table + totals(table), manifest)
    print(text, end='')
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
