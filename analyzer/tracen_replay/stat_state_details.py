"""Read source-bound stat details from the visible gameplay panels.

The ordinary stat-cap reader in this module handles the persistent main stat
bar.  Training-result cards are a separate layout: their current value can be
legible while the cap is briefly covered by the result animation.
The result-card reader below therefore records a current numerator only when
the field label and value crop agree at the same geometry.  It never turns a
partial cap into a cap value.
"""

from copy import deepcopy
import hashlib
import json
import math
import re

from .layout import pane_size, place, place_y
from .reconcile import FIELDS
from .stats import BOXES


# These are the stable result-card columns in the source OCR coordinate space,
# on the PC pane.  The gameplay pane is 810x1080 there after the reader's crop,
# but OCR boxes retain the 148px horizontal source offset and can therefore
# extend past x=810.
# A broad envelope is intentional: ordinary box jitter is allowed while the
# value remains tied to the result grid rather than arbitrary frame numbers.
_RESULT_COLUMNS = {
    'speed': (285, 480),
    'stamina': (480, 680),
    'power': (680, 870),
    'guts': (285, 480),
    'wit': (480, 680),
}
_RESULT_ROWS = {
    'speed': (805, 905),
    'stamina': (805, 905),
    'power': (805, 905),
    'guts': (925, 1015),
    'wit': (925, 1015),
}


def _result_card(field):
    """The field's result card in this recording, as (left, top, right, bottom), or None.

    The columns and rows above are the PC pane's; the cards are pinned to
    the centre of the game's clear area.
    """
    column, row = _RESULT_COLUMNS.get(field), _RESULT_ROWS.get(field)
    if column is None or row is None:
        return None
    return place((column[0], row[0], column[1], row[1]), 'mc')


# Result-card sparkles are a foreground animation.  A warm, locally bright
# component inside an unresolved value crop is useful pixel evidence that the
# card was covered; the static card background is broad and has no such local
# component.  These thresholds are deliberately conservative and are applied
# only after the result-card and same-card label geometry checks below.
RESULT_OCCLUSION_SCHEMA = 'tracen-replay/result-card-occlusion-v1'
RESULT_OCCLUSION_BASIS = 'source_bound_result_card_pixel_occlusion'
_PANE_LEFT = 148
_GLARE_MIN_AREA = 20
_GLARE_MIN_COMPONENTS = 2
_GLARE_MIN_LOCAL_CONTRAST = 15.0
_LEADING_DIGIT_GLARE_MIN_AREA = 80
_LEADING_DIGIT_GLARE_MIN_WIDTH = 8
_LEADING_DIGIT_GLARE_MIN_HEIGHT = 16
_LEADING_DIGIT_GLARE_MAX_LEFT_OFFSET = 28
_LEADING_DIGIT_GLARE_MAX_RIGHT_OFFSET = 48


def _confidence_at_least(value, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(value) and minimum <= value <= 100


def _confidence_valid(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(value) and 0 <= value <= 100


def _result_component_digest(components):
    canonical = [
        {'area': component['area'], 'box': list(component['box'])}
        for component in components
    ]
    return hashlib.sha256(json.dumps(
        canonical, ensure_ascii=False, separators=(',', ':'),
        sort_keys=True).encode('utf-8')).hexdigest()


def _result_card_label(field, lines, value_box):
    """Return a source label anchored to one result-card value crop.

    A one-character OCR deletion/substitution is accepted for the label only;
    the fixed card geometry still has to identify the field.  This permits a
    blocked ``Speed`` glyph such as ``peed`` to support an occlusion proof
    without repairing the field's numeric value.
    """

    if not _valid_box(value_box):
        return None
    value_x, value_y = _center(value_box)
    target = re.sub(r'\s+', '', field).casefold()

    def edit_distance_at_most_one(actual):
        actual = re.sub(r'\s+', '', str(actual)).casefold()
        if actual == target:
            return True
        if abs(len(actual) - len(target)) > 1:
            return False
        # The bounded check is intentionally small: label identity is a
        # geometry anchor, not a general fuzzy-name repair mechanism.
        if len(actual) == len(target):
            return sum(a != b for a, b in zip(actual, target)) <= 1
        if len(actual) > len(target):
            actual, target_local = target, actual
        else:
            target_local = target
        left = right = 0
        skipped = False
        while left < len(actual) and right < len(target_local):
            if actual[left] == target_local[right]:
                left += 1
                right += 1
                continue
            if skipped:
                return False
            skipped = True
            right += 1
        return True

    labels = []
    for line in lines if isinstance(lines, list) else []:
        if (not isinstance(line, dict)
                or not _confidence_at_least(line.get('confidence'), 90)
                or not _valid_box(line.get('box'))
                or not edit_distance_at_most_one(line.get('text', ''))):
            continue
        label_box = line['box']
        label_x, label_y = _center(label_box)
        if (abs(label_x - value_x) <= 90
                and 18 <= value_y - label_y <= 90
                and _result_label_box_eligible(field, label_box)):
            labels.append(line)
    return labels[0] if len(labels) == 1 else None


def _local_mean(values, radius=4):
    """Return a small edge-preserving local mean without a new dependency."""

    height, width = values.shape
    padding = ((radius, radius + 1), (radius, radius + 1))
    padded = __import__('numpy').pad(values, padding, mode='edge')
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    size = 2 * radius + 1
    return ((integral[size:, size:] - integral[:-size, size:]
             - integral[size:, :-size] + integral[:-size, :-size])
            / float(size * size))


def _connected_components(mask):
    """Return bounded connected-component geometry for a boolean mask."""

    import numpy as np

    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    components = []
    for y, x in zip(*np.nonzero(mask)):
        y, x = int(y), int(x)
        if seen[y, x]:
            continue
        stack = [(y, x)]
        seen[y, x] = True
        points = []
        while stack:
            yy, xx = stack.pop()
            points.append((yy, xx))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                y2, x2 = yy + dy, xx + dx
                if (0 <= y2 < height and 0 <= x2 < width
                        and mask[y2, x2] and not seen[y2, x2]):
                    seen[y2, x2] = True
                    stack.append((y2, x2))
        ys = [point[0] for point in points]
        xs = [point[1] for point in points]
        components.append({
            'area': len(points),
            'box': [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
        })
    return components


def _result_glare_components(pixels, box):
    """Find foreground sparkle components inside one exact result crop."""

    import numpy as np

    left, top, right, bottom = [int(round(value)) for value in box]
    x0, x1 = left - _PANE_LEFT, right - _PANE_LEFT
    x0, x1 = max(0, x0), min(pixels.shape[1], x1)
    y0, y1 = max(0, top), min(pixels.shape[0], bottom)
    if x1 <= x0 or y1 <= y0:
        return [], 0.0
    crop = pixels[y0:y1, x0:x1].astype(np.float32)
    red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    gray = (0.299 * red + 0.587 * green + 0.114 * blue).astype(np.float32)
    local = _local_mean(gray)
    # The result animation's cream/white sparkles are warmer than the card's
    # blue/gray body.  Local contrast removes broad lighting/card gradients.
    mask = ((red >= 205) & (green >= 175) & (blue <= 215)
            & ((red - blue) >= 20) & ((green - blue) >= 8)
            & (gray - local >= _GLARE_MIN_LOCAL_CONTRAST))
    components = []
    for component in _connected_components(mask):
        area = component['area']
        cx0, cy0, cx1, cy1 = component['box']
        width, height = cx1 - cx0, cy1 - cy0
        # Components touching the crop boundary are usually the card fill or
        # a border.  Require a compact foreground shape inside the value box.
        if (area < _GLARE_MIN_AREA or area > 1000
                or cx0 <= 1 or cy0 <= 1
                or cx1 >= mask.shape[1] - 1 or cy1 >= mask.shape[0] - 1
                or width < 4 or height < 4):
            continue
        components.append({
            'area': area,
            'box': [cx0 + x0 + _PANE_LEFT, cy0 + y0,
                    cx1 + x0 + _PANE_LEFT, cy1 + y0],
        })
    components.sort(key=lambda item: (-item['area'], item['box']))
    return components, float(mask.mean())


def _result_crop_sha256(pixels, box):
    """Hash the exact source crop used for one result-card field."""

    left, top, right, bottom = [int(round(value)) for value in box]
    x0, x1 = max(0, left - _PANE_LEFT), min(pixels.shape[1], right - _PANE_LEFT)
    y0, y1 = max(0, top), min(pixels.shape[0], bottom)
    if x1 <= x0 or y1 <= y0:
        return None
    return hashlib.sha256(pixels[y0:y1, x0:x1].tobytes()).hexdigest()


def _leading_digit_glare(components, box):
    """Recognize one large sparkle covering a result crop's leading glyph.

    The usual proof requires two compact components.  A leading digit can be
    hidden by one contiguous sparkle, however, as in a ``1381`` card rendered
    to OCR as ``381``.  Restrict this exception to a large component at the
    crop's left edge and a malformed/partial ratio; a lone arbitrary sparkle
    elsewhere remains insufficient proof.
    """

    if not isinstance(components, list) or len(components) != 1:
        return False
    component = components[0]
    if not isinstance(component, dict):
        return False
    component_box = component.get('box')
    if not _valid_box(component_box) or type(component.get('area')) is not int:
        return False
    left, _top, right, _bottom = [float(value) for value in box]
    c_left, c_top, c_right, c_bottom = [float(value) for value in component_box]
    width, height = c_right - c_left, c_bottom - c_top
    return (
        _LEADING_DIGIT_GLARE_MIN_AREA <= component['area'] <= 1000
        and width >= _LEADING_DIGIT_GLARE_MIN_WIDTH
        and height >= _LEADING_DIGIT_GLARE_MIN_HEIGHT
        and left <= c_left <= left + _LEADING_DIGIT_GLARE_MAX_LEFT_OFFSET
        and c_right <= left + _LEADING_DIGIT_GLARE_MAX_RIGHT_OFFSET
        and c_right <= right
        and c_bottom <= float(box[3])
    )


def _source_pixels(pane):
    """Return one RGB array for a gameplay pane, or ``None`` if malformed."""

    import numpy as np

    try:
        image = pane.convert('RGB') if hasattr(pane, 'convert') else pane
        pixels = np.asarray(image)
        width, height = pane_size()
        if pixels.shape != (height, width, 3):
            return None
        return pixels.astype(np.uint8, copy=False)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def read_result_card_occlusion(raw, pane):
    """Return source-bound proof for unresolved result fields covered by glare.

    This function reports metadata only.  It never supplies a numeric value.
    A result field must have fixed result-card geometry, a same-card label,
    an unresolved ratio, and compact warm foreground components in the exact
    source pixels.  Missing OCR or low confidence without the pixel proof
    remains an ordinary unknown field.  A single large component is accepted
    only for a leading-digit obstruction at the crop's left edge.
    """

    if (not isinstance(raw, dict) or raw.get('result_grid') is not True
            or str(raw.get('header', '')).strip().casefold() != 'training'):
        return {}
    pixels = _source_pixels(pane)
    if pixels is None:
        return {}
    source_timestamp_ms = raw.get('source_timestamp_ms')
    evidence = raw.get('evidence')
    if (type(source_timestamp_ms) is not int or source_timestamp_ms < 0
            or not isinstance(evidence, str) or not evidence.strip()):
        # A pixel proof without the reading identity cannot be attached to a
        # state observation safely.  Missing identity is an ordinary unknown.
        return {}
    expected_hash = raw.get('gameplay_sha256')
    actual_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
    if not isinstance(expected_hash, str) or expected_hash != actual_hash:
        raise ValueError('Result-card occlusion pixels differ from the source observation.')
    regions = raw.get('regions')
    lines = raw.get('lines')
    if not isinstance(regions, dict) or not isinstance(lines, list):
        return {}

    def accepted_ratio(observation, minimum_confidence):
        if not isinstance(observation, dict):
            return False
        compact = re.sub(r'\s+', '', str(observation.get('text', '')).strip())
        match = re.fullmatch(r'(\d{1,4})/(\d{3,4})', compact)
        if (not match
                or not _confidence_at_least(observation.get('confidence'),
                                            minimum_confidence)):
            return False
        return 1000 <= int(match[2]) and int(match[1]) <= int(match[2])

    fields, proof = [], {}
    for field in FIELDS[:5]:
        observation = regions.get('result.' + field)
        if (not _result_region_eligible(field, observation)
                or not str(observation.get('text', '')).strip()
                or not _confidence_valid(observation.get('confidence'))
                or accepted_ratio(observation, 97)):
            continue
        refined = regions.get('numeric_result.' + field)
        if (isinstance(refined, dict)
                and not _confidence_valid(refined.get('confidence'))):
            continue
        if accepted_ratio(refined, 90):
            continue
        label = _result_card_label(field, lines, observation['box'])
        if label is None:
            continue
        components, mask_fraction = _result_glare_components(pixels, observation['box'])
        leading_digit = _leading_digit_glare(components, observation['box'])
        if len(components) < _GLARE_MIN_COMPONENTS and not leading_digit:
            continue
        source_crop_sha256 = _result_crop_sha256(pixels, observation['box'])
        if source_crop_sha256 is None:
            continue
        minimum_components = 1 if leading_digit else _GLARE_MIN_COMPONENTS
        minimum_component_area = (
            _LEADING_DIGIT_GLARE_MIN_AREA if leading_digit
            else _GLARE_MIN_AREA)
        fields.append(field)
        proof[field] = {
            'status': 'unresolved_occluded_result_field',
            'field': field,
            'occlusion_scope': 'leading_digit' if leading_digit else 'multi_component',
            'region': deepcopy(observation),
            'label': deepcopy(label),
            'pixel': {
                'basis': 'same_source_result_value_crop_glare',
                'box': list(observation['box']),
                'mask_fraction': round(mask_fraction, 6),
                'components': deepcopy(components),
                'components_sha256': _result_component_digest(components),
                'source_crop_sha256': source_crop_sha256,
                'minimum_components': minimum_components,
                'minimum_component_area': minimum_component_area,
                'minimum_local_contrast': _GLARE_MIN_LOCAL_CONTRAST,
            },
        }
    if not fields:
        return {}
    return {
        'schema_version': RESULT_OCCLUSION_SCHEMA,
        'basis': RESULT_OCCLUSION_BASIS,
        'source_timestamp_ms': source_timestamp_ms,
        'evidence': evidence,
        'gameplay_sha256': expected_hash,
        'fields': sorted(fields),
        'proof': proof,
    }


def _valid_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if not all(type(item) in (int, float) for item in value):
        return False
    try:
        coordinates = [float(item) for item in value]
    except (OverflowError, TypeError, ValueError):
        return False
    if not all(math.isfinite(item) for item in coordinates):
        return False
    left, top, right, bottom = coordinates
    return right > left and bottom > top


def _center(value):
    return ((float(value[0]) + float(value[2])) / 2,
            (float(value[1]) + float(value[3])) / 2)


def _within_box(box, bounds):
    x, y = _center(box)
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def _result_label_box_eligible(field, box):
    """Require the full OCR label box to fit the field's result geometry."""

    card = _result_card(field)
    if card is None or not _valid_box(box):
        return False
    left, top, right, bottom = [float(value) for value in box]
    width, height = right - left, bottom - top
    return (18 <= width <= 150 and 10 <= height <= 55
            and card[0] <= left and right <= card[2]
            and card[1] - 45 <= top and bottom <= card[1] + 25)


def _result_region_eligible(field, observation):
    """Check the fixed result-card geometry for one field crop."""

    if not isinstance(observation, dict) or not _valid_box(observation.get('box')):
        return False
    box = observation['box']
    left, top, right, bottom = [float(item) for item in box]
    width, height = right - left, bottom - top
    if not 70 <= width <= 210 or not 24 <= height <= 80:
        return False
    card = _result_card(field)
    return card is not None and _within_box(box, card)


def _result_label(field, lines, value_box):
    """Return one same-card label, or ``None`` when identity is ambiguous."""

    if not _valid_box(value_box):
        return None
    value_x, value_y = _center(value_box)
    labels = []
    for line in lines if isinstance(lines, list) else []:
        if (not isinstance(line, dict)
                or not _confidence_at_least(line.get('confidence'), 90)):
            continue
        if str(line.get('text', '')).strip().casefold() != field.casefold():
            continue
        if not _valid_box(line.get('box')):
            continue
        label_box = line['box']
        label_x, label_y = _center(label_box)
        # The label is directly above its card value.  This relative check is
        # stronger than accepting a field name anywhere in the frame and
        # works for both rows of the result grid.
        if (abs(label_x - value_x) <= 90
                and 18 <= value_y - label_y <= 90):
            labels.append(line)
    if len(labels) != 1:
        return None
    return labels[0]


def _occluded_result_numerator(text):
    """Read a numerator when the cap tail contains an OCR decimal artifact.

    A decimal in the cap fragment is a useful signal that the result card was
    read through a transient overlay (for example ``556/1.0``).  The fragment
    is retained as diagnostic text and is never converted into a cap.  Plain
    short ratios remain in the existing partial-counter path.
    """

    if not isinstance(text, str):
        return None
    compact = re.sub(r'\s+', '', text.strip())
    match = re.fullmatch(r'(\d{1,4})/([1-9]\d{0,2}[.,]\d{1,3})', compact)
    return int(match[1]) if match else None


def _result_card_numerator(text):
    """Parse one result-card numerator without repairing an unknown cap.

    A complete ratio is ordinary result evidence even when its detector
    confidence is just below the parser's strict canonical threshold.  A
    decimal or otherwise malformed cap can still expose a trustworthy
    numerator, but remains explicitly cap-unknown.  Both forms are accepted
    only after the caller proves the same-card label and geometry.
    """

    if not isinstance(text, str):
        return None, None, None
    compact = re.sub(r'\s+', '', text.strip())
    complete = re.fullmatch(r'(\d{1,4})/(\d{3,4})', compact)
    if complete:
        value, cap = int(complete[1]), int(complete[2])
        if cap >= 1000 and value <= cap:
            return value, cap, 'complete'
        return None, None, None
    value = _occluded_result_numerator(compact)
    if value is not None:
        return value, None, 'malformed_cap'
    return None, None, None


def _validated_result_occlusion_fields(raw):
    """Return fields whose source-bound pixel proof must stay unknown.

    ``result_card_occlusion`` is attached by the source-pixel recovery path
    before parsing.  Validate its identity, geometry, and digest structure
    against this raw reading before allowing it to suppress a scalar result;
    malformed metadata must never hide an otherwise readable field.
    """

    metadata = raw.get('result_card_occlusion') if isinstance(raw, dict) else None
    if (not isinstance(metadata, dict)
            or metadata.get('schema_version') != RESULT_OCCLUSION_SCHEMA
            or metadata.get('basis') != RESULT_OCCLUSION_BASIS
            or type(metadata.get('source_timestamp_ms')) is not int
            or metadata.get('source_timestamp_ms') < 0
            or metadata.get('source_timestamp_ms') != raw.get('source_timestamp_ms')
            or not isinstance(metadata.get('evidence'), str)
            or not metadata.get('evidence').strip()
            or metadata.get('evidence') != raw.get('evidence')
            or not isinstance(metadata.get('gameplay_sha256'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', metadata.get('gameplay_sha256'))
            or metadata.get('gameplay_sha256') != raw.get('gameplay_sha256')):
        return frozenset()
    fields = metadata.get('fields')
    proof = metadata.get('proof')
    regions = raw.get('regions')
    lines = raw.get('lines')
    if (not isinstance(fields, list) or not fields
            or any(type(field) is not str or field not in FIELDS[:5]
                   for field in fields)
            or len(set(fields)) != len(fields)
            or not isinstance(proof, dict)
            or set(proof) != set(fields)
            or not isinstance(regions, dict)
            or not isinstance(lines, list)):
        return frozenset()

    accepted = []
    for field in fields:
        item = proof.get(field)
        region = item.get('region') if isinstance(item, dict) else None
        if (not isinstance(item, dict)
                or item.get('status') != 'unresolved_occluded_result_field'
                or item.get('field') != field
                or region != regions.get('result.' + field)
                or not _result_region_eligible(field, region)):
            return frozenset()
        label = item.get('label')
        if not isinstance(label, dict) or label != _result_card_label(field, lines, region['box']):
            return frozenset()
        pixel = item.get('pixel')
        if not isinstance(pixel, dict) or pixel.get('box') != region.get('box'):
            return frozenset()
        if (pixel.get('basis') != 'same_source_result_value_crop_glare'
                or not _confidence_valid(region.get('confidence'))
                or not _confidence_valid(label.get('confidence'))):
            return frozenset()
        components = pixel.get('components')
        if not isinstance(components, list) or not components:
            return frozenset()
        minimum_components = pixel.get('minimum_components')
        minimum_area = pixel.get('minimum_component_area')
        scope = item.get('occlusion_scope', 'multi_component')
        if scope == 'leading_digit':
            if (minimum_components != 1
                    or minimum_area != _LEADING_DIGIT_GLARE_MIN_AREA
                    or not _leading_digit_glare(components, region['box'])):
                return frozenset()
        elif scope == 'multi_component':
            if (minimum_components != _GLARE_MIN_COMPONENTS
                    or minimum_area != _GLARE_MIN_AREA
                    or len(components) < _GLARE_MIN_COMPONENTS):
                return frozenset()
        else:
            return frozenset()
        seen_boxes = set()
        left, top, right, bottom = [float(value) for value in region['box']]
        for component in components:
            if not isinstance(component, dict):
                return frozenset()
            component_box = component.get('box')
            area = component.get('area')
            if (not _valid_box(component_box) or type(area) is not int
                    or area < minimum_area or area > 1000):
                return frozenset()
            c_left, c_top, c_right, c_bottom = [float(value) for value in component_box]
            if not (left + 1 < c_left and top + 1 < c_top
                    and c_right < right - 1 and c_bottom < bottom - 1):
                return frozenset()
            if area > (c_right - c_left) * (c_bottom - c_top):
                return frozenset()
            key = tuple(component_box)
            if key in seen_boxes:
                return frozenset()
            seen_boxes.add(key)
        if (pixel.get('components_sha256') != _result_component_digest(components)
                or not isinstance(pixel.get('components_sha256'), str)
                or not re.fullmatch(r'[0-9a-fA-F]{64}', pixel.get('components_sha256'))
                or not isinstance(pixel.get('source_crop_sha256'), str)
                or not re.fullmatch(r'[0-9a-fA-F]{64}', pixel.get('source_crop_sha256'))
                or not _confidence_valid(pixel.get('mask_fraction'))
                or not 0 < float(pixel.get('mask_fraction')) < 1
                or pixel.get('minimum_local_contrast') != _GLARE_MIN_LOCAL_CONTRAST):
            return frozenset()
        accepted.append(field)
    return frozenset(accepted)


def read_training_result_values(raw):
    """Read current stat numerators from a source-bound result-card crop.

    Returns ``(values, provenance)``.  This path is deliberately limited to
    high-rate result readings, where the named result crops, same-frame labels,
    and a matching detector line are available.  A malformed or missing cap does
    not block a clearly readable current value, but the cap remains unknown.
    Conflicting labels, boxes, or numerators are rejected.
    """

    if not isinstance(raw, dict):
        return {}, {}
    if (raw.get('result_grid') is not True
            or raw.get('current_grid') is True
            or not isinstance(raw.get('header'), str)
            or raw['header'].strip().casefold() != 'training'):
        return {}, {}
    regions = raw.get('regions')
    lines = raw.get('lines')
    if not isinstance(regions, dict) or not isinstance(lines, list):
        return {}, {}
    values, provenance = {}, {}
    occluded_fields = _validated_result_occlusion_fields(raw)
    for field in FIELDS[:5]:
        if field in occluded_fields:
            # The detector's scalar numerator is retained only as raw OCR
            # evidence; a source-pixel obstruction makes the state field
            # explicitly unknown until a later clear frame is observed.
            continue
        observation = regions.get('result.' + field)
        if not _result_region_eligible(field, observation):
            continue
        value, cap, format_kind = _result_card_numerator(
            observation.get('text', ''))
        if value is None:
            continue
        # A complete ratio gets a slightly stronger threshold than a
        # malformed-cap numerator.  The former can be promoted as a normal
        # total only when its source line is still high-confidence; the latter
        # is intentionally allowed at the existing weak-crop threshold.
        minimum_confidence = 90 if format_kind == 'malformed_cap' else 95
        if not _confidence_at_least(observation.get('confidence'),
                                    minimum_confidence):
            continue
        label = _result_label(field, lines, observation['box'])
        if label is None:
            continue

        # If the full detector also saw a ratio in this card, it must agree on
        # the numerator.  Duplicate same-value lines are one observation; a
        # disagreement leaves the field unresolved instead of selecting a
        # convenient candidate.
        line_values = []
        line_readings = []
        for line in lines:
            if (not isinstance(line, dict)
                    or not _confidence_at_least(line.get('confidence'),
                                                minimum_confidence)):
                continue
            if not _valid_box(line.get('box')):
                continue
            if not _within_box(line['box'], _result_card(field)):
                continue
            text = str(line.get('text', '')).strip()
            line_value, line_cap, line_kind = _result_card_numerator(text)
            if line_value is not None:
                line_values.append(line_value)
                line_readings.append((line_value, line_cap, line_kind, text))
        # Require the same parseable numerator in a detector line as in the
        # field crop.  This keeps a stale crop from becoming canonical and
        # leaves ordinary short partial ratios to result_counter.py.
        if not line_values or set(line_values) != {value}:
            continue
        if not any(reading[0] == value for reading in line_readings):
            continue

        # A numerator can survive a cap disagreement, but a cap is canonical
        # only when the region and every parseable detector ratio agree.  This
        # prevents a crop's 1355 from masking a same-frame detector reading of
        # 1500 or a partial ``/1.0`` tail.  The source label and numerator stay
        # useful even when this cap proof is unresolved.
        detector_cap_values = {
            reading[1] for reading in line_readings
            if reading[0] == value and reading[2] == 'complete'
        }
        detector_has_partial = any(
            reading[0] == value and reading[2] != 'complete'
            for reading in line_readings
        )
        cap_agrees = (
            format_kind == 'complete'
            and cap is not None
            and not detector_has_partial
            and detector_cap_values == {cap}
        )

        if cap_agrees:
            cap_status = 'readable'
        elif (format_kind == 'malformed_cap'
              and detector_has_partial
              and not detector_cap_values):
            # Preserve the established status for the ordinary weak-cap path
            # where both source views expose the same malformed tail.
            cap_status = 'unresolved_occluded_or_malformed'
        else:
            cap_status = 'unresolved_conflicting_or_partial_detector_cap'

        values[field] = value
        provenance[field] = {
            'basis': 'same_frame_labeled_result_card_numerator',
            'region': deepcopy(observation),
            'label': deepcopy(label),
            'cap_status': cap_status,
            'cap_text': str(observation.get('text', '')).strip(),
        }
        if not cap_agrees:
            if detector_cap_values:
                provenance[field]['detector_caps'] = sorted(detector_cap_values)
            if detector_has_partial:
                provenance[field]['detector_cap_status'] = 'partial_or_malformed'
        if cap_agrees:
            provenance[field]['cap'] = cap
    return values, provenance


def _turn_number_from_line(line):
    if not isinstance(line, dict) or line.get('confidence', 0) < 90:
        return None
    text = re.sub(r'\s+', ' ', str(line.get('text', '')).strip()).casefold()
    match = re.fullmatch(r'(\d{1,2})\s+turn\(s\)', text)
    return int(match[1]) if match else None


# The goal countdown on the PC pane; the header it sits in is pinned to the top.
_GOAL_COUNTDOWN_BOUNDS = (235, 35, 430, 115)


def _countdown_region_value(region, turn_anchor, left_anchor):
    """Read the bound countdown crop only when its header geometry agrees."""

    if not isinstance(region, dict) or not _confidence_at_least(
            region.get('confidence'), 90):
        return None
    text = re.sub(r'\s+', ' ', str(region.get('text', '')).strip()).casefold()
    if not re.fullmatch(r'\d{1,2}', text) or not _valid_box(region.get('box')):
        return None
    box = region['box']
    if not _within_box(box, place(_GOAL_COUNTDOWN_BOUNDS, 'tc')):
        return None
    left, top, right, bottom = [float(value) for value in box]
    width, height = right - left, bottom - top
    # ``regions.countdown`` is a small source crop around the leading number,
    # rather than a free-standing OCR token.  Keep broad jitter tolerance but
    # reject a whole-panel or unrelated numeric crop.
    if not 20 <= width <= 100 or not 15 <= height <= 75:
        return None
    turn_box = turn_anchor['box']
    left_box = left_anchor['box']
    region_center_x, region_center_y = _center(box)
    turn_center_x, _ = _center(turn_box)
    left_center_x, _ = _center(left_box)
    if region_center_x >= min(turn_center_x, left_center_x):
        return None
    horizontal_gap = max(turn_box[0] - right, left - turn_box[2], 0)
    if horizontal_gap > 32:
        return None
    anchor_top = min(turn_box[1], left_box[1])
    anchor_bottom = max(turn_box[3], left_box[3])
    if bottom < anchor_top - 18 or top > anchor_bottom + 18:
        return None
    return int(text)


def read_goal_turns(lines, countdown_region=None):
    """Read the final-goal countdown from its same-frame text anchors.

    The concert countdown also contains a number and ``turn(s)``, but it has
    no ``left`` anchor and is lower on the panel.  Requiring all three pieces
    in the upper goal geometry prevents that number from becoming the goal
    countdown.  Missing or conflicting text stays ``(None, None)``.
    """

    if not isinstance(lines, list):
        return None, None
    turn_anchors = []
    left_anchors = []
    candidates = []
    number_observations = []
    bounds = place(_GOAL_COUNTDOWN_BOUNDS, 'tc')
    for line in lines:
        if not isinstance(line, dict) or line.get('confidence', 0) < 90:
            continue
        if not _valid_box(line.get('box')):
            continue
        box = line['box']
        # Header goal panel: keep the bounds broad enough for detector jitter,
        # while excluding the separate Concert in card below it.
        if not _within_box(box, bounds):
            continue
        text = re.sub(r'\s+', ' ', str(line.get('text', '')).strip()).casefold()
        if text == 'turn(s)':
            turn_anchors.append(line)
        else:
            merged = _turn_number_from_line(line)
            if merged is not None:
                turn_anchors.append(line)
                candidates.append(merged)
                number_observations.append((merged, line))
        if text == 'left':
            left_anchors.append(line)
        if re.fullmatch(r'\d{1,2}', text):
            value = int(text)
            candidates.append(value)
            number_observations.append((value, line))
    if len(turn_anchors) != 1 or len(left_anchors) != 1:
        return None, None
    region_value = _countdown_region_value(
        countdown_region, turn_anchors[0], left_anchors[0])
    if region_value is not None:
        candidates.append(region_value)
    if len(set(candidates)) != 1:
        return None, None
    value = candidates[0] if candidates else None
    if value is None:
        return None, None
    number_observation = next(
        (line for candidate, line in number_observations if candidate == value),
        None,
    )
    if number_observation is None and region_value is None:
        return None, None
    proof = {
        'basis': 'same_frame_goal_countdown_geometry',
        'value': value,
        'number': deepcopy(number_observation if number_observation is not None
                           else countdown_region),
        'turn_anchor': deepcopy(turn_anchors[0]),
        'left_anchor': deepcopy(left_anchors[0]),
    }
    if region_value is not None:
        proof['countdown_region'] = deepcopy(countdown_region)
    return value, proof


def read_main_stat_caps(raw):
    """Return independently readable caps and their same-frame OCR proof.

    Header labels and column geometry distinguish the stat bar from scenario
    currency caps, skill prices, and the separate animated result grid. Missing
    or conflicting text stays missing; no default cap values are supplied.
    """
    # The lower stat bar remains a direct source observation while a career
    # menu is open, even when the blue current-grid pixel probe is false.  A
    # result grid is a different layout and is never allowed to supply these
    # caps.  The non-grid path below requires all five labeled current values
    # at the stat-bar geometry, so a stray ``/number`` in an unrelated panel
    # cannot become a cap.
    if raw.get('result_grid') is True:
        return {}, {}
    lines = [line for line in raw.get('lines', [])
             if isinstance(line, dict) and line.get('confidence', 0) >= 90
             and len(line.get('box', [])) == 4]
    caps, proof = {}, {}
    stat_bar_values = {}
    for field, column in zip(FIELDS[:5], BOXES[:5]):
        # The stat bar is pinned to the bottom of the clear area.
        left, _, right, _ = place(column, 'bc')

        def center_in(line, x0, y0, x1, y1):
            x, y, xx, yy = line['box']
            return x0 <= (x + xx) / 2 <= x1 and y0 <= (y + yy) / 2 <= y1

        # The same main-stat bar occurs at two vertical positions in the
        # career UI.  Anchor all subordinate rows to the observed header
        # rather than hard-coding one y-band; this keeps the proof tied to the
        # source geometry and works through menu transitions.
        headers = [line for line in lines
                   if str(line.get('text', '')).strip().casefold() == field
                   and center_in(line, left - 24, place_y(650, 'b'), right + 24, place_y(785, 'b'))]
        if len(headers) != 1:
            continue
        header_y = (headers[0]['box'][1] + headers[0]['box'][3]) / 2
        values = [line for line in lines
                  if re.fullmatch(r'\d{1,4}', str(line.get('text', '')).strip())
                  and center_in(line, left - 12, header_y + 10, right + 12, header_y + 70)]
        if len({int(line['text']) for line in values}) == 1 and values:
            stat_bar_values[field] = values[0]
        candidates = []
        for line in lines:
            # RapidOCR sometimes renders the thin slash as a V in this
            # low-contrast row. Keep the literal observation and mark the
            # normalization in proof; the labeled column and current value
            # geometry still have to agree.
            match = re.fullmatch(r'(?:\d{1,4}\s*)?[/VYlI]\s*(\d{1,4})',
                                 str(line.get('text', '')).strip(), re.IGNORECASE)
            if (match and int(match[1]) > 0
                    and (line.get('confidence', 0) >= 95
                         if str(line.get('text', '')).lstrip().startswith('/')
                         else line.get('confidence', 0) >= 90)
                    and center_in(line, left - 12, header_y + 20, right + 12, header_y + 100)):
                candidates.append((int(match[1]), line))
        values = {value for value, _ in candidates}
        if len(values) == 1:
            caps[field] = candidates[0][0]
            proof[field] = dict(header=deepcopy(headers[0]),
                                readings=[deepcopy(line) for _, line in candidates],
                                basis='labeled_main_stat_bar_cap',
                                slash_normalization=(
                                    'ocr_slash_variant' if any(
                                        not str(line.get('text', '')).lstrip().startswith('/')
                                        for _, line in candidates)
                                    else None))
    # A false current-grid probe is accepted only for a complete, labeled
    # main stat bar. The ordinary source-bar fixtures do not include current
    # values and therefore remain unknown.
    if raw.get('current_grid') is not True and len(stat_bar_values) < len(FIELDS[:5]):
        return {}, {}
    return caps, proof
