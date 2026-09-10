"""Validate the versioned full-recording report envelope.

The validator checks the structure needed to load a report and its source-linked
timeline. It deliberately does not require semantic completeness: unknown OCR
fields, partial inventory observations, and unverified mechanics remain valid
report values.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping


FULL_RECORDING_SCHEMA = "tracen-replay/full-recording-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_TIME_BASE = re.compile(r"^(\d+)/(\d+)$")


class ReportContractError(ValueError):
    """Raised when a report cannot be safely consumed as a full-recording report."""


def _error(path, message):
    raise ReportContractError(f"{path}: {message}")


def _object(value, path):
    if not isinstance(value, Mapping):
        _error(path, "must be an object")
    return value


def _array(value, path):
    if not isinstance(value, list):
        _error(path, "must be an array")
    return value


def _required(mapping, key, path):
    if key not in mapping:
        _error(f"{path}.{key}", "is required")
    return mapping[key]


def _text(value, path, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _error(path, "must be non-empty text")
    return value


def _integer(value, path, *, minimum=None):
    if type(value) is not int:
        _error(path, "must be an integer")
    if minimum is not None and value < minimum:
        _error(path, f"must be at least {minimum}")
    return value


def _number(value, path, *, minimum=None, positive=False):
    if type(value) not in (int, float):
        _error(path, "must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        _error(path, "must be a finite number")
    if positive and number <= 0:
        _error(path, "must be greater than 0")
    if minimum is not None and number < minimum:
        _error(path, f"must be at least {minimum}")
    return number


def _boolean(value, path):
    if type(value) is not bool:
        _error(path, "must be a boolean")
    return value


def _sha256(value, path):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _error(path, "must be a 64-character SHA-256 hex digest")
    return value


def _text_array(value, path):
    values = _array(value, path)
    for index, item in enumerate(values):
        _text(item, f"{path}[{index}]")
    return values


def _object_array(value, path):
    values = _array(value, path)
    for index, item in enumerate(values):
        _object(item, f"{path}[{index}]")
    return values


def _validate_source(report):
    source = _object(_required(report, "source", "report"), "report.source")
    _text(_required(source, "name", "report.source"), "report.source.name")
    _sha256(_required(source, "sha256", "report.source"), "report.source.sha256")
    duration = _integer(_required(source, "duration_ms", "report.source"),
                        "report.source.duration_ms", minimum=1)
    _integer(_required(source, "size_bytes", "report.source"),
             "report.source.size_bytes", minimum=0)
    _number(_required(source, "timeline_origin_seconds", "report.source"),
            "report.source.timeline_origin_seconds")
    _integer(_required(source, "width", "report.source"), "report.source.width", minimum=1)
    _integer(_required(source, "height", "report.source"), "report.source.height", minimum=1)
    codec = _required(source, "codec", "report.source")
    if codec is not None:
        _text(codec, "report.source.codec")
    return duration


def _validate_clip(report, source_duration):
    clip = _object(_required(report, "clip", "report"), "report.clip")
    start = _integer(_required(clip, "source_start_ms", "report.clip"),
                     "report.clip.source_start_ms", minimum=0)
    duration = _integer(_required(clip, "duration_ms", "report.clip"),
                        "report.clip.duration_ms", minimum=1)
    if start + duration > source_duration:
        _error("report.clip", "source interval exceeds report.source.duration_ms")


def _validate_frames(report, source_duration):
    sampling = _object(_required(report, "sampling", "report"), "report.sampling")
    _number(_required(sampling, "requested_fps", "report.sampling"),
            "report.sampling.requested_fps", positive=True)
    frame_count = _integer(_required(sampling, "frame_count", "report.sampling"),
                           "report.sampling.frame_count", minimum=1)
    _text(_required(sampling, "method", "report.sampling"), "report.sampling.method")
    _boolean(_required(sampling, "guarantees_all_events", "report.sampling"),
             "report.sampling.guarantees_all_events")

    frames = _object_array(_required(report, "frames", "report"), "report.frames")
    if not frames:
        _error("report.frames", "must contain at least one sampled frame")
    if frame_count != len(frames):
        _error("report.sampling.frame_count", "must equal the number of frames")

    ids = set()
    evidence = set()
    previous_timestamp = None
    for index, frame in enumerate(frames):
        path = f"report.frames[{index}]"
        frame_id = _text(_required(frame, "id", path), f"{path}.id")
        if frame_id in ids:
            _error(f"{path}.id", "must be unique")
        ids.add(frame_id)
        evidence_path = _text(_required(frame, "evidence", path), f"{path}.evidence")
        if evidence_path in evidence:
            _error(f"{path}.evidence", "must identify a unique frame proof")
        evidence.add(evidence_path)
        timestamp = _integer(_required(frame, "source_timestamp_ms", path),
                             f"{path}.source_timestamp_ms", minimum=0)
        if timestamp > source_duration:
            _error(f"{path}.source_timestamp_ms", "exceeds source duration")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            _error(f"{path}.source_timestamp_ms", "must be strictly increasing")
        previous_timestamp = timestamp
        _integer(_required(frame, "clip_timestamp_ms", path),
                 f"{path}.clip_timestamp_ms", minimum=0)
        _integer(_required(frame, "source_pts", path), f"{path}.source_pts")
        time_base = _text(_required(frame, "time_base", path), f"{path}.time_base")
        match = _TIME_BASE.fullmatch(time_base)
        if not match or int(match[1]) <= 0 or int(match[2]) <= 0:
            _error(f"{path}.time_base", "must be a positive rational such as '1/60'")


def _validate_recognition(report):
    recognition = report.get("recognition")
    if recognition is None:
        return
    recognition = _object(recognition, "report.recognition")
    _boolean(_required(recognition, "enabled", "report.recognition"),
             "report.recognition.enabled")
    model = _required(recognition, "model", "report.recognition")
    if model is not None:
        _text(model, "report.recognition.model")


_GAMEPLAY_ARRAYS = (
    "readings", "screens", "checkpoints", "events", "intervals",
    "training_previews", "dialogue_choices", "turn_action_receipts",
    "lesson_purchases", "skill_purchases", "skill_receipts", "concerts",
    "races", "song_acquisitions", "unparsed_receipt_candidates",
    "lesson_debit_observations",
)
_GAMEPLAY_OBJECTS = ("performance_accounting", "fan_accounting", "owned_skill_inventory")


def _validate_gameplay(report, *, required):
    if "gameplay_tracking" not in report:
        if required:
            _error("report.gameplay_tracking", "is required for an analyzed report")
        return
    data = _object(report["gameplay_tracking"], "report.gameplay_tracking")
    _text(_required(data, "method", "report.gameplay_tracking"),
          "report.gameplay_tracking.method")
    _boolean(_required(data, "auxiliary_log_used", "report.gameplay_tracking"),
             "report.gameplay_tracking.auxiliary_log_used")
    if data["auxiliary_log_used"] is not False:
        _error("report.gameplay_tracking.auxiliary_log_used",
               "must be false for the gameplay-only full-recording contract")
    input_region = _array(_required(data, "input_region", "report.gameplay_tracking"),
                          "report.gameplay_tracking.input_region")
    if len(input_region) != 4:
        _error("report.gameplay_tracking.input_region", "must contain four coordinates")
    for index, coordinate in enumerate(input_region):
        _integer(coordinate, f"report.gameplay_tracking.input_region[{index}]", minimum=0)
    for key in _GAMEPLAY_ARRAYS:
        _object_array(_required(data, key, "report.gameplay_tracking"),
                      f"report.gameplay_tracking.{key}")
    for key in _GAMEPLAY_OBJECTS:
        _object(_required(data, key, "report.gameplay_tracking"),
                f"report.gameplay_tracking.{key}")
    if "hint_card_observations" in data:
        _object_array(data["hint_card_observations"], "report.gameplay_tracking.hint_card_observations")
    if "hint_card_recovery" in data:
        recovery = _object(data["hint_card_recovery"], "report.gameplay_tracking.hint_card_recovery")
        for key in ("accepted", "rejected"):
            _object_array(_required(recovery, key, "report.gameplay_tracking.hint_card_recovery"),
                          f"report.gameplay_tracking.hint_card_recovery.{key}")
    inventory = data["owned_skill_inventory"]
    _boolean(_required(inventory, "complete", "report.gameplay_tracking.owned_skill_inventory"),
             "report.gameplay_tracking.owned_skill_inventory.complete")
    _object_array(_required(inventory, "observed_owned_cards",
                             "report.gameplay_tracking.owned_skill_inventory"),
                  "report.gameplay_tracking.owned_skill_inventory.observed_owned_cards")
    _object_array(_required(inventory, "summary_frames",
                             "report.gameplay_tracking.owned_skill_inventory"),
                  "report.gameplay_tracking.owned_skill_inventory.summary_frames")
    _text(_required(inventory, "scope", "report.gameplay_tracking.owned_skill_inventory"),
          "report.gameplay_tracking.owned_skill_inventory.scope")
    _text_array(_required(inventory, "unresolved",
                          "report.gameplay_tracking.owned_skill_inventory"),
                "report.gameplay_tracking.owned_skill_inventory.unresolved")


def validate(report, *, require_gameplay=False):
    """Validate and return a full-recording report without changing its values.

    The default validates the persisted capture envelope. ``require_gameplay``
    additionally requires the analyzed gameplay section used by the report
    viewer. The function intentionally accepts null/unknown semantic fields.
    """
    report = _object(report, "report")
    schema = _required(report, "schema_version", "report")
    if schema != FULL_RECORDING_SCHEMA:
        _error("report.schema_version", f"unsupported value {schema!r}")
    source_duration = _validate_source(report)
    _validate_clip(report, source_duration)
    _validate_frames(report, source_duration)
    _object_array(_required(report, "observations", "report"), "report.observations")
    _text_array(_required(report, "limitations", "report"), "report.limitations")
    _validate_recognition(report)
    _validate_gameplay(report, required=require_gameplay)
    for key in ("verification", "evidence_integrity_snapshot"):
        if key in report and report[key] is not None:
            _object(report[key], f"report.{key}")
    if 'turn_ledger' in report:
        from .turn_ledger import build as build_turn_ledger
        try:
            expected = build_turn_ledger(report)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            _error('report.turn_ledger', f'cannot project core report records: {exc}')
        if report['turn_ledger'] != expected:
            _error('report.turn_ledger', 'does not match the source report collections')
    return report
