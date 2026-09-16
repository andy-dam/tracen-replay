"""How a result-card reader does on a reader dataset, per field, judged by the card.

Every result box in the dataset carries the value before and after the
training and its gain (from the stat bars bracketing the card) and what the
analyzer's own reader read. A reader's reads are judged the way the
accounting uses them:

- ``value read``: a card's stat counts as read when at least one of its
  frames yields the value before or after the training;
- ``gain recovered``: a card whose training raised the stat counts when at
  least one frame yields the gain as an overlay, or the value after the
  training (the stat bar before the card gives the rest);
- ``false values`` and ``false gains``: frames whose value is neither the
  value before nor after nor strictly between them (a count-up), or whose
  gain is not the card's gain; any read of a blank box is false.

Performance counters are judged per frame against the consensus of their
menu visit.

    python -m tools.reader_baseline DATASET [--markdown OUT.md]

The table is the baseline a learned reader is compared against; it is
written to the local records, never to the repository.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

FIELD_ORDER = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points', 'dance', 'passion', 'vocal', 'visual', 'composure')


def parse(text):
    """A transcription's value and gain: ``190/1341`` is (190, None), ``+16`` is (None, 16), empty is (None, None)."""
    if not text:
        return None, None
    if text.startswith('+'):
        return (None, int(text[1:])) if text[1:].isdigit() else (None, None)
    head = text.split('/', 1)[0]
    return (int(head), None) if head.isdigit() else (None, None)


def current_reads(rows):
    """The analyzer's own reads of each row, as ``(value, gain)``."""
    return [(row.get('read_value'), row.get('read_gain')) for row in rows]


def judge(rows, reads):
    """Frame counts per (split, kind, field) and card outcomes per (split, field, visit) for one reader."""
    frames = defaultdict(lambda: dict(frames=0, value_reads=0, false_values=0, gain_reads=0, false_gains=0, exact=0))
    cards = {}
    for row, (value, gain) in zip(rows, reads):
        if row['kind'] == 'performance_counter':
            if row.get('expected') is None:
                continue
            cell = frames[(row['split'], row['kind'], row['field'])]
            cell['frames'] += 1
            if value is not None:
                cell['value_reads'] += 1
                cell['exact'] += value == row['expected']
                cell['false_values'] += value != row['expected']
            continue
        blank = row.get('content') == 'blank'
        if row.get('before') is None and not blank:
            continue
        cell = frames[(row['split'], row['kind'], row['field'])]
        cell['frames'] += 1
        if row.get('before') is not None:
            card = cards.setdefault((row['split'], row['field'], row['visit']),
                                    dict(gain=row['gain'], value_read=False, gain_recovered=False))
        if blank:
            # A box with no ink shows nothing: any read of it is false.
            cell['value_reads'] += value is not None
            cell['false_values'] += value is not None
            cell['gain_reads'] += gain is not None
            cell['false_gains'] += gain is not None
            continue
        low, high = sorted((row['before'], row['after']))
        if value is not None:
            cell['value_reads'] += 1
            if value in (row['before'], row['after']):
                card['value_read'] = True
                if value == row['after']:
                    card['gain_recovered'] = True
            elif not low < value < high:
                cell['false_values'] += 1
        if gain is not None:
            cell['gain_reads'] += 1
            if row['gain'] and gain == row['gain']:
                card['gain_recovered'] = True
            else:
                cell['false_gains'] += 1
    return frames, cards


def union_cards(*card_sets):
    """Card outcomes when several readers' reads are pooled."""
    out = {}
    for cards in card_sets:
        for key, card in cards.items():
            merged = out.setdefault(key, dict(gain=card['gain'], value_read=False, gain_recovered=False))
            merged['value_read'] |= card['value_read']
            merged['gain_recovered'] |= card['gain_recovered']
    return out


def summarize(frames, cards):
    """One row per (split, kind, field) and a total per (split, kind), in a fixed order."""
    card_cells = defaultdict(lambda: dict(cards=0, cards_read=0, gain_cards=0, gains_recovered=0))
    for (split, field, _), card in cards.items():
        cell = card_cells[(split, field)]
        cell['cards'] += 1
        cell['cards_read'] += card['value_read']
        if card['gain']:
            cell['gain_cards'] += 1
            cell['gains_recovered'] += card['gain_recovered']
    rows = []
    for (split, kind, field), cell in frames.items():
        rows.append(dict(split=split, kind=kind, field=field, **cell,
                         **(card_cells[(split, field)] if kind == 'result_box' else dict(cards=0, cards_read=0, gain_cards=0, gains_recovered=0))))
    totals = defaultdict(lambda: defaultdict(int))
    for row in rows:
        total = totals[(row['split'], row['kind'])]
        for key, value in row.items():
            if isinstance(value, int):
                total[key] += value
    rows += [dict(split=s, kind=k, field='all', **dict(t)) for (s, k), t in totals.items()]
    order = {f: i for i, f in enumerate(FIELD_ORDER + ('all',))}
    return sorted(rows, key=lambda r: (r['split'], r['kind'], order.get(r['field'], 99)))


def pct(part, whole):
    return '-' if not whole else f'{100 * part / whole:.1f}%'


def markdown(table, manifest=None, title='Current reader'):
    lines = [f'### {title}', '']
    if manifest:
        lines += [f"Dataset built {manifest.get('built_at')} from {len(manifest.get('runs', []))} runs, {manifest.get('crops')} crops.", '']
    lines += ['| split | field | cards | value read | gain cards | gain recovered | frames | false values | false gains |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in table:
        if row['kind'] != 'result_box':
            continue
        lines.append(f"| {row['split']} | {row['field']} | {row['cards']} | {pct(row['cards_read'], row['cards'])} | {row['gain_cards']} | "
                     f"{pct(row['gains_recovered'], row['gain_cards'])} | {row['frames']} | {row['false_values']} | {row['false_gains']} |")
    counters = [row for row in table if row['kind'] == 'performance_counter']
    if counters:
        lines += ['', '| split | counter | frames | read | exact | false |', '|---|---|---:|---:|---:|---:|']
        for row in counters:
            lines.append(f"| {row['split']} | {row['field']} | {row['frames']} | {pct(row['value_reads'], row['frames'])} | "
                         f"{pct(row['exact'], row['frames'])} | {row['false_values']} |")
    lines += ['', 'value read: cards with a frame yielding the value before or after the training. gain recovered: cards whose '
                  'training raised the stat with a frame yielding the gain or the value after. false: frames whose value is none '
                  'of before, after or a count-up between them, or whose gain is not the card\'s.']
    return '\n'.join(lines) + '\n'


def load(dataset):
    dataset = Path(dataset)
    rows = [json.loads(line) for line in (dataset / 'crops.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    manifest_path = dataset / 'manifest.json'
    return rows, (json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('dataset', type=Path, help='directory holding crops.jsonl and manifest.json')
    parser.add_argument('--markdown', type=Path, help='write the table here as well as printing it')
    args = parser.parse_args(argv)
    rows, manifest = load(args.dataset)
    text = markdown(summarize(*judge(rows, current_reads(rows))), manifest)
    print(text, end='')
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
