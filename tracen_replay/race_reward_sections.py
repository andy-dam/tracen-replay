"""Keep visible reward section identity separate from item identity."""
from copy import deepcopy
from .race_section_layout import _box, _confidence, HEADER_LEFT, HEADER_RIGHT


def annotate(row):
    items = deepcopy(row.get('facts', {}).get('visible_item_quantities', []))
    for item in items:
        item['section'] = None
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
