"""Source-bound lower bounds for distinct inheritance lines during a scroll."""
from copy import deepcopy
from .gameplay import receipt_rows
from .inheritance_occurrences import _expected_raw_text
from .inheritance_spark_identity import (
    MAX_ADJACENT_MS, MAX_SCROLL_PIXELS, MIN_SCROLL_PIXELS, STATIONARY_PIXELS,
    _center, _lines, _matching_anchors, _same_context, _stable_box,
)
from .source_clock import elapsed
_KINDS = frozenset(("inheritance_spark", "inheritance_inspiration"))
_PAYLOAD = ("kind", "field", "name", "amount", "direction", "value")
_BOTTOM_ENTRY_CENTER = 920
_Y_TOLERANCE, _ANCHOR_TOLERANCE = 9, 5
def _key(effect):
    return "|".join(str(effect.get(part) or "") for part in ("kind", "field", "name"))
def _same_payload(left, right):
    return isinstance(left, dict) and isinstance(right, dict) and all(
        left.get(part) == right.get(part) for part in _PAYLOAD
    )
def _bounds(event, times):
    first, last = event.get("first_seen_ms"), event.get("last_seen_ms")
    if first is None or last is None:
        return None
    if (first is not None and (type(first) is not int or first < 0)
            or last is not None and (type(last) is not int or last < 0)
            or first is not None and last is not None and last < first):
        return None
    if not times:
        return None
    low, high = min(times), max(times)
    first, last = max(low, first if first is not None else low), min(
        high, last if last is not None else high)
    return (first, last) if first <= last else None
def _row_targets(row, event, effect):
    if (not isinstance(row, dict) or row.get("screen") != "event_outcome"
            or not _same_context(event, row, row)):
        return None
    observed = row.get("effects")
    if not isinstance(observed, (list, tuple)):
        return []
    same_key = [item for item in observed
                if isinstance(item, dict) and _key(item) == _key(effect)]
    if (not same_key or any(not _same_payload(item, effect)
                            or item.get("raw_text") != effect.get("raw_text")
                            for item in same_key)):
        return None
    raw_text = effect.get("raw_text")
    if not isinstance(raw_text, str) or raw_text != _expected_raw_text(effect):
        return None
    matches = [line for line in _lines(row) if line.get("text") == raw_text]
    result = []
    for line in sorted(matches, key=lambda item: (_center(item["box"]), item["box"])):
        if any(_stable_box(line["box"], prior["box"])
               and abs(_center(line["box"]) - _center(prior["box"])) <= 6
               for prior in result):
            continue
        result.append(dict(text=raw_text, box=list(line["box"]),
                           line_index=row["ocr"]["neural"].index(line),
                           confidence=line["confidence"]))
    return result
def _same_snapshot(left, right):
    return len(left) == len(right) and all(
        _stable_box(a["box"], b["box"])
        and abs(_center(a["box"]) - _center(b["box"])) <= 6
        for a, b in zip(left, right))
def _snapshot(event, effect, timestamp, rows):
    options, boundary = [], False
    for evidence, row in rows:
        if not isinstance(evidence, str) or row.get("evidence") != evidence:
            boundary = True
            continue
        targets = _row_targets(row, event, effect)
        if targets is None:
            boundary = True
        else:
            options.append((evidence, row, targets))
    if not options:
        return dict(source_timestamp_ms=timestamp, evidence=None, row=None,
                    targets=[], ambiguous=True)
    selected = max(options, key=lambda item: (
        len(item[2]), sum(line["confidence"] for line in item[2]), item[0]))
    evidence, row, targets = selected
    if any(not _same_snapshot(targets, option[2]) for option in options
           if option is not selected):
        boundary = True
    return dict(source_timestamp_ms=timestamp, evidence=evidence, row=row,
                targets=targets, ambiguous=boundary)
def _anchor_motion(event, previous, current, target_texts):
    anchors = _matching_anchors(previous["row"], current["row"], target_texts)
    if len(anchors) < 2:
        return None, "insufficient_exact_anchors"
    for index, first in enumerate(anchors):
        for second in anchors[index + 1:]:
            for key in ('first_box', 'second_box'):
                a, b = first[key], second[key]
                if abs(_center(a) - _center(b)) < 0.6 * max(a[3] - a[1], b[3] - b[1]):
                    return None, "overlapping_anchor_geometry"
    deltas = [_center(anchor["second_box"]) - _center(anchor["first_box"])
              for anchor in anchors]
    if any(abs(delta - deltas[0]) > _ANCHOR_TOLERANCE for delta in deltas[1:]):
        return None, "incompatible_anchor_motion"
    delta = sum(deltas) / len(deltas)
    if abs(delta) <= STATIONARY_PIXELS:
        mode = "stationary"
    # Allow one pixel of quantization around the shared motion bound.
    elif -MAX_SCROLL_PIXELS <= delta <= -(MIN_SCROLL_PIXELS - 1):
        mode = "upward_scroll"
    else:
        return None, "reverse_or_unbounded_motion"
    return dict(mode=mode, delta_y=delta, anchors=anchors), None
def _transition(event, effect, previous, current, segment):
    if current["ambiguous"] or previous["ambiguous"]:
        return None, "conflicting_source_snapshot"
    if elapsed(previous["source_timestamp_ms"], current["source_timestamp_ms"]) > MAX_ADJACENT_MS:
        return None, "non_adjacent_source_rows"
    if not current["targets"] or not _same_context(event, previous["row"], current["row"]):
        return None, "context_or_target_gap"
    target_texts = {effect.get("raw_text"), effect.get("original_text")} - {None}
    motion, reason = _anchor_motion(event, previous, current, target_texts)
    if motion is None:
        return None, reason
    active = [(index, item) for index, item in enumerate(segment["tracks"])
              if item["active"]]
    possibilities = {}
    for track_index, item in active:
        expected = _center(item["last_box"]) + motion["delta_y"]
        candidates = [index for index, line in enumerate(current["targets"])
                      if _stable_box(item["last_box"], line["box"])
                      and abs(_center(line["box"]) - expected) <= _Y_TOLERANCE]
        if len(candidates) > 1:
            return None, "non_unique_target_geometry"
        possibilities[track_index] = (expected, candidates)
    owners = {}
    for track_index, (_, candidates) in possibilities.items():
        for line_index in candidates:
            owners.setdefault(line_index, []).append(track_index)
    if any(len(track_indices) > 1 for track_indices in owners.values()):
        return None, "non_unique_target_geometry"
    matches = {track_index: candidates[0]
               for track_index, (_, candidates) in possibilities.items()
               if candidates}
    used = set(matches.values())
    closed = []
    for track_index, item in active:
        expected, candidates = possibilities[track_index]
        if candidates:
            continue
        elif (motion["mode"] == "upward_scroll"
              and current["targets"]
              and expected < min(_center(line["box"]) for line in _lines(current["row"]))):
            closed.append(track_index)
        else:
            return None, "interior_target_dropout"
    new_indices = [index for index in range(len(current["targets"])) if index not in used]
    if new_indices and motion['mode'] != 'upward_scroll':
        return None, "stationary_target_addition"
    mapped_centers = [_center(current["targets"][index]["box"]) for index in used]
    for index in new_indices:
        center = _center(current["targets"][index]["box"])
        if (center < receipt_rows(_BOTTOM_ENTRY_CENTER, _BOTTOM_ENTRY_CENTER)[0]
                or mapped_centers and center < max(mapped_centers) - 3):
            return None, "unanchored_interior_target"
    return dict(matches=matches, new_indices=new_indices, closed=closed, motion=motion), None
def _start(snapshot):
    tracks = []
    for index, line in enumerate(snapshot["targets"], 1):
        tracks.append(dict(track_id=f"track-{index:03d}", entry=dict(
            source_timestamp_ms=snapshot["source_timestamp_ms"], evidence=snapshot["evidence"],
            box=list(line["box"]), line_index=line["line_index"],
            basis="simultaneous_snapshot"), last_box=list(line["box"]),
            observations=[dict(source_timestamp_ms=snapshot["source_timestamp_ms"],
                               evidence=snapshot["evidence"], box=list(line["box"]),
                               line_index=line["line_index"])],
            continuity=[], active=True))
    return dict(start_timestamp_ms=snapshot["source_timestamp_ms"],
                end_timestamp_ms=snapshot["source_timestamp_ms"], distinct_count=len(tracks),
                tracks=tracks, frame_counts=[dict(
                    source_timestamp_ms=snapshot["source_timestamp_ms"],
                    evidence=snapshot["evidence"], simultaneous_count=len(tracks))])
def _apply(segment, snapshot, update):
    for track_index, line_index in update["matches"].items():
        item, line = segment["tracks"][track_index], snapshot["targets"][line_index]
        previous = item["observations"][-1]
        item["last_box"] = list(line["box"])
        item["observations"].append(dict(source_timestamp_ms=snapshot["source_timestamp_ms"],
                                          evidence=snapshot["evidence"], box=list(line["box"]),
                                          line_index=line["line_index"]))
        item["continuity"].append(dict(
            source_timestamp_ms=snapshot["source_timestamp_ms"],
            evidence_pair=[previous["evidence"], snapshot["evidence"]],
            mode=update["motion"]["mode"], delta_y=update["motion"]["delta_y"],
            anchors=deepcopy(update["motion"]["anchors"])))
    for track_index in update["closed"]:
        segment["tracks"][track_index]["active"] = False
    for line_index in update["new_indices"]:
        line = snapshot["targets"][line_index]
        segment["tracks"].append(dict(
            track_id=f"track-{len(segment['tracks']) + 1:03d}",
            entry=dict(source_timestamp_ms=snapshot["source_timestamp_ms"],
                       evidence=snapshot["evidence"], box=list(line["box"]),
                       line_index=line["line_index"], basis="bottom_entry",
                       proof=dict(bottom_entry_center=_center(line["box"]),
                                  source_timestamp_ms=snapshot["source_timestamp_ms"],
                                  mode=update["motion"]["mode"],
                                  delta_y=update["motion"]["delta_y"],
                                  anchors=deepcopy(update["motion"]["anchors"]))),
            last_box=list(line["box"]), observations=[dict(
                source_timestamp_ms=snapshot["source_timestamp_ms"],
                evidence=snapshot["evidence"], box=list(line["box"]),
                line_index=line["line_index"])], continuity=[], active=True))
    segment["distinct_count"] = len(segment["tracks"])
    segment["end_timestamp_ms"] = snapshot["source_timestamp_ms"]
    segment["frame_counts"].append(dict(source_timestamp_ms=snapshot["source_timestamp_ms"],
                                         evidence=snapshot["evidence"],
                                         simultaneous_count=len(snapshot["targets"])))
def _public(segment):
    result = deepcopy(segment)
    for item in result["tracks"]:
        item.pop("last_box", None)
        item.pop("active", None)
    return result
def track(event, effect, rows_by_evidence):
    """Return a lower bound for exact ``effect`` lines; total remains unknown."""
    empty = {"semantic_key": _key(effect) if isinstance(effect, dict) else "",
             "minimum_observed_count": 0, "total_count": None,
             "count_complete": False, "segments": [], "breaks": []}
    if (not isinstance(event, dict) or not isinstance(effect, dict)
            or effect.get("kind") not in _KINDS or not isinstance(rows_by_evidence, dict)):
        return empty
    source_rows = [(evidence, row) for evidence, row in rows_by_evidence.items()
                   if isinstance(row, dict) and type(row.get("source_timestamp_ms")) is int]
    bounds = _bounds(event, [row[1]["source_timestamp_ms"] for row in source_rows])
    if bounds is None:
        return empty
    first, last = bounds
    grouped = {}
    for evidence, row in source_rows:
        if first <= row["source_timestamp_ms"] <= last:
            grouped.setdefault(row["source_timestamp_ms"], []).append((evidence, row))
    snapshots = [_snapshot(event, effect, timestamp, grouped[timestamp])
                 for timestamp in sorted(grouped)]
    segments, breaks, segment = [], [], None
    previous = None
    for snapshot in snapshots:
        if segment is None:
            if snapshot["targets"] and not snapshot["ambiguous"]:
                segment = _start(snapshot)
            previous = snapshot
            continue
        update, reason = _transition(event, effect, previous, snapshot, segment)
        if update is None:
            breaks.append(dict(previous_timestamp_ms=previous["source_timestamp_ms"],
                               source_timestamp_ms=snapshot["source_timestamp_ms"],
                               evidence_pair=[previous["evidence"], snapshot["evidence"]],
                               reason=reason))
            segments.append(_public(segment))
            segment = (_start(snapshot)
                       if (snapshot["targets"] and not snapshot["ambiguous"]
                           and reason not in {"interior_target_dropout",
                                              "unanchored_interior_target"})
                       else None)
        else:
            _apply(segment, snapshot, update)
        previous = snapshot
    if segment is not None:
        segments.append(_public(segment))
    selected_index = max(range(len(segments)),
                         key=lambda index: (segments[index]["distinct_count"],
                                            -segments[index]["start_timestamp_ms"]), default=None)
    selected = segments[selected_index] if selected_index is not None else None
    return dict(semantic_key=_key(effect),
                payload={part: deepcopy(effect.get(part)) for part in _PAYLOAD},
                minimum_observed_count=selected["distinct_count"] if selected else 0,
                total_count=None, count_complete=False, segments=segments,
                selected_segment_index=selected_index,
                selected_track_evidence=deepcopy(selected["tracks"]) if selected else [],
                breaks=breaks, source_observation_count=sum(len(rows) for rows in grouped.values()))

__all__ = ["track"]
