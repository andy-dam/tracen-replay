"""Recover effects from source-proven occluded receipt lines.

The ordinary receipt parser intentionally abstains when a cursor or animated
particle crosses a receipt line.  Numeric recovery handles a useful subset of
those lines, but the same source-bound reread policy also applies to
friendship, hints, conditions, songs, and other receipt effects.  This module
plans a bounded reread from the occlusion evidence and promotes only effects
whose clear reread comes from the same physical receipt line.

No expected label, name, amount, or residual is sent to OCR.  The source row
and receipt geometry are used only to select a bounded window and bind a
candidate back to its owning outcome event.
"""

from __future__ import annotations

import copy
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .gameplay import effects_from_lines


SCHEMA = "tracen-replay/occluded-receipt-recovery-v1"
MAX_WINDOW_MS = 5_000
DEFAULT_PRE_MS = 750
DEFAULT_POST_MS = 250
DEFAULT_FPS = 16
# The full-run default covers the planned source evidence while retaining a
# finite resource bound.  The same defaults are used by fresh OCR and replay
# preparation, so a prepared cache cannot be validated under a different
# coverage budget than the worker that would have produced it.
DEFAULT_MAX_WINDOWS = 128
DEFAULT_MAX_DURATION_MS = 240_000

_RECEIPT_SCREENS = frozenset(("unknown", "event_outcome"))


class OccludedReceiptRecoveryError(ValueError):
    """Raised when an occluded receipt recovery input is unsafe or stale."""


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _valid_box(value: Any) -> bool:
    """Validate one full gameplay-space receipt box."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if not all(_finite_number(item) for item in value):
        return False
    left, top, right, bottom = (float(item) for item in value)
    return (
        148 <= left < right <= 958
        and 770 <= top < bottom <= 1000
    )


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _valid_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _text_digest(value: Any) -> str:
    """Fingerprint source-observed line text without making it an OCR target."""

    return hashlib.sha256(_normal_text(value).encode("utf-8")).hexdigest()


def _safe_child_path(root: Path, value: Any, *, label: str) -> Path:
    """Resolve one cache path while keeping it inside ``root``.

    Recovery manifests are JSON supplied by a previous process.  A valid
    source hash does not make an absolute path or ``..`` path safe, so every
    path is checked before it is opened.
    """

    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise OccludedReceiptRecoveryError(f"{label} must be a relative path.")
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise OccludedReceiptRecoveryError(f"{label} escapes the recovery cache.")
    # Windows accepts drive-qualified paths even when ``Path`` is constructed
    # from a POSIX-looking manifest string.  A colon is never valid in these
    # relative artifact names.
    if ":" in str(value):
        raise OccludedReceiptRecoveryError(f"{label} is not a relative cache path.")
    base = root.resolve()
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise OccludedReceiptRecoveryError(f"{label} escapes the recovery cache.") from exc
    return resolved


def _safe_relative_name(value: Any) -> bool:
    """Check a manifest name before comparing it with source metadata."""

    if not isinstance(value, str) or not value.strip():
        return False
    candidate = Path(value)
    return (
        not candidate.is_absolute()
        and ":" not in value
        and all(part not in {"", ".", ".."} for part in candidate.parts)
    )


def _timestamp(value: Any) -> bool:
    return type(value) is int and value >= 0


def _event_bounds(event: Any) -> tuple[int, int] | None:
    if not isinstance(event, dict):
        return None
    first, last = event.get("first_seen_ms"), event.get("last_seen_ms")
    if not (_timestamp(first) and _timestamp(last)) or last < first:
        return None
    return first, last


def _owner_events(events: Any, timestamp: int) -> list[tuple[int, dict[str, Any]]]:
    if not isinstance(events, list):
        return []
    result = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("kind") != "outcome":
            continue
        bounds = _event_bounds(event)
        if bounds is not None and bounds[0] <= timestamp <= bounds[1]:
            result.append((index, event))
    return result


def _owner_ref(index: int, event: dict[str, Any]) -> str | None:
    value = event.get("id")
    return value if isinstance(value, str) and value else None


def _source_row_owner(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return a source-owned receipt context when no outcome was assembled.

    A malformed or low-confidence receipt can leave ``outcome_events`` with
    no accepted event at all.  The source row can still carry a stable event
    context title, and the receipt line itself is already source-bound by its
    timestamp, evidence path, and obstruction geometry.  Keep this fallback
    ownerless (there is no fabricated event id); the title is only used to
    reject a reread from a different event context.
    """

    if row.get("screen") not in _RECEIPT_SCREENS:
        return None
    title = row.get("context_title")
    if not isinstance(title, str) or not title.strip():
        title = row.get("context_title_candidate")
    if not isinstance(title, str) or not title.strip():
        return None
    return {"context_title": title.strip()}


def _physical_overlay(line: Any) -> bool:
    """Require the line-level, source-pixel obstruction record.

    ``receipt_overlay_evidence`` by itself is not enough: a panel-level
    overlay can be nearby without crossing this particular line.  The
    annotation stage records the intersecting boxes on each blocked line.
    """

    if not isinstance(line, dict) or not _valid_box(line.get("box")):
        return False
    overlays = line.get("overlay_boxes")
    if not isinstance(overlays, list):
        return False
    return any(_valid_box(box) for box in overlays)


def _line_already_observed(row: dict[str, Any], line: dict[str, Any]) -> bool:
    """Avoid rereading a line already recovered by another receipt pass."""

    text = _normal_text(line.get("text"))
    if not text:
        return False
    for effect in row.get("effects", []):
        if not isinstance(effect, dict):
            continue
        if text in {
            _normal_text(effect.get("raw_text")),
            _normal_text(effect.get("normalized_text")),
        }:
            return True
    return False


def _trigger(timestamp: int, row: dict[str, Any], line: dict[str, Any],
             owner_index: int | None, owner: dict[str, Any] | None,
             start: int, end: int) -> dict[str, Any]:
    """Build a label-free recovery trigger from source facts."""

    owner_ref = _owner_ref(owner_index, owner) if owner is not None and owner_index is not None else None
    trigger = dict(
        source_timestamp_ms=timestamp,
        evidence=row.get("evidence"),
        line_box=list(line["box"]),
        source_line_text_sha256=_text_digest(line.get("text")),
        kind="occluded_receipt_line",
        owner_ref=owner_ref,
        owner_start_ms=owner.get("first_seen_ms") if owner else None,
        owner_end_ms=owner.get("last_seen_ms") if owner else None,
        window_start_ms=start,
        window_end_ms=end,
        physical_overlay_count=sum(
            1 for box in line.get("overlay_boxes", []) if _valid_box(box)
        ),
    )
    if isinstance(owner, dict) and isinstance(owner.get("context_title"), str):
        # This is source metadata for ownership checks only.  It is never
        # included in the OCR request or used as an expected effect label.
        trigger["owner_context_title"] = owner["context_title"]
    return trigger


def _merge_windows(requests: list[dict[str, Any]], *, max_window_ms: int) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for request in sorted(requests, key=lambda item: (
        item["start_ms"], item["end_ms"], item["source_timestamp_ms"],
    )):
        if (
            windows
            and request["start_ms"] <= windows[-1]["end_ms"]
            and max(windows[-1]["end_ms"], request["end_ms"])
            - windows[-1]["start_ms"] <= max_window_ms
        ):
            windows[-1]["end_ms"] = max(windows[-1]["end_ms"], request["end_ms"])
            windows[-1]["triggers"].append(request["trigger"])
        else:
            windows.append(dict(
                start_ms=request["start_ms"],
                end_ms=request["end_ms"],
                reason="source_bound_occluded_receipt_review",
                triggers=[request["trigger"]],
            ))
    return windows


def plan(readings: Any, events: Any, duration_ms: int, *,
         pre_ms: int = DEFAULT_PRE_MS, post_ms: int = DEFAULT_POST_MS,
         max_window_ms: int = MAX_WINDOW_MS) -> list[dict[str, Any]]:
    """Plan bounded rereads for every source-proven occluded receipt line.

    The returned trigger contains geometry and event ownership metadata, but
    no OCR target text or inferred value.  A point observation at ``t`` gets a
    window ``[t - pre_ms, t + post_ms]``.  When the original event has only a
    single sampled frame, the owning event is still considered unique while
    the clear reread may occur anywhere in that bounded window.
    """

    if type(duration_ms) is not int or duration_ms <= 0:
        raise OccludedReceiptRecoveryError("duration_ms must be a positive integer.")
    for name, value in (("pre_ms", pre_ms), ("post_ms", post_ms), ("max_window_ms", max_window_ms)):
        if type(value) is not int or value < 0:
            raise OccludedReceiptRecoveryError(f"{name} must be a nonnegative integer.")
    if max_window_ms <= 0 or max_window_ms > MAX_WINDOW_MS:
        raise OccludedReceiptRecoveryError("max_window_ms must be between 1 and 5000.")
    if pre_ms + post_ms + 1 > max_window_ms:
        raise OccludedReceiptRecoveryError("pre_ms and post_ms exceed max_window_ms.")

    requests: list[dict[str, Any]] = []
    if not isinstance(readings, list):
        return []
    for row in readings:
        if not isinstance(row, dict) or row.get("screen") not in _RECEIPT_SCREENS:
            continue
        timestamp = row.get("source_timestamp_ms")
        if not _timestamp(timestamp) or timestamp >= duration_ms:
            continue
        evidence = row.get("evidence")
        if not _safe_relative_name(evidence):
            # Without the original frame identity there is no source-bound
            # receipt line to reread or replay.
            continue
        facts = row.get("facts")
        lines = facts.get("occluded_receipt_lines") if isinstance(facts, dict) else None
        if not isinstance(lines, list):
            continue
        owners = _owner_events(events, timestamp)
        if len(owners) == 1:
            owner_index, owner = owners[0]
        elif owners:
            # Multiple accepted events overlap this source line.  Keep the
            # trigger unresolved rather than assigning it to whichever event
            # happens to be last in the list.
            owner_index, owner = None, None
        else:
            # The original event may be absent because every receipt line in
            # that frame was unreadable.  A source-owned context is still
            # usable for bounded reread and remains explicitly ownerless.
            owner_index, owner = None, _source_row_owner(row)
        start = max(0, timestamp - pre_ms)
        end = min(duration_ms, timestamp + post_ms + 1)
        if start >= end or end - start > max_window_ms:
            continue
        for line in lines:
            if not _physical_overlay(line) or _line_already_observed(row, line):
                continue
            requests.append(dict(
                source_timestamp_ms=timestamp,
                start_ms=start,
                end_ms=end,
                trigger=_trigger(timestamp, row, line, owner_index, owner, start, end),
            ))
    return _merge_windows(requests, max_window_ms=max_window_ms)


def _same_receipt_line(first: Any, second: Any) -> bool:
    """Match a reread line to the blocked physical line without text recall."""

    if not (_valid_box(first) and _valid_box(second)):
        return False
    a, b = tuple(float(value) for value in first), tuple(float(value) for value in second)
    first_center = (a[1] + a[3]) / 2
    second_center = (b[1] + b[3]) / 2
    first_width, second_width = a[2] - a[0], b[2] - b[0]
    first_height, second_height = a[3] - a[1], b[3] - b[1]
    return (
        abs(a[0] - b[0]) <= 16
        and abs(a[2] - b[2]) <= 20
        and abs(first_center - second_center) <= 18
        and abs(first_height - second_height) <= 12
        and abs(first_width - second_width) <= 32
    )


def _adjacent_receipt_line(first: Any, second: Any) -> bool:
    """Recognize a neighboring line in the same receipt text block.

    This is deliberately narrower than :func:`_same_receipt_line`: an
    adjacent line is useful as identity evidence, but it must not be treated
    as the blocked line itself.  Receipt rows normally overlap or nearly
    touch vertically, share a left edge, and remain within one text-line
    height.  A larger gap is kept out so an unrelated lower-panel label cannot
    inherit the blocked line's owner.
    """

    if not (_valid_box(first) and _valid_box(second)) or _same_receipt_line(first, second):
        return False
    a = tuple(float(value) for value in first)
    b = tuple(float(value) for value in second)
    upper, lower = sorted((a, b), key=lambda box: (box[1] + box[3]) / 2)
    upper_center = (upper[1] + upper[3]) / 2
    lower_center = (lower[1] + lower[3]) / 2
    vertical_gap = lower[1] - upper[3]
    return (
        0 < lower_center - upper_center <= 35
        and vertical_gap <= 6
        and abs(upper[0] - lower[0]) <= 16
    )


def _line_confidence(line: dict[str, Any]) -> float:
    values = []
    for key in ("confidence", "pre_occlusion_confidence"):
        value = line.get(key)
        if _finite_number(value):
            values.append(float(value))
    return max(values, default=0.0)


def _fresh_lines(row: dict[str, Any]) -> list[dict[str, Any]]:
    ocr = row.get("ocr")
    lines = ocr.get("neural") if isinstance(ocr, dict) else None
    if not isinstance(lines, list):
        return []
    return [
        line for line in lines
        if isinstance(line, dict)
        and _valid_box(line.get("box"))
        and line.get("overlay_occluded") is not True
        and _finite_number(line.get("confidence"))
        and float(line["confidence"]) >= 90
    ]


def _effect_signature(effect: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(effect.get(key) for key in (
        "kind", "field", "name", "amount", "direction", "value",
    ))


def _effect_key(effect: dict[str, Any]) -> str:
    """Make a duplicate key even when malformed fields are unhashable."""

    return json.dumps(
        {key: effect.get(key) for key in (
            "kind", "field", "name", "amount", "direction", "value",
        )},
        sort_keys=True,
        ensure_ascii=False,
        default=repr,
    )


def _slot_key(effect: dict[str, Any]) -> str:
    """Identify one physical receipt slot's semantics without the OCR name.

    Dense rereads of a single receipt line spell a damaged recipient or hint
    name differently from frame to frame.  Those spellings are one physical
    observation, not several transactions, so slot-level collapsing keys on
    kind/field/amount/direction/value only; the strongest reading supplies the
    literal name.  Distinct receipt lines still differ by geometry.
    """

    return json.dumps(
        {key: effect.get(key) for key in (
            "kind", "field", "amount", "direction", "value",
        )},
        sort_keys=True,
        ensure_ascii=False,
        default=repr,
    )


_RECEIPT_TERMINATORS = (".", "!", "\u3002", "\uff01")


def _complete_receipt_sentence(text: Any) -> bool:
    """Require a complete receipt sentence before a reread can be promoted.

    Every ordinary receipt line ends with a sentence terminator.  A wrapped
    receipt's first visual line (``Gained 1 hint level(s) for Pace Chaser``
    whose name continues on the next line) or a sentence whose tail is hidden
    by the cursor has none, and the grammar would otherwise parse it as a
    shorter, different effect.  The ordinary parser keeps its own wrapped
    receipt handling; this guard applies only to single restored lines.
    """

    normal = _normal_text(text)
    return bool(normal) and normal.endswith(_RECEIPT_TERMINATORS)


def _same_receipt_panel(row: Any, context: Any) -> bool:
    if not isinstance(row, dict) or row.get("screen") != "event_outcome":
        return False
    if isinstance(context, str) and context:
        row_contexts = {
            value for value in (
                row.get("context_title"),
                row.get("context_title_candidate"),
            )
            if isinstance(value, str) and value
        }
        if row_contexts and context not in row_contexts:
            return False
    return True


def _receipt_panel_bounds(base: Any, trigger: dict[str, Any]) -> tuple[int, int] | None:
    """Return the source span during which the trigger's receipt panel is visible.

    The trigger carries the owner event and inspection window bounds known at
    planning time.  Registered inspection frames can extend the same outcome
    panel by a few frames beyond that owner span (the panel is still on screen
    with the same context).  A clear reading in those frames is evidence about
    the same physical receipt, so the scope walks outward through contiguous
    ``event_outcome`` rows with a compatible context and stops at the first
    row that is not the same panel.  No other timestamp tolerance is used.
    """

    if not isinstance(base, list):
        return None
    starts = [value for value in (trigger.get("owner_start_ms"), trigger.get("window_start_ms"))
              if type(value) is int]
    ends = [value for value in (trigger.get("owner_end_ms"), trigger.get("window_end_ms"))
            if type(value) is int]
    if not starts or not ends:
        return None
    start, end = min(starts), max(ends)
    context = trigger.get("owner_context_title")
    rows = sorted(
        (row for row in base if isinstance(row, dict) and _timestamp(row.get("source_timestamp_ms"))),
        key=lambda row: row["source_timestamp_ms"],
    )
    for row in rows:
        timestamp = row["source_timestamp_ms"]
        if timestamp <= end:
            continue
        if _same_receipt_panel(row, context):
            end = timestamp
        else:
            break
    for row in reversed(rows):
        timestamp = row["source_timestamp_ms"]
        if timestamp >= start:
            continue
        if _same_receipt_panel(row, context):
            start = timestamp
        else:
            break
    return start, end


def _base_rows_for_trigger(base: Any, trigger: dict[str, Any]) -> list[dict[str, Any]]:
    bounds = _receipt_panel_bounds(base, trigger)
    if bounds is None:
        return []
    start, end = bounds
    context = trigger.get("owner_context_title")
    selected = []
    for row in base:
        if not isinstance(row, dict) or not _timestamp(row.get("source_timestamp_ms")):
            continue
        timestamp = row["source_timestamp_ms"]
        if not start <= timestamp <= end:
            continue
        if isinstance(context, str) and context:
            row_contexts = {
                value for value in (
                    row.get("context_title"),
                    row.get("context_title_candidate"),
                )
                if isinstance(value, str) and value
            }
            if row_contexts and context not in row_contexts:
                continue
        selected.append(row)
    return selected


def _same_receipt_column(first: Any, second: Any) -> bool:
    """Match receipt-line geometry while ignoring a vertical panel shift.

    The receipt bubble moves when the event title wraps differently or a
    line above disappears; every line keeps its left edge, width and height
    but changes y.  This is deliberately weaker than ``_same_receipt_line``
    and is only used together with a bounded name comparison.
    """

    if not (_valid_box(first) and _valid_box(second)):
        return False
    a = tuple(float(value) for value in first)
    b = tuple(float(value) for value in second)
    return (
        abs(a[0] - b[0]) <= 16
        and abs(a[2] - b[2]) <= 20
        and abs((a[3] - a[1]) - (b[3] - b[1])) <= 12
    )


def _row_overlay_boxes(row: Any) -> list[list[Any]]:
    """Collect every recorded obstruction box for one source row."""

    boxes: list[list[Any]] = []

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for box in value:
                if _valid_box(box) and list(box) not in boxes:
                    boxes.append(list(box))

    if not isinstance(row, dict):
        return boxes
    for key in ("overlay_boxes", "animated_overlay_boxes"):
        collect(row.get(key))
    facts = row.get("facts")
    if isinstance(facts, dict):
        for blocked in facts.get("occluded_receipt_lines") or []:
            if isinstance(blocked, dict):
                for key in ("overlay_boxes", "animated_overlay_boxes"):
                    collect(blocked.get(key))
        evidence = facts.get("receipt_overlay_evidence")
        if isinstance(evidence, dict):
            for key in ("overlay_boxes", "animated_overlay_boxes"):
                collect(evidence.get(key))
    return boxes


def _boxes_intersect(first: Any, second: Any) -> bool:
    if not (_valid_box(first) and _valid_box(second)):
        return False
    a = tuple(float(v) for v in first)
    b = tuple(float(v) for v in second)
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _base_has_clear_slot_reading(
    base: Any,
    effect: dict[str, Any],
    line_box: Any,
    triggers: list[dict[str, Any]],
    *,
    require_name_variant: bool = False,
) -> bool:
    """Return true when a base frame already read this receipt line unblocked.

    A dense reread of a line is a fallback for lines the base never read
    clearly.  If any base frame of the same receipt panel contains an
    unblocked line (no overlay, bounded confidence) at the same physical slot
    whose parsed effect has the same kind/field/amount/direction/value, the
    base reading is the canonical observation regardless of how the reread
    spelled the name.  When the panel shifted vertically, the same holds for
    a line in the same column whose name is a bounded spelling variant.

    ``require_name_variant`` is set by the clean adjacent-line path: two
    unobstructed readings of one slot that name different recipients are a
    genuine contradiction for the conflict handler to quarantine, so only a
    bounded spelling variant of the unobstructed base reading is treated as
    the same observation.  A reread of a blocked line, by contrast, is an
    obstructed view and can never contradict an unobstructed reading of the
    same slot, so the occluded path matches the slot regardless of spelling.
    """

    if not _valid_box(line_box):
        return False
    slot = _slot_key(effect)
    name = effect.get("name")
    seen_rows: set[int] = set()
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        for row in _base_rows_for_trigger(base, trigger):
            if id(row) in seen_rows:
                continue
            seen_rows.add(id(row))
            # A line the annotation stage recorded as blocked in this frame is
            # not a clear reading even when its OCR confidence stayed high
            # (an overlay can cross a glyph without lowering the score).
            facts = row.get("facts")
            blocked_boxes = [
                blocked.get("box")
                for blocked in ((facts.get("occluded_receipt_lines") if isinstance(facts, dict) else None) or [])
                if isinstance(blocked, dict) and _physical_overlay(blocked)
            ]
            overlays = _row_overlay_boxes(row)
            for line in _fresh_lines(row):
                if any(_same_receipt_line(line.get("box"), blocked_box) for blocked_box in blocked_boxes):
                    continue
                # Any recorded obstruction touching the line means this frame
                # is not an unobstructed reading, whatever the OCR score.
                if any(_boxes_intersect(line.get("box"), overlay) for overlay in overlays):
                    continue
                same_line = _same_receipt_line(line_box, line.get("box"))
                same_column = _same_receipt_column(line_box, line.get("box"))
                if not (same_line or same_column):
                    continue
                for parsed in _line_effects(line.get("text"), line.get("confidence")):
                    if _slot_key(parsed) != slot:
                        continue
                    names_compatible = (
                        name is None and parsed.get("name") is None
                    ) or (
                        isinstance(name, str) and isinstance(parsed.get("name"), str)
                        and _source_text_compatible(name, parsed.get("name"))
                    )
                    if same_line and not require_name_variant:
                        return True
                    if names_compatible:
                        return True
    return False


def _clear_base_conflicts(
    base: Any,
    restored_line: dict[str, Any],
    triggers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Veto a reread whose parsed value disagrees with a clear base reading.

    The blocked source line is by definition damaged, so a dense reread that
    merely agrees with that damaged text is not proof.  When the same physical
    slot was read clearly (unblocked, bounded confidence) in any base frame of
    the same receipt panel, the reread's amount/direction must agree with it.
    ``Energy went down by 8`` read while the cursor hides the ``1`` cannot
    replace a clear ``Energy went down by 18``.  Names are compared only when
    they match exactly (see ``_receipt_effect_subject_matches``), so a damaged
    recipient spelling never manufactures a conflict by itself.
    """

    conflicts: list[dict[str, Any]] = []
    seen_rows: set[int] = set()
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        for row in _base_rows_for_trigger(base, trigger):
            if id(row) in seen_rows:
                continue
            seen_rows.add(id(row))
            # The receipt panel scrolls and reflows between frames, so a
            # clear reading of this line can sit at another y in the same
            # column.  ``_receipt_line_conflicts_with_clear_reread`` compares
            # parsed subjects (kind/field and exact recipient name) before it
            # reports an amount or direction disagreement, so a same-column
            # line about a different subject never becomes a false conflict.
            clear_lines = [
                line for line in _fresh_lines(row)
                if _same_receipt_line(restored_line.get("box"), line.get("box"))
                or _same_receipt_column(restored_line.get("box"), line.get("box"))
            ]
            if not clear_lines:
                continue
            conflicts.extend(
                _receipt_line_conflicts_with_clear_reread(restored_line, clear_lines)
            )
    return conflicts


def _trigger_matches(row: dict[str, Any], windows: Any) -> list[dict[str, Any]]:
    timestamp = row.get("source_timestamp_ms")
    if not _timestamp(timestamp) or not isinstance(windows, list):
        return []
    result = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        start, end = window.get("start_ms"), window.get("end_ms")
        if (
            not (_timestamp(start) and _timestamp(end))
            or end <= start
            or end - start > MAX_WINDOW_MS
        ):
            continue
        if not start <= timestamp < end:
            continue
        triggers = window.get("triggers")
        if isinstance(triggers, list):
            for trigger in triggers:
                if not isinstance(trigger, dict):
                    continue
                trigger_start = trigger.get("window_start_ms")
                trigger_end = trigger.get("window_end_ms")
                if (
                    type(trigger_start) is not int
                    or type(trigger_end) is not int
                    or trigger_end <= trigger_start
                    or trigger_end - trigger_start > MAX_WINDOW_MS
                    or trigger_start < start
                    or trigger_end > end
                    or not trigger_start <= timestamp < trigger_end
                ):
                    continue
                result.append(trigger)
    return result


def _validate_replay_windows(value: Any, label: str) -> list[dict[str, Any]]:
    """Validate the source-bound plan used by a cached replay.

    ``last-plan.json`` is mutable metadata, even when the underlying receipt
    pixels and OCR cache have been hash checked by the replay manifest.  Keep
    the replay contract closed over timestamps, owner identity and physical
    geometry.  In particular, labels, names and amounts are not accepted as
    plan inputs, so a hand-edited plan cannot turn an arbitrary readable line
    into a promoted effect.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Occluded-receipt recovery {label} must be an array.")
    window_keys = {"start_ms", "end_ms", "reason", "triggers", "fps"}
    trigger_keys = {
        "source_timestamp_ms", "evidence", "line_box", "source_line_text_sha256",
        "kind", "owner_ref",
        "owner_start_ms", "owner_end_ms", "window_start_ms", "window_end_ms",
        "physical_overlay_count", "owner_context_title",
    }
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, int, tuple[tuple[Any, ...], ...]]] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"Occluded-receipt recovery {label}[{index}] must be an object.")
        unknown = set(raw) - window_keys
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] has unsupported fields: {names}."
            )
        start, end = raw.get("start_ms"), raw.get("end_ms")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] has invalid bounds."
            )
        if end - start > MAX_WINDOW_MS:
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] exceeds the five-second bound."
            )
        triggers = raw.get("triggers")
        if not isinstance(triggers, list) or not triggers:
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] must have triggers."
            )
        normalized_triggers: list[dict[str, Any]] = []
        identities: list[tuple[Any, ...]] = []
        for trigger_index, trigger in enumerate(triggers):
            if not isinstance(trigger, dict):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}].triggers[{trigger_index}] is invalid."
                )
            unknown = set(trigger) - trigger_keys
            if unknown:
                names = ", ".join(sorted(map(str, unknown)))
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}].triggers[{trigger_index}] "
                    f"has unsupported fields: {names}."
                )
            timestamp = trigger.get("source_timestamp_ms")
            if type(timestamp) is not int or timestamp < 0 or not start <= timestamp < end:
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger timestamp is outside its window."
                )
            evidence = trigger.get("evidence")
            if not _safe_relative_name(evidence):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger evidence is invalid."
                )
            if trigger.get("kind") != "occluded_receipt_line":
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger kind is invalid."
                )
            line_box = trigger.get("line_box")
            if not _valid_box(line_box):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger geometry is invalid."
                )
            source_text_sha256 = trigger.get("source_line_text_sha256")
            if not _valid_digest(source_text_sha256):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger source text binding is invalid."
                )
            owner_ref = trigger.get("owner_ref")
            if owner_ref is not None and (not isinstance(owner_ref, str) or not owner_ref.strip()):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger owner is invalid."
                )
            owner_start, owner_end = trigger.get("owner_start_ms"), trigger.get("owner_end_ms")
            if owner_ref is None:
                if owner_start is not None or owner_end is not None:
                    raise ValueError(
                        f"Occluded-receipt recovery {label}[{index}] unowned trigger has owner bounds."
                    )
            elif (
                type(owner_start) is not int or type(owner_end) is not int
                or owner_start < 0 or owner_end < owner_start
                or not owner_start <= timestamp <= owner_end
            ):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger owner bounds are invalid."
                )
            trigger_start = trigger.get("window_start_ms")
            trigger_end = trigger.get("window_end_ms")
            if (
                type(trigger_start) is not int or type(trigger_end) is not int
                or trigger_start < 0 or trigger_end <= trigger_start
                or not trigger_start <= timestamp < trigger_end
            ):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger window is invalid."
                )
            if trigger_end - trigger_start > MAX_WINDOW_MS:
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger window exceeds the five-second bound."
                )
            if trigger_start < start or trigger_end > end:
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger window escapes its parent window."
                )
            overlay_count = trigger.get("physical_overlay_count")
            if type(overlay_count) is not int or overlay_count < 1:
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger overlay count is invalid."
                )
            context = trigger.get("owner_context_title")
            if context is not None and (not isinstance(context, str) or not context.strip()):
                raise ValueError(
                    f"Occluded-receipt recovery {label}[{index}] trigger context is invalid."
                )
            normalized = {
                "source_timestamp_ms": timestamp,
                "evidence": evidence,
                "line_box": list(line_box),
                "source_line_text_sha256": source_text_sha256,
                "kind": "occluded_receipt_line",
                "owner_ref": owner_ref.strip() if isinstance(owner_ref, str) else None,
                "owner_start_ms": owner_start,
                "owner_end_ms": owner_end,
                "window_start_ms": trigger_start,
                "window_end_ms": trigger_end,
                "physical_overlay_count": overlay_count,
            }
            if isinstance(context, str):
                normalized["owner_context_title"] = context.strip()
            normalized_triggers.append(normalized)
            identities.append((timestamp, evidence, tuple(line_box), owner_ref))
        reason = raw.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] reason is invalid."
            )
        fps = raw.get("fps")
        if fps is not None and (type(fps) is not int or not 4 <= fps <= 60):
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] fps is invalid."
            )
        identity = (start, end, tuple(sorted(identities, key=repr)))
        if identity in seen:
            raise ValueError(
                f"Occluded-receipt recovery {label}[{index}] duplicates another window."
            )
        # Capped source requests can overlap when their union exceeds the
        # five-second inspection limit. Each trigger retains its own bounds;
        # overlap alone does not create an additional effect observation.
        seen.add(identity)
        item: dict[str, Any] = {
            "start_ms": start,
            "end_ms": end,
            "triggers": normalized_triggers,
        }
        if isinstance(reason, str):
            item["reason"] = reason.strip()
        if fps is not None:
            item["fps"] = fps
        result.append(item)
    return result


def _validate_inspection_windows(
    value: Any,
    duration_ms: int,
    label: str = "inspection.windows",
) -> list[dict[str, Any]]:
    """Validate the actual windows represented by a receipt cache."""

    if not isinstance(value, list):
        raise OccludedReceiptRecoveryError(f"{label} must be an array.")
    allowed = {"start_ms", "end_ms", "fps", "reason"}
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise OccludedReceiptRecoveryError(f"{label}[{index}] must be an object.")
        unknown = set(raw) - allowed
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            raise OccludedReceiptRecoveryError(
                f"{label}[{index}] has unsupported fields: {names}."
            )
        start, end, fps = raw.get("start_ms"), raw.get("end_ms"), raw.get("fps")
        if (
            type(start) is not int
            or type(end) is not int
            or type(fps) is not int
            or start < 0
            or end <= start
            or end > duration_ms
            or end - start > MAX_WINDOW_MS
            or not 4 <= fps <= 60
        ):
            raise OccludedReceiptRecoveryError(f"{label}[{index}] has invalid bounds or FPS.")
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise OccludedReceiptRecoveryError(f"{label}[{index}] has no reason.")
        key = (start, end, fps)
        if key in seen:
            raise OccludedReceiptRecoveryError(f"{label}[{index}] duplicates a cache window.")
        # The producer deliberately keeps overlapping requests separate if
        # merging them would exceed MAX_WINDOW_MS. Source frame identities
        # and canonical receipt merging, not disjoint windows, prevent
        # duplicate effects. Exact duplicate cache windows remain invalid.
        seen.add(key)
        result.append(dict(start_ms=start, end_ms=end, fps=fps, reason=reason.strip()))
    return result


def _validate_plan_metadata(value: Any, source_sha256: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a recovery plan before reusing any of its processed rows."""

    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise OccludedReceiptRecoveryError("Receipt recovery plan schema is invalid.")
    if value.get("source_sha256") != source_sha256:
        raise OccludedReceiptRecoveryError("Receipt recovery plan belongs to another recording.")
    requested_fps = value.get("requested_fps")
    if type(requested_fps) is not int or not 4 <= requested_fps <= 60:
        raise OccludedReceiptRecoveryError("Receipt recovery plan requested_fps is invalid.")
    arrays: list[list[dict[str, Any]]] = []
    for label in ("requested_windows", "processed_windows", "pending_windows"):
        raw = value.get(label)
        if not isinstance(raw, list):
            raise OccludedReceiptRecoveryError(f"Receipt recovery plan {label} must be an array.")
        try:
            arrays.append(_validate_replay_windows(raw, label))
        except ValueError as exc:
            raise OccludedReceiptRecoveryError(str(exc)) from exc
    return arrays[0], arrays[1], arrays[2]


def _window_key(window: dict[str, Any], default_fps: int) -> tuple[int, int, int]:
    fps = window.get("fps", default_fps)
    return window["start_ms"], window["end_ms"], fps


def _with_fps(windows: Any, fps: int) -> list[dict[str, Any]]:
    """Attach the sampling rate to plan rows before persisting them."""

    if not isinstance(windows, list):
        return []
    return [dict(window, fps=fps) for window in windows]


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OccludedReceiptRecoveryError(f"{label} is unreadable JSON.") from exc


def _validate_model_provenance(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise OccludedReceiptRecoveryError(f"{label} must be a nonempty model hash map.")
    result: dict[str, str] = {}
    for name, digest in value.items():
        if not isinstance(name, str) or not name.strip() or not _valid_digest(digest):
            raise OccludedReceiptRecoveryError(f"{label} contains an invalid model hash.")
        result[name] = digest
    return result


def _registered_inspection_witnesses(
    readings: Any,
    directory: Path,
) -> dict[tuple[Path, int], dict[str, Any] | None]:
    """Index immutable manifest witnesses for cached OCR sidecars.

    Receipt inspection manifests written by earlier versions do not carry a
    digest for every ``*.v2.json`` sidecar.  They do, however, persist the
    neural OCR lines that were parsed for each reading.  The manifest itself
    is bound by the replay input hash, so those lines are a useful compatible
    witness for validating old caches.  Newer producers may additionally add
    ``sidecar_sha256``; that optional field is checked by the caller.

    Invalid reading paths are left to the existing reading validation below.
    This index is only a lookup aid and must never make malformed metadata
    acceptable.
    """

    result: dict[tuple[Path, int], dict[str, Any] | None] = {}
    if not isinstance(readings, list):
        return result
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        timestamp = reading.get("source_timestamp_ms")
        evidence = reading.get("evidence")
        if type(timestamp) is not int or not isinstance(evidence, str):
            continue
        try:
            evidence_path = _safe_child_path(
                directory, evidence, label="registered receipt reading evidence"
            )
        except OccludedReceiptRecoveryError:
            continue
        key = (evidence_path.resolve(), timestamp)
        # Duplicate manifest readings are rejected by the normal reading loop;
        # do not let this compatibility index choose one of them first.
        if key in result:
            result[key] = None
        else:
            result[key] = reading
    return result


def _validate_registered_sidecar_witness(
    raw: dict[str, Any],
    witness: dict[str, Any] | None,
    sidecar_path: Path,
    label: str,
) -> None:
    """Ensure mutable OCR JSON still matches the bound inspection reading.

    The source frame and gameplay PNG hashes prove the pixels, but they do not
    prove that a mutable OCR sidecar still contains the observation originally
    registered in the bound inspection manifest.  Compare the complete line
    objects when an old manifest provides ``ocr.neural``.  A producer-side
    sidecar digest, when present, strengthens this check to the full JSON file.
    """

    if witness is None:
        return
    declared_sidecar_sha = witness.get("sidecar_sha256")
    if declared_sidecar_sha is not None:
        if not _valid_digest(declared_sidecar_sha):
            raise OccludedReceiptRecoveryError(
                f"{label} has an invalid registered OCR sidecar digest."
            )
        try:
            actual_sidecar_sha = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise OccludedReceiptRecoveryError(
                f"{label} is unreadable."
            ) from exc
        if actual_sidecar_sha != declared_sidecar_sha:
            raise OccludedReceiptRecoveryError(
                f"{label} changed after its inspection manifest was bound."
            )

    registered_ocr = witness.get("ocr")
    if not isinstance(registered_ocr, dict) or "neural" not in registered_ocr:
        # A source/proof hash authenticates the pixels, but it cannot bind a
        # mutable OCR sidecar's interpretation.  Existing legacy manifests
        # remain compatible when they carry the producer-side full sidecar
        # digest; an entry with neither immutable witness is unusable for
        # replay and must fail closed before reparse can promote effects.
        if declared_sidecar_sha is None:
            raise OccludedReceiptRecoveryError(
                f"{label} has no immutable OCR witness."
            )
        return
    registered_lines = registered_ocr.get("neural")
    if not isinstance(registered_lines, list):
        raise OccludedReceiptRecoveryError(
            f"{label} has an invalid registered OCR witness."
        )
    if raw.get("lines") != registered_lines:
        raise OccludedReceiptRecoveryError(
            f"{label} lines differ from the bound inspection witness."
        )


def _validate_inspection_cache(
    inspection: Any,
    directory: Path,
    source_sha256: str,
    duration_ms: int,
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Validate source, sidecar, frame, and path identity of a cache."""

    directory = Path(directory).resolve()
    if not isinstance(inspection, dict):
        raise OccludedReceiptRecoveryError("Receipt recovery inspection is not an object.")
    if inspection.get("source_sha256") != source_sha256:
        raise OccludedReceiptRecoveryError("Receipt recovery observations belong to another recording.")
    windows = _validate_inspection_windows(inspection.get("windows"), duration_ms)
    readings = inspection.get("readings")
    if not isinstance(readings, list):
        raise OccludedReceiptRecoveryError("Receipt recovery inspection readings must be an array.")
    if readings and not windows:
        raise OccludedReceiptRecoveryError("Receipt readings have no containing cache window.")

    window_by_key = {
        (item["start_ms"], item["end_ms"], item["fps"]): item
        for item in windows
    }
    registered_witnesses = _registered_inspection_witnesses(
        readings, directory
    )
    frame_by_evidence: dict[Path, tuple[dict[str, Any], dict[str, Any]]] = {}
    models: dict[str, dict[str, str]] = {}
    for item in windows:
        start, end, fps = item["start_ms"], item["end_ms"], item["fps"]
        frame_dir = _safe_child_path(
            directory,
            Path("receipt-inspection") / f"{start}-{end}-{fps}",
            label="receipt window",
        )
        manifest_path = frame_dir / "frames.json"
        if not manifest_path.is_file():
            raise OccludedReceiptRecoveryError("Receipt window is missing its frame manifest.")
        frames = _load_json(manifest_path, "Receipt frame manifest")
        if not isinstance(frames, list) or not frames:
            raise OccludedReceiptRecoveryError("Receipt frame manifest must be a nonempty array.")
        seen_ids: set[str] = set()
        seen_timestamps: set[int] = set()
        for frame_index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise OccludedReceiptRecoveryError("Receipt frame manifest contains a malformed frame.")
            frame_id, frame_evidence, timestamp = (
                frame.get("id"), frame.get("evidence"), frame.get("source_timestamp_ms")
            )
            if (
                not isinstance(frame_id, str)
                or not frame_id.strip()
                or not _safe_relative_name(frame_id)
                or Path(frame_id).name != frame_id
                or frame_id in seen_ids
                or not isinstance(frame_evidence, str)
                or type(timestamp) is not int
                or not start <= timestamp < end
            ):
                raise OccludedReceiptRecoveryError(
                    f"Receipt frame manifest frame {frame_index} is invalid."
                )
            source_frame = _safe_child_path(frame_dir, frame_evidence, label="receipt source frame")
            if not source_frame.is_file():
                raise OccludedReceiptRecoveryError("Receipt source frame is missing.")
            proof_path = frame_dir / f"{frame_id}.png"
            sidecar_path = frame_dir / f"{frame_id}.v2.json"
            if not sidecar_path.is_file():
                sidecar_path = frame_dir / f"{frame_id}.json"
            if not proof_path.is_file() or not sidecar_path.is_file():
                raise OccludedReceiptRecoveryError("Receipt frame is missing its proof or OCR sidecar.")
            raw = _load_json(sidecar_path, "Receipt OCR sidecar")
            if not isinstance(raw, dict):
                raise OccludedReceiptRecoveryError("Receipt OCR sidecar must be an object.")
            if (
                raw.get("source_sha256") != source_sha256
                or raw.get("source_timestamp_ms") != timestamp
                or raw.get("evidence") != str(proof_path.relative_to(directory)).replace("\\", "/")
                or not _valid_digest(raw.get("source_frame_sha256"))
                or not _valid_digest(raw.get("gameplay_sha256"))
                or not isinstance(raw.get("engine_fingerprint"), str)
                or not raw.get("engine_fingerprint").strip()
            ):
                raise OccludedReceiptRecoveryError("Receipt OCR sidecar source identity is invalid.")
            model_hashes = _validate_model_provenance(raw.get("model_sha256"), "Receipt OCR model hashes")
            if hashlib.sha256(source_frame.read_bytes()).hexdigest() != raw["source_frame_sha256"]:
                raise OccludedReceiptRecoveryError("Receipt source frame changed.")
            try:
                from .frame_cache import rgb_sha256
                gameplay_hash = rgb_sha256(proof_path)
            except (OSError, ValueError) as exc:
                raise OccludedReceiptRecoveryError("Receipt gameplay proof is unreadable.") from exc
            if gameplay_hash != raw["gameplay_sha256"]:
                raise OccludedReceiptRecoveryError("Receipt gameplay proof changed.")
            normalized_proof = proof_path.resolve()
            witness = registered_witnesses.get((normalized_proof, timestamp))
            if witness is None and (
                normalized_proof, timestamp
            ) in registered_witnesses:
                raise OccludedReceiptRecoveryError(
                    "Receipt inspection manifest has duplicate witnesses for a physical frame."
                )
            _validate_registered_sidecar_witness(
                raw,
                witness,
                sidecar_path,
                "Receipt OCR sidecar",
            )
            frame_by_evidence[normalized_proof] = (frame, raw)
            seen_ids.add(frame_id)
            if timestamp in seen_timestamps:
                raise OccludedReceiptRecoveryError("Receipt frame manifest repeats a timestamp.")
            seen_timestamps.add(timestamp)
            fingerprint = raw["engine_fingerprint"]
            previous = models.get(fingerprint)
            if previous is not None and previous != model_hashes:
                raise OccludedReceiptRecoveryError("One receipt OCR fingerprint has different models.")
            models[fingerprint] = model_hashes

    seen_readings: set[Path] = set()
    for index, reading in enumerate(readings):
        if not isinstance(reading, dict):
            raise OccludedReceiptRecoveryError(f"Receipt reading {index} must be an object.")
        timestamp, evidence = reading.get("source_timestamp_ms"), reading.get("evidence")
        if type(timestamp) is not int or not 0 <= timestamp < duration_ms:
            raise OccludedReceiptRecoveryError(f"Receipt reading {index} has an invalid timestamp.")
        evidence_path = _safe_child_path(directory, evidence, label="receipt reading evidence")
        if evidence_path.suffix.lower() != ".png":
            raise OccludedReceiptRecoveryError("Receipt reading evidence must be a PNG proof.")
        if evidence_path in seen_readings:
            raise OccludedReceiptRecoveryError("Receipt cache repeats a physical proof frame.")
        seen_readings.add(evidence_path)
        frame_info = frame_by_evidence.get(evidence_path.resolve())
        if frame_info is None:
            raise OccludedReceiptRecoveryError("Receipt reading evidence is outside its frame manifest.")
        frame, raw = frame_info
        if frame.get("source_timestamp_ms") != timestamp or raw.get("source_timestamp_ms") != timestamp:
            raise OccludedReceiptRecoveryError("Receipt reading timestamp is not bound to its proof frame.")
        if not any(
            item["start_ms"] <= timestamp < item["end_ms"]
            and evidence_path.parent.resolve()
            == (directory / "receipt-inspection" / f"{item['start_ms']}-{item['end_ms']}-{item['fps']}" ).resolve()
            for item in windows
        ):
            raise OccludedReceiptRecoveryError("Receipt reading is outside its declared cache window.")

    return models, windows


def _box_exact(first: Any, second: Any) -> bool:
    """Compare source geometry without accepting a moved trigger."""

    return (
        _valid_box(first)
        and _valid_box(second)
        and tuple(first) == tuple(second)
    )


def _build_source_line_index(base: Any) -> dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None]:
    """Index source receipt lines once for a bounded replay pass."""

    index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None] = {}
    if not isinstance(base, list):
        return index
    for row in base:
        if not isinstance(row, dict):
            continue
        timestamp, evidence = row.get("source_timestamp_ms"), row.get("evidence")
        if type(timestamp) is not int or not isinstance(evidence, str):
            continue
        facts = row.get("facts")
        lines = facts.get("occluded_receipt_lines") if isinstance(facts, dict) else None
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict) or not _valid_box(line.get("box")):
                continue
            key = (timestamp, evidence, tuple(line["box"]))
            if key in index:
                index[key] = None
            else:
                index[key] = line
    return index


def _source_line_for_trigger(
    base: Any,
    trigger: dict[str, Any],
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Resolve a trigger back to exactly one immutable base receipt line.

    A trigger is mutable JSON metadata.  Its timestamp, evidence path, line
    geometry, overlay count, and source-observed text fingerprint therefore
    all have to agree with the original reading before a reread can select an
    effect.  This is what prevents a trigger from being moved onto a nearby
    energy line or reused for another recipient in the same event context.
    """

    if not isinstance(base, list) or not isinstance(trigger, dict):
        return None
    timestamp = trigger.get("source_timestamp_ms")
    evidence = trigger.get("evidence")
    line_box = trigger.get("line_box")
    text_sha256 = trigger.get("source_line_text_sha256")
    if (
        type(timestamp) is not int
        or not _safe_relative_name(evidence)
        or not _valid_box(line_box)
        or not _valid_digest(text_sha256)
    ):
        return None
    if source_index is not None:
        line = source_index.get((timestamp, evidence, tuple(line_box)))
        matches = [line] if line is not None else []
    else:
        matches = []
    for row in ([] if source_index is not None else base):
        if not isinstance(row, dict):
            continue
        if row.get("source_timestamp_ms") != timestamp or row.get("evidence") != evidence:
            continue
        facts = row.get("facts")
        lines = facts.get("occluded_receipt_lines") if isinstance(facts, dict) else None
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict) or not _box_exact(line.get("box"), line_box):
                continue
            if not _physical_overlay(line):
                continue
            overlays = line.get("overlay_boxes")
            overlay_count = sum(1 for box in overlays if _valid_box(box))
            if trigger.get("physical_overlay_count") != overlay_count:
                return None
            if trigger.get("source_line_text_sha256") != _text_digest(line.get("text")):
                return None
            matches.append(line)
    if len(matches) != 1:
        return None
    line = matches[0]
    if not _physical_overlay(line):
        return None
    overlays = line.get("overlay_boxes")
    overlay_count = sum(1 for box in overlays if _valid_box(box))
    if trigger.get("physical_overlay_count") != overlay_count:
        return None
    if trigger.get("source_line_text_sha256") != _text_digest(line.get("text")):
        return None
    return line


def _source_bound_triggers(
    base: Any,
    triggers: Any,
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Return only triggers whose immutable source line binding is intact."""

    if not isinstance(triggers, list):
        return []
    result = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        if _source_line_for_trigger(base, trigger, source_index) is not None:
            result.append(trigger)
    return result


def _source_text_compatible(source_text: Any, reread_text: Any) -> bool:
    """Allow OCR typos while keeping a recipient/effect identity binding."""

    source = _normal_text(source_text).casefold()
    reread = _normal_text(reread_text).casefold()
    if not source or not reread:
        return False
    if source == reread:
        return True
    # The source text is observed evidence, not an expected label.  A bounded
    # edit-distance ratio handles the known partial glyph failures while
    # rejecting a different recipient or hint name at the same coordinates.
    return SequenceMatcher(None, source, reread, autojunk=False).ratio() >= 0.85


def _semantic_effect_match(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Compare effect semantics while allowing only bounded name OCR drift."""

    if any(observed.get(key) != expected.get(key)
           for key in ("kind", "field", "amount", "direction", "value")):
        return False
    expected_name = expected.get("name")
    observed_name = observed.get("name")
    if expected_name is None:
        return observed_name is None
    return _source_text_compatible(expected_name, observed_name)


def _line_effects(text: Any, confidence: int | float = 100) -> list[dict[str, Any]]:
    """Parse one observed line through the ordinary receipt grammar."""

    if not isinstance(text, str) or not _normal_text(text):
        return []
    try:
        return effects_from_lines([dict(text=text, confidence=confidence)])
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


_PERFORMANCE_RECEIPT_FIELDS = {
    "dance": "dance",
    "passion": "passion",
    "vocal": "vocal",
    "vocals": "vocal",
    "visual": "visual",
    "visuals": "visual",
    "composure": "composure",
}


def _performance_receipt_semantics(text: Any) -> dict[str, Any] | None:
    """Read the fixed performance receipt shape without repairing its value.

    The performance animation reader can establish a positive direction from
    a signed ``+N`` card when OCR drops the word ``up`` from the receipt.  This
    small grammar only identifies the observed field, direction token, and
    amount; the animation/card agreement is checked by the caller.  In
    particular, it never fills a missing amount or chooses a field from a
    neighboring line.
    """

    normalized = _normal_text(text)
    if not normalized:
        return None
    complete = re.fullmatch(
        r"(Dance|Passion|Vocals?|Visuals?|Composure)\s+went\s+"
        r"(up|down)\s+by\s+(\d{1,3})[.!]?",
        normalized,
        re.IGNORECASE,
    )
    if complete is not None:
        label = complete.group(1).casefold()
        return {
            "field": _PERFORMANCE_RECEIPT_FIELDS[label],
            "direction": complete.group(2).casefold(),
            "amount": int(complete.group(3)),
        }
    missing_up = re.fullmatch(
        r"(Dance|Passion|Vocals?|Visuals?|Composure)\s+went\s*"
        r"by\s+(\d{1,3})[.!]?",
        normalized,
        re.IGNORECASE,
    )
    if missing_up is None:
        return None
    label = missing_up.group(1).casefold()
    return {
        "field": _PERFORMANCE_RECEIPT_FIELDS[label],
        "direction": None,
        "amount": int(missing_up.group(2)),
    }


def _performance_semantics_agree(
    expected: dict[str, Any], observed: dict[str, Any]
) -> bool:
    """Compare two performance receipt readings without repairing values."""

    if (
        expected.get("field") != observed.get("field")
        or expected.get("amount") != observed.get("amount")
    ):
        return False
    expected_direction = expected.get("direction")
    observed_direction = observed.get("direction")
    return not (
        expected_direction is not None
        and observed_direction is not None
        and expected_direction != observed_direction
    )


def _receipt_effect_subject_matches(
    source_effect: dict[str, Any], clear_effect: dict[str, Any]
) -> bool:
    """Identify two parsed effects from the same physical receipt line.

    The contradiction guard is deliberately separate from the fuzzy source
    text matcher.  It only compares fields that the ordinary receipt grammar
    has parsed, and it requires the parsed kind/field plus an exact normalized
    named subject when a name is present.  That keeps a damaged source name
    from becoming an automatic conflict while still binding numeric and
    direction checks for nameless receipts such as Energy and performance
    cards.
    """

    if source_effect.get("kind") != clear_effect.get("kind"):
        return False
    if source_effect.get("field") != clear_effect.get("field"):
        return False
    source_name = source_effect.get("name")
    clear_name = clear_effect.get("name")
    if source_name is None or clear_name is None:
        return source_name is None and clear_name is None
    return _normal_text(source_name).casefold() == _normal_text(clear_name).casefold()


def _receipt_line_conflicts_with_clear_reread(
    source_line: dict[str, Any],
    clear_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return semantic disagreements at the source line's physical slot.

    ``_restored_occluded_receipt_row`` replaces same-box OCR before parsing
    the source line.  Without this check, a clear reread such as ``Energy
    went up by 20`` can be silently erased and replaced with a source line
    saying ``Energy went down by 20``.  Every clear line is compared before
    replacement; any source/clear amount or direction disagreement therefore
    leaves the occurrence unresolved.  Multiple clear OCR variants count as
    one physical slot but every contradictory variant still vetoes promotion.

    An unparseable source line does not create a conflict: its source-bound
    recovery may still be established by a bounded clear reread or the
    existing signed-card path.  The helper never invents an amount or treats
    a different physical line as evidence for this slot.
    """

    if not isinstance(source_line, dict) or not _valid_box(source_line.get("box")):
        return []
    source_effects = _line_effects(source_line.get("text"))
    source_performance = _performance_receipt_semantics(source_line.get("text"))
    if not source_effects and source_performance is None:
        return []

    conflicts: list[dict[str, Any]] = []
    for clear_line in clear_lines:
        if not isinstance(clear_line, dict):
            continue
        if not (_same_receipt_line(source_line.get("box"), clear_line.get("box"))
                or _same_receipt_column(source_line.get("box"), clear_line.get("box"))):
            continue
        clear_effects = _line_effects(
            clear_line.get("text"), clear_line.get("confidence")
        )
        clear_performance = _performance_receipt_semantics(clear_line.get("text"))

        for source_effect in source_effects:
            for clear_effect in clear_effects:
                if not _receipt_effect_subject_matches(source_effect, clear_effect):
                    continue
                if _semantic_effect_match(source_effect, clear_effect):
                    continue
                conflicts.append(dict(
                    basis="same_physical_receipt_line_semantic_conflict",
                    source_text=_normal_text(source_line.get("text")),
                    clear_text=_normal_text(clear_line.get("text")),
                    source_box=list(source_line["box"]),
                    clear_box=list(clear_line["box"]),
                    source_effect={
                        key: copy.deepcopy(source_effect.get(key))
                        for key in ("kind", "field", "name", "amount", "direction", "value")
                        if key in source_effect
                    },
                    clear_effect={
                        key: copy.deepcopy(clear_effect.get(key))
                        for key in ("kind", "field", "name", "amount", "direction", "value")
                        if key in clear_effect
                    },
                ))

        if source_performance is None or clear_performance is None:
            continue
        if (
            source_performance.get("field") != clear_performance.get("field")
            or source_performance.get("amount") != clear_performance.get("amount")
            or (
                source_performance.get("direction") is not None
                and clear_performance.get("direction") is not None
                and source_performance.get("direction")
                != clear_performance.get("direction")
            )
        ):
            conflicts.append(dict(
                basis="same_physical_receipt_line_performance_conflict",
                source_text=_normal_text(source_line.get("text")),
                clear_text=_normal_text(clear_line.get("text")),
                source_box=list(source_line["box"]),
                clear_box=list(clear_line["box"]),
                source_effect=copy.deepcopy(source_performance),
                clear_effect=copy.deepcopy(clear_performance),
            ))
    return conflicts


def _restored_occluded_receipt_row(
    row: dict[str, Any],
    source_line: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return a private row view with one source-proven line made readable.

    ``receipt_occlusion`` intentionally marks a blocked OCR line with zero
    confidence in the normal row.  Recovery may inspect that line only after
    the source facts have supplied a valid intersecting overlay and a bounded
    confidence.  The caller receives a deep-copied row; the original cached
    observation is never unblocked or rewritten.
    """

    ocr = row.get("ocr")
    neural = ocr.get("neural") if isinstance(ocr, dict) else None
    if not isinstance(neural, list):
        return None
    confidence = _line_confidence(source_line)
    if not _finite_number(confidence) or confidence < 90:
        return None
    clear_lines = _fresh_lines(row)
    if _receipt_line_conflicts_with_clear_reread(source_line, clear_lines):
        # Preserve the disagreement in the caller's original reread row.  A
        # source line is not allowed to win merely because this private view
        # would otherwise replace the clear candidate at the same box.
        return None
    restored = copy.deepcopy(source_line)
    restored["confidence"] = confidence
    restored["overlay_occluded"] = False
    restored["animated_overlay_occluded"] = False
    # A normal reread may already contain a clear variant at this geometry.
    # Replace that physical slot in the private view so one receipt cannot
    # create two candidates merely because two OCR channels reported it.
    restored_neural = [
        copy.deepcopy(line) for line in neural
        if not isinstance(line, dict)
        or not _same_receipt_line(line.get("box"), restored.get("box"))
    ]
    restored_neural.append(restored)
    updated = copy.deepcopy(row)
    updated_ocr = copy.deepcopy(ocr)
    updated_ocr["neural"] = restored_neural
    updated["ocr"] = updated_ocr
    return updated, restored


def _attach_source_context(
    row: dict[str, Any],
    triggers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Give a private reread view the one context proven by its owner.

    A dense inspection frame may not OCR the event heading at all.  The
    source plan still carries the event context that was bound to the blocked
    line.  Use that context only for the private owner check; the canonical
    row and its raw OCR remain unchanged.  Conflicting contexts are left
    untouched so the normal owner validator abstains.
    """

    if any(
        isinstance(value, str) and value.strip()
        for value in (row.get("context_title"), row.get("context_title_candidate"))
    ):
        return row
    contexts = {
        trigger.get("owner_context_title").strip()
        for trigger in triggers
        if isinstance(trigger, dict)
        and isinstance(trigger.get("owner_context_title"), str)
        and trigger.get("owner_context_title").strip()
    }
    if len(contexts) != 1:
        return row
    result = copy.deepcopy(row)
    result["context_title"] = next(iter(contexts))
    return result


def _source_line_triggers(
    base: Any,
    triggers: list[dict[str, Any]],
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None],
    source_line: dict[str, Any],
    row_timestamp: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Bind a recovered line to source triggers using geometry and text."""

    selected: list[dict[str, Any]] = []
    source_texts: set[str] = set()
    for trigger in triggers:
        if not isinstance(trigger, dict) or not _same_receipt_line(
            trigger.get("line_box"), source_line.get("box")
        ):
            continue
        original = _source_line_for_trigger(base, trigger, source_index)
        if original is None or not _source_text_compatible(
            original.get("text"), source_line.get("text")
        ):
            continue
        selected.append(trigger)
        source_texts.add(_normal_text(original.get("text")))
    if len(selected) > 1:
        distances = [
            abs(int(trigger["source_timestamp_ms"]) - row_timestamp)
            for trigger in selected
            if type(trigger.get("source_timestamp_ms")) is int
        ]
        if distances:
            nearest = min(distances)
            selected = [
                trigger for trigger in selected
                if type(trigger.get("source_timestamp_ms")) is int
                and abs(trigger["source_timestamp_ms"] - row_timestamp) == nearest
            ]
            source_texts = {
                _normal_text(
                    original.get("text")
                )
                for trigger in selected
                if (original := _source_line_for_trigger(base, trigger, source_index))
                is not None
            }
    return selected, source_texts


def _source_bound_occluded_effects(
    base: Any,
    row: dict[str, Any],
    triggers: list[dict[str, Any]],
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Recover ordinary and signed-animation effects from one blocked row.

    The normal parser has already removed occluded OCR lines.  This helper
    reintroduces one validated source line at a time in a private row view and
    runs the existing grammar and performance-card reader against it.  A
    candidate is eligible only when its line geometry, observed field, and
    amount agree with the source line and a source-bound trigger.  It does not
    use an expected label/value or infer from another frame.
    """

    if not isinstance(row, dict) or row.get("screen") != "event_outcome":
        return []
    timestamp = row.get("source_timestamp_ms")
    if type(timestamp) is not int:
        return []
    facts = row.get("facts")
    lines = facts.get("occluded_receipt_lines") if isinstance(facts, dict) else None
    if not isinstance(lines, list):
        return []
    source_lines = [
        line for line in lines
        if isinstance(line, dict)
        and _valid_box(line.get("box"))
        and _physical_overlay(line)
    ]
    observations: dict[tuple[Any, ...], dict[str, Any]] = {}
    try:
        from .animated_performance import candidates as performance_candidates
    except (ImportError, AttributeError):
        performance_candidates = None

    for source_line in source_lines:
        bound_triggers, source_texts = _source_line_triggers(
            base, triggers, source_index, source_line, timestamp
        )
        if not bound_triggers or len(source_texts) != 1:
            continue
        restored_pair = _restored_occluded_receipt_row(row, source_line)
        if restored_pair is None:
            continue
        restored_row, restored_line = restored_pair
        # Context OCR can be absent on the clear reread even though the
        # source-bound trigger has one validated owner context.  Attach it
        # only to this private owner-check view; no context is inferred into
        # the promoted canonical row.
        restored_row = _attach_source_context(restored_row, bound_triggers)
        trigger_boxes = [
            trigger.get("line_box") for trigger in bound_triggers
            if _valid_box(trigger.get("line_box"))
        ]
        if not trigger_boxes:
            continue

        # First use the ordinary receipt grammar. This covers effects such as
        # ``Energy recovered by N`` whose only failure was the occlusion gate.
        # Two general guards apply before any parse is trusted: the restored
        # line must be a complete receipt sentence (a wrapped prefix or a
        # cursor-clipped tail parses as a different, shorter effect), and its
        # parsed value must not contradict a clear base reading of the same
        # physical slot (the blocked source text is damaged by definition and
        # cannot corroborate a reread that merely repeats the damage).
        if not _complete_receipt_sentence(restored_line.get("text")):
            grammar_effects: list[dict[str, Any]] = []
        elif _clear_base_conflicts(base, restored_line, bound_triggers):
            grammar_effects = []
        else:
            grammar_effects = _line_effects(
                restored_line.get("text"), restored_line.get("confidence")
            )
        for effect in grammar_effects:
            if not _source_semantically_matches(effect, source_texts):
                continue
            candidates = _candidate_lines(
                restored_row, effect, trigger_boxes, source_texts
            )
            if len(candidates) != 1:
                continue
            proof = dict(
                basis="source_bound_occluded_receipt_line",
                source_timestamp_ms=timestamp,
                evidence=restored_row.get("evidence"),
                line_box=list(restored_line["box"]),
                source_line_text_sha256=_text_digest(restored_line.get("text")),
                confidence=float(restored_line["confidence"]),
            )
            # The annotation stage records whether the obstruction crossed the
            # recipient name itself.  It is carried as observed metadata so
            # cross-frame selection can prefer a reading whose name glyphs
            # were not under the cursor; it is never used to alter the text.
            if isinstance(source_line.get("recipient_name_occluded"), bool):
                proof["recipient_name_occluded"] = source_line["recipient_name_occluded"]
            observed = copy.deepcopy(effect)
            observed["source_bound_receipt_proof"] = proof
            key = (_slot_key(observed), tuple(restored_line["box"]))
            current = observations.get(key)
            if current is None or float(observed.get("confidence", 0)) > float(
                current["effect"].get("confidence", 0)
            ):
                observations[key] = dict(
                    effect=observed,
                    line=list(candidates[0]["box"]),
                    row=restored_row,
                    triggers=list(bound_triggers),
                    source_texts=set(source_texts),
                )

        if performance_candidates is None:
            continue
        try:
            animation = performance_candidates(
                restored_row.get("ocr", {}).get("neural", []),
                restored_row.get("screen"),
            )
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            animation = []
        source_semantics = _performance_receipt_semantics(source_line.get("text"))
        if source_semantics is None:
            continue
        # The fresh line is only a reread candidate.  When the immutable
        # trigger line is itself parseable, its field and amount must agree
        # with the candidate before the signed card can repair an omitted
        # direction token.  Fuzzy line similarity alone must not authorize a
        # different numeric gain.
        trigger_semantics = [
            _performance_receipt_semantics(text)
            for text in source_texts
        ]
        if any(
            semantic is not None
            and not _performance_semantics_agree(semantic, source_semantics)
            for semantic in trigger_semantics
        ):
            continue
        for candidate in animation:
            if not isinstance(candidate, dict):
                continue
            receipt_text = candidate.get("receipt_text")
            candidate_semantics = _performance_receipt_semantics(receipt_text)
            if candidate_semantics is None:
                continue
            if not _source_text_compatible(source_line.get("text"), receipt_text):
                continue
            if (
                candidate.get("kind") != "performance_change"
                or candidate.get("field") != source_semantics["field"]
                or candidate.get("amount") != source_semantics["amount"]
                or candidate_semantics["field"] != source_semantics["field"]
                or candidate_semantics["amount"] != source_semantics["amount"]
                or source_semantics.get("direction") == "down"
                or candidate_semantics.get("direction") == "down"
            ):
                continue
            source_direction = source_semantics.get("direction")
            candidate_direction = candidate_semantics.get("direction")
            if (
                source_direction is not None
                and candidate_direction is not None
                and source_direction != candidate_direction
            ):
                continue
            if not _finite_number(candidate.get("confidence")):
                continue
            observed = dict(
                kind="performance_change",
                field=candidate["field"],
                amount=int(candidate["amount"]),
                raw_text=receipt_text,
                confidence=min(
                    float(candidate["confidence"]),
                    float(restored_line["confidence"]),
                ),
                source_bound_receipt_proof=dict(
                    basis=(
                        "source_bound_same_frame_signed_gain_and_receipt"
                        if candidate.get("receipt_normalization")
                        else "source_bound_same_frame_performance_card_and_receipt"
                    ),
                    source_timestamp_ms=timestamp,
                    evidence=restored_row.get("evidence"),
                    line_box=list(restored_line["box"]),
                    source_line_text_sha256=_text_digest(source_line.get("text")),
                    receipt_text=receipt_text,
                    receipt_normalization=candidate.get("receipt_normalization"),
                    direction_basis=candidate.get("direction_basis"),
                    gain_box=copy.deepcopy(candidate.get("gain_box")),
                    label_box=copy.deepcopy(candidate.get("label_box")),
                    confidence=float(candidate["confidence"]),
                ),
            )
            key = (_slot_key(observed), tuple(restored_line["box"]))
            current = observations.get(key)
            if current is None or float(observed["confidence"]) > float(
                current["effect"].get("confidence", 0)
            ):
                observations[key] = dict(
                    effect=observed,
                    line=list(restored_line["box"]),
                    row=restored_row,
                    triggers=list(bound_triggers),
                    source_texts=set(source_texts),
                )
    return list(observations.values())


def _exact_effect_match(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Match a clear line's parsed semantics without fuzzy name repair."""

    for key in ("kind", "field", "amount", "direction", "value"):
        if expected.get(key) != observed.get(key):
            return False
    expected_name = expected.get("name")
    observed_name = observed.get("name")
    if expected_name is None or observed_name is None:
        return expected_name is None and observed_name is None
    return _normal_text(expected_name).casefold() == _normal_text(observed_name).casefold()


def _exact_effect_line(effect: dict[str, Any], line: dict[str, Any]) -> bool:
    """Require one complete, unobstructed OCR line for an effect."""

    if not isinstance(line, dict) or line.get("overlay_occluded") is True:
        return False
    confidence = line.get("confidence")
    if not _finite_number(confidence) or float(confidence) < 95:
        return False
    parsed = _line_effects(line.get("text"), confidence)
    return any(_exact_effect_match(effect, candidate) for candidate in parsed)


def _source_semantically_matches(effect: dict[str, Any], source_texts: set[str]) -> bool:
    """Reject a parsed source trigger that disagrees on value or direction.

    Damaged source text may be unparseable, in which case geometry and bounded
    text similarity remain the available identity proof.  When the source line
    itself is parseable, however, a similar sentence with another amount or
    up/down direction must not authorize a different clear effect.
    """

    parsed = [candidate for text in source_texts for candidate in _line_effects(text)]
    return not parsed or any(_semantic_effect_match(effect, candidate) for candidate in parsed)


def _owner_key(trigger: dict[str, Any]) -> tuple[Any, ...] | None:
    """Return an owner identity stable across row-list reassembly.

    Sequential ``outcome-*`` identifiers are convenient display references,
    but inserting a validated supplement can renumber them.  The source
    interval and context are the stable ownership boundary; the trigger's
    source timestamp/evidence binds that interval to a particular receipt.
    """

    start, end = trigger.get("owner_start_ms"), trigger.get("owner_end_ms")
    context = trigger.get("owner_context_title")
    if type(start) is int and type(end) is int and isinstance(context, str) and context:
        return ("bounds", start, end, context)
    owner_ref = trigger.get("owner_ref")
    if isinstance(owner_ref, str) and owner_ref:
        return ("ref", owner_ref)
    if isinstance(context, str) and context:
        return ("context", context)
    return None


def _candidate_lines(
    row: dict[str, Any],
    effect: dict[str, Any],
    trigger_boxes: list[Any],
    source_texts: set[str] | None = None,
) -> list[dict[str, Any]]:
    def semantic_match(line: dict[str, Any]) -> bool:
        """Bind a clear OCR line to the parsed effect without fuzzy values.

        Source trigger text is intentionally tolerant of a damaged glyph: it
        is an observed identity anchor and may be the only surviving view of
        an occluded line.  A clear reread is different.  Its parsed amount,
        direction, field/kind, and named recipient must agree with the effect
        that would be promoted.  Otherwise whole-sentence edit distance can
        accept a changed digit or an up/down reversal merely because the rest
        of the sentence is similar.
        """
        parsed = _line_effects(line.get("text"), line.get("confidence"))
        for candidate in parsed:
            if _semantic_effect_match(effect, candidate):
                return True
        return False

    effect_texts = tuple(
        text for text in (
            effect.get("raw_text"),
            effect.get("normalized_text"),
            effect.get("original_text"),
        )
        if _normal_text(text)
    )
    if not effect_texts:
        return []
    candidates = []
    for line in _fresh_lines(row):
        line_text = _normal_text(line.get("text"))
        # The parser can repair a single OCR glyph or a fixed receipt word
        # before it stores ``raw_text``.  Requiring literal equality here
        # discarded a source-bound clear reread even though the line and its
        # parsed effect were the same physical receipt.  The source trigger
        # and physical geometry remain mandatory below; this comparison only
        # accepts a bounded OCR spelling variant.
        if not any(_source_text_compatible(text, line_text) for text in effect_texts):
            continue
        if not semantic_match(line):
            continue
        if source_texts is not None and not any(
            _source_text_compatible(source_text, line_text)
            for source_text in source_texts
        ):
            continue
        if any(_same_receipt_line(box, line.get("box")) for box in trigger_boxes):
            candidates.append(line)
    # Neural channels can repeat the same physical line.  Collapse those
    # variants by geometry so they do not masquerade as independent evidence;
    # distinct receipt rows with the same text remain separate groups.
    groups: list[list[dict[str, Any]]] = []
    for line in candidates:
        group = next((group for group in groups
                      if _same_receipt_line(group[0].get("box"), line.get("box"))), None)
        if group is None:
            groups.append([line])
        else:
            group.append(line)
    if len(groups) != 1:
        return []
    return [max(groups[0], key=lambda line: float(line.get("confidence", 0)))]


def _candidate_source_triggers(
    base: Any,
    triggers: list[dict[str, Any]],
    candidate: dict[str, Any],
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None],
    row_timestamp: int | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Bind one clear OCR line to only its matching source trigger lines.

    A merged five-second reread can contain several blocked receipt lines.  A
    row-level nearest-timestamp choice is unsafe: the nearest trigger may be a
    different stat line in the same frame.  Resolve the candidate's physical
    line and source text first, then let the normal owner check validate the
    resulting trigger set.
    """

    selected: list[dict[str, Any]] = []
    source_texts: set[str] = set()
    candidate_box = candidate.get("box")
    candidate_text = candidate.get("text")
    for trigger in _source_bound_triggers(base, triggers, source_index):
        trigger_box = trigger.get("line_box")
        if not _same_receipt_line(trigger_box, candidate_box):
            continue
        source_line = _source_line_for_trigger(base, trigger, source_index)
        if source_line is None or not _source_text_compatible(
            source_line.get("text"), candidate_text
        ):
            continue
        selected.append(trigger)
        source_texts.add(_normal_text(source_line.get("text")))
    # A merged reread can contain repeated source annotations for the same
    # physical slot. Choose the nearest validated source frame for this clear
    # row; retaining every spelling would make one receipt look ambiguous even
    # though the geometry and owner are stable. Multiple equally-near source
    # lines remain subject to the normal owner/text checks below.
    if len(selected) > 1 and type(row_timestamp) is int:
        distances = [
            abs(int(trigger["source_timestamp_ms"]) - row_timestamp)
            for trigger in selected
            if type(trigger.get("source_timestamp_ms")) is int
        ]
        if distances:
            nearest = min(distances)
            selected = [
                trigger for trigger in selected
                if type(trigger.get("source_timestamp_ms")) is int
                and abs(trigger["source_timestamp_ms"] - row_timestamp) == nearest
            ]
            source_texts = {
                _normal_text(_source_line_for_trigger(base, trigger, source_index).get("text"))
                for trigger in selected
                if _source_line_for_trigger(base, trigger, source_index) is not None
            }
    return selected, source_texts


def _base_has_matching_source_effect(
    base: Any,
    effect: dict[str, Any],
    triggers: list[dict[str, Any]],
) -> bool:
    """Avoid re-promoting an effect already visible on the same receipt line.

    A source annotation can be made after the ordinary sampler already saw a
    clear version of that line.  The recovery window then contains several
    OCR spellings of one existing hint or stat, and treating each spelling as
    a new effect creates duplicate awards.  Keep the source-bound recovery for
    genuinely missing lines only: an existing effect must share semantic value,
    physical line geometry, context, and the trigger's bounded source scope.
    """

    if not isinstance(base, list):
        return False
    effect_texts = tuple(
        text for text in (
            effect.get("raw_text"),
            effect.get("normalized_text"),
            effect.get("original_text"),
        )
        if _normal_text(text)
    )
    if not effect_texts:
        return False
    for trigger in triggers:
        trigger_box = trigger.get("line_box")
        if not _valid_box(trigger_box):
            continue
        # The scope is the whole visible receipt panel, not only the owner or
        # inspection window recorded at planning time: a registered inspection
        # frame a few frames past the owner span still shows the same receipt,
        # and its clear reading of this line is the canonical effect.
        for row in _base_rows_for_trigger(base, trigger):
            for existing in row.get("effects", []):
                if not isinstance(existing, dict) or not _semantic_effect_match(effect, existing):
                    continue
                existing_texts = tuple(
                    text for text in (
                        existing.get("raw_text"),
                        existing.get("normalized_text"),
                        existing.get("original_text"),
                    )
                    if _normal_text(text)
                )
                lines = _fresh_lines(row)
                if not lines:
                    continue
                # ``_semantic_effect_match`` above already required a bounded
                # name variant, so a vertical panel shift (same column) is
                # accepted as the same physical line here.
                if any(
                    (_same_receipt_line(trigger_box, line.get("box"))
                     or _same_receipt_column(trigger_box, line.get("box")))
                    and any(
                        _source_text_compatible(text, line.get("text"))
                        for text in (*effect_texts, *existing_texts)
                    )
                    for line in lines
                ):
                    return True
    return False


def _row_owner_matches(
    row: dict[str, Any],
    triggers: list[dict[str, Any]],
    source_rows: Any = None,
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None] | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    # A merged bounded window can span several receipt rows.  Resolve its
    # ownership against the reread row's physical line first, then use the
    # nearest source trigger for that line.  Without this narrowing, a later
    # event in the same merged window would make every earlier clear row look
    # ambiguous even when its geometry is unique.
    if source_rows is not None:
        triggers = _source_bound_triggers(source_rows, triggers, source_index)
    row_lines = _fresh_lines(row)
    geometric = [
        trigger for trigger in triggers
        if _valid_box(trigger.get("line_box"))
        and any(_same_receipt_line(trigger.get("line_box"), line.get("box"))
                for line in row_lines)
    ]
    if geometric:
        distances = [
            abs(int(trigger["source_timestamp_ms"]) - int(row["source_timestamp_ms"]))
            for trigger in geometric
            if type(trigger.get("source_timestamp_ms")) is int
        ]
        if distances:
            nearest = min(distances)
            geometric = [
                trigger for trigger in geometric
                if type(trigger.get("source_timestamp_ms")) is int
                and abs(trigger["source_timestamp_ms"] - row["source_timestamp_ms"]) == nearest
            ]
        triggers = geometric
    # A selected line cannot inherit one known owner while another selected
    # anchor is ownerless or malformed.  Keeping only the known references
    # would make an ambiguous source interval appear uniquely attributable.
    # All-ownerless triggers remain eligible for the explicit source-context
    # fallback below; mixing the two provenance modes is never safe.
    owner_refs = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            return None, []
        owner_ref = trigger.get("owner_ref")
        if owner_ref is not None and (
            not isinstance(owner_ref, str) or not owner_ref.strip()
        ):
            return None, []
        owner_refs.append(owner_ref.strip() if isinstance(owner_ref, str) else None)
    known_owner_refs = {ref for ref in owner_refs if ref is not None}
    if len(known_owner_refs) > 1 or (
        known_owner_refs and any(ref is None for ref in owner_refs)
    ):
        return None, []
    owner_keys = {
        key for trigger in triggers
        if (key := _owner_key(trigger)) is not None
    }
    row_titles = {
        value for value in (row.get("context_title"), row.get("context_title_candidate"))
        if isinstance(value, str) and value
    }
    source_titles = set(row_titles)
    if isinstance(source_rows, list):
        source_titles = set()
        trigger_source_timestamp = next(
            (
                trigger.get("source_timestamp_ms")
                for trigger in triggers
                if type(trigger.get("source_timestamp_ms")) is int
            ),
            None,
        )
        trigger_evidence = next(
            (
                trigger.get("evidence")
                for trigger in triggers
                if isinstance(trigger.get("evidence"), str)
            ),
            None,
        )
        for source_row in source_rows:
            if not isinstance(source_row, dict):
                continue
            if (
                source_row.get("source_timestamp_ms") == trigger_source_timestamp
                and source_row.get("evidence")
                == trigger_evidence
            ):
                source_titles.update(
                    value for value in (
                        source_row.get("context_title"),
                        source_row.get("context_title_candidate"),
                    )
                    if isinstance(value, str) and value
                )
        # Some source rows omit the heading even though the owning event
        # context is bound in the validated trigger.  A missing source title
        # is therefore incomplete evidence, rather than a contradiction.  A
        # reread with no context of its own may use the unique trigger context
        # through the private recovery view; any present context must still
        # agree with the source rows or the owner check rejects it below.
        if source_titles and (not row_titles or not source_titles.intersection(row_titles)):
            return None, []
    if len(owner_keys) > 1:
        return None, []
    if not owner_keys:
        # No accepted outcome event owns this source row.  Permit the
        # explicitly ownerless source-context fallback only when every
        # trigger carries the same context title.  This keeps a clear reread
        # useful for a malformed event receipt without manufacturing an
        # event id or crossing unrelated receipt contexts.
        selected = [trigger for trigger in triggers if trigger.get("owner_ref") is None]
        titles = {
            trigger.get("owner_context_title") for trigger in selected
            if isinstance(trigger.get("owner_context_title"), str)
            and trigger.get("owner_context_title")
        }
        if len(titles) != 1 or not selected:
            return None, []
        if not row_titles or any(title not in titles for title in row_titles):
            return None, []
        if any(title not in row_titles for title in titles):
            return None, []
        return None, selected
    owner_key = next(iter(owner_keys))
    selected = [trigger for trigger in triggers if _owner_key(trigger) == owner_key]
    # The sequential reference is retained only as an audit hint.  The
    # stable interval/context key above decides ownership across reassembly.
    owner_ref = next(
        (
            trigger.get("owner_ref") for trigger in selected
            if isinstance(trigger.get("owner_ref"), str) and trigger.get("owner_ref")
        ),
        None,
    )
    titles = {
        trigger.get("owner_context_title") for trigger in selected
        if isinstance(trigger.get("owner_context_title"), str)
        and trigger.get("owner_context_title")
    }
    if titles and (any(title not in titles for title in row_titles)
                   or any(title not in row_titles for title in titles)):
        return None, []
    return owner_ref, selected


def _adjacent_owner(
    row: dict[str, Any],
    triggers: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Resolve the owner for a clear line neighboring an occluded trigger.

    The neighboring line has no blocked-line annotation of its own, so it
    inherits ownership only when every adjacent anchor has one stable owner
    identity.  An ownerless anchor, two owner references, or missing context
    is intentionally insufficient for identity recovery.
    """

    if not isinstance(row, dict) or not isinstance(triggers, list) or not triggers:
        return None, []
    # Every geometrically selected anchor participates in ownership.  Do not
    # drop malformed/ownerless anchors while collecting keys: an unknown
    # anchor can overlap this clear line just as easily as a known one, and
    # silently filtering it would let the known owner win by accident.
    if any(
        not isinstance(trigger, dict)
        or type(trigger.get("source_timestamp_ms")) is not int
        or trigger.get("source_timestamp_ms") < 0
        or not isinstance(trigger.get("evidence"), str)
        or not trigger.get("evidence").strip()
        or not _valid_box(trigger.get("line_box"))
        for trigger in triggers
    ):
        return None, []
    trigger_keys = [_owner_key(trigger) for trigger in triggers]
    if any(key is None or key[0] != "bounds" for key in trigger_keys):
        return None, []
    owner_keys = set(trigger_keys)
    if len(owner_keys) != 1:
        return None, []
    key = next(iter(owner_keys))
    selected = list(triggers)
    owner_refs = {
        trigger.get("owner_ref") for trigger in selected
        if isinstance(trigger.get("owner_ref"), str) and trigger.get("owner_ref")
    }
    contexts = {
        trigger.get("owner_context_title") for trigger in selected
        if isinstance(trigger.get("owner_context_title"), str)
        and trigger.get("owner_context_title").strip()
    }
    if len(owner_refs) != 1 or len(contexts) != 1:
        return None, []
    row_contexts = {
        value.strip() for value in (
            row.get("context_title"), row.get("context_title_candidate")
        ) if isinstance(value, str) and value.strip()
    }
    if not row_contexts or not row_contexts.intersection(contexts):
        return None, []
    if any(context not in row_contexts for context in contexts):
        return None, []
    return next(iter(owner_refs)), selected


def _base_has_exact_effect(
    base: Any,
    effect: dict[str, Any],
    triggers: list[dict[str, Any]],
) -> bool:
    """Avoid adding an adjacent reread for an already observed exact effect."""

    if not isinstance(base, list) or not isinstance(effect, dict):
        return False
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        owner_start, owner_end = trigger.get("owner_start_ms"), trigger.get("owner_end_ms")
        window_start, window_end = trigger.get("window_start_ms"), trigger.get("window_end_ms")
        context = trigger.get("owner_context_title")
        for row in base:
            if not isinstance(row, dict) or not _timestamp(row.get("source_timestamp_ms")):
                continue
            timestamp = row["source_timestamp_ms"]
            in_owner = (
                type(owner_start) is int and type(owner_end) is int
                and owner_start <= timestamp <= owner_end
            )
            in_window = (
                type(window_start) is int and type(window_end) is int
                and window_start <= timestamp < window_end
            )
            if not (in_owner or in_window):
                continue
            if isinstance(context, str) and context:
                row_contexts = {
                    value for value in (
                        row.get("context_title"), row.get("context_title_candidate")
                    ) if isinstance(value, str) and value
                }
                if row_contexts and context not in row_contexts:
                    continue
            if any(
                isinstance(existing, dict) and _exact_effect_match(effect, existing)
                for existing in row.get("effects", [])
            ):
                return True
    return False


def _identity_proof(
    effect: dict[str, Any],
    row: dict[str, Any],
    line: dict[str, Any],
    anchor: dict[str, Any],
    owner_ref: str,
    anchor_source_line: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record the source-bound identity of an unobstructed adjacent line.

    Keep the physical overlay boxes from the validated trigger line with the
    proof.  Some historical base readings have the overlay component boxes but
    no OCR alignment sidecar; dropping those boxes at this boundary makes the
    later identity resolver unable to distinguish a source-bound spelling
    correction from an ordinary competing name.  The boxes are copied from the
    source line and are never inferred from the clean text.
    """

    proof = dict(
        basis="source_bound_clean_adjacent_receipt_line",
        source_timestamp_ms=row.get("source_timestamp_ms"),
        evidence=row.get("evidence"),
        line_box=list(line.get("box", [])),
        source_line_text_sha256=_text_digest(line.get("text")),
        owner_ref=owner_ref,
        owner_start_ms=anchor.get("owner_start_ms"),
        owner_end_ms=anchor.get("owner_end_ms"),
        owner_context_title=anchor.get("owner_context_title"),
        anchor_source_timestamp_ms=anchor.get("source_timestamp_ms"),
        anchor_evidence=anchor.get("evidence"),
        anchor_line_box=list(anchor.get("line_box", [])),
        anchor_source_line_text_sha256=anchor.get("source_line_text_sha256"),
        anchor_window_start_ms=anchor.get("window_start_ms"),
        anchor_window_end_ms=anchor.get("window_end_ms"),
        confidence=float(line.get("confidence")),
    )
    for key in ("kind", "field", "amount", "direction", "value"):
        proof[key] = copy.deepcopy(effect.get(key))
    if isinstance(anchor_source_line, dict):
        overlay_boxes = []
        for key in ("overlay_boxes", "animated_overlay_boxes"):
            values = anchor_source_line.get(key)
            if not isinstance(values, list):
                continue
            for box in values:
                if not _valid_box(box):
                    continue
                normalized = list(box)
                if normalized not in overlay_boxes:
                    overlay_boxes.append(normalized)
        if overlay_boxes:
            proof["anchor_overlay_boxes"] = overlay_boxes
    # Bind the clean source row's provenance to the proof.  These fields are
    # optional for legacy anchors, but when present they must survive merge and
    # remain equal to the row that supplied the clean OCR.
    source_binding = {}
    for key in (
        "source_sha256", "source_frame_sha256", "source_frame_id",
        "engine_fingerprint", "model_sha256", "gameplay_sha256",
    ):
        value = row.get(key)
        if value is not None:
            source_binding[key] = copy.deepcopy(value)
    if source_binding:
        proof["source_binding"] = source_binding
    return proof


def _clear_slot_candidates(
    base: Any,
    row: dict[str, Any],
    source_triggers: list[dict[str, Any]],
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Return one frame's ordinary (unblocked) clear-line recoveries.

    This is the former inline block of ``scoped_observations`` unchanged in
    behaviour; it is a helper so the cross-frame slot pass can see which
    physical slots already have a clear reading before any cursor-read
    variant of the same slot is considered.
    """

    timestamp = row.get("source_timestamp_ms")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for effect in row.get("effects", []):
        if not isinstance(effect, dict):
            continue
        if ("confidence" in effect
                and (not _finite_number(effect.get("confidence"))
                     or float(effect["confidence"]) < 90)):
            continue
        # First match the effect to one clear physical OCR line using all
        # source-bound trigger boxes.  Then narrow the trigger set to that
        # candidate's own line.  This prevents an adjacent hint/stat line
        # in the same merged window from making an otherwise unique effect
        # look owner-ambiguous.
        candidates = _candidate_lines(
            row,
            effect,
            [trigger.get("line_box") for trigger in source_triggers],
        )
        if len(candidates) != 1:
            # Multiple complete lines with identical text in one bounded
            # window are not enough to assign the effect to this trigger.
            continue
        candidate = candidates[0]
        effect_triggers, source_texts = _candidate_source_triggers(
            base, source_triggers, candidate, source_index, timestamp
        )
        if not effect_triggers or len(source_texts) != 1:
            continue
        if not _source_semantically_matches(effect, source_texts):
            continue
        if _base_has_matching_source_effect(base, effect, effect_triggers):
            continue
        owner_ref, triggers = _row_owner_matches(
            row, effect_triggers, base, source_index
        )
        if owner_ref is None and not triggers:
            continue
        trigger_boxes = [trigger.get("line_box") for trigger in triggers
                         if _valid_box(trigger.get("line_box"))]
        if not trigger_boxes:
            continue
        candidates = _candidate_lines(row, effect, trigger_boxes, source_texts)
        if len(candidates) != 1:
            continue
        signature = _effect_key(effect)
        if signature in seen:
            continue
        seen.add(signature)
        owner_keys = {
            key for trigger in triggers
            if (key := _owner_key(trigger)) is not None
        }
        owner_key = next(iter(owner_keys), None) if len(owner_keys) == 1 else None
        records.append(dict(
            effect=effect,
            line=list(candidates[0]["box"]),
            triggers=list(triggers),
            owner=owner_ref,
            owner_key=owner_key,
            slot_key=_slot_key(effect),
        ))
    return records


def _occluded_slot_candidates(
    base: Any,
    row: dict[str, Any],
    source_triggers: list[dict[str, Any]],
    source_index: dict[tuple[int, str, tuple[Any, ...]], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Return one frame's owner-resolved blocked-line recoveries.

    Every check of the former inline block is preserved: a valid effect, a
    resolvable single owner, a valid line box, and no clear base reading of
    the same effect within the receipt panel.  Cross-frame selection of the
    strongest reading per slot happens in ``scoped_observations``.
    """

    candidates: list[dict[str, Any]] = []
    for observation in _source_bound_occluded_effects(
        base, row, source_triggers, source_index
    ):
        effect = observation.get("effect")
        if not isinstance(effect, dict):
            continue
        triggers = observation.get("triggers")
        if not isinstance(triggers, list) or not triggers:
            continue
        owner_ref, owner_triggers = _row_owner_matches(
            observation.get("row"), triggers, base, source_index
        )
        if owner_ref is None and not owner_triggers:
            continue
        if not _valid_box(observation.get("line")):
            continue
        if _base_has_matching_source_effect(base, effect, owner_triggers):
            continue
        if _base_has_clear_slot_reading(base, effect, observation.get("line"), owner_triggers):
            continue
        owner_keys = {
            key for trigger in owner_triggers
            if (key := _owner_key(trigger)) is not None
        }
        if len(owner_keys) > 1:
            continue
        owner_key = next(iter(owner_keys), None)
        if owner_key is None:
            owner_key = tuple(
                (
                    trigger.get("source_timestamp_ms"),
                    trigger.get("evidence"),
                    tuple(trigger.get("line_box", ())),
                )
                for trigger in owner_triggers
            )
        candidates.append(dict(
            effect=effect,
            line=list(observation["line"]),
            owner_ref=owner_ref,
            owner_triggers=list(owner_triggers),
            owner_key=owner_key,
            slot_key=_slot_key(effect),
        ))
    return candidates


def _select_slot_winner(
    group: dict[str, Any],
    base_timestamps: set[int] | frozenset[int] = frozenset(),
) -> None:
    """Choose one reading for a physical receipt slot across dense frames.

    Mirrors the inheritance identity rule: a literal name is kept when its
    spelling repeats at two distinct source timestamps, never merely because
    a single frame's OCR was confident.  When no spelling repeats (or several
    do), a reading whose recipient glyphs were not under the cursor is
    preferred.  Only then does confidence break the tie, and the effect then
    records every observed spelling so the unresolved identity is explicit
    for consumers instead of silently choosing one.
    """

    members = group.get("members") or []
    if not members:
        return

    def name_of(member: dict[str, Any]) -> str | None:
        name = member["candidate"]["effect"].get("name")
        return _normal_text(name).casefold() if isinstance(name, str) else None

    def confidence_of(member: dict[str, Any]) -> float:
        value = member["candidate"]["effect"].get("confidence", 0)
        return float(value) if _finite_number(value) else 0.0

    def name_occluded(member: dict[str, Any]) -> bool | None:
        proof = member["candidate"]["effect"].get("source_bound_receipt_proof")
        value = proof.get("recipient_name_occluded") if isinstance(proof, dict) else None
        return value if isinstance(value, bool) else None

    spellings: dict[str | None, set[Any]] = {}
    for member in members:
        spellings.setdefault(name_of(member), set()).add(member["timestamp"])
    repeated = [name for name, times in spellings.items() if len(times) >= 2]
    support = sorted(
        ((len(times), name) for name, times in spellings.items()),
        key=lambda item: -item[0],
    )
    basis = None
    if len(spellings) <= 1:
        pool = members
    elif len(repeated) == 1:
        pool = [member for member in members if name_of(member) == repeated[0]]
        basis = "repeated_spelling_across_source_timestamps"
    elif len(support) > 1 and support[0][0] > support[1][0] and support[0][0] >= 2:
        # Several spellings repeat.  The one seen at strictly more distinct
        # source timestamps has the strongest independent repetition; it is
        # still recorded as unresolved because a competing spelling repeated.
        pool = [member for member in members if name_of(member) == support[0][1]]
        basis = "plurality_of_source_timestamps_identity_unresolved"
    else:
        unoccluded = [member for member in members if name_occluded(member) is False]
        if unoccluded and len({name_of(member) for member in unoccluded}) == 1:
            pool = unoccluded
            basis = "recipient_glyphs_not_under_cursor"
        else:
            pool = members
            basis = "strongest_reading_identity_unresolved"
    # Within the chosen spelling, a dense frame that is not itself a base
    # capture frame is an independent source observation and carries the
    # proof; a reread of a frame already in the base adds no independent
    # evidence and would only rewrite that base row.  Confidence orders the
    # remaining candidates.
    independent = [
        member for member in pool
        if member["timestamp"] not in base_timestamps
    ]
    if independent:
        pool = independent
    winner = max(pool, key=confidence_of)
    if len(spellings) > 1:
        effect = winner["candidate"]["effect"]
        winner_name = effect.get("name")
        candidates = [winner_name] if isinstance(winner_name, str) else []
        for member in sorted(members, key=lambda item: -confidence_of(item)):
            name = member["candidate"]["effect"].get("name")
            if isinstance(name, str) and name not in candidates:
                candidates.append(name)
        effect["observed_name_candidates"] = candidates
        proof = effect.get("source_bound_receipt_proof")
        if isinstance(proof, dict):
            proof["name_identity_basis"] = basis
            proof["distinct_spelling_count"] = len(spellings)
            proof["distinct_source_timestamps"] = len({
                member["timestamp"] for member in members
            })
    group["winner_row_index"] = winner["row_index"]
    group["winner_candidate"] = winner["candidate"]


def scoped_observations(base: Any, fresh: Any, windows: Any) -> list[dict[str, Any]]:
    """Select clear effects tied to one blocked receipt and one owner.

    A clear frame may fall outside the original event's last sampled frame;
    ownership comes from the bounded trigger window and is rejected when
    multiple event owners overlap.  Effects from neighboring receipt rows do
    not match the blocked line's geometry, even if their text is complete.
    """

    if not isinstance(fresh, list):
        return []
    originals: dict[int, list[dict[str, Any]]] = {}
    if isinstance(base, list):
        for row in base:
            if isinstance(row, dict) and _timestamp(row.get("source_timestamp_ms")):
                originals.setdefault(row["source_timestamp_ms"], []).append(row)
    source_index = _build_source_line_index(base)

    result: list[dict[str, Any]] = []
    # Blocked-line rereads are evaluated across all dense frames first so one
    # physical slot keeps exactly one strongest semantic observation.  Frame
    # order must not decide which OCR spelling of a damaged recipient or hint
    # name survives; the highest-confidence reading of that slot does.  Each
    # winner is later accepted only from its own frame.
    row_triggers: dict[int, list[dict[str, Any]]] = {}
    row_clear_records: dict[int, list[dict[str, Any]]] = {}
    row_candidates: dict[int, list[dict[str, Any]]] = {}
    clear_slots: list[dict[str, Any]] = []
    slot_winners: list[dict[str, Any]] = []
    for index, row in enumerate(fresh):
        if not isinstance(row, dict) or row.get("screen") != "event_outcome":
            continue
        if not _timestamp(row.get("source_timestamp_ms")):
            continue
        source_triggers = _source_bound_triggers(
            base, _trigger_matches(row, windows), source_index
        )
        if not source_triggers:
            continue
        row_triggers[index] = source_triggers
        clear_records = _clear_slot_candidates(base, row, source_triggers, source_index)
        if clear_records:
            row_clear_records[index] = clear_records
            clear_slots.extend(clear_records)
        candidates = _occluded_slot_candidates(base, row, source_triggers, source_index)
        if not candidates:
            continue
        # A blocked-line reread is a fallback for a slot that no dense frame
        # read clearly.  When any frame in the window read the same physical
        # slot unblocked with the same semantics, the clear reading is the
        # canonical observation and the cursor-read spelling is dropped.
        candidates = [
            candidate for candidate in candidates
            if not any(
                clear["slot_key"] == candidate["slot_key"]
                and clear["owner_key"] == candidate["owner_key"]
                and _same_receipt_line(clear["line"], candidate["line"])
                for clear in clear_slots
            )
        ]
        if not candidates:
            continue
        row_candidates[index] = candidates
        for candidate in candidates:
            candidate_name = candidate["effect"].get("name")
            group = next(
                (
                    item for item in slot_winners
                    if item["slot_key"] == candidate["slot_key"]
                    and item["owner_key"] == candidate["owner_key"]
                    and (
                        _same_receipt_line(item["line"], candidate["line"])
                        or (
                            _same_receipt_column(item["line"], candidate["line"])
                            and isinstance(candidate_name, str)
                            and any(
                                isinstance(member["candidate"]["effect"].get("name"), str)
                                and _source_text_compatible(
                                    member["candidate"]["effect"]["name"], candidate_name
                                )
                                for member in item["members"]
                            )
                        )
                    )
                ),
                None,
            )
            if group is None:
                group = dict(
                    slot_key=candidate["slot_key"], owner_key=candidate["owner_key"],
                    line=list(candidate["line"]), members=[],
                )
                slot_winners.append(group)
            group["members"].append(dict(
                row_index=index,
                candidate=candidate,
                timestamp=row.get("source_timestamp_ms"),
            ))
    base_timestamps = frozenset(originals)
    for group in slot_winners:
        group["members"] = [
            member for member in group["members"]
            if not any(
                clear["slot_key"] == group["slot_key"]
                and clear["owner_key"] == group["owner_key"]
                and _same_receipt_line(clear["line"], group["line"])
                for clear in clear_slots
            )
        ]
        _select_slot_winner(group, base_timestamps)

    def _is_slot_winner(index: int, candidate: dict[str, Any]) -> bool:
        return any(
            item.get("winner_row_index") == index
            and item.get("winner_candidate") is candidate
            for item in slot_winners
        )

    for index, row in enumerate(fresh):
        if index not in row_triggers:
            continue
        timestamp = row.get("source_timestamp_ms")
        source_triggers = row_triggers[index]
        selected_effects: list[dict[str, Any]] = []
        selected_lines: list[list[float | int]] = []
        selected_triggers: list[dict[str, Any]] = []
        selected_owners: list[str | None] = []
        selected_records: list[dict[str, Any]] = []
        seen = set()
        for record in row_clear_records.get(index, []):
            seen.add(_effect_key(record["effect"]))
            selected_records.append(dict(
                effect=copy.deepcopy(record["effect"]),
                line=list(record["line"]),
                triggers=list(record["triggers"]),
                owner=record["owner"],
            ))

        # The occlusion annotator deliberately removes blocked receipt lines
        # from the ordinary effect list.  The shared receipt grammar and the
        # signed performance-card reader were re-run against one source-bound
        # line at a time in the pre-pass above; accept only this frame's
        # strongest-per-slot winners so a dense reread can recover the line
        # without turning OCR spelling variants into repeated transactions.
        for candidate in row_candidates.get(index, []):
            effect = candidate["effect"]
            if _effect_key(effect) in seen:
                continue
            if not _is_slot_winner(index, candidate):
                continue
            seen.add(_effect_key(effect))
            selected_records.append(dict(
                effect=copy.deepcopy(effect),
                line=list(candidate["line"]),
                triggers=list(candidate["owner_triggers"]),
                owner=candidate["owner_ref"],
            ))

        # A blocked line can sit directly beside a second receipt line.  The
        # ordinary source match above deliberately selects only the blocked
        # line, so preserve a clear neighboring effect as its own observation
        # with its own geometry and owner proof.  This lets later identity
        # reconciliation replace an obstructed spelling without treating the
        # neighboring line as the blocked line or borrowing its semantics.
        selected_signatures = {
            _effect_key(record["effect"]) for record in selected_records
        }
        for effect in row.get("effects", []):
            if not isinstance(effect, dict):
                continue
            signature = _effect_key(effect)
            if signature in selected_signatures:
                continue
            lines = [
                line for line in _fresh_lines(row)
                if _exact_effect_line(effect, line)
                and any(
                    _adjacent_receipt_line(trigger.get("line_box"), line.get("box"))
                    for trigger in source_triggers
                )
            ]
            groups: list[list[dict[str, Any]]] = []
            for line in lines:
                group = next(
                    (
                        group for group in groups
                        if _same_receipt_line(group[0].get("box"), line.get("box"))
                    ),
                    None,
                )
                if group is None:
                    groups.append([line])
                else:
                    group.append(line)
            # Multiple physical lines with the same parsed effect are
            # ambiguous. Neural variants in one physical slot are collapsed
            # to their strongest view and do not count as independent proof.
            if len(groups) != 1:
                continue
            candidate = max(groups[0], key=lambda item: float(item.get("confidence", 0)))
            adjacent_triggers = [
                trigger for trigger in source_triggers
                if _adjacent_receipt_line(trigger.get("line_box"), candidate.get("box"))
            ]
            # A merged inspection window can contain the same neighboring
            # receipt slot at several source timestamps.  Narrow by nearest
            # source frame before resolving ownership; ties stay together so
            # an equally plausible ownerless/ambiguous anchor still vetoes
            # promotion instead of being silently discarded.
            if any(
                type(trigger.get("source_timestamp_ms")) is not int
                for trigger in adjacent_triggers
            ):
                adjacent_triggers = []
            elif len(adjacent_triggers) > 1 and type(timestamp) is int:
                distances = [
                    abs(int(trigger["source_timestamp_ms"]) - timestamp)
                    for trigger in adjacent_triggers
                ]
                if distances:
                    nearest = min(distances)
                    adjacent_triggers = [
                        trigger for trigger in adjacent_triggers
                        if abs(trigger["source_timestamp_ms"] - timestamp) == nearest
                    ]
            owner_ref, owner_triggers = _adjacent_owner(row, adjacent_triggers)
            if owner_ref is None or not owner_triggers:
                # Ownerless or mixed-owner evidence must never resolve a
                # named identity, even when the OCR line is perfectly clear.
                continue
            if _base_has_exact_effect(base, effect, owner_triggers):
                continue
            # A clean adjacent reread supplies an identity only for a line the
            # base read under an overlay.  When a base frame already read this
            # exact slot unblocked with the same semantics, that reading is
            # authoritative; a one-glyph reread variant must not displace it.
            if _base_has_clear_slot_reading(
                base, effect, candidate.get("box"), owner_triggers,
                require_name_variant=True,
            ):
                continue
            anchor = min(
                owner_triggers,
                key=lambda trigger: abs(
                    int(trigger.get("source_timestamp_ms", 0)) - int(timestamp)
                ),
            )
            adjacent_effect = copy.deepcopy(effect)
            anchor_source_line = _source_line_for_trigger(
                base, anchor, source_index
            )
            adjacent_effect["source_bound_identity_proof"] = _identity_proof(
                adjacent_effect, row, candidate, anchor, owner_ref,
                anchor_source_line=anchor_source_line,
            )
            selected_records.append(dict(
                effect=adjacent_effect,
                line=list(candidate["box"]),
                triggers=list(owner_triggers),
                owner=owner_ref,
                adjacent=True,
            ))
            selected_signatures.add(signature)
        if not selected_records:
            continue
        known_owners = {
            record["owner"] for record in selected_records
            if record["owner"] is not None
        }
        if len(known_owners) > 1 or (
            known_owners and any(record["owner"] is None for record in selected_records)
        ):
            # One clear frame must not combine effects from two overlapping
            # source events, and a known event must not absorb a line whose
            # owner is unresolved.  Preserve the whole candidate for review
            # rather than silently discarding the ambiguous provenance.
            continue
        rejected_mixed = []
        selected_effects = [record["effect"] for record in selected_records]
        selected_lines = [record["line"] for record in selected_records]
        selected_triggers = [
            trigger for record in selected_records for trigger in record["triggers"]
        ]
        selected_owners = [record["owner"] for record in selected_records]
        owner_keys = {
            key for trigger in selected_triggers
            if (key := _owner_key(trigger)) is not None
        }
        if len(owner_keys) > 1:
            # One clear frame must not combine effects from two overlapping
            # source events.  This is checked after line-level narrowing so
            # unrelated receipt lines in one merged window remain harmless.
            continue
        old_rows = originals.get(timestamp, [])
        # A duplicate base timestamp has no single canonical row.  Do not let
        # a later receipt reread choose one implicitly.
        if len(old_rows) > 1:
            continue
        old = old_rows[0] if old_rows else None
        facts = copy.deepcopy(old.get("facts", {})) if old else {}
        unique_triggers = []
        seen_trigger_keys: set[tuple[Any, ...]] = set()
        for trigger in selected_triggers:
            trigger_key = (
                trigger.get("source_timestamp_ms"),
                trigger.get("evidence"),
                tuple(trigger.get("line_box", ())),
            )
            if trigger_key in seen_trigger_keys:
                continue
            seen_trigger_keys.add(trigger_key)
            unique_triggers.append({
                key: copy.deepcopy(trigger.get(key))
                for key in (
                    "source_timestamp_ms",
                    "evidence",
                    "line_box",
                    "source_line_text_sha256",
                    # Preserve the owner binding selected from the validated
                    # outcome plan.  The source row and its OCR are
                    # independently hash-bound, but the recovery fact also
                    # needs to carry the owner inputs that produced it so a
                    # later identity proof cannot change only
                    # ``requested_owner_ref`` and still pass validation.
                    "owner_ref",
                    "owner_start_ms",
                    "owner_end_ms",
                    "owner_context_title",
                )
            })
        recovery = dict(
            schema=SCHEMA,
            method="source_bound_same_receipt_line",
            source_timestamp_ms=timestamp,
            evidence=row.get("evidence"),
            requested_owner_ref=next(
                (owner for owner in selected_owners if owner is not None), None
            ),
            trigger_count=len(unique_triggers),
            trigger_sources=unique_triggers,
            trigger_line_boxes=[list(item["line_box"]) for item in unique_triggers],
            selected_line_boxes=selected_lines,
            effect_signatures=[list(_effect_signature(effect)) for effect in selected_effects],
            independent_frame_count=1,
        )
        if rejected_mixed:
            recovery["rejected_effects"] = rejected_mixed
            recovery["rejection_basis"] = "mixed_owner_proof"
        if recovery["requested_owner_ref"] is None:
            recovery["owner_basis"] = "source_row_context"
        else:
            recovery["owner_basis"] = "outcome_event"
        prior_recovery = facts.get("occluded_receipt_recovery")
        if isinstance(prior_recovery, dict) and prior_recovery != recovery:
            proofs = facts.setdefault("occluded_receipt_recovery_proofs", [])
            if isinstance(proofs, list) and prior_recovery not in proofs:
                proofs.append(prior_recovery)
            if isinstance(proofs, list) and recovery not in proofs:
                proofs.append(copy.deepcopy(recovery))
        # The singular entry is the proof selected for this fresh pass. Keep
        # older entries only in the explicit history list; leaving stale proof
        # in place would let downstream assembly certify the wrong line.
        facts["occluded_receipt_recovery"] = recovery
        promoted = copy.deepcopy(old) if old else copy.deepcopy(row)
        # ``merge`` is the canonical base-row assembler. Keep this helper's
        # output limited to effects selected from the clear reread so an old
        # unrelated effect is never mislabeled as recovery evidence.
        promoted["effects"] = selected_effects
        promoted["facts"] = facts
        if old:
            # A targeted receipt reread must never import state or action
            # facts from the alternate frame into the canonical base row.
            promoted["stats"] = copy.deepcopy(old.get("stats", {}))
        else:
            promoted["stats"] = {}
        result.append(promoted)
    return result


def _sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise OccludedReceiptRecoveryError(f"Cannot read {path}.") from exc


def _validate_cache_provenance(
    inspection: Any,
    directory: Path,
    source_sha256: str,
) -> dict[str, dict[str, str]]:
    """Compatibility entry point for the replay loader's cache validator.

    The full validator below also checks the source duration.  This legacy
    three-argument entry point is retained for callers that already loaded a
    pinned inspection manifest; its duration is bounded by the manifest's
    declared window ends.
    """

    if not isinstance(inspection, dict):
        raise OccludedReceiptRecoveryError("Receipt recovery inspection is not an object.")
    ends = [
        item.get("end_ms")
        for item in inspection.get("windows", [])
        if isinstance(item, dict) and type(item.get("end_ms")) is int
    ]
    timestamps = [
        item.get("source_timestamp_ms")
        for item in inspection.get("readings", [])
        if isinstance(item, dict) and type(item.get("source_timestamp_ms")) is int
    ]
    duration_ms = max([*ends, *timestamps, 0]) + 1
    models, _ = _validate_inspection_cache(
        inspection, Path(directory), source_sha256, duration_ms
    )
    return models


def recover(source: str | Path, root: str | Path, source_info: dict[str, Any],
            readings: list[dict[str, Any]], events: list[dict[str, Any]], *,
            allow_ocr: bool = True,
            max_windows: int = DEFAULT_MAX_WINDOWS,
            max_duration_ms: int = DEFAULT_MAX_DURATION_MS,
            dense_workers: int = 1,
            fps: int = DEFAULT_FPS,
            model_dir: str | Path = ".local/models/rapidocr") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run or replay one bounded generic occluded-receipt pass.

    Fresh inspection uses the existing source-bound receipt inspector. Replay
    reparses cached source frames and never starts OCR. The helper writes its
    own namespace so the immutable base neural caches are untouched.
    """

    if type(max_windows) is not int or max_windows < 0:
        raise OccludedReceiptRecoveryError("max_windows must be a nonnegative integer.")
    if type(max_duration_ms) is not int or max_duration_ms < 0:
        raise OccludedReceiptRecoveryError("max_duration_ms must be a nonnegative integer.")
    if type(fps) is not int or not 4 <= fps <= 60:
        raise OccludedReceiptRecoveryError("fps must be between 4 and 60.")
    if not isinstance(source_info, dict) or not _valid_digest(source_info.get("sha256")):
        raise OccludedReceiptRecoveryError("source_info.sha256 is required.")
    duration_ms = source_info.get("duration_ms")
    if type(duration_ms) is not int or duration_ms <= 0:
        raise OccludedReceiptRecoveryError("source_info.duration_ms must be positive.")

    from .full_recording import save_json
    from .inspect_receipts import inspect
    from .inspect_receipts import merge
    from .inspect_training import reparse_inspection
    from .vision import NeuralReader

    root = Path(root).resolve()
    directory = root / "occluded-receipt-recovery"
    requested = plan(readings, events, duration_ms)
    requested_with_fps = _with_fps(requested, fps)
    manifest = directory / "receipt-inspection.json"
    if not requested and not manifest.exists():
        return readings, dict(
            schema=SCHEMA,
            method="bounded_source_bound_occluded_receipt_recovery",
            source_sha256=source_info["sha256"],
            requested_windows=[], processed_windows=[], pending_windows=[],
            new_frames=0, complete_event_history=False,
        )
    directory.mkdir(parents=True, exist_ok=True)
    capture_path = directory / "capture.json"
    capture = dict(source=source_info)
    if capture_path.exists():
        try:
            existing_capture = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OccludedReceiptRecoveryError("Receipt recovery capture is unreadable.") from exc
        if existing_capture != capture:
            raise OccludedReceiptRecoveryError("Receipt recovery source changed.")
    else:
        save_json(capture_path, capture)

    existing = None
    existing_windows: list[dict[str, Any]] = []
    prior_processed: list[dict[str, Any]] = []
    resumed_unfinished_inspection = False
    if manifest.exists():
        existing = _load_json(manifest, "Receipt recovery inspection")
        if not isinstance(existing, dict) or existing.get("source_sha256") != source_info["sha256"]:
            raise OccludedReceiptRecoveryError("Receipt recovery observations belong to another recording.")
        _, existing_windows = _validate_inspection_cache(
            existing, directory, source_info["sha256"], duration_ms
        )
        plan_path = directory / "last-plan.json"
        if plan_path.is_file():
            prior_plan = _load_json(plan_path, "Receipt recovery plan")
        else:
            # Inspection writes source-bound frames before the final plan.
            # After interruption, reuse those frames only when every cached
            # window is an exact request independently recomputed from the
            # current source rows. No ownership is inferred from the cache.
            requested_by_key = {_window_key(window, fps): window
                                for window in requested_with_fps}
            cached_keys = {(window['start_ms'], window['end_ms'], window['fps'])
                           for window in existing_windows}
            if not cached_keys <= requested_by_key.keys():
                raise OccludedReceiptRecoveryError(
                    'Unfinished receipt inspection contains windows outside the current source plan.'
                )
            prior_plan = dict(
                schema=SCHEMA, source_sha256=source_info['sha256'],
                manifest_sha256=_sha256_file(manifest), requested_fps=fps,
                requested_windows=requested_with_fps,
                processed_windows=[window for key, window in requested_by_key.items() if key in cached_keys],
                pending_windows=[window for key, window in requested_by_key.items() if key not in cached_keys],
            )
            resumed_unfinished_inspection = True
        _, prior_processed, _ = _validate_plan_metadata(
            prior_plan, source_info["sha256"]
        )
        declared_manifest = prior_plan.get("manifest_sha256")
        if not _valid_digest(declared_manifest):
            raise OccludedReceiptRecoveryError(
                "Receipt recovery plan is not bound to its inspection manifest."
            )
        if declared_manifest != _sha256_file(manifest):
            raise OccludedReceiptRecoveryError("Receipt recovery plan is stale.")
        actual_keys = {
            (window["start_ms"], window["end_ms"], window["fps"])
            for window in existing_windows
        }
        for index, processed_window in enumerate(prior_processed):
            key = _window_key(processed_window, int(prior_plan.get("requested_fps", fps)))
            if key not in actual_keys:
                raise OccludedReceiptRecoveryError(
                    f"Receipt recovery processed_windows[{index}] is not an inspected cache window."
                )
    old_count = len(existing.get("readings", [])) if isinstance(existing, dict) else 0
    completed = {
        (window["start_ms"], window["end_ms"], window["fps"])
        for window in existing_windows
    }
    processed, pending = [], []
    used_ms = 0
    new_windows = 0
    reader = None
    steps = []
    for window in requested:
        key = (window["start_ms"], window["end_ms"], fps)
        if key not in completed:
            if not allow_ocr or new_windows >= max_windows or used_ms + window["end_ms"] - window["start_ms"] > max_duration_ms:
                pending.append(dict(window, fps=fps))
                continue
            steps.append((window, "new"))
            used_ms += window["end_ms"] - window["start_ms"]
            new_windows += 1
        elif allow_ocr:
            steps.append((window, "revalidate"))
        else:
            steps.append((window, None))
    if allow_ocr:
        from .dense_inspection_pool import prepare_windows
        prepare_windows(source, directory, [w for w, kind in steps if kind == "new"], fps,
                        kind="base", model_dir=model_dir, workers=dense_workers)
    for window, kind in steps:
        if kind is not None:
            if reader is None:
                reader = NeuralReader(model_dir)
            inspect(source, directory, window["start_ms"], window["end_ms"], fps, reader=reader)
        processed.append(dict(window, fps=fps))

    if not manifest.exists():
        metadata = dict(
            schema=SCHEMA,
            method="bounded_source_bound_occluded_receipt_recovery",
            source_sha256=source_info["sha256"],
            requested_windows=requested_with_fps,
            processed_windows=processed,
            pending_windows=pending,
            requested_fps=fps,
            model_dir=str(model_dir),
            new_frames=0,
            manifest_sha256=None,
            unpromoted_observations=[],
            ocr_engine_fingerprint=reader.fingerprint if reader else None,
            observed_ocr_models={},
            complete_event_history=False,
        )
        save_json(directory / "last-plan.json", metadata)
        return readings, metadata

    inspection = _load_json(manifest, "Receipt recovery inspection")
    models, validated_windows = _validate_inspection_cache(
        inspection, directory, source_info["sha256"], duration_ms
    )
    actual_window_keys = {
        (window["start_ms"], window["end_ms"], window["fps"])
        for window in validated_windows
    }
    registered_processed: list[dict[str, Any]] = []
    registered_keys: set[tuple[int, int, int]] = set()
    for candidate in [*prior_processed, *processed]:
        key = _window_key(candidate, fps)
        if key not in actual_window_keys:
            raise OccludedReceiptRecoveryError(
                "Receipt recovery processed window is not present in the inspected cache."
            )
        if key in registered_keys:
            continue
        registered_keys.add(key)
        registered_processed.append(dict(candidate, fps=key[2]))
    processed = registered_processed
    fresh = reparse_inspection(inspection, directory)

    def relocate(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str) and value.endswith((".png", ".jpg", ".json")) and (directory / value).is_file():
            return (directory / value).relative_to(root).as_posix()
        return value

    relocated = relocate(fresh)
    # A previously inspected subwindow remains the only source-bound scope
    # when a later plan merges it into a larger request.  Its triggers came
    # from the same hashed plan and are checked against the immutable base
    # rows by scoped_observations.
    scope_windows = list(prior_processed)
    prior_keys = {_window_key(window, fps) for window in scope_windows}
    scope_windows.extend(
        window for window in processed
        if _window_key(window, fps) not in prior_keys
    )
    promoted = scoped_observations(readings, relocated, scope_windows)
    promoted_times = {row.get("source_timestamp_ms") for row in promoted}
    unassigned = [
        dict(
            source_timestamp_ms=row.get("source_timestamp_ms"),
            evidence=row.get("evidence"),
            reason="outside_requested_scope_or_ambiguous_receipt_owner",
            effects=copy.deepcopy(row.get("effects", [])),
        )
        for row in relocated
        if isinstance(row, dict)
        and row.get("effects")
        and row.get("source_timestamp_ms") not in promoted_times
    ]
    merged = merge(readings, promoted)
    manifest_hash = _sha256_file(manifest)
    metadata = dict(
        schema=SCHEMA,
        method="bounded_source_bound_occluded_receipt_recovery",
        source_sha256=source_info["sha256"],
        requested_windows=requested_with_fps,
        processed_windows=processed,
        pending_windows=pending,
        requested_fps=fps,
        model_dir=str(model_dir),
        new_frames=len(inspection.get("readings", [])) - old_count,
        manifest_sha256=manifest_hash,
        promoted_source_timestamps=sorted(promoted_times),
        unpromoted_observations=unassigned,
        ocr_engine_fingerprint=reader.fingerprint if reader else None,
        observed_ocr_models=models,
        resumed_unfinished_inspection=resumed_unfinished_inspection,
        complete_event_history=False,
    )
    save_json(directory / "last-plan.json", metadata)
    return merged, metadata


def prepare_plan(source: str | Path, root: str | Path, report: dict[str, Any] | str | Path,
                 *, max_windows: int = DEFAULT_MAX_WINDOWS,
                 max_duration_ms: int = DEFAULT_MAX_DURATION_MS,
                 fps: int = DEFAULT_FPS,
                 model_dir: str | Path = ".local/models/rapidocr",
                 readings: list[dict[str, Any]] | None = None,
                 events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Register pending generic receipt windows for a disposable cache.

    This is the cache preparation boundary for replay-only workflows.  It
    verifies the recording hash against the cached report, reparses existing
    immutable OCR sidecars, and invokes :func:`recover` with OCR disabled.
    Thus a clone can record exactly which source-bound windows still need a
    fresh proof before a later execution pass, while a cached full run cannot
    mistake an absent proof for a completed recovery.

    ``readings`` and ``events`` are optional validated, pre-recovery inputs.
    A replay-input worker that has already merged its declared inspections and
    other recoveries should pass those rows and their outcome events here.  It
    keeps planning on the same row assembly that the worker will consume,
    while the default path remains compatible with a standalone cached report.
    The values are source observations only; this function never accepts
    expected effects or amounts as planning input.
    """

    root = Path(root).resolve()
    if isinstance(report, (str, Path)):
        report_path = Path(report)
        try:
            report_value = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OccludedReceiptRecoveryError("Cached report is not readable JSON.") from exc
    else:
        report_value = report
    if not isinstance(report_value, dict):
        raise OccludedReceiptRecoveryError("Cached report must be an object.")
    source_info = report_value.get("source")
    if not isinstance(source_info, dict) or not _valid_digest(source_info.get("sha256")):
        raise OccludedReceiptRecoveryError("Cached report source.sha256 is required.")
    actual_source_sha256 = _sha256_file(Path(source).resolve())
    if actual_source_sha256 != source_info["sha256"]:
        raise OccludedReceiptRecoveryError("Cached report belongs to another recording.")

    from .full_recording import cached_readings
    from .transactions import outcome_events

    try:
        if readings is None:
            readings = cached_readings(report_value, root)
        elif not isinstance(readings, list):
            raise OccludedReceiptRecoveryError("Prepared readings must be an array.")
        if events is None:
            events = outcome_events(readings)
        elif not isinstance(events, list):
            raise OccludedReceiptRecoveryError("Prepared outcome events must be an array.")
        _, metadata = recover(
            source,
            root,
            source_info,
            readings,
            events,
            allow_ocr=False,
            max_windows=max_windows,
            max_duration_ms=max_duration_ms,
            fps=fps,
            model_dir=model_dir,
        )
    except OccludedReceiptRecoveryError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise OccludedReceiptRecoveryError("Could not prepare the generic receipt recovery plan.") from exc
    return metadata


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Register source-bound occluded-receipt recovery windows without OCR."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--root", "--cache-root", dest="root", required=True, type=Path,
                        help="Disposable cache root containing capture/report and neural sidecars.")
    parser.add_argument("--report", type=Path,
                        help="Cached report JSON (defaults to ROOT/report.json).")
    parser.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS)
    parser.add_argument("--max-duration-ms", type=int, default=DEFAULT_MAX_DURATION_MS)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    args = parser.parse_args()
    report = args.report or args.root / "report.json"
    try:
        metadata = prepare_plan(
            args.source,
            args.root,
            report,
            max_windows=args.max_windows,
            max_duration_ms=args.max_duration_ms,
            fps=args.fps,
            model_dir=args.model_dir,
        )
    except OccludedReceiptRecoveryError as exc:
        parser.error(str(exc))
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    _main()
