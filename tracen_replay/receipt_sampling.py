"""Plan bounded receipt re-sampling without supplying expected labels to OCR."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = 'tracen-replay/receipt-sampling-plan-v1'
MAX_WINDOW_MS = 5_000
DEFAULT_PAD_MS = 500
DEFAULT_MERGE_GAP_MS = 250
DEFAULT_FPS = 16
DEFAULT_MIN_INSPECTION_FPS = 16
DEFAULT_MAX_FOOTAGE_MS = 30_000
DEFAULT_MAX_FRAMES = 480

_OPTIONAL_SCREEN_MARKERS = (
    'concert_info',
    'concert info',
    'concert_panel',
    'bonus_panel',
    'owned_skill_inventory',
    'skill_inventory',
    'inventory_panel',
    'final_summary',
    'career_summary',
)
_RECEIPT_EVENT_KINDS = frozenset(('outcome', 'receipt', 'event_outcome'))


class ReceiptSamplingError(ValueError):
    """Raised when a receipt sampling plan is stale, malformed, or unsafe."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError as exc:
        raise ReceiptSamplingError(f'Cannot read {path}.') from exc
    return digest.hexdigest()


def _valid_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _require_digest(value: Any, label: str) -> str:
    if not _valid_digest(value):
        raise ReceiptSamplingError(f'{label} must be a SHA-256 digest.')
    return value.lower()


def _require_duration(source: Any, label: str) -> int:
    if not isinstance(source, dict) or type(source.get('duration_ms')) is not int:
        raise ReceiptSamplingError(f'{label}.duration_ms must be an integer.')
    duration = source['duration_ms']
    if duration <= 0:
        raise ReceiptSamplingError(f'{label}.duration_ms must be positive.')
    return duration


def _require_source(source: Any, label: str) -> tuple[str, int]:
    if not isinstance(source, dict):
        raise ReceiptSamplingError(f'{label} must be an object.')
    digest = _require_digest(source.get('sha256'), f'{label}.sha256')
    return digest, _require_duration(source, label)


def _require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ReceiptSamplingError(f'{label} must be an integer.')
    if minimum is not None and value < minimum:
        raise ReceiptSamplingError(f'{label} must be at least {minimum}.')
    return value


def _evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _optional_marker(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in ('screen', 'screen_label', 'kind', 'type', 'scope'):
        value = row.get(key)
        if not isinstance(value, str):
            continue
        lowered = value.lower().replace('-', '_')
        for marker in _OPTIONAL_SCREEN_MARKERS:
            if marker in lowered:
                return marker
    return None


def _range(value: Any, label: str, duration: int) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    start = value.get('first_seen_ms', value.get('start_ms', value.get('source_timestamp_ms')))
    end = value.get('last_seen_ms', value.get('end_ms', value.get('source_timestamp_ms')))
    if type(start) is not int or type(end) is not int:
        return None
    if start < 0 or start >= duration or end < start or end > duration:
        return None
    # A point event still needs a non-empty source interval for inspect_receipts.
    if end == start:
        end += 1
    return start, end


def _point_range(value: Any, label: str, duration: int) -> tuple[int, int] | None:
    if not isinstance(value, dict) or type(value.get('source_timestamp_ms')) is not int:
        return None
    point = value['source_timestamp_ms']
    if point < 0 or point >= duration:
        return None
    return point, point + 1


def _target(target_id: str, kind: str, bounds: tuple[int, int], evidence: Any, reason: str) -> dict[str, Any]:
    return dict(id=target_id, kind=kind, anchor_start_ms=bounds[0], anchor_end_ms=bounds[1],
                evidence=_evidence(evidence), reason=reason)


def _extract_targets(report: dict[str, Any], duration: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = report.get('gameplay_tracking', {})
    if not isinstance(data, dict):
        return [], []
    readings = data.get('readings', [])
    if not isinstance(readings, list):
        readings = []
    targets: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    target_number = 1
    deferred_number = 1

    def defer(kind: str, reason: str, value: Any = None, bounds: tuple[int, int] | None = None):
        nonlocal deferred_number
        item = dict(id=f'deferred-source-{deferred_number:04d}', kind=kind, reason=reason)
        deferred_number += 1
        if bounds is not None:
            item.update(anchor_start_ms=bounds[0], anchor_end_ms=bounds[1])
        if isinstance(value, dict):
            item['evidence'] = _evidence(value.get('evidence'))
        deferred.append(item)

    def add(kind: str, bounds: tuple[int, int] | None, evidence: Any, reason: str):
        nonlocal target_number
        if bounds is None:
            defer(kind, 'invalid_receipt_observation_bounds', evidence)
            return
        targets.append(_target(f'target-{target_number:04d}', kind, bounds, evidence, reason))
        target_number += 1

    for reading in readings:
        if not isinstance(reading, dict):
            continue
        marker = _optional_marker(reading)
        if marker:
            facts = reading.get('facts', {})
            if isinstance(facts, dict) and facts.get('occluded_receipt_lines'):
                defer('occluded_receipt_line', 'optional_panel_not_receipt_target', reading)
            if reading.get('conflicting_readings'):
                defer('conflicting_receipt_readings', 'optional_panel_not_receipt_target', reading)
            continue
        facts = reading.get('facts', {})
        lines = facts.get('occluded_receipt_lines', []) if isinstance(facts, dict) else []
        if isinstance(lines, list):
            for line in lines:
                bounds = _point_range(reading, 'reading.source_timestamp_ms', duration)
                if bounds is None:
                    defer('occluded_receipt_line', 'invalid_receipt_observation_bounds', reading)
                else:
                    add('occluded_receipt_line', bounds, reading.get('evidence'),
                        'source reading contains an occluded receipt line')
        if reading.get('conflicting_readings'):
            add('conflicting_receipt_readings', _range(reading, 'reading', duration), reading.get('evidence'),
                'source reading contains conflicting receipt readings')
        if isinstance(facts, dict) and facts.get('conflicting_receipt_readings'):
            add('conflicting_receipt_readings', _range(reading, 'reading', duration), reading.get('evidence'),
                'source reading contains conflicting receipt readings')

    candidates = data.get('unparsed_receipt_candidates', [])
    if not isinstance(candidates, list):
        candidates = []
    for candidate in candidates:
        marker = _optional_marker(candidate)
        bounds = _range(candidate, 'unparsed_receipt_candidate', duration)
        if marker:
            defer('unparsed_receipt_candidate', 'optional_panel_not_receipt_target', candidate, bounds)
            continue
        add('unparsed_receipt_candidate', bounds, candidate.get('evidence') if isinstance(candidate, dict) else None,
            'source report retained an unparsed receipt candidate')

    events = data.get('events', [])
    if not isinstance(events, list):
        events = []
    for event in events:
        if not isinstance(event, dict) or not event.get('conflicting_readings'):
            continue
        bounds = _range(event, 'event', duration)
        if _optional_marker(event):
            defer('conflicting_receipt_readings', 'optional_panel_not_receipt_target', event, bounds)
            continue
        if event.get('kind') not in _RECEIPT_EVENT_KINDS:
            defer('conflicting_receipt_readings', 'non_receipt_event_conflict', event, bounds)
            continue
        add('conflicting_receipt_readings', bounds, event.get('evidence'),
            'source event retained conflicting receipt readings')

    return targets, deferred


def _unresolved_intervals(report: dict[str, Any], duration: int) -> list[dict[str, Any]]:
    data = report.get('gameplay_tracking', {})
    if not isinstance(data, dict):
        return []
    result = []
    for kind, container in (
        ('stat', data.get('intervals', [])),
        ('performance', data.get('performance_accounting', {}).get('intervals', [])
         if isinstance(data.get('performance_accounting', {}), dict) else []),
    ):
        if not isinstance(container, list):
            continue
        for index, row in enumerate(container):
            if not isinstance(row, dict):
                continue
            status = row.get('status')
            if not isinstance(status, str) or status.lower() in {'balanced', 'verified', 'complete'}:
                continue
            bounds = _range(row, f'{kind}[{index}]', duration)
            if bounds is None:
                continue
            # Status and interval bounds determine priority.  Residual values
            # are deliberately neither copied into the plan nor sent to OCR.
            result.append(dict(kind=kind, index=index, start_ms=bounds[0], end_ms=bounds[1], status=status))
    return result


def _overlap(left: tuple[int, int], right: tuple[int, int]) -> int:
    return max(0, min(left[1], right[1]) - max(left[0], right[0]))


def _annotate_priority(targets: list[dict[str, Any]], intervals: list[dict[str, Any]]) -> None:
    for target in targets:
        bounds = (target['anchor_start_ms'], target['anchor_end_ms'])
        stat = sum(_overlap(bounds, (row['start_ms'], row['end_ms']))
                   for row in intervals if row['kind'] == 'stat')
        performance = sum(_overlap(bounds, (row['start_ms'], row['end_ms']))
                          for row in intervals if row['kind'] == 'performance')
        target['priority'] = dict(unresolved_stat_overlap_ms=stat,
                                  unresolved_performance_overlap_ms=performance,
                                  unresolved_overlap_ms=stat + performance,
                                  overlaps_unresolved_interval=bool(stat or performance))


def _merge_targets(targets: list[dict[str, Any]], merge_gap_ms: int, *,
                   pad_ms: int = 0, duration: int | None = None) -> list[dict[str, Any]]:
    """Merge targets by their eventual padded windows, not raw anchors."""
    if duration is None:
        duration = max((row['anchor_end_ms'] for row in targets), default=0)
    groups: list[dict[str, Any]] = []

    def padded(row: dict[str, Any]) -> tuple[int, int]:
        return (max(0, row['anchor_start_ms'] - pad_ms),
                min(duration, row['anchor_end_ms'] + pad_ms))

    ordered = sorted(targets, key=lambda row: (*padded(row), row['id']))
    for target in ordered:
        window_start, window_end = padded(target)
        if groups and window_start <= groups[-1]['window_end_ms'] + merge_gap_ms:
            group = groups[-1]
            group['anchor_end_ms'] = max(group['anchor_end_ms'], target['anchor_end_ms'])
            group['window_end_ms'] = max(group['window_end_ms'], window_end)
            group['target_ids'].append(target['id'])
            group['targets'].append(target)
        else:
            groups.append(dict(anchor_start_ms=target['anchor_start_ms'],
                               anchor_end_ms=target['anchor_end_ms'],
                               window_start_ms=window_start, window_end_ms=window_end,
                               target_ids=[target['id']], targets=[target]))
    return groups


def _coverage_intervals(inspection: dict[str, Any] | None, threshold: int) -> list[dict[str, int]]:
    if inspection is None:
        return []
    result = []
    for window in inspection['windows']:
        if window['fps'] >= threshold:
            result.append(dict(start_ms=window['start_ms'], end_ms=window['end_ms'], fps=window['fps']))
    return sorted(result, key=lambda row: (row['start_ms'], row['end_ms'], row['fps']))


def _subtract_coverage(bounds: tuple[int, int], coverage: list[dict[str, int]]) -> tuple[list[tuple[int, int]], list[dict[str, int]]]:
    remaining = [bounds]
    covered: list[dict[str, int]] = []
    for item in coverage:
        next_remaining = []
        for start, end in remaining:
            overlap_start = max(start, item['start_ms'])
            overlap_end = min(end, item['end_ms'])
            if overlap_start >= overlap_end:
                next_remaining.append((start, end))
                continue
            covered.append(dict(start_ms=overlap_start, end_ms=overlap_end, fps=item['fps']))
            if start < overlap_start:
                next_remaining.append((start, overlap_start))
            if overlap_end < end:
                next_remaining.append((overlap_end, end))
        remaining = next_remaining
    return remaining, covered


def _split(bounds: tuple[int, int], maximum: int) -> list[tuple[int, int]]:
    start, end = bounds
    return [(point, min(point + maximum, end)) for point in range(start, end, maximum)]


def _estimated_frames(start: int, end: int, fps: int) -> int:
    # Keep the budget calculation integral so malformed, very large source
    # durations cannot overflow a float conversion during plan validation.
    # decode_frames permits ceil(duration * fps) + 1 for timestamp rounding.
    return max(1, ((end - start) * fps + 999) // 1000 + 1)


def _validate_policy(*, pad_ms: int, merge_gap_ms: int, fps: int,
                     min_inspection_fps: int, max_footage_ms: int,
                     max_frames: int, max_window_ms: int) -> None:
    for name, value in (
        ('pad_ms', pad_ms), ('merge_gap_ms', merge_gap_ms), ('fps', fps),
        ('min_inspection_fps', min_inspection_fps), ('max_footage_ms', max_footage_ms),
        ('max_frames', max_frames), ('max_window_ms', max_window_ms),
    ):
        if type(value) is not int:
            raise ReceiptSamplingError(f'{name} must be an integer.')
    if not 0 <= pad_ms <= MAX_WINDOW_MS:
        raise ReceiptSamplingError('pad_ms must be between 0 and 5000.')
    if not 0 <= merge_gap_ms <= MAX_WINDOW_MS:
        raise ReceiptSamplingError('merge_gap_ms must be between 0 and 5000.')
    if not 4 <= fps <= 60:
        raise ReceiptSamplingError('fps must be between 4 and 60.')
    if not 4 <= min_inspection_fps <= 60:
        raise ReceiptSamplingError('min_inspection_fps must be between 4 and 60.')
    if max_footage_ms <= 0 or max_frames <= 0:
        raise ReceiptSamplingError('Sampling budgets must be positive.')
    if not 1 <= max_window_ms <= MAX_WINDOW_MS:
        raise ReceiptSamplingError('max_window_ms must be between 1 and 5000.')


def _validate_inspection(inspection: Any, source_sha256: str, duration: int) -> dict[str, Any]:
    if not isinstance(inspection, dict):
        raise ReceiptSamplingError('Existing receipt inspection must be an object.')
    if inspection.get('source_sha256') != source_sha256:
        raise ReceiptSamplingError('Existing receipt inspection belongs to another source.')
    windows = inspection.get('windows')
    if not isinstance(windows, list):
        raise ReceiptSamplingError('Existing receipt inspection windows are malformed.')
    clean = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ReceiptSamplingError(f'Existing receipt inspection window {index} is malformed.')
        start = _require_int(window.get('start_ms'), f'inspection.windows[{index}].start_ms', minimum=0)
        end = _require_int(window.get('end_ms'), f'inspection.windows[{index}].end_ms', minimum=0)
        fps = _require_int(window.get('fps'), f'inspection.windows[{index}].fps', minimum=4)
        if start >= end or end > duration or end - start > MAX_WINDOW_MS or fps > 60:
            raise ReceiptSamplingError(f'Existing receipt inspection window {index} is outside supported bounds.')
        clean.append(dict(start_ms=start, end_ms=end, fps=fps))
    return dict(source_sha256=source_sha256, windows=clean)


def _validate_bound_inputs(report: Any, capture: Any, source_sha256: str) -> int:
    if not isinstance(report, dict):
        raise ReceiptSamplingError('Report must be a JSON object.')
    tracking = report.get('gameplay_tracking')
    if not isinstance(tracking, dict) or tracking.get('auxiliary_log_used') is not False:
        raise ReceiptSamplingError('Report gameplay_tracking.auxiliary_log_used must be false.')
    report_source, duration = _require_source(report.get('source'), 'report.source')
    capture_source, capture_duration = _require_source(
        capture.get('source') if isinstance(capture, dict) else None, 'capture.source')
    if report_source != source_sha256 or capture_source != source_sha256:
        raise ReceiptSamplingError('Source, capture, and report identities do not match.')
    if duration != capture_duration:
        raise ReceiptSamplingError('Capture and report durations do not match.')
    return duration


def build_plan(report: dict[str, Any], capture: dict[str, Any], *,
               source_sha256: str, report_sha256: str, capture_sha256: str,
               existing_inspection: dict[str, Any] | None = None,
               existing_inspection_sha256: str | None = None,
               pad_ms: int = DEFAULT_PAD_MS,
               merge_gap_ms: int = DEFAULT_MERGE_GAP_MS,
               fps: int = DEFAULT_FPS,
               min_inspection_fps: int = DEFAULT_MIN_INSPECTION_FPS,
               max_footage_ms: int = DEFAULT_MAX_FOOTAGE_MS,
               max_frames: int = DEFAULT_MAX_FRAMES,
               max_window_ms: int = MAX_WINDOW_MS,
               paths: dict[str, str] | None = None) -> dict[str, Any]:
    source_sha256 = _require_digest(source_sha256, 'source_sha256')
    report_sha256 = _require_digest(report_sha256, 'report_sha256')
    capture_sha256 = _require_digest(capture_sha256, 'capture_sha256')
    duration = _validate_bound_inputs(report, capture, source_sha256)
    _validate_policy(pad_ms=pad_ms, merge_gap_ms=merge_gap_ms, fps=fps,
                     min_inspection_fps=min_inspection_fps, max_footage_ms=max_footage_ms,
                     max_frames=max_frames, max_window_ms=max_window_ms)
    if existing_inspection is None:
        if existing_inspection_sha256 is not None:
            raise ReceiptSamplingError('Inspection hash supplied without inspection data.')
    else:
        existing_inspection = _validate_inspection(existing_inspection, source_sha256, duration)
        if not _valid_digest(existing_inspection_sha256):
            raise ReceiptSamplingError('Existing inspection hash is required.')
        existing_inspection_sha256 = existing_inspection_sha256.lower()

    targets, deferred = _extract_targets(report, duration)
    intervals = _unresolved_intervals(report, duration)
    _annotate_priority(targets, intervals)
    groups = _merge_targets(targets, merge_gap_ms, pad_ms=pad_ms, duration=duration)
    coverage = _coverage_intervals(existing_inspection, max(fps, min_inspection_fps))
    candidates = []
    for group_index, group in enumerate(groups):
        raw_bounds = (group['window_start_ms'], group['window_end_ms'])
        if raw_bounds[0] >= raw_bounds[1]:
            deferred.append(dict(target_ids=group['target_ids'], start_ms=raw_bounds[0],
                                 end_ms=raw_bounds[1], reason='empty_window_after_source_clamp'))
            continue
        remaining, covered = _subtract_coverage(raw_bounds, coverage)
        if covered:
            deferred.append(dict(target_ids=group['target_ids'], covered_segments=covered,
                                 reason='already_inspected_at_sufficient_fps'
                                 if not remaining else 'partially_inspected_at_sufficient_fps'))
        if not remaining:
            continue
        group_priority = max((row.get('priority', {}).get('unresolved_overlap_ms', 0)
                              for row in group['targets']), default=0)
        overlaps = bool(group_priority)
        for split_index, bounds in enumerate(remaining):
            for chunk_index, chunk in enumerate(_split(bounds, max_window_ms)):
                chunk_targets = [row for row in group['targets']
                                 if row['anchor_start_ms'] < chunk[1] and row['anchor_end_ms'] > chunk[0]]
                if not chunk_targets:
                    chunk_targets = list(group['targets'])
                candidates.append(dict(
                    candidate_id=f'candidate-{group_index:04d}-{split_index:02d}-{chunk_index:02d}',
                    start_ms=chunk[0], end_ms=chunk[1], target_ids=[row['id'] for row in chunk_targets],
                    priority=dict(overlaps_unresolved_interval=overlaps,
                                  unresolved_overlap_ms=group_priority),
                    estimated_footage_ms=chunk[1] - chunk[0],
                    estimated_frames=_estimated_frames(chunk[0], chunk[1], fps),
                ))

    candidates.sort(key=lambda row: (
        not row['priority']['overlaps_unresolved_interval'],
        -row['priority']['unresolved_overlap_ms'], row['start_ms'], row['end_ms'], row['candidate_id']))
    selected = []
    footage = 0
    frames = 0
    for candidate in candidates:
        next_footage = footage + candidate['estimated_footage_ms']
        next_frames = frames + candidate['estimated_frames']
        if next_footage <= max_footage_ms and next_frames <= max_frames:
            selected.append(candidate)
            footage, frames = next_footage, next_frames
            continue
        if next_footage > max_footage_ms and next_frames > max_frames:
            reason = 'footage_and_frame_budget_exhausted'
        elif next_footage > max_footage_ms:
            reason = 'footage_budget_exhausted'
        else:
            reason = 'frame_budget_exhausted'
        deferred.append(dict(target_ids=candidate['target_ids'], start_ms=candidate['start_ms'],
                             end_ms=candidate['end_ms'], reason=reason,
                             estimated_footage_ms=candidate['estimated_footage_ms'],
                             estimated_frames=candidate['estimated_frames']))

    windows = []
    for index, candidate in enumerate(selected, 1):
        windows.append(dict(id=f'window-{index:04d}', start_ms=candidate['start_ms'],
                            end_ms=candidate['end_ms'], fps=fps, target_ids=candidate['target_ids'],
                            selection_order=index, priority=candidate['priority'],
                            estimated_footage_ms=candidate['estimated_footage_ms'],
                            estimated_frames=candidate['estimated_frames'], status='planned'))

    deferred_target_ids = sorted({target_id for row in deferred for target_id in row.get('target_ids', [])})
    plan = dict(
        schema=SCHEMA, version=1,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        source=dict(sha256=source_sha256, duration_ms=duration),
        report=dict(sha256=report_sha256),
        capture=dict(sha256=capture_sha256),
        inspection=dict(sha256=existing_inspection_sha256, minimum_sufficient_fps=max(fps, min_inspection_fps)),
        policy=dict(pad_ms=pad_ms, merge_gap_ms=merge_gap_ms, fps=fps,
                    min_inspection_fps=min_inspection_fps,
                    max_window_ms=max_window_ms, max_footage_ms=max_footage_ms,
                    max_frames=max_frames, no_expected_labels_to_ocr=True),
        unresolved_intervals=intervals,
        targets=targets,
        windows=windows,
        deferred=deferred,
        summary=dict(eligible_target_count=len(targets), selected_window_count=len(windows),
                     selected_footage_ms=footage, estimated_frames=frames,
                     deferred_target_count=len(deferred_target_ids),
                     deferred_entry_count=len(deferred),
                     skipped_optional_targets=sum(1 for row in deferred
                                                  if row.get('reason') == 'optional_panel_not_receipt_target')),
        limits=dict(maximum_window_ms=max_window_ms, footage_budget_ms=max_footage_ms,
                    frame_budget=max_frames),
        execution=dict(status='dry_run', report_unchanged=True,
                       caller_must_reparse_report=True),
    )
    if paths:
        plan['paths'] = dict(paths)
    return plan


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptSamplingError(f'{label} is not valid JSON.') from exc
    if not isinstance(data, dict):
        raise ReceiptSamplingError(f'{label} must be a JSON object.')
    return data, hashlib.sha256(raw).hexdigest()


def create_plan(source: str | Path, report_path: str | Path, *, output: str | Path | None = None,
                pad_ms: int = DEFAULT_PAD_MS, merge_gap_ms: int = DEFAULT_MERGE_GAP_MS,
                fps: int = DEFAULT_FPS, min_inspection_fps: int = DEFAULT_MIN_INSPECTION_FPS,
                max_footage_ms: int = DEFAULT_MAX_FOOTAGE_MS, max_frames: int = DEFAULT_MAX_FRAMES,
                max_window_ms: int = MAX_WINDOW_MS,
                run_root: str | Path | None = None) -> tuple[Path, dict[str, Any]]:
    source_path = Path(source).resolve()
    report_path = Path(report_path).resolve()
    if not source_path.is_file():
        raise ReceiptSamplingError('Source must be an existing local video file.')
    if not report_path.is_file():
        raise ReceiptSamplingError('Report must be an existing report.json.')
    evidence_root = Path(run_root).resolve() if run_root is not None else report_path.parent
    if not evidence_root.is_dir():
        raise ReceiptSamplingError('Evidence root must be an existing directory.')
    try:
        report_path.relative_to(evidence_root)
    except ValueError as exc:
        raise ReceiptSamplingError('Report must be inside the evidence root.') from exc
    report, report_sha256 = _load_json(report_path, 'Report')
    capture_path = evidence_root / 'capture.json'
    capture, capture_sha256 = _load_json(capture_path, 'Capture')
    source_sha256 = _sha256_file(source_path)
    inspection_path = evidence_root / 'receipt-inspection.json'
    existing = None
    existing_sha256 = None
    if inspection_path.exists():
        existing, existing_sha256 = _load_json(inspection_path, 'Existing receipt inspection')
    duration = _validate_bound_inputs(report, capture, source_sha256)
    if output is None:
        output_path = report_path.parent / 'receipt-sampling-plan-v1.json'
    else:
        output_path = Path(output).resolve()
    plan = build_plan(
        report, capture, source_sha256=source_sha256, report_sha256=report_sha256,
        capture_sha256=capture_sha256, existing_inspection=existing,
        existing_inspection_sha256=existing_sha256, pad_ms=pad_ms, merge_gap_ms=merge_gap_ms,
        fps=fps, min_inspection_fps=min_inspection_fps, max_footage_ms=max_footage_ms,
        max_frames=max_frames, max_window_ms=max_window_ms,
        paths=dict(source=str(source_path), report=str(report_path), capture=str(capture_path),
                   receipt_inspection=str(inspection_path), run_root=str(evidence_root)),
    )
    write_plan(output_path, plan)
    return output_path, plan


def write_plan(path: str | Path, plan: dict[str, Any]) -> None:
    path = Path(path).resolve()
    if path.exists():
        raise ReceiptSamplingError(f'Plan output already exists; refusing to overwrite: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8', newline='') as stream:
            json.dump(plan, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    except FileExistsError as exc:
        raise ReceiptSamplingError(f'Plan output appeared during creation; refusing to overwrite: {path}') from exc
    except OSError as exc:
        raise ReceiptSamplingError(f'Cannot write plan: {path}') from exc


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get('schema') != SCHEMA or plan.get('version') != 1:
        raise ReceiptSamplingError('Receipt sampling plan schema is unsupported or malformed.')
    _, duration = _require_source(plan.get('source'), 'plan.source')
    for section in ('report', 'capture'):
        if not isinstance(plan.get(section), dict):
            raise ReceiptSamplingError(f'plan.{section} is malformed.')
        _require_digest(plan[section].get('sha256'), f'plan.{section}.sha256')
    policy = plan.get('policy')
    if not isinstance(policy, dict):
        raise ReceiptSamplingError('plan.policy is malformed.')
    policy_values = {}
    for name in ('pad_ms', 'merge_gap_ms', 'fps', 'min_inspection_fps',
                 'max_footage_ms', 'max_frames', 'max_window_ms'):
        policy_values[name] = _require_int(policy.get(name), f'plan.policy.{name}')
    _validate_policy(**policy_values)
    if policy.get('no_expected_labels_to_ocr') is not True:
        raise ReceiptSamplingError('plan.policy.no_expected_labels_to_ocr must be true.')
    inspection = plan.get('inspection')
    if not isinstance(inspection, dict):
        raise ReceiptSamplingError('plan.inspection is malformed.')
    if inspection.get('sha256') is not None:
        _require_digest(inspection.get('sha256'), 'plan.inspection.sha256')
    minimum_fps = _require_int(inspection.get('minimum_sufficient_fps'),
                               'plan.inspection.minimum_sufficient_fps')
    if minimum_fps != max(policy_values['fps'], policy_values['min_inspection_fps']):
        raise ReceiptSamplingError('plan.inspection.minimum_sufficient_fps disagrees with policy.')
    windows = plan.get('windows')
    if not isinstance(windows, list):
        raise ReceiptSamplingError('plan.windows is malformed.')
    total_footage = 0
    total_frames = 0
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ReceiptSamplingError(f'plan.windows[{index}] is malformed.')
        start = _require_int(window.get('start_ms'), f'plan.windows[{index}].start_ms', minimum=0)
        end = _require_int(window.get('end_ms'), f'plan.windows[{index}].end_ms', minimum=0)
        fps = _require_int(window.get('fps'), f'plan.windows[{index}].fps', minimum=4)
        if start >= end or end > duration or end - start > policy_values['max_window_ms'] or fps > 60:
            raise ReceiptSamplingError(f'plan.windows[{index}] is outside supported bounds.')
        if fps != policy_values['fps']:
            raise ReceiptSamplingError(f'plan.windows[{index}].fps disagrees with plan.policy.fps.')
        footage = end - start
        frames = _estimated_frames(start, end, fps)
        if type(window.get('estimated_footage_ms')) is not int or window['estimated_footage_ms'] != footage:
            raise ReceiptSamplingError(f'plan.windows[{index}] has an invalid footage estimate.')
        if type(window.get('estimated_frames')) is not int or window['estimated_frames'] != frames:
            raise ReceiptSamplingError(f'plan.windows[{index}] has an invalid frame estimate.')
        total_footage += footage
        total_frames += frames
    if total_footage > policy_values['max_footage_ms']:
        raise ReceiptSamplingError('Plan windows exceed the declared footage budget.')
    if total_frames > policy_values['max_frames']:
        raise ReceiptSamplingError('Plan windows exceed the declared frame budget.')
    limits = plan.get('limits')
    if not isinstance(limits, dict) or limits.get('maximum_window_ms') != policy_values['max_window_ms'] \
            or limits.get('footage_budget_ms') != policy_values['max_footage_ms'] \
            or limits.get('frame_budget') != policy_values['max_frames']:
        raise ReceiptSamplingError('plan.limits disagrees with plan.policy.')
    summary = plan.get('summary')
    if not isinstance(summary, dict) \
            or summary.get('selected_window_count') != len(windows) \
            or summary.get('selected_footage_ms') != total_footage \
            or summary.get('estimated_frames') != total_frames:
        raise ReceiptSamplingError('plan.summary does not match its windows.')
    paths = plan.get('paths')
    if not isinstance(paths, dict):
        raise ReceiptSamplingError('plan.paths is required for execution.')
    for key in ('source', 'report', 'capture', 'receipt_inspection', 'run_root'):
        if not isinstance(paths.get(key), str) or not paths[key]:
            raise ReceiptSamplingError(f'plan.paths.{key} is required.')
    return plan


def execute_plan(plan_path: str | Path, *, source: str | Path | None = None,
                 report_path: str | Path | None = None) -> dict[str, Any]:
    plan_path = Path(plan_path).resolve()
    plan, plan_file_sha256 = _load_json(plan_path, 'Receipt sampling plan')
    plan = _validate_plan(plan)
    source_path = Path(source).resolve() if source is not None else Path(plan['paths']['source']).resolve()
    report_path = Path(report_path).resolve() if report_path is not None else Path(plan['paths']['report']).resolve()
    capture_path = Path(plan['paths']['capture']).resolve()
    run_root = Path(plan['paths']['run_root']).resolve()
    try:
        report_path.relative_to(run_root)
    except ValueError as exc:
        raise ReceiptSamplingError('Plan report must be inside its evidence root.') from exc
    if capture_path != run_root / 'capture.json':
        raise ReceiptSamplingError('Plan capture path must be the evidence root capture.json.')
    inspection_path = Path(plan['paths']['receipt_inspection']).resolve()
    if inspection_path != run_root / 'receipt-inspection.json':
        raise ReceiptSamplingError('Plan receipt-inspection path does not match its run root.')
    if _sha256_file(source_path) != plan['source']['sha256']:
        raise ReceiptSamplingError('Plan source identity is stale or mismatched.')
    report, report_sha256 = _load_json(report_path, 'Report')
    capture, capture_sha256 = _load_json(capture_path, 'Capture')
    duration = _validate_bound_inputs(report, capture, plan['source']['sha256'])
    if duration != plan['source']['duration_ms']:
        raise ReceiptSamplingError('Plan source duration is stale.')
    if report_sha256 != plan['report']['sha256']:
        raise ReceiptSamplingError('Plan report identity is stale; create a new plan.')
    if capture_sha256 != plan['capture']['sha256']:
        raise ReceiptSamplingError('Plan capture identity is stale; create a new plan.')
    expected_inspection_sha256 = plan['inspection'].get('sha256')
    current_inspection_sha256 = _sha256_file(inspection_path) if inspection_path.exists() else None
    if current_inspection_sha256 != expected_inspection_sha256:
        raise ReceiptSamplingError('Existing receipt inspection identity changed; plan is stale.')
    report_before = report_sha256
    from . import inspect_receipts

    calls = []
    for window in plan['windows']:
        inspect_receipts.inspect(source_path, run_root, window['start_ms'], window['end_ms'], window['fps'])
        calls.append(dict(start_ms=window['start_ms'], end_ms=window['end_ms'], fps=window['fps']))
    if calls:
        # inspect_receipts records a window even when every OCR reading is
        # rejected.  Verify that postcondition so an unsuccessful probe is
        # still remembered as inspected and cannot be selected forever.
        inspection, _ = _load_json(inspection_path, 'Receipt inspection')
        inspection = _validate_inspection(inspection, plan['source']['sha256'], duration)
        recorded = {(row['start_ms'], row['end_ms'], row['fps']) for row in inspection['windows']}
        missing = [row for row in calls
                   if (row['start_ms'], row['end_ms'], row['fps']) not in recorded]
        if missing:
            raise ReceiptSamplingError('Receipt inspector did not persist attempted window coverage.')
    report_after = _sha256_file(report_path)
    if report_after != report_before:
        raise ReceiptSamplingError('Receipt inspection changed report.json; refusing to publish execution result.')
    return dict(plan_sha256=plan_file_sha256, status='executed', windows=calls,
                selected_window_count=len(calls), report_sha256=report_after,
                report_unchanged=True, caller_must_reparse_report=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, nargs='?')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--evidence-root', type=Path,
                        help='Directory containing capture.json and receipt-inspection.json.')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute', action='store_true', help='Run inspect_receipts for planned windows after saving the plan.')
    parser.add_argument('--execute-plan', type=Path, help='Execute an existing source-bound plan.')
    parser.add_argument('--pad-ms', type=int, default=DEFAULT_PAD_MS)
    parser.add_argument('--merge-gap-ms', type=int, default=DEFAULT_MERGE_GAP_MS)
    parser.add_argument('--fps', type=int, default=DEFAULT_FPS)
    parser.add_argument('--min-inspection-fps', type=int, default=DEFAULT_MIN_INSPECTION_FPS)
    parser.add_argument('--max-footage-ms', type=int, default=DEFAULT_MAX_FOOTAGE_MS)
    parser.add_argument('--max-frames', type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument('--max-window-ms', type=int, default=MAX_WINDOW_MS)
    args = parser.parse_args()
    try:
        if args.execute_plan is not None:
            if args.execute or args.output is not None:
                parser.error('--execute-plan cannot be combined with --execute or --output.')
            result = execute_plan(args.execute_plan, source=args.source, report_path=args.report)
            print(json.dumps(result, ensure_ascii=False))
            return
        if args.source is None or args.report is None:
            parser.error('SOURCE and --report are required when creating a plan.')
        plan_path, plan = create_plan(
            args.source, args.report, output=args.output, pad_ms=args.pad_ms,
            merge_gap_ms=args.merge_gap_ms, fps=args.fps,
            min_inspection_fps=args.min_inspection_fps,
            max_footage_ms=args.max_footage_ms, max_frames=args.max_frames,
            max_window_ms=args.max_window_ms, run_root=args.evidence_root)
        result = dict(plan_path=str(plan_path), summary=plan['summary'], status='dry_run')
        if args.execute:
            result['execution'] = execute_plan(plan_path, source=args.source, report_path=args.report)
        print(json.dumps(result, ensure_ascii=False))
    except ReceiptSamplingError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
