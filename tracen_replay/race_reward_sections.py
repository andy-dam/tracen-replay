"""Keep visible reward section identity separate from item identity."""
import math
from copy import deepcopy
from .race_section_layout import _box, _confidence, HEADER_LEFT, HEADER_RIGHT


_VALIDATED_SECTION_BASIS = "validated_race_quantity_layout_guard"
_SECTION_RANGES = {
    "items": (500, 700),
    "bonus": (700, 840),
}
_SECTION_BASELINES = {
    "items": 612,
    "bonus": 778,
}


def _validated_section(item, row):
    """Read only the section proof emitted after sidecar provenance checks."""

    proof = item.pop("_validated_section_proof", None)
    if not isinstance(proof, dict) or proof.get("basis") != _VALIDATED_SECTION_BASIS:
        return None
    section = proof.get("section")
    if section not in _SECTION_RANGES:
        return None
    header = proof.get("header")
    if not isinstance(header, dict):
        return None
    if str(header.get("text", "")).strip().casefold() != section:
        return None
    box = _box(header.get("box"))
    if box is None or not HEADER_LEFT <= box[0] <= HEADER_RIGHT:
        return None
    lower, upper = _SECTION_RANGES[section]
    if not lower <= box[1] <= upper or abs(box[3] - _SECTION_BASELINES[section]) > 8:
        return None
    confidence = _confidence(header)
    minimum = 90.0 if section == "bonus" else 97.0
    if not math.isfinite(confidence) or confidence < minimum:
        return None
    timestamp = proof.get("source_timestamp_ms")
    if type(timestamp) is int and type(row.get("source_timestamp_ms")) is int and timestamp != row.get("source_timestamp_ms"):
        return None
    source_hash = proof.get("source_frame_sha256")
    if source_hash is not None:
        if (not isinstance(source_hash, str) or len(source_hash) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in source_hash)):
            return None
        row_hash = row.get("source_frame_sha256")
        if row_hash is not None and source_hash.casefold() != str(row_hash).casefold():
            return None
    item_box = _box(item.get("box"))
    if item_box is None:
        return None
    center = (item_box[1] + item_box[3]) / 2
    if not 50 <= center - box[3] <= 150:
        return None
    item["section"] = section
    item["section_header"] = deepcopy(header)
    return section


def annotate(row):
    items = deepcopy(row.get('facts', {}).get('visible_item_quantities', []))
    for item in items:
        validated = _validated_section(item, row)
        item['section'] = None
        if validated is not None:
            item['section'] = validated
    if row.get('screen') != 'race_result':
        return items
    headers = {}
    for line in row.get('ocr', {}).get('neural', []):
        name = line.get('text', '').strip().casefold()
        box = _box(line.get('box'))
        if name not in ('items', 'bonus') or box is None or _confidence(line) < 97:
            continue
        if not HEADER_LEFT <= box[0] <= HEADER_RIGHT or not 500 <= box[1] <= 900:
            continue
        if name in headers:
            return items
        headers[name] = line
    ordered = sorted(headers, key=lambda name: headers[name]['box'][1])
    if ordered == ['bonus', 'items']:
        return items
    for index, name in enumerate(ordered):
        header = headers[name]
        bottom = header['box'][3]
        next_top = headers[ordered[index+1]]['box'][1] if index+1 < len(ordered) else 940
        if next_top <= bottom:
            return [dict(item, section=None) for item in items]
        candidates = []
        for item in items:
            box = _box(item.get('box'))
            if box is None or box[1] < bottom or box[3] > next_top:
                continue
            center = (box[1]+box[3])/2
            # Both supported reward rows sit roughly 100 pixels below their
            # header. A distant unlabeled row cannot inherit the last header.
            if 50 <= center-bottom <= 150:
                candidates.append((item, center))
        if not candidates or max(y for _, y in candidates)-min(y for _, y in candidates) > 24:
            continue
        for item, _ in candidates:
            item['section'] = name
            item['section_header'] = deepcopy(header)
    return items
