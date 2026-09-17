"""Train and compare the first learned reader of a training result card.

The reader takes one result box (a stat's value and cap, a "+N" gain
overlay, or nothing) and transcribes it: a string over the digits, "/" and
"+", decoded by CTC over the crop's width, with a confidence per character
(the peak over the columns it spans). A stat box is transcribed as its value
and the slash (``value/``); the cap after the slash is left out, because it
is often half covered and the accounting never needs it. A read counts in
the shapes the game renders (``value/`` for a stat, a plain value for skill
points, ``+gain``, no leading zeros) when its least certain character is at
or above a confidence threshold.

Training runs in rounds. The first trains on the labeled boxes of the
training runs. Each later round retrains from nothing with the hard frames
added: training boxes nobody read, each labeled with the text its card
allows (the value before or after the training, the gain) that the previous
round's model finds far likelier than any other. Each round is judged on
every box of the held-out runs the way the accounting uses a reader (see
``reader_baseline``): per card, whether its value or its gain was read, and
per frame, how many reads were false; per held-out run and over all of
them; and on all frames and on the ordinary pass's frames alone. The
analyzer's own reader is judged on the same boxes, alone and pooled with the
learned one.

Three conditions share the head, the data and the schedule:

- ``scratch``: our own convolutional trunk, trained from nothing;
- ``frozen``: an ImageNet-pretrained ResNet-18 trunk kept fixed;
- ``finetune``: the same trunk, trained along with the head.

The pretrained conditions are a comparison row only and are never exported:
the reader is our own. The ``scratch`` model is written to ONNX (input
``crop``: RGB in [0, 1], batch x 3 x 64 x 192), checked against onnxruntime,
and timed per box on the CPU in both runtimes.

    python -m tools.train_reader DATASET --output DIR [--conditions scratch frozen finetune]
        [--rounds N] [--epochs N] [--epoch-size N] [--threads N] [--device auto|cuda|cpu]

Requires the ``train`` extra (torch, torchvision, onnx). Training runs on the
GPU whenever PyTorch has CUDA; the default wheel on some platforms is the CPU
build, so install torch and torchvision from the PyTorch index for the
card's CUDA version (``--index-url https://download.pytorch.org/whl/cu130``).
Results, the model and the log go under ``--output``, in the local records.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from tools.reader_baseline import CARD_FIELDS, agreed, current_reads, judge, load, summarize, union_cards

CHARS = '0123456789/+'
BLANK = 0
HEIGHT, WIDTH = 64, 192
BATCH = 64
THRESHOLDS = (0.5, 0.9, 0.97)
# Crops of one card's box showing one target kept for training: a card's
# high-rate rereads repeat the same picture dozens of times.
MAX_PER_TARGET = 8
SHARE_WEIGHTS = dict(badge=2)
# The shapes a result box can take, as the game renders them: a stat's value
# followed by the slash before its cap (anything after the slash is ignored),
# a plain skill point value, or a gain; no number has a leading zero, so a
# value half covered by a sparkle ("01/") is not a read.
STAT_VALUE = re.compile(r'[1-9]\d{0,3}/\d{0,4}')
PLAIN_VALUE = re.compile(r'0|[1-9]\d{0,3}')
GAIN = re.compile(r'\+[1-9]\d{0,2}')
# A hard training box is labeled by the text its card allows that the model
# finds at least this probable and this many times likelier than any other.
# Looser picks take frames whose number is only partly visible, which teaches
# the model to fill in hidden digits.
VERIFIED_FLOOR = 0.9
VERIFIED_RATIO = 100.0
# Held-out boxes are judged on every frame, and on the ordinary pass's frames
# alone: what a reader gets without the analyzer's high-rate rereads.
SCOPES = {'all frames': lambda row: True, 'pass frames': lambda row: row['frame'].startswith('gameplay/')}


def encode(text):
    """A target text as CTC class indices (0 is the blank)."""
    return [CHARS.index(ch) + 1 for ch in text]


def ctc_decode(columns, probabilities=None):
    """Greedy CTC decode of per-column class indices; repeats collapse, blanks drop.

    With the per-column probabilities of the chosen classes, also returns
    each emitted character's confidence: the peak over the columns it spans.
    """
    out = []
    confidences = []
    previous = BLANK
    for position, index in enumerate(columns):
        p = probabilities[position] if probabilities is not None else 1.0
        if index != BLANK and index != previous:
            out.append(CHARS[index - 1])
            confidences.append(p)
        elif index != BLANK:
            confidences[-1] = max(confidences[-1], p)
        previous = index
    return (''.join(out), confidences) if probabilities is not None else ''.join(out)


def learned_read(field, text, confidences, threshold):
    """A transcription as ``(value, gain)``, counted only in an accepted shape and at the threshold.

    The threshold applies to the characters the accounting uses: a stat's
    value and the slash that marks it as a stat box, a plain skill point
    value, or a gain.
    """
    if not text:
        return None, None
    if GAIN.fullmatch(text):
        return (None, int(text[1:])) if min(confidences) >= threshold else (None, None)
    if field == 'skill_points' and PLAIN_VALUE.fullmatch(text):
        return (int(text), None) if min(confidences) >= threshold else (None, None)
    if field != 'skill_points' and STAT_VALUE.fullmatch(text):
        slash = text.index('/')
        return (int(text[:slash]), None) if min(confidences[:slash + 1]) >= threshold else (None, None)
    return None, None


def candidates(row):
    """The texts a box's card allows: the value before, the value after, the gain, and nothing."""
    if row.get('before') is None:
        return []
    if row['field'] == 'skill_points':
        texts = [str(row['before']), str(row['after'])]
    else:
        texts = [f"{row['before']}/", f"{row['after']}/"]
    texts = list(dict.fromkeys(texts))
    if row.get('gain'):
        texts.append(f"+{row['gain']}")
    return texts + ['']


def pick_verified(texts, log_likelihoods):
    """The candidate a box is labeled with, or None: probable enough, far likelier than the rest, not empty."""
    order = sorted(range(len(texts)), key=lambda i: -log_likelihoods[i])
    best = order[0]
    if texts[best] == '' or math.exp(log_likelihoods[best]) < VERIFIED_FLOOR:
        return None
    if len(order) > 1 and log_likelihoods[best] - log_likelihoods[order[1]] < math.log(VERIFIED_RATIO):
        return None
    return texts[best]


def verified_labels(model, rows, cache, dataset):
    """Label the training boxes nobody read, by the text their card allows that the model clearly prefers.

    The stat bars around the card leave a box only a handful of possible
    texts; scoring each with the model's CTC likelihood picks one far more
    reliably than reading the box freely, and the pick is never a value the
    card could not show. These are the frames covered by sparkles, faded
    digits and zooming overlays that the first labels never reach.
    """
    import torch
    import torch.nn.functional as F
    device = next(model.parameters()).device
    model.eval()
    pool = [r for r in rows if r['split'] == 'train' and r['kind'] == 'result_box' and r['content'] == 'unknown' and candidates(r)]
    out = []
    with torch.no_grad():
        for start in range(0, len(pool), 256):
            chunk = pool[start:start + 256]
            for row in chunk:
                if row['crop'] not in cache:
                    cache[row['crop']] = load_crop(dataset / row['crop'])
            logp = model(as_input([cache[r['crop']] for r in chunk]).to(device))
            pairs = [(b, text) for b, row in enumerate(chunk) for text in candidates(row)]
            targets = [encode(text) for _, text in pairs]
            nll = F.ctc_loss(logp[:, [b for b, _ in pairs], :],
                             torch.tensor([t for target in targets for t in target], dtype=torch.long, device=device),
                             torch.full((len(pairs),), logp.shape[0], dtype=torch.long, device=device),
                             torch.tensor([len(target) for target in targets], dtype=torch.long, device=device),
                             blank=BLANK, reduction='none', zero_infinity=False).cpu().tolist()
            for b, row in enumerate(chunk):
                texts = [text for pair_b, text in pairs if pair_b == b]
                scores = [-nll[i] for i, (pair_b, _) in enumerate(pairs) if pair_b == b]
                text = pick_verified(texts, scores)
                if text is not None:
                    out.append(dict(row, target=text, content='gain' if text.startswith('+') else 'badge', verified=True))
    return out


def training_groups(rows, kinds):
    """Labeled training crops grouped by what they show, at most MAX_PER_TARGET per card box and target."""
    buckets = defaultdict(list)
    for row in rows:
        if row['split'] != 'train' or row['kind'] not in kinds or row.get('target') is None:
            continue
        # Verified hard frames keep their own buckets, so the cap never trades
        # them for easy frames of the same card.
        buckets[(row['run'], row.get('visit') or row['frame'], row['kind'], row['field'], row['target'], bool(row.get('verified')))].append(row)
    groups = defaultdict(list)
    for bucket in buckets.values():
        bucket.sort(key=lambda r: r['source_timestamp_ms'])
        if len(bucket) > MAX_PER_TARGET:
            step = len(bucket) / MAX_PER_TARGET
            bucket = [bucket[int(i * step)] for i in range(MAX_PER_TARGET)]
        for row in bucket:
            groups[row['content']].append(row)
    return dict(groups)


def epoch_rows(groups, size, rng):
    """One epoch: a weighted share from each group (badges, the hardest, count twice), drawn with
    replacement where a group is small."""
    names = sorted(groups)
    weights = {name: SHARE_WEIGHTS.get(name, 1) for name in names}
    picks = []
    for name in names:
        share = max(1, size * weights[name] // sum(weights.values()))
        pool = groups[name]
        picks += rng.sample(pool, share) if len(pool) >= share else [rng.choice(pool) for _ in range(share)]
    rng.shuffle(picks)
    return picks


def load_crop(path):
    """A crop as uint8 RGB, 64 x 192, channels first."""
    image = Image.open(path).convert('RGB').resize((WIDTH, HEIGHT), Image.BILINEAR)
    return np.ascontiguousarray(np.asarray(image, dtype=np.uint8).transpose(2, 0, 1))


def build_model(condition):
    import torch
    from torch import nn
    classes = len(CHARS) + 1

    class Normalize(nn.Module):
        def __init__(self, mean, std):
            super().__init__()
            self.register_buffer('mean', torch.tensor(mean).view(1, 3, 1, 1))
            self.register_buffer('std', torch.tensor(std).view(1, 3, 1, 1))

        def forward(self, x):
            return (x - self.mean) / self.std

    class Head(nn.Module):
        """Stack each column's rows (a cap's small digits sit low, a value's span the box), give each
        column its neighbours' context, and classify each column."""
        def __init__(self, channels, rows, width=192):
            super().__init__()
            self.context = nn.Sequential(nn.Conv1d(channels * rows, width, 3, padding=1), nn.ReLU(inplace=True),
                                         nn.Conv1d(width, width, 3, padding=1), nn.ReLU(inplace=True))
            self.linear = nn.Linear(width, classes)

        def forward(self, features):
            columns = self.context(features.flatten(1, 2))
            return self.linear(columns.permute(2, 0, 1)).log_softmax(dim=-1)

    if condition == 'scratch':
        def conv(i, o):
            return [nn.Conv2d(i, o, 3, padding=1, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True)]
        # Two convolutions at each of the first two scales resolve the thin
        # cap digits before the height is pooled away; the width is pooled
        # only twice, leaving 48 columns.
        trunk = nn.Sequential(*conv(3, 32), *conv(32, 32), nn.MaxPool2d((2, 2)),
                              *conv(32, 64), *conv(64, 64), nn.MaxPool2d((2, 2)),
                              *conv(64, 128), nn.MaxPool2d((2, 1)),
                              *conv(128, 128), nn.MaxPool2d((2, 1)))
        return nn.Sequential(Normalize([0.5] * 3, [0.5] * 3), trunk, Head(128, rows=HEIGHT // 16))
    import torchvision
    resnet = torchvision.models.resnet18(weights=torchvision.models.ResNet18_Weights.IMAGENET1K_V1)
    trunk = nn.Sequential(resnet.conv1, resnet.bn1, resnet.relu, resnet.maxpool, resnet.layer1, resnet.layer2)
    if condition == 'frozen':
        for parameter in trunk.parameters():
            parameter.requires_grad = False
    return nn.Sequential(Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]), trunk, Head(128, rows=HEIGHT // 8))


def as_input(arrays):
    import torch
    return torch.from_numpy(np.stack(arrays)).float().div_(255.0)


def augment(batch, rng):
    """Small shifts and changes of brightness and contrast; the boxes are otherwise fixed."""
    import torch
    batch = torch.roll(batch, shifts=(rng.randint(-2, 2), rng.randint(-3, 3)), dims=(2, 3))
    contrast = 1 + 0.3 * (rng.random() - 0.5)
    brightness = 0.15 * (rng.random() - 0.5)
    return ((batch - 0.5) * contrast + 0.5 + brightness).clamp_(0, 1)


def train_condition(condition, groups, cache, epochs, epoch_size, seed, log, device):
    import torch
    from torch import nn
    torch.manual_seed(seed)
    rng = random.Random(seed)
    model = build_model(condition).to(device)
    parameters = [p for p in model.parameters() if p.requires_grad]
    rate = 3e-4 if condition == 'finetune' else 2e-3
    optimizer = torch.optim.AdamW(parameters, lr=rate, weight_decay=1e-4)
    steps = epochs * math.ceil(epoch_size / BATCH)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=rate, total_steps=steps)
    loss_fn = nn.CTCLoss(blank=BLANK, zero_infinity=True)
    started = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        if condition == 'frozen':
            model[1].eval()
        picks = epoch_rows(groups, epoch_size, rng)
        total = 0.0
        for start in range(0, len(picks), BATCH):
            chunk = picks[start:start + BATCH]
            batch = augment(as_input([cache[r['crop']] for r in chunk]).to(device, non_blocking=True), rng)
            targets = [encode(r['target']) for r in chunk]
            logp = model(batch)
            loss = loss_fn(logp, torch.tensor([t for target in targets for t in target], dtype=torch.long, device=device),
                           torch.full((len(chunk),), logp.shape[0], dtype=torch.long, device=device),
                           torch.tensor([len(target) for target in targets], dtype=torch.long, device=device))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            schedule.step()
            total += loss.item() * len(chunk)
        log(f'{condition} epoch {epoch + 1}/{epochs} loss {total / len(picks):.4f} ({time.perf_counter() - started:.0f}s)')
    return model


def transcribe(model, arrays):
    """Each crop's text and the confidence of each of its characters."""
    import torch
    model.eval()
    device = next(model.parameters()).device
    out = []
    with torch.no_grad():
        for start in range(0, len(arrays), 256):
            top, index = model(as_input(arrays[start:start + 256]).to(device)).exp().max(dim=-1)
            top, index = top.cpu(), index.cpu()
            for b in range(top.shape[1]):
                out.append(ctc_decode(index[:, b].tolist(), top[:, b].tolist()))
    return out


def labeled_breakdown(rows, texts):
    """Exact transcriptions per content over labeled rows; for badges, whether the value is right."""
    cells = defaultdict(lambda: dict(n=0, exact=0, value=0))
    for row, (text, _) in zip(rows, texts):
        if row.get('target') is None:
            continue
        cell = cells[row['content']]
        cell['n'] += 1
        cell['exact'] += text == row['target']
        if row['content'] == 'badge':
            cell['value'] += text.partition('/')[0] == row['target'].partition('/')[0]
    return {content: dict(cell) for content, cell in sorted(cells.items())}


def card_scores(rows, reads):
    """Held-out result-box totals for one reader's reads: cards and false reads."""
    frames, cards = judge(rows, reads)
    table = [r for r in summarize(frames, cards) if r['split'] == 'holdout' and r['kind'] == 'result_box' and r['field'] == 'all']
    keys = CARD_FIELDS + ('false_values', 'false_gains')
    total = table[0] if table else dict.fromkeys(keys, 0)
    return {key: total[key] for key in keys}, cards


def union_scores(current_cards, learned_cards):
    pooled = union_cards(current_cards, learned_cards)
    outcomes = [(card, agreed(card)) for card in pooled.values()]
    return dict(cards_read=sum(c['value_read'] for c, _ in outcomes),
                gains_recovered=sum(c['gain_recovered'] for c, _ in outcomes if c['gain']),
                cards_agreed=sum(a[0] for _, a in outcomes), cards_agreed_false=sum(a[1] for _, a in outcomes),
                gains_agreed=sum(a[2] for c, a in outcomes if c['gain']), gains_agreed_false=sum(a[3] for _, a in outcomes))


def evaluate(holdout, reads, current_cards=None):
    """Scores and card outcomes per scope and per held-out run, and over all runs.

    With the current reader's card outcomes, each score also carries what
    the two readers read pooled.
    """
    scores, cards = {}, {}
    runs = sorted({row['run'] for row in holdout}) + ['all']
    for scope, keep in SCOPES.items():
        for run in runs:
            index = [i for i, row in enumerate(holdout) if keep(row) and run in ('all', row['run'])]
            score, card = card_scores([holdout[i] for i in index], [reads[i] for i in index])
            if current_cards is not None:
                score['pooled'] = union_scores(current_cards[scope][run], card)
            scores.setdefault(scope, {})[run] = score
            cards.setdefault(scope, {})[run] = card
    return scores, cards


def latency_ms(run, arrays, repeats=200):
    for i in range(10):
        run(arrays[i % len(arrays)])
    started = time.perf_counter()
    for i in range(repeats):
        run(arrays[i % len(arrays)])
    return 1000 * (time.perf_counter() - started) / repeats


def export_onnx(model, path, arrays):
    """Export with a dynamic batch; check onnxruntime agrees with torch; time both per crop on the CPU,
    and onnxruntime on DirectML too where the analyzer's OCR runs on it."""
    import onnxruntime as ort
    import torch
    model = model.cpu().eval()
    torch.onnx.export(model, as_input(arrays[:1]), str(path), input_names=['crop'], output_names=['log_probs'],
                      dynamic_axes={'crop': {0: 'batch'}, 'log_probs': {1: 'batch'}}, opset_version=17, dynamo=False)
    session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
    sample = as_input(arrays[:64])
    with torch.no_grad():
        expected = model(sample).argmax(-1).numpy()
    got = session.run(None, {'crop': sample.numpy()})[0].argmax(-1)
    with torch.no_grad():
        torch_ms = latency_ms(lambda a: model(as_input([a])), arrays)
    onnx_ms = latency_ms(lambda a: session.run(None, {'crop': as_input([a]).numpy()}), arrays)
    out = dict(agreement=float((expected == got).mean()), torch_ms_per_crop=torch_ms, onnx_ms_per_crop=onnx_ms)
    if 'DmlExecutionProvider' in ort.get_available_providers():
        directml = ort.InferenceSession(str(path), providers=['DmlExecutionProvider', 'CPUExecutionProvider'])
        out['onnx_directml_ms_per_crop'] = latency_ms(lambda a: directml.run(None, {'crop': as_input([a]).numpy()}), arrays)
    return out


def final_markdown(results):
    """What a final run records: what it trained on, and its export; nothing was held out to judge."""
    lines = ['### Final reader, trained on every run', '',
             f"{results['epochs']} epochs of {results['epoch_size']} crops per round on {results.get('device', 'cpu')}. "
             'Nothing was held out, so its accuracy is the held-out result of the same settings.', '',
             '| reader | round | training crops | verified hard frames |', '|---|---:|---:|---:|']
    for condition, entry in results['conditions'].items():
        for index, done in enumerate(entry.get('rounds', [])):
            lines.append(f"| {condition} | {index + 1} | {done['training_crops']} | {done['verified_hard_frames']} |")
    return lines


def results_markdown(results):
    def share(part, whole):
        return f"{part}/{whole} ({part / whole:.0%})" if whole else '-'
    if results.get('final'):
        lines = final_markdown(results)
        e = results.get('exported')
        if e:
            lines += ['', f"Exported {e['condition']}: onnxruntime agrees with torch on {e['agreement']:.1%} of columns; "
                          f"{e['onnx_ms_per_crop']:.2f} ms per box in onnxruntime on the CPU."]
        return '\n'.join(lines) + '\n'
    current = results['current_reader']
    lines = ['### Our reader against the current reader, per held-out recording', '',
             f"Confidence threshold 0.9. {results['epochs']} epochs of {results['epoch_size']} crops per round on "
             f"{results.get('device', 'cpu')}; each later round adds the hard training frames the previous round's model "
             "labeled from the stat bars. Pass frames are the ordinary 4-per-second pass alone, without the high-rate rereads.", '',
             'Value read: any frame of the card yields the value before or after the training. Two frames: at least two frames '
             'read the same value, the way the analyzer settles a card; "wrong" counts cards where two frames agree on a value the '
             'card cannot show.', '',
             '| frames | recording | reader | value read | value by two frames | two frames wrong | gain recovered | gain by two frames | two frames wrong | false value frames | false gain frames |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']

    def row(scope, run, name, t, pooled=None):
        p = pooled or t
        return (f"| {scope} | {run} | {name} | {share(p['cards_read'], t['cards'])} | {share(p['cards_agreed'], t['cards'])} | "
                f"{p['cards_agreed_false']} | {share(p['gains_recovered'], t['gain_cards'])} | {share(p['gains_agreed'], t['gain_cards'])} | "
                f"{p['gains_agreed_false']} | {'-' if pooled else t['false_values']} | {'-' if pooled else t['false_gains']} |")
    for scope, runs in current.items():
        for run, s in runs.items():
            lines.append(row(scope, run, 'current', s))
            for condition, entry in results['conditions'].items():
                if 'skipped' in entry:
                    continue
                for index, done in enumerate(entry['rounds']):
                    lines.append(row(scope, run, f'{condition} round {index + 1}', done['thresholds']['0.9'][scope][run]))
                last = entry['rounds'][-1]['thresholds']['0.9'][scope][run]
                lines.append(row(scope, run, f'current and {condition} pooled', last, last['pooled']))
    lines += ['', '| reader | round | threshold | value read | gain recovered | false values | false gains |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for condition, entry in results['conditions'].items():
        if 'skipped' in entry:
            lines.append(f"| {condition} | skipped: {entry['skipped']} | | | | | |")
            continue
        for index, done in enumerate(entry['rounds']):
            for threshold, by_scope in done['thresholds'].items():
                t = by_scope['all frames']['all']
                lines.append(f"| {condition} | {index + 1} | {threshold} | {share(t['cards_read'], t['cards'])} | "
                             f"{share(t['gains_recovered'], t['gain_cards'])} | {t['false_values']} | {t['false_gains']} |")
    lines += ['', '| reader | parameters | round | training crops | verified hard frames | held-out boxes | transcribed exactly | badge value right |',
              '|---|---:|---:|---:|---:|---|---:|---:|']
    for condition, entry in results['conditions'].items():
        if 'skipped' in entry:
            continue
        for index, done in enumerate(entry['rounds']):
            for content, cell in done['holdout_labeled'].items():
                value = format(cell['value'] / cell['n'], '.1%') if content == 'badge' else '-'
                lines.append(f"| {condition} | {entry['parameters']} | {index + 1} | {done['training_crops']} | {done['verified_hard_frames']} | "
                             f"{content} ({cell['n']}) | {cell['exact'] / cell['n']:.1%} | {value} |")
    if 'exported' in results:
        e = results['exported']
        directml = e.get('onnx_directml_ms_per_crop')
        lines += ['', f"Exported {e['condition']}: onnxruntime agrees with torch on {e['agreement']:.1%} of columns; "
                      f"{e['torch_ms_per_crop']:.2f} ms per box in torch and {e['onnx_ms_per_crop']:.2f} ms in onnxruntime on the CPU"
                      + (f", {directml:.2f} ms in onnxruntime on DirectML." if directml is not None else '.')]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--conditions', nargs='+', default=['scratch', 'frozen', 'finetune'])
    parser.add_argument('--epochs', type=int, default=25)
    parser.add_argument('--epoch-size', type=int, default=12000)
    parser.add_argument('--rounds', type=int, default=2, help='later rounds add the hard frames the previous model labeled')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--threads', type=int, default=4, help='CPU threads for torch; leave cores for other work')
    parser.add_argument('--device', default='auto', help="'cuda', 'cpu', or 'auto': the GPU whenever PyTorch has CUDA")
    parser.add_argument('--final', action='store_true',
                        help='train on every run, held-out ones included, for the model that ships; nothing is judged, '
                             'so its accuracy is the held-out result of the same settings without this flag')
    args = parser.parse_args(argv)
    import torch
    torch.set_num_threads(args.threads)
    if args.device == 'auto':
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    else:
        device = torch.device(args.device)
    if device.type == 'cuda':
        torch.backends.cudnn.benchmark = True
    args.output.mkdir(parents=True, exist_ok=True)
    log_path = args.output / 'train.log'

    def log(line):
        print(line, flush=True)
        with log_path.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    rows, _ = load(args.dataset)
    boxes = [r for r in rows if r['kind'] == 'result_box']
    if args.final:
        boxes = [dict(r, split='train') for r in boxes]
    holdout = [r for r in boxes if r['split'] == 'holdout']
    device_name = torch.cuda.get_device_name(device) if device.type == 'cuda' else 'cpu'
    cache = {}

    def ensure(subset):
        for row in subset:
            if row['crop'] not in cache:
                cache[row['crop']] = load_crop(args.dataset / row['crop'])
    ensure(holdout)
    holdout_arrays = [cache[r['crop']] for r in holdout]
    current, current_cards = evaluate(holdout, current_reads(holdout))
    results = dict(dataset=str(args.dataset), device=device_name, epochs=args.epochs, epoch_size=args.epoch_size,
                   rounds=args.rounds, final=args.final, current_reader=current, conditions={})
    log(f"training on {device_name}; held-out boxes {len(holdout)}; current reader: {json.dumps(current['all frames']['all'])}")
    trained = {}
    for condition in args.conditions:
        entry = dict(rounds=[])
        verified = []
        try:
            for round_index in range(args.rounds):
                groups = training_groups(boxes + verified, {'result_box'})
                ensure([r for group in groups.values() for r in group])
                distinct = sum(len(group) for group in groups.values())
                log(f"{condition} round {round_index + 1}: training crops {distinct} "
                    f"({', '.join(f'{k} {len(v)}' for k, v in sorted(groups.items()))}), {len(verified)} verified hard frames")
                model = train_condition(condition, groups, cache, args.epochs, args.epoch_size, args.seed, log, device)
                done = dict(training_crops=distinct, verified_hard_frames=len(verified), thresholds={})
                if holdout:
                    texts = transcribe(model, holdout_arrays)
                    for threshold in THRESHOLDS:
                        reads = [learned_read(row['field'], text, confidences, threshold) for row, (text, confidences) in zip(holdout, texts)]
                        done['thresholds'][str(threshold)], _ = evaluate(holdout, reads, current_cards)
                    done['holdout_labeled'] = labeled_breakdown(holdout, texts)
                    log(f"{condition} round {round_index + 1}: {json.dumps(done['thresholds']['0.9']['all frames']['all'])}")
                entry['rounds'].append(done)
                if round_index + 1 < args.rounds:
                    verified = verified_labels(model, boxes, cache, args.dataset)
                    # Kept beside the results so the labels a round trained on can be audited by eye.
                    with (args.output / f'{condition}-verified-round-{round_index + 2}.jsonl').open('w', encoding='utf-8') as stream:
                        for row in verified:
                            stream.write(json.dumps(dict(crop=row['crop'], run=row['run'], field=row['field'], target=row['target'])) + '\n')
        except Exception as exc:  # pretrained weights that cannot be downloaded, for example
            log(f'{condition}: skipped ({exc})')
            results['conditions'][condition] = dict(skipped=str(exc))
            continue
        entry['parameters'] = sum(p.numel() for p in model.parameters())
        results['conditions'][condition] = entry
        trained[condition] = model
    chosen = 'scratch' if 'scratch' in trained else None
    if chosen:
        path = args.output / 'reader.onnx'
        torch.save(trained[chosen].state_dict(), args.output / 'reader.pt')
        # The export is checked and timed on held-out crops, or on training crops when every run trained.
        sample = holdout_arrays or [cache[r['crop']] for r in boxes[:256] if r['crop'] in cache]
        results['exported'] = dict(condition=chosen, path=path.name, input=dict(name='crop', height=HEIGHT, width=WIDTH, channels=3, scale='rgb 0..1'),
                                   classes=['blank'] + list(CHARS), **export_onnx(trained[chosen], path, sample))
        log(f"exported {chosen}: {json.dumps(results['exported'])}")
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    (args.output / 'results.md').write_text(results_markdown(results), encoding='utf-8')
    return results


if __name__ == '__main__':
    main()
