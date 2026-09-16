"""Train and compare the first learned reader on a reader dataset.

The reader takes one fixed crop (a stat badge or a gain overlay from a
training result card) and emits a digit string with a confidence, by CTC
decoding over the crop's width. Three conditions are trained on the same
crops and judged on the same held-out run:

- ``scratch``: a small convolutional net trained from nothing;
- ``frozen``: an ImageNet-pretrained ResNet-18 trunk kept fixed under a
  trained linear head;
- ``finetune``: the same trunk and head, all of it trained.

Training uses the ``confirmed`` crops of the training split only (a gain
overlay's label 0 means "no overlay", an empty target). Each condition is
then scored on the held-out ``confirmed`` crops (the value was read there by
the current reader too) and on the held-out ``hard`` crops (the current
reader missed or misread them), per kind, by exact match; alongside, the
share it reads at a confidence of at least 0.9 and how accurate those reads
are, which is the pair the baseline table reports for the current reader.
The best condition by held-out exact match is exported to ONNX and checked
against onnxruntime, and its latency per crop is measured on the CPU.

    python -m tools.train_reader DATASET --output DIR [--epochs N] [--conditions scratch frozen finetune]

Requires the ``train`` extra (torch, torchvision). Results, the model and its
manifest go under ``--output``, in the local records.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image

CHARS = '0123456789'
BLANK = 0
HEIGHT, WIDTH = 64, 192
DEFAULT_KINDS = ('stat_badge', 'gain_overlay')
CONFIDENT = 0.9


def encode(label):
    """Digits of a label as CTC targets; 0 for a gain overlay is an empty target."""
    if label is None:
        return []
    text = '' if label == 0 else str(label)
    return [CHARS.index(ch) + 1 for ch in text]


def ctc_decode(columns):
    """Greedy CTC decode of per-column class indices; repeats collapse, blanks drop."""
    out = []
    previous = BLANK
    for index in columns:
        if index != BLANK and index != previous:
            out.append(CHARS[index - 1])
        previous = index
    return ''.join(out)


def value_of(text):
    return 0 if text == '' else int(text) if text.isdigit() else None


def select_rows(rows, kinds):
    """The training crops and the two held-out sets, per kind."""
    train = [r for r in rows if r['kind'] in kinds and r['split'] == 'train' and r['status'] == 'confirmed']
    holdout = {status: [r for r in rows if r['kind'] in kinds and r['split'] == 'holdout' and r['status'] == status]
               for status in ('confirmed', 'hard')}
    return train, holdout


def balance_rows(train_rows, seed):
    """Even out the training mix without inventing crops.

    Gain overlays outnumber badges many times over, and most of them carry
    label 0 (a stat the card did not raise). Zero-gain crops are cut down to
    the number of real gains, and every other kind is repeated up to the
    largest kind's size, so no kind is drowned out.
    """
    rng = random.Random(seed)
    zeros = [r for r in train_rows if r['kind'] == 'gain_overlay' and r['label'] == 0]
    gains = [r for r in train_rows if r['kind'] == 'gain_overlay' and r['label'] != 0]
    rng.shuffle(zeros)
    kept = gains + zeros[:len(gains)] + [r for r in train_rows if r['kind'] != 'gain_overlay']
    by_kind = defaultdict(list)
    for row in kept:
        by_kind[row['kind']].append(row)
    largest = max((len(v) for v in by_kind.values()), default=0)
    out = []
    for group in by_kind.values():
        repeats = max(1, round(largest / len(group)))
        out.extend(group * repeats)
    rng.shuffle(out)
    return out


def load_image(path, channels):
    image = Image.open(path).convert('L').resize((WIDTH, HEIGHT), Image.BILINEAR)
    import numpy as np
    array = (np.asarray(image, dtype='float32') / 255.0 - 0.5) / 0.5
    if channels == 3:
        array = np.stack([array] * 3)
    else:
        array = array[None]
    return array


def build_model(condition):
    import torch
    from torch import nn

    class Head(nn.Module):
        def __init__(self, channels):
            super().__init__()
            self.linear = nn.Linear(channels, len(CHARS) + 1)

        def forward(self, features):
            # (B, C, H, W) -> (W, B, classes): one prediction per column.
            columns = features.mean(dim=2).permute(2, 0, 1)
            return self.linear(columns).log_softmax(dim=-1)

    if condition == 'scratch':
        def block(i, o, pool):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True), nn.MaxPool2d(pool))
        trunk = nn.Sequential(block(1, 32, (2, 2)), block(32, 64, (2, 2)), block(64, 128, (2, 1)), block(128, 128, (2, 1)))
        return nn.Sequential(trunk, Head(128)), 1
    import torchvision
    resnet = torchvision.models.resnet18(weights=torchvision.models.ResNet18_Weights.IMAGENET1K_V1)
    trunk = nn.Sequential(resnet.conv1, resnet.bn1, resnet.relu, resnet.maxpool, resnet.layer1, resnet.layer2)
    if condition == 'frozen':
        for parameter in trunk.parameters():
            parameter.requires_grad = False
    return nn.Sequential(trunk, Head(128)), 3


def augment(batch, rng):
    """Small shifts and brightness changes; the crops are otherwise fixed."""
    import torch
    dx = rng.randint(-3, 3)
    dy = rng.randint(-2, 2)
    batch = torch.roll(batch, shifts=(dy, dx), dims=(2, 3))
    return batch * (1 + 0.2 * (rng.random() - 0.5)) + 0.2 * (rng.random() - 0.5)


def predict(model, arrays):
    """Decoded strings and confidences (mean top probability over emitted columns)."""
    import torch
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(arrays), 128):
            batch = torch.from_numpy(__import__('numpy').stack(arrays[start:start + 128]))
            logp = model(batch)  # (W, B, C)
            probs = logp.exp()
            top, idx = probs.max(dim=-1)
            for b in range(batch.shape[0]):
                cols = idx[:, b].tolist()
                emitted = [top[w, b].item() for w in range(len(cols)) if cols[w] != BLANK]
                confidence = sum(emitted) / len(emitted) if emitted else top[:, b].mean().item()
                out.append((ctc_decode(cols), confidence))
    return out


def score(rows, predictions):
    """Exact match overall, and the confident subset's coverage and accuracy, per kind."""
    per_kind = defaultdict(lambda: dict(n=0, exact=0, confident=0, confident_exact=0))
    for row, (text, confidence) in zip(rows, predictions):
        cell = per_kind[row['kind']]
        cell['n'] += 1
        right = value_of(text) == row['label']
        cell['exact'] += right
        if confidence >= CONFIDENT:
            cell['confident'] += 1
            cell['confident_exact'] += right
    return {kind: dict(n=c['n'], exact=c['exact'] / c['n'] if c['n'] else None,
                       confident_coverage=c['confident'] / c['n'] if c['n'] else None,
                       confident_accuracy=c['confident_exact'] / c['confident'] if c['confident'] else None)
            for kind, c in per_kind.items()}


def train_condition(condition, train_rows, train_arrays, epochs, seed, log):
    import torch
    from torch import nn
    torch.manual_seed(seed)
    rng = random.Random(seed)
    model, channels = build_model(condition)
    targets = [encode(r['label']) for r in train_rows]
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.Adam(parameters, lr=1e-4 if condition == 'finetune' else 1e-3)
    loss_fn = nn.CTCLoss(blank=BLANK, zero_infinity=True)
    order = list(range(len(train_rows)))
    started = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        if condition == 'frozen':
            model[0].eval()
        rng.shuffle(order)
        total = 0.0
        for start in range(0, len(order), 64):
            picks = order[start:start + 64]
            batch = torch.from_numpy(__import__('numpy').stack([train_arrays[i] for i in picks]))
            batch = augment(batch, rng)
            flat = [t for i in picks for t in targets[i]]
            lengths = torch.tensor([len(targets[i]) for i in picks], dtype=torch.long)
            logp = model(batch)
            input_lengths = torch.full((len(picks),), logp.shape[0], dtype=torch.long)
            loss = loss_fn(logp, torch.tensor(flat, dtype=torch.long), input_lengths, lengths)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.item() * len(picks)
        log(f'{condition} epoch {epoch + 1}/{epochs} loss {total / len(order):.4f} ({time.perf_counter() - started:.0f}s)')
    return model, channels


def latency_ms(model, arrays, repeats=200):
    import torch
    model.eval()
    sample = torch.from_numpy(arrays[0][None])
    with torch.no_grad():
        for _ in range(10):
            model(sample)
        started = time.perf_counter()
        for i in range(repeats):
            model(torch.from_numpy(arrays[i % len(arrays)][None]))
    return 1000 * (time.perf_counter() - started) / repeats


def export_onnx(model, channels, path, arrays):
    """Export with a dynamic batch and check onnxruntime agrees with torch on a few crops."""
    import numpy as np
    import onnxruntime as ort
    import torch
    model.eval()
    sample = torch.from_numpy(arrays[0][None])
    torch.onnx.export(model, sample, str(path), input_names=['crop'], output_names=['log_probs'],
                      dynamic_axes={'crop': {0: 'batch'}, 'log_probs': {1: 'batch'}}, opset_version=17, dynamo=False)
    session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
    checked = arrays[:50]
    with torch.no_grad():
        torch_out = model(torch.from_numpy(np.stack(checked))).argmax(-1).numpy()
    onnx_out = session.run(None, {'crop': np.stack(checked)})[0].argmax(-1)
    agree = float((torch_out == onnx_out).mean())
    started = time.perf_counter()
    for i in range(200):
        session.run(None, {'crop': arrays[i % len(arrays)][None]})
    return dict(agreement=agree, onnx_latency_ms=1000 * (time.perf_counter() - started) / 200)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kinds', nargs='+', default=list(DEFAULT_KINDS))
    parser.add_argument('--conditions', nargs='+', default=['scratch', 'frozen', 'finetune'])
    parser.add_argument('--epochs', type=int, default=15)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--threads', type=int, default=4, help='CPU threads for torch; leave cores for other work')
    parser.add_argument('--limit', type=int, help='use at most this many training crops (a quick run)')
    args = parser.parse_args(argv)
    import torch
    torch.set_num_threads(args.threads)
    args.output.mkdir(parents=True, exist_ok=True)
    log_path = args.output / 'train.log'

    def log(line):
        print(line, flush=True)
        with log_path.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    rows = [json.loads(line) for line in (args.dataset / 'crops.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    train_rows, holdout = select_rows(rows, set(args.kinds))
    distinct = len(train_rows)
    train_rows = balance_rows(train_rows, args.seed)
    if args.limit:
        train_rows = train_rows[:args.limit]
    log(f'training crops {distinct} distinct, {len(train_rows)} after balancing; held-out confirmed {len(holdout["confirmed"])}, hard {len(holdout["hard"])}')
    images = {}

    def arrays_for(subset, channels):
        out = []
        for row in subset:
            key = (row['crop'], channels)
            if key not in images:
                images[key] = load_image(args.dataset / row['crop'], channels)
            out.append(images[key])
        return out

    results = dict(dataset=str(args.dataset), kinds=args.kinds, epochs=args.epochs, training_crops=len(train_rows), conditions={})
    best = None
    for condition in args.conditions:
        try:
            model, channels = train_condition(condition, train_rows, arrays_for(train_rows, 1 if condition == 'scratch' else 3), args.epochs, args.seed, log)
        except Exception as exc:  # a missing download of pretrained weights, for example
            log(f'{condition}: skipped ({exc})')
            results['conditions'][condition] = dict(skipped=str(exc))
            continue
        entry = dict(parameters=sum(p.numel() for p in model.parameters()), torch_latency_ms=latency_ms(model, arrays_for(train_rows[:200], channels)))
        for name, subset in (('train', train_rows[:2000]), ('holdout_confirmed', holdout['confirmed']), ('holdout_hard', holdout['hard'])):
            if subset:
                entry[name] = score(subset, predict(model, arrays_for(subset, channels)))
        results['conditions'][condition] = entry
        log(f'{condition}: {json.dumps(entry)}')
        confirmed = entry.get('holdout_confirmed', {})
        merit = sum((v['exact'] or 0) * v['n'] for v in confirmed.values())
        if best is None or merit > best[0]:
            best = (merit, condition, model, channels)
    if best is not None:
        _, condition, model, channels = best
        path = args.output / 'reader.onnx'
        results['exported'] = dict(condition=condition, path=str(path), input=dict(height=HEIGHT, width=WIDTH, channels=channels),
                                   classes=['blank'] + list(CHARS), **export_onnx(model, channels, path, arrays_for(train_rows[:200], channels)))
        log(f'exported {condition} -> {path}: {json.dumps(results["exported"])}')
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    return results


if __name__ == '__main__':
    main()
