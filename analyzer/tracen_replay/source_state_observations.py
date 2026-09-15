"""Expose accepted same-frame numeric snapshots separately from reconciliation."""

from copy import deepcopy
import math
import re

from .stat_state_details import (
    _RESULT_COLUMNS,
    _RESULT_ROWS,
    _result_component_digest,
    _result_label_box_eligible,
)


CHANNEL_FIELDS = {'stats': ('speed','stamina','power','guts','wit','skill_points'),
                  'performance': ('dance','passion','vocal','visual','composure')}

# ``facts.result_values`` is a typed, same-reading total from the training
# result card.  It is a state snapshot, but it is a different source layout
# from the ordinary current-stat grid.  Keep the screen gate here so a
# candidate/result hint or an unrelated fact cannot silently become a state.
RESULT_SNAPSHOT_SCREENS = frozenset(('training_result',))
RESULT_OCCLUSION_SCHEMA = 'tracen-replay/result-card-occlusion-v1'
RESULT_OCCLUSION_BASIS = 'source_bound_result_card_pixel_occlusion'
_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')


def _box(value):
    """Return a finite positive box, or ``None`` for malformed geometry."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           for item in value):
        return None
    left, top, right, bottom = (float(item) for item in value)
    if not all(math.isfinite(item) for item in (left, top, right, bottom)):
        return None
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _label_matches(field, text):
    """Accept the result label or one bounded OCR edit used by the reader."""

    actual = re.sub(r'\s+', '', str(text)).casefold()
    target = field.casefold()
    if actual == target:
        return True
    if abs(len(actual) - len(target)) > 1:
        return False
    if len(actual) == len(target):
        return sum(left != right for left, right in zip(actual, target)) <= 1
    if len(actual) > len(target):
        actual, target = target, actual
    left = right = 0
    skipped = False
    while left < len(actual) and right < len(target):
        if actual[left] == target[right]:
            left += 1
            right += 1
            continue
        if skipped:
            return False
        skipped = True
        right += 1
    return True


def _same_box(left, right):
    left_box, right_box = _box(left), _box(right)
    return left_box is not None and right_box is not None and left_box == right_box


def _sha256(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value)


def _result_proof_geometry(field, item):
    """Validate the bounded geometry emitted by the pixel reader."""

    if not isinstance(item, dict):
        return False
    region = item.get('region')
    region_box = _box(region.get('box')) if isinstance(region, dict) else None
    if region_box is None:
        return False
    left, top, right, bottom = region_box
    if not (70 <= right - left <= 210 and 24 <= bottom - top <= 80):
        return False
    region_confidence = region.get('confidence') if isinstance(region, dict) else None
    if (isinstance(region_confidence, bool)
            or not isinstance(region_confidence, (int, float))
            or not math.isfinite(float(region_confidence))
            or not 0 <= region_confidence <= 100):
        return False
    column, row = _RESULT_COLUMNS.get(field), _RESULT_ROWS.get(field)
    if (column is None or row is None
            or not (column[0] <= (left + right) / 2 <= column[1])
            or not (row[0] <= (top + bottom) / 2 <= row[1])):
        return False
    label = item.get('label')
    label_box = _box(label.get('box')) if isinstance(label, dict) else None
    label_confidence = label.get('confidence') if isinstance(label, dict) else None
    if (label_box is None or not _label_matches(field, label.get('text', ''))
            or isinstance(label_confidence, bool)
            or not isinstance(label_confidence, (int, float))
            or not math.isfinite(float(label_confidence))
            or not 90 <= label_confidence <= 100
            or not _result_label_box_eligible(field, label.get('box'))):
        return False
    label_x = (label_box[0] + label_box[2]) / 2
    label_y = (label_box[1] + label_box[3]) / 2
    value_x, value_y = (left + right) / 2, (top + bottom) / 2
    if not (abs(label_x - value_x) <= 90
            and 18 <= value_y - label_y <= 90
            and _RESULT_COLUMNS[field][0] <= label_x <= _RESULT_COLUMNS[field][1]
            and _RESULT_ROWS[field][0] - 45 <= label_y <= _RESULT_ROWS[field][0] + 25):
        return False
    pixel = item.get('pixel')
    if not isinstance(pixel, dict) or not _same_box(pixel.get('box'), region.get('box')):
        return False
    components = pixel.get('components')
    mask_fraction = pixel.get('mask_fraction')
    if (isinstance(mask_fraction, bool) or not isinstance(mask_fraction, (int, float))
            or not math.isfinite(float(mask_fraction))
            or not 0 < float(mask_fraction) < 1):
        return False
    local_contrast = pixel.get('minimum_local_contrast')
    occlusion_scope = item.get('occlusion_scope', 'multi_component')
    minimum_components = pixel.get('minimum_components')
    minimum_component_area = pixel.get('minimum_component_area')
    if occlusion_scope == 'leading_digit':
        leading_component = components[0] if isinstance(components, list) and len(components) == 1 else None
        leading_box = (leading_component.get('box')
                       if isinstance(leading_component, dict) else None)
        leading_area = (leading_component.get('area')
                        if isinstance(leading_component, dict) else None)
        leading_left = leading_box[0] if _box(leading_box) is not None else None
        leading_right = leading_box[2] if _box(leading_box) is not None else None
        leading_top = leading_box[1] if _box(leading_box) is not None else None
        leading_bottom = leading_box[3] if _box(leading_box) is not None else None
        if (minimum_components != 1
                or minimum_component_area != 80
                or type(leading_area) is not int
                or leading_area < 80
                or leading_left is None
                or leading_right is None
                or leading_top is None
                or leading_bottom is None
                or leading_right - leading_left < 8
                or leading_bottom - leading_top < 16
                or leading_left > left + 28
                or leading_right > left + 48):
            return False
    elif occlusion_scope == 'multi_component':
        if (minimum_components != 2
                or minimum_component_area != 20):
            return False
    else:
        return False
    if (pixel.get('basis') != 'same_source_result_value_crop_glare'
            or type(minimum_components) is not int
            or type(minimum_component_area) is not int
            or isinstance(local_contrast, bool)
            or not isinstance(local_contrast, (int, float))
            or not math.isfinite(float(local_contrast))
            or local_contrast != 15.0):
        return False
    if (not _sha256(pixel.get('components_sha256'))
            or not _sha256(pixel.get('source_crop_sha256'))):
        return False
    if not isinstance(components, list) or len(components) < minimum_components:
        return False
    seen_boxes = set()
    for component in components:
        if not isinstance(component, dict):
            return False
        component_box = _box(component.get('box'))
        area = component.get('area')
        if (component_box is None or type(area) is not int
                or area < minimum_component_area
                or area > 1000):
            return False
        if not (left + 1 < component_box[0]
                and top + 1 < component_box[1]
                and component_box[2] < right - 1
                and component_box[3] < bottom - 1):
            return False
        if area > (component_box[2] - component_box[0]) * (component_box[3] - component_box[1]):
            return False
        key = tuple(component_box)
        if key in seen_boxes:
            return False
        seen_boxes.add(key)
    if pixel['components_sha256'] != _result_component_digest(components):
        return False
    return True


def _typed_values(source, fields):
    """Return only accepted non-negative integer fields from one source.

    Missing/invalid fields remain unknown to the caller.  In particular this
    helper does not fill a field from another dictionary or calculate a total
    from an effect/gain.
    """

    if not isinstance(source, dict):
        return None
    values = {
        field: value if type(value) is int and value >= 0 else None
        for field, value in source.items()
        if field in fields
    }
    return values if any(value is not None for value in values.values()) else None


def _stats_snapshot(row, stats, facts):
    """Select one direct stats snapshot without composing layouts.

    The current grid has precedence when it contributes any valid value.  A
    result total is considered only when the parser classified this reading
    as ``training_result`` and the current grid supplied no valid fields.
    This preserves partial/unknown fields and prevents cross-crop overrides.
    """

    current = _typed_values(stats.get('values'), CHANNEL_FIELDS['stats'])
    if current is not None:
        return current, 'stats.values'
    screen = row.get('screen')
    if isinstance(screen, str) and screen in RESULT_SNAPSHOT_SCREENS:
        result = _typed_values(facts.get('result_values'), CHANNEL_FIELDS['stats'])
        if result is not None:
            return result, 'facts.result_values'
    return None, None


def _result_occlusion(facts, values, row=None):
    """Accept only validated result-card occlusion metadata for unknowns."""

    metadata = facts.get('result_card_occlusion') if isinstance(facts, dict) else None
    if (not isinstance(metadata, dict)
            or metadata.get('schema_version') != RESULT_OCCLUSION_SCHEMA
            or metadata.get('basis') != RESULT_OCCLUSION_BASIS
            or type(metadata.get('source_timestamp_ms')) is not int
            or metadata.get('source_timestamp_ms') < 0
            or not isinstance(metadata.get('evidence'), str)
            or not metadata.get('evidence').strip()
            or not isinstance(metadata.get('gameplay_sha256'), str)
            or not _SHA256.fullmatch(metadata.get('gameplay_sha256'))):
        return [], None
    if isinstance(row, dict):
        if (row.get('source_timestamp_ms') != metadata['source_timestamp_ms']
                or row.get('evidence') != metadata['evidence']):
            return [], None
        row_hash = row.get('gameplay_sha256')
        if row_hash is not None and row_hash != metadata['gameplay_sha256']:
            return [], None
    fields = metadata.get('fields')
    proof = metadata.get('proof')
    if (not isinstance(fields, list) or not fields
            or any(type(field) is not str for field in fields)
            or len(set(fields)) != len(fields)
            or not isinstance(proof, dict)
            or set(proof) != set(fields)):
        return [], None
    accepted = []
    for field in fields:
        if (field not in CHANNEL_FIELDS['stats']
                or type(values.get(field)) is int):
            return [], None
        item = proof.get(field)
        if (not isinstance(item, dict)
                or item.get('status') != 'unresolved_occluded_result_field'
                or item.get('field') != field
                or not _result_proof_geometry(field, item)):
            return [], None
        accepted.append(field)
    return sorted(accepted), deepcopy(metadata)


def build_observations(readings, maximum_gap_ms=500):
    """Never fill one frame's unknown fields with another frame's values."""
    result, active = [], {}
    rows = [(index,row) for index,row in enumerate(readings)
            if isinstance(row,dict) and type(row.get('source_timestamp_ms')) is int
            and row['source_timestamp_ms'] >= 0]
    rows.sort(key=lambda pair: pair[1]['source_timestamp_ms'])
    for index,row in rows:
        time, evidence = row['source_timestamp_ms'], row.get('evidence')
        if not isinstance(evidence,str) or not evidence:
            active.clear()
            continue
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
        stats_source, stats_source_path = _stats_snapshot(row, stats, facts)
        inputs = {'stats': stats_source, 'performance': facts.get('performance_points')}
        source_paths = {'stats': stats_source_path, 'performance': 'facts.performance_points'}
        for channel,fields in CHANNEL_FIELDS.items():
            source = inputs[channel]
            source_path = source_paths[channel]
            if not isinstance(source, dict):
                active.pop(channel,None)
                continue
            values = {field: value if type(value) is int and value >= 0 else None
                      for field,value in source.items() if field in fields}
            if not any(value is not None for value in values.values()):
                active.pop(channel,None)
                continue
            payload = {'kind':'state','channel':channel,'values':values}
            calendar = stats.get('calendar_text')
            if isinstance(calendar,str) and calendar.strip():
                payload['calendar_text'] = calendar
            remaining = stats.get('turns_remaining_to_goal')
            if type(remaining) is int and remaining >= 0:
                payload['turns_remaining_to_goal'] = remaining
            cap_key = 'stat_caps' if channel == 'stats' else 'performance_caps'
            if isinstance(facts.get(cap_key), dict):
                caps = {field:value for field,value in facts[cap_key].items()
                        if field in fields and type(value) is int and value > 0}
                if caps:
                    payload['caps'] = caps
            if channel == 'stats' and source_path == 'facts.result_values':
                occluded, occlusion_proof = _result_occlusion(facts, values, row)
                if occluded:
                    payload['occluded_fields'] = occluded
                    payload['occlusion_provenance'] = occlusion_proof
            current = active.get(channel)
            if (current is None or current['payload'] != payload
                    or current.get('observation_basis') != source_path
                    or time-current['end_ms'] > maximum_gap_ms):
                ref = f'/gameplay_tracking/state_observations/{len(result)}'
                current = {'id':ref,'source_ref':ref,'category':'state','phase':'observed',
                           'payload':payload,'start_ms':time,'end_ms':time,'evidence':[],
                           'source_observations':[],'uncertain':False,
                           'cross_frame_values_merged':False,'reconciliation_endpoint_inferred':False,
                           'observation_basis':source_path}
                result.append(current)
                active[channel] = current
            current['end_ms'] = time
            if evidence not in current['evidence']:
                current['evidence'].append(evidence)
            current['source_observations'].append({'source_ref':f'/gameplay_tracking/readings/{index}',
                'source_timestamp_ms':time,'evidence':evidence,'values':deepcopy(values),
                'value_source':source_path})
    return result
