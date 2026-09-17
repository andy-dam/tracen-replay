"""The learned result-card reader: our own network reading a training result card.

The recognizer reads a training result card through OCR. This reader runs a
small convolutional network, trained from nothing on boxes the analyzer's own
evidence labeled (``tools/train_reader.py``), on the same boxes cut wider,
and transcribes each one: a stat's value and the slash after it (``190/``),
a plain skill point value (``2097``), a gain overlay (``+15``) or nothing. A
read counts only in those shapes, with no leading zero, and only when its
least certain character is at least ``THRESHOLD`` sure.

The reads are observations. They are stored on each training result reading
(``facts['learned_result_reads']``) with the model's fingerprint, so a report
can be checked without the model, and the causal accounting uses a gain read
only where the stat bars leave exactly that difference for the stat
unexplained; a read never fills a field on its own.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
from PIL import Image

# Pane-space geometry: the recognizer's result boxes in screen coordinates,
# with the pane's left edge at x=148. Result boxes sit in two rows of three,
# skill points last.
PANE_LEFT = 148
PANE_SIZE = (810, 1080)
STAT_FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit')
BADGE_BOXES = {field: ((322, 518, 714)[i % 3], 834 if i < 3 else 952) for i, field in enumerate(STAT_FIELDS)}
BADGE_BOXES = {field: (x, y, x + 126, y + 42) for field, (x, y) in BADGE_BOXES.items()}
SKILL_BOX = (708, 952, 810, 990)
RESULT_BOXES = {**BADGE_BOXES, 'skill_points': SKILL_BOX}
# A training's result card sits in the same place whether or not its banner
# was legible enough to confirm the screen, so a frame left at "candidate" is
# read as well. What a read may do with an amount does not change: the stat
# bars decide, and a read counts only where it equals a difference they left.
RESULT_SCREENS = ('training_result', 'training_result_candidate')
# The recognizer's boxes start exactly where a three-digit value begins, so a
# four-digit value, the card's bounce or a zooming overlay pushes digits
# outside them. The reader's boxes are wider: this much more on the left, top,
# right and bottom, in pane pixels.
READER_MARGIN = (30, 8, 6, 8)

HEIGHT, WIDTH = 64, 192
CHARS = '0123456789/+'
BLANK = 0
THRESHOLD = 0.9
# The shapes a result box can take as the game renders them: a stat's value
# followed by the slash before its cap (anything after the slash is ignored),
# a plain skill point value, or a gain; no number has a leading zero.
STAT_VALUE = re.compile(r'[1-9]\d{0,3}/\d{0,4}')
PLAIN_VALUE = re.compile(r'0|[1-9]\d{0,3}')
GAIN = re.compile(r'\+[1-9]\d{0,2}')


def pane_box(box, margin=(0, 0, 0, 0)):
    """A screen-space box as a pane-space crop box, widened by ``margin``."""
    left, top, right, bottom = margin
    return (box[0] - PANE_LEFT - left, box[1] - top, box[2] - PANE_LEFT + right, box[3] + bottom)


def box_array(pane, field):
    """One result box of a pane as the network sees it: uint8 RGB, 64 x 192, channels first."""
    crop = pane.crop(pane_box(RESULT_BOXES[field], READER_MARGIN)).resize((WIDTH, HEIGHT), Image.BILINEAR)
    return np.ascontiguousarray(np.asarray(crop.convert('RGB'), dtype=np.uint8).transpose(2, 0, 1))


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


def read_shape(field, text, confidences, threshold=THRESHOLD):
    """A transcription as ``(value, gain)``, counted only in an accepted shape and at the threshold.

    The threshold applies to the characters a read rests on: a stat's value
    and the slash that marks it as a stat box, a plain skill point value, or
    a gain.
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


class LearnedReader:
    """An exported reader model, run with onnxruntime.

    The CPU provider is the default: its results do not depend on the graphics
    driver, so the same frames always give the same reads.
    """

    def __init__(self, model_path, providers=('CPUExecutionProvider',)):
        import onnxruntime as ort
        data = Path(model_path).read_bytes()
        self.model_sha256 = hashlib.sha256(data).hexdigest()
        self.session = ort.InferenceSession(data, providers=list(providers))
        self.input_name = self.session.get_inputs()[0].name

    def transcribe(self, arrays):
        """Each box's text and per-character confidences."""
        out = []
        for start in range(0, len(arrays), 256):
            batch = np.stack(arrays[start:start + 256]).astype(np.float32) / 255.0
            log_probs = self.session.run(None, {self.input_name: batch})[0]
            probs = np.exp(log_probs)
            index = probs.argmax(axis=-1)
            top = probs.max(axis=-1)
            for b in range(index.shape[1]):
                out.append(ctc_decode(index[:, b].tolist(), top[:, b].tolist()))
        return out


def annotate(readings, root, reader, threshold=THRESHOLD):
    """Attach the reader's reads to every training result reading whose pane is under ``root``.

    A reading already annotated by the same model is left alone. Returns the
    number of readings annotated.
    """
    root = Path(root)
    pending = []
    for row in readings:
        if not isinstance(row, dict) or row.get('screen') not in RESULT_SCREENS or not isinstance(row.get('evidence'), str):
            continue
        facts = row.get('facts')
        if not isinstance(facts, dict):
            continue
        existing = facts.get('learned_result_reads')
        if isinstance(existing, dict) and existing.get('model_sha256') == reader.model_sha256:
            continue
        path = root / row['evidence']
        if path.is_file():
            pending.append((row, path))
    annotated = 0
    for start in range(0, len(pending), 64):
        chunk = []
        for row, path in pending[start:start + 64]:
            with Image.open(path) as image:
                if image.size != PANE_SIZE:
                    continue
                pane = image.convert('RGB')
            chunk.append((row, [box_array(pane, field) for field in RESULT_BOXES]))
        texts = reader.transcribe([array for _, arrays in chunk for array in arrays])
        for index, (row, _) in enumerate(chunk):
            fields = {}
            for offset, field in enumerate(RESULT_BOXES):
                text, confidences = texts[index * len(RESULT_BOXES) + offset]
                value, gain = read_shape(field, text, confidences, threshold)
                fields[field] = dict(text=text, confidence=round(min(confidences), 4) if confidences else None,
                                     value=value, gain=gain)
            row['facts']['learned_result_reads'] = dict(model_sha256=reader.model_sha256, threshold=threshold, fields=fields)
            annotated += 1
    return annotated


def learned_gains(readings, field, start, end, slack_ms=250):
    """The gains the reader read for ``field`` on training result frames between ``start`` and ``end``."""
    gains = set()
    for row in readings:
        time = row.get('source_timestamp_ms') if isinstance(row, dict) else None
        if row.get('screen') not in RESULT_SCREENS or type(time) is not int or not start - slack_ms <= time <= end + slack_ms:
            continue
        reads = (row.get('facts') or {}).get('learned_result_reads')
        gain = ((reads or {}).get('fields') or {}).get(field, {}).get('gain') if isinstance(reads, dict) else None
        if type(gain) is int and gain > 0:
            gains.add(gain)
    return gains
