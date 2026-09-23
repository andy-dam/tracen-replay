"""Source-backed continuity checks for receipt observations.

An obscured receipt line can explain why two explicit observations belong to
one visual receipt, but it is never an award on its own.  The helpers here
therefore only suppress a later explicit duplicate after a bounded source
chain proves continuity.
"""

import re
import math

from .gameplay import receipt_rows
from .layout import inside_pane
from .source_clock import elapsed


_ALLOWED_RECEIPT_SCREENS = frozenset(("unknown", "event_outcome"))
_MAX_BRIDGE_STEP_MS = 250
_MIN_OCCLUDED_OBSERVATIONS = 2
_MIN_OCCLUDED_CONFIDENCE = 95
_MAX_DOWNWARD_JITTER_PX = 4

_HINT_PREFIX_RE = re.compile(r"^\s*gained\s*(?P<amount>\d+)\s+hint\b", re.IGNORECASE)
_COMPLETE_HINT_RE = re.compile(
    r"^\s*gained\s*(?P<amount>\d+)\s+hint\s+(?:level\(s\)|levels?)\s+for\s+(?P<name>.+?)(?P<terminal>[.!])\s*$",
    re.IGNORECASE,
)


def _effect_key(effect):
    return "|".join(str(effect.get(part) or "") for part in ("kind", "field", "name"))


def _effect_signature(effect):
    return tuple(effect.get(part) for part in ("kind", "field", "name", "amount", "direction", "value"))


def _line_confidence(line):
    values = []
    for key in ("confidence", "pre_occlusion_confidence"):
        value = line.get(key)
        if type(value) in (int, float) and math.isfinite(value):
            values.append(value)
    return max(values, default=0)


def _valid_box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(type(value) in (int, float) and math.isfinite(value) for value in box)
            and inside_pane(box))


def _same_receipt_slot(first, second):
    """Allow the receipt list to move while retaining its horizontal slot."""
    if not _valid_box(first) or not _valid_box(second):
        return False
    first_height = first[3] - first[1]
    second_height = second[3] - second[1]
    first_center = (first[1] + first[3]) / 2
    second_center = (second[1] + second[3]) / 2
    top, bottom = receipt_rows(785, 965)
    return (top <= first_center <= bottom and top <= second_center <= bottom
            and abs(first[0] - second[0]) <= 12
            and abs(first[2] - second[2]) <= 20
            and abs(first_height - second_height) <= 10
            and abs(first_center - second_center) <= 80)


def _same_line_geometry(first, second):
    """Collapse OCR and occlusion records for one physical text line."""
    if not _valid_box(first) or not _valid_box(second):
        return False
    first_height = first[3] - first[1]
    second_height = second[3] - second[1]
    first_center = (first[1] + first[3]) / 2
    second_center = (second[1] + second[3]) / 2
    return (abs(first[0] - second[0]) <= 3
            and abs(first[2] - second[2]) <= 4
            and abs(first_height - second_height) <= 4
            and abs(first_center - second_center) <= 8)


def _hint_prefix_amount(text):
    """Return the literal amount in a visible hint receipt prefix."""
    match = _HINT_PREFIX_RE.match(str(text or ""))
    if match is None:
        return None
    token = match.group("amount")
    # Leading zeroes are not a normalization rule for OCR evidence.  They
    # make the numeric token ambiguous, so leave that line unknown.
    if len(token) > 1 and token.startswith("0"):
        return None
    return int(token)


def _complete_hint_identity(text):
    """Parse only the complete, terminal receipt grammar used for conflicts."""
    match = _COMPLETE_HINT_RE.match(str(text or ""))
    if match is None:
        return None
    return int(match.group("amount")), match.group("name").strip()


def _hint_text_matches(text, effect):
    """Require the amount prefix and the exact visible name suffix."""
    if type(effect.get("amount")) is not int or not effect.get("name"):
        return False
    text = str(text or "").strip()
    if _hint_prefix_amount(text) != effect["amount"]:
        return False
    name = str(effect["name"]).strip()
    for match in re.finditer(re.escape(name), text, re.IGNORECASE):
        before = text[:match.start()]
        after = text[match.end():].strip()
        if before and not re.search(r"[^\w\s]$|\s$", before):
            continue
        if after not in ('', '.', '!'):
            continue
        return True
    return False


def _receiptish_text(text):
    text = str(text or "")
    return bool(re.search(r"\bgained\s+\d+\s+hint\b", text, re.IGNORECASE)
                or re.search(r"\bwent\s+(?:up|down)\b", text, re.IGNORECASE)
                or re.search(r"\b(?:gained|received|earned)\s+\d+\s+(?:fans?|skill)\b", text, re.IGNORECASE)
                or re.search(r"^\s*learned\s+(?:the\s+)?(?:song|technique)\b", text, re.IGNORECASE))


def _is_boundary(row):
    """Reject screens and dialogue that cannot be part of one receipt chain.

    A long line in the receipt band that reads as none of the receipt
    grammars is narrative. A line the parser turned into one of the row's
    effects (a friendship status, a condition) is a receipt line whatever
    its grammar, so it does not end a chain.
    """
    if row.get("screen", "unknown") not in _ALLOWED_RECEIPT_SCREENS:
        return True
    parsed = {" ".join(str(effect.get("raw_text", "")).split()).casefold()
              for effect in row.get("effects", []) if isinstance(effect, dict) and effect.get("raw_text")}
    top, bottom = receipt_rows(790, 950)
    for line in row.get("ocr", {}).get("neural", []):
        box = line.get("box", [])
        confidence = _line_confidence(line)
        if (line.get("overlay_occluded") is True):
            continue
        if (confidence >= 95 and _valid_box(box)
                and top < (box[1] + box[3]) / 2 < bottom
                and len(str(line.get("text", ""))) > 25
                and not _receiptish_text(line.get("text"))
                and " ".join(str(line.get("text", "")).split()).casefold() not in parsed):
            return True
    return False


def _titles_are_compatible(left_event, right_event, rows):
    anchors = {event.get("context_title") for event in (left_event, right_event)
               if event.get("context_title")}
    if len(anchors) > 1:
        return False
    expected = next(iter(anchors), None)
    for row in rows:
        observed = {row.get("context_title"), row.get("context_title_candidate")} - {None, ""}
        if expected:
            if any(title != expected for title in observed):
                return False
        elif observed:
            return False
    return True


def _explicit_observations(event, effect, rows_by_evidence):
    """Return source rows that explicitly contain this exact effect value."""
    key = _effect_key(effect)
    observations = []
    for evidence in event.get("field_evidence", {}).get(key, []):
        row = rows_by_evidence.get(evidence)
        if row is None:
            continue
        if any(_effect_signature(observed) == _effect_signature(effect)
               for observed in row.get("effects", [])):
            observations.append((row["source_timestamp_ms"], evidence, row))
    return sorted(observations, key=lambda item: (item[0], item[1]))


def _explicit_hint_line(row, effect):
    """Return one unobscured OCR line that supports an explicit hint."""
    matches=[]
    for line in row.get("ocr", {}).get("neural", []):
        confidence = line.get("confidence")
        if (line.get("overlay_occluded") is True
                or type(confidence) not in (int, float)
                or not math.isfinite(confidence)
                or confidence < 95
                or not _valid_box(line.get("box"))):
            continue
        if _hint_text_matches(line.get("text"), effect):
            matches.append(line)
    return matches[0] if len(matches) == 1 else None


def _candidate_line(line, source, index, effect):
    """Describe a visible hint-prefix line without turning it into an effect."""
    if not isinstance(line, dict) or not _valid_box(line.get("box")):
        return None
    if source == "occluded":
        if line.get("recipient_name_occluded") is not False:
            return None
        overlays = line.get("overlay_boxes")
        if not isinstance(overlays, list) or not any(_valid_box(box) for box in overlays):
            return None
    elif line.get("overlay_occluded") is True:
        return None
    amount = _hint_prefix_amount(line.get("text"))
    if amount is None:
        return None
    confidence = _line_confidence(line)
    status = "unknown"
    complete = _complete_hint_identity(line.get("text"))
    if confidence >= _MIN_OCCLUDED_CONFIDENCE and complete is not None:
        complete_amount, complete_name = complete
        if complete_amount != effect.get("amount") or complete_name != effect.get("name"):
            status = "contradiction"
        else:
            status = "exact"
    return dict(
        source=source,
        index=index,
        amount=amount,
        box=list(line["box"]),
        text=str(line.get("text") or ""),
        confidence=confidence,
        status=status,
        raw=dict(line),
    )


def _hint_line_candidates(row, effect):
    """Collect physical hint-prefix lines and merge duplicate OCR channels."""
    candidates = []
    facts = row.get("facts", {}).get("occluded_receipt_lines", [])
    blocked_boxes = []
    if isinstance(facts, list):
        for index, line in enumerate(facts):
            candidate = _candidate_line(line, "occluded", index, effect)
            if candidate is not None:
                candidates.append(candidate)
            elif isinstance(line, dict) and _valid_box(line.get("box")):
                # A malformed or recipient-occluded facts record must not be
                # bypassed by the duplicate neural line at the same pixels.
                blocked_boxes.append(list(line["box"]))
    neural = row.get("ocr", {}).get("neural", [])
    if isinstance(neural, list):
        for index, line in enumerate(neural):
            candidate = _candidate_line(line, "ocr", index, effect)
            if (candidate is not None
                    and not any(_same_line_geometry(candidate["box"], box) for box in blocked_boxes)):
                candidates.append(candidate)

    groups = []
    for candidate in candidates:
        group = next((group for group in groups if _same_line_geometry(group["box"], candidate["box"])), None)
        if group is None:
            groups.append(dict(box=candidate["box"], candidates=[candidate]))
        else:
            group["candidates"].append(candidate)

    merged = []
    for group in groups:
        entries = group["candidates"]
        target = [candidate for candidate in entries if candidate["amount"] == effect.get("amount")]
        # A complete high-confidence different name/rank or amount in this
        # physical slot is a contradiction.  Adjacent receipt rows with a
        # different box remain separate groups and are harmless.
        contradiction = any(candidate["status"] == "contradiction" for candidate in entries)
        if target and any(candidate["amount"] != effect.get("amount") for candidate in entries):
            contradiction = True
        selected = max(
            entries,
            key=lambda candidate: (
                candidate["status"] == "exact",
                candidate["source"] == "occluded",
                candidate["confidence"],
            ),
        )
        merged.append(dict(
            box=list(group["box"]),
            candidates=entries,
            target=target,
            contradiction=contradiction,
            selected=selected,
        ))
    return merged


def _candidate_evidence(candidate):
    return dict(
        source=candidate["source"],
        line_index=candidate["index"],
        text=candidate["text"],
        box=list(candidate["box"]),
        confidence=candidate["confidence"],
        amount=candidate["amount"],
        status=candidate["status"],
    )


def _middle_hint_support(row, effect, left_box=None, right_box=None):
    """Return one unique line track member, allowing unknown middle text."""
    groups = _hint_line_candidates(row, effect)
    if left_box is not None and right_box is not None:
        groups = [group for group in groups
                  if _same_receipt_slot(left_box, group["box"])
                  and _same_receipt_slot(right_box, group["box"])]
    if len(groups) != 1:
        return None
    group = groups[0]
    if group["contradiction"] or not group["target"]:
        return None
    target = group["target"]
    if any(candidate["status"] == "contradiction" for candidate in target):
        return None
    selected = max(
        target,
        key=lambda candidate: (
            candidate["status"] == "exact",
            candidate["source"] == "occluded",
            candidate["confidence"],
        ),
    )
    has_occluded = any(candidate["source"] == "occluded" for candidate in target)
    has_exact = any(candidate["status"] == "exact" for candidate in target)
    has_unknown = any(candidate["status"] == "unknown" for candidate in target)
    return dict(
        kind="occluded" if has_occluded else ("explicit" if has_exact else "geometry"),
        box=list(group["box"]),
        text=selected["text"],
        confidence=selected["confidence"],
        source=selected["source"],
        occluded_observation=has_occluded,
        unknown=has_unknown,
        raw_evidence=[_candidate_evidence(candidate) for candidate in target],
    )


def find_occluded_hint_bridge(left_event, left_effect, right_event, right_effect, rows_by_evidence):
    """Find a bounded source chain linking two explicit hint observations.

    A valid bridge needs two independently sampled, high-confidence explicit
    endpoint lines.  Intermediate text may be unreadable, but each sampled
    row must expose one unique, geometrically tracked line with the same
    visible amount prefix.  The track is continuity evidence only: it never
    creates an award from an uncertain line.
    """
    if (_effect_signature(left_effect) != _effect_signature(right_effect)
            or left_effect.get("kind") != "skill_hint_change"):
        return None
    left_observations = _explicit_observations(left_event, left_effect, rows_by_evidence)
    right_observations = _explicit_observations(right_event, right_effect, rows_by_evidence)
    if not left_observations or not right_observations:
        return None

    all_rows = sorted(rows_by_evidence.values(), key=lambda row: (row.get("source_timestamp_ms", 0), row.get("evidence", "")))
    candidates = []
    for left_time, left_evidence, left_row in reversed(left_observations):
        for right_time, right_evidence, right_row in right_observations:
            if right_time <= left_time:
                continue
            middle = [row for row in all_rows if left_time < row.get("source_timestamp_ms", 0) < right_time]
            if not middle or any(_is_boundary(row) for row in middle):
                continue
            if not _titles_are_compatible(left_event, right_event, middle):
                continue

            left_line = _explicit_hint_line(left_row, left_effect)
            right_line = _explicit_hint_line(right_row, right_effect)
            if left_line is None or right_line is None:
                continue

            supports = []
            for row in middle:
                support = _middle_hint_support(row, left_effect, left_line["box"], right_line["box"])
                if support is None:
                    supports = []
                    break
                supports.append((row["source_timestamp_ms"], row["evidence"], support))
            if not supports:
                continue
            supports.sort(key=lambda item: (item[0], item[1]))
            if sum(support[2]["occluded_observation"] for support in supports) < _MIN_OCCLUDED_OBSERVATIONS:
                continue
            track = [(left_time, left_evidence, dict(
                kind="explicit", box=list(left_line["box"]), text=left_line.get("text", ""),
                confidence=left_line.get("confidence", 0), source="ocr", unknown=False,
                occluded_observation=False, raw_evidence=[],
            ))]
            track.extend(supports)
            track.append((right_time, right_evidence, dict(
                kind="explicit", box=list(right_line["box"]), text=right_line.get("text", ""),
                confidence=right_line.get("confidence", 0), source="ocr", unknown=False,
                occluded_observation=False, raw_evidence=[],
            )))
            if any(not _same_receipt_slot(previous[2]["box"], current[2]["box"])
                   for previous, current in zip(track, track[1:])):
                continue
            centers = [((item[2]["box"][1] + item[2]["box"][3]) / 2) for item in track]
            # Receipt rows scroll upward.  A small amount of detector jitter
            # is harmless, while a downward reset indicates another receipt.
            if any(current > previous + _MAX_DOWNWARD_JITTER_PX
                   for previous, current in zip(centers, centers[1:])):
                continue
            proof_times = [item[0] for item in track]
            if any(elapsed(earlier, later) > _MAX_BRIDGE_STEP_MS for earlier, later in zip(proof_times, proof_times[1:])):
                continue
            candidates.append((right_time - left_time, -left_time, left_evidence, right_evidence, supports, track))

    if not candidates:
        return None
    _, _, left_evidence, right_evidence, selected, track = min(candidates, key=lambda item: item[:4])
    unknown_slot_evidence = []
    for timestamp, evidence, support in selected:
        if not support["unknown"]:
            continue
        for raw_evidence in support["raw_evidence"]:
            unknown_slot_evidence.append(dict(
                evidence=evidence,
                source_timestamp_ms=timestamp,
                **raw_evidence,
            ))
    track_evidence = []
    for timestamp, evidence, support in track:
        track_evidence.append(dict(
            evidence=evidence,
            source_timestamp_ms=timestamp,
            kind=support["kind"],
            box=list(support["box"]),
            text=support["text"],
            confidence=support["confidence"],
            source=support["source"],
            unknown=support["unknown"],
            occluded_observation=support["occluded_observation"],
        ))
    occluded_times = [support[0] for support in selected if support[2]["occluded_observation"]]
    return dict(
        explicit_evidence=[left_evidence, right_evidence],
        continuity_evidence=[support[1] for support in selected],
        occluded_evidence=[support[1] for support in selected if support[2]["occluded_observation"]],
        unknown_slot_evidence=unknown_slot_evidence,
        track_evidence=track_evidence,
        first_occluded_ms=min(occluded_times),
        last_occluded_ms=max(occluded_times),
        basis="bounded_same_slot_hint_track_between_explicit_observations",
    )


def collapse_cross_event_hint_duplicates(events, readings):
    """Suppress only later explicit hint duplicates proven by source continuity.

    This deliberately leaves obscured-only observations out of ``effects``.
    It also leaves the later event itself in place so unrelated explicit
    rewards in that event retain their own transaction boundary.
    """
    rows_by_evidence = {row["evidence"]: row for row in readings}
    for right_index, right_event in enumerate(events):
        for right_effect in list(right_event.get("effects", [])):
            if right_effect.get("kind") != "skill_hint_change":
                continue
            candidates = []
            for left_event in events[:right_index]:
                for left_effect in left_event.get("effects", []):
                    if _effect_signature(left_effect) != _effect_signature(right_effect):
                        continue
                    bridge = find_occluded_hint_bridge(left_event, left_effect, right_event, right_effect,
                                                       rows_by_evidence)
                    if bridge is not None:
                        candidates.append((left_event, left_effect, bridge))
            if len(candidates) != 1:
                continue

            left_event, left_effect, bridge = candidates[0]
            key = _effect_key(right_effect)
            right_proofs = list(right_event.get("field_evidence", {}).get(key, []))
            left_effect.setdefault("occluded_continuity_evidence", [])
            for evidence in bridge["occluded_evidence"]:
                if evidence not in left_effect["occluded_continuity_evidence"]:
                    left_effect["occluded_continuity_evidence"].append(evidence)
            left_effect.setdefault("unknown_slot_evidence", [])
            for observation in bridge["unknown_slot_evidence"]:
                if observation not in left_effect["unknown_slot_evidence"]:
                    left_effect["unknown_slot_evidence"].append(observation)
            left_effect.setdefault("cross_event_duplicate_evidence", []).append(dict(
                event_id=right_event.get("id"),
                evidence=right_proofs,
                bridge_evidence=bridge["occluded_evidence"],
                continuity_evidence=bridge["continuity_evidence"],
                unknown_slot_evidence=bridge["unknown_slot_evidence"],
                track_evidence=bridge["track_evidence"],
                basis=bridge["basis"],
            ))
            right_event.setdefault("deduplicated_receipt_effects", []).append(dict(
                effect=dict(right_effect),
                evidence=right_proofs,
                bridge_evidence=bridge["occluded_evidence"],
                continuity_evidence=bridge["continuity_evidence"],
                unknown_slot_evidence=bridge["unknown_slot_evidence"],
                track_evidence=bridge["track_evidence"],
                basis=bridge["basis"],
            ))
            right_event["effects"] = [effect for effect in right_event["effects"] if effect is not right_effect]
            right_event.get("field_evidence", {}).pop(key, None)
    return events
