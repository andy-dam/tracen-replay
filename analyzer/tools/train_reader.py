"""Train and compare the first learned reader of a training result card.

The reader takes one result box (a stat's value and cap, a "+N" gain
overlay, or nothing) and transcribes it: a string over the digits, "/" and
"+", decoded by CTC over the crop's width, with a confidence per character
(the peak over the columns it spans). It trains on the labeled boxes of the
training runs and is judged on every box of the held-out run the way the
accounting uses a reader (see ``reader_baseline``): per card, whether its
value or its gain was read, and per frame, how many reads were false. A read
counts only in the shape the analyzer's own reader accepts (``value/cap``
for a stat, a plain value for skill points, ``+gain``) and when the least
certain character the accounting uses (a stat's value digits, not its cap)
is at or above a confidence threshold. The analyzer's own reader is judged
on the same boxes, alone and pooled with the learned one.

Three conditions share the head, the data and the schedule:

- ``scratch``: our own convolutional trunk, trained from nothing;
- ``frozen``: an ImageNet-pretrained ResNet-18 trunk kept fixed;
- ``finetune``: the same trunk, trained along with the head.

The pretrained conditions are a comparison row only and are never exported:
the reader is our own. The ``scratch`` model is written to ONNX (input
``crop``: RGB in [0, 1], batch x 3 x 64 x 192), checked against onnxruntime,
and timed per box on the CPU in both runtimes.

    python -m tools.train_reader DATASET --output DIR [--conditions scratch frozen finetune]
        [--epochs N] [--epoch-size N] [--threads N]

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

from tools.reader_baseline import current_reads, judge, load, summarize, union_cards

CHARS = '0123456789/+'
BLANK = 0
HEIGHT, WIDTH = 64, 192
BATCH = 64
THRESHOLDS = (0.5, 0.9, 0.97)
# Crops of one card's box showing one target kept for training: a card's
# high-rate rereads repeat the same picture dozens of times.
MAX_PER_TARGET = 8
SHARE_WEIGHTS = dict(badge=2)
# The shapes a result box can take, as the game renders them: no number has
# a leading zero, and a stat's cap is never below 1000. A transcription of a
# box half covered by the card's sparkles or a digit still fading in ("01/1348",
# "10/160") fails these and is not a read.
STAT_VALUE = re.compile(r'[1-9]\d{0,3}/[1-9]\d{3}')
PLAIN_VALUE = re.compile(r'0|[1-9]\d{0,3}')
GAIN = re.compile(r'\+[1-9]\d{0,2}')


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
    value (the digits before the slash; the cap is only a shape check), a
    plain skill point value, or a gain.
    """
    if not text:
        return None, None
    if GAIN.fullmatch(text):
        return (None, int(text[1:])) if min(confidences) >= threshold else (None, None)
    if field == 'skill_points' and PLAIN_VALUE.fullmatch(text):
        return (int(text), None) if min(confidences) >= threshold else (None, None)
    if field != 'skill_points' and STAT_VALUE.fullmatch(text):
        value, cap = (int(part) for part in text.split('/'))
        used = confidences[:text.index('/')]
        return (value, None) if value <= cap and min(used) >= threshold else (None, None)
    return None, None


def training_groups(rows, kinds):
    """Labeled training crops grouped by what they show, at most MAX_PER_TARGET per card box and target."""
    buckets = defaultdict(list)
    for row in rows:
        if row['split'] != 'train' or row['kind'] not in kinds or row.get('target') is None:
            continue
        buckets[(row['run'], row.get('visit') or row['frame'], row['kind'], row['field'], row['target'])].append(row)
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
    """Exact transcriptions per content over labeled rows; for badges, the value and the cap separately."""
    cells = defaultdict(lambda: dict(n=0, exact=0, value=0, cap=0))
    for row, (text, _) in zip(rows, texts):
        if row.get('target') is None:
            continue
        cell = cells[row['content']]
        cell['n'] += 1
        cell['exact'] += text == row['target']
        if row['content'] == 'badge':
            target_value, _, target_cap = row['target'].partition('/')
            value, _, cap = text.partition('/')
            cell['value'] += value == target_value
            cell['cap'] += cap == target_cap
    return {content: dict(cell) for content, cell in sorted(cells.items())}


def card_scores(rows, reads):
    """Held-out result-box totals for one reader's reads: cards and false reads."""
    frames, cards = judge(rows, reads)
    table = [r for r in summarize(frames, cards) if r['split'] == 'holdout' and r['kind'] == 'result_box' and r['field'] == 'all']
    total = table[0] if table else dict(cards=0, cards_read=0, gain_cards=0, gains_recovered=0, false_values=0, false_gains=0)
    return dict(cards=total['cards'], cards_read=total['cards_read'], gain_cards=total['gain_cards'],
                gains_recovered=total['gains_recovered'], false_values=total['false_values'], false_gains=total['false_gains']), cards


def union_scores(current_cards, learned_cards):
    pooled = union_cards(current_cards, learned_cards)
    return dict(cards_read=sum(c['value_read'] for c in pooled.values()),
                gains_recovered=sum(c['gain_recovered'] for c in pooled.values() if c['gain']))


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


def results_markdown(results):
    current = results['current_reader']
    lines = ['### Learned reader against the current reader (held-out run)', '',
             f"{current['cards']} cards, {current['gain_cards']} of them with a gain. "
             f"Training crops: {results['training_crops']} distinct, {results['epochs']} epochs of {results['epoch_size']} "
             f"on {results.get('device', 'cpu')}.", '',
             '| reader | threshold | value read | gain recovered | false values | false gains | pooled value read | pooled gain recovered |',
             '|---|---:|---:|---:|---:|---:|---:|---:|',
             f"| current | - | {current['cards_read']} | {current['gains_recovered']} | {current['false_values']} | {current['false_gains']} | - | - |"]
    for condition, entry in results['conditions'].items():
        if 'skipped' in entry:
            lines.append(f"| {condition} | skipped: {entry['skipped']} | | | | | | |")
            continue
        for threshold, s in entry['holdout'].items():
            lines.append(f"| {condition} | {threshold} | {s['cards_read']} | {s['gains_recovered']} | {s['false_values']} | "
                         f"{s['false_gains']} | {s['pooled']['cards_read']} | {s['pooled']['gains_recovered']} |")
    lines += ['', '| reader | parameters | boxes | content | transcribed exactly | badge value | badge cap |',
              '|---|---:|---|---|---:|---:|---:|']
    for condition, entry in results['conditions'].items():
        if 'skipped' in entry:
            continue
        for split in ('holdout', 'train'):
            for content, cell in entry[split + '_labeled'].items():
                value = format(cell['value'] / cell['n'], '.1%') if content == 'badge' else '-'
                cap = format(cell['cap'] / cell['n'], '.1%') if content == 'badge' else '-'
                lines.append(f"| {condition} | {entry['parameters']} | {split} | {content} ({cell['n']}) | "
                             f"{cell['exact'] / cell['n']:.1%} | {value} | {cap} |")
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
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--epoch-size', type=int, default=6000)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--threads', type=int, default=4, help='CPU threads for torch; leave cores for other work')
    parser.add_argument('--device', default='auto', help="'cuda', 'cpu', or 'auto': the GPU whenever PyTorch has CUDA")
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
    groups = training_groups(boxes, {'result_box'})
    holdout = [r for r in boxes if r['split'] == 'holdout']
    distinct = sum(len(g) for g in groups.values())
    device_name = torch.cuda.get_device_name(device) if device.type == 'cuda' else 'cpu'
    log(f"training on {device_name}; training crops {distinct} ({', '.join(f'{k} {len(v)}' for k, v in sorted(groups.items()))}); held-out boxes {len(holdout)}")
    cache = {}
    for row in [r for g in groups.values() for r in g] + holdout:
        if row['crop'] not in cache:
            cache[row['crop']] = load_crop(args.dataset / row['crop'])
    holdout_arrays = [cache[r['crop']] for r in holdout]
    current, current_cards = card_scores(holdout, current_reads(holdout))
    results = dict(dataset=str(args.dataset), device=device_name, epochs=args.epochs, epoch_size=args.epoch_size, training_crops=distinct,
                   current_reader=current, conditions={})
    log(f'current reader: {json.dumps(current)}')
    trained = {}
    for condition in args.conditions:
        try:
            model = train_condition(condition, groups, cache, args.epochs, args.epoch_size, args.seed, log, device)
        except Exception as exc:  # pretrained weights that cannot be downloaded, for example
            log(f'{condition}: skipped ({exc})')
            results['conditions'][condition] = dict(skipped=str(exc))
            continue
        texts = transcribe(model, holdout_arrays)
        entry = dict(parameters=sum(p.numel() for p in model.parameters()), holdout={})
        for threshold in THRESHOLDS:
            reads = [learned_read(row['field'], text, confidences, threshold) for row, (text, confidences) in zip(holdout, texts)]
            scores, cards = card_scores(holdout, reads)
            scores['pooled'] = union_scores(current_cards, cards)
            entry['holdout'][str(threshold)] = scores
        entry['holdout_labeled'] = labeled_breakdown(holdout, texts)
        sample = random.Random(args.seed).sample([r for g in groups.values() for r in g], min(2000, distinct))
        entry['train_labeled'] = labeled_breakdown(sample, transcribe(model, [cache[r['crop']] for r in sample]))
        results['conditions'][condition] = entry
        trained[condition] = model
        log(f'{condition}: {json.dumps(entry)}')
    chosen = 'scratch' if 'scratch' in trained else None
    if chosen:
        path = args.output / 'reader.onnx'
        torch.save(trained[chosen].state_dict(), args.output / 'reader.pt')
        results['exported'] = dict(condition=chosen, path=path.name, input=dict(name='crop', height=HEIGHT, width=WIDTH, channels=3, scale='rgb 0..1'),
                                   classes=['blank'] + list(CHARS), **export_onnx(trained[chosen], path, holdout_arrays))
        log(f"exported {chosen}: {json.dumps(results['exported'])}")
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    (args.output / 'results.md').write_text(results_markdown(results), encoding='utf-8')
    return results


if __name__ == '__main__':
    main()
