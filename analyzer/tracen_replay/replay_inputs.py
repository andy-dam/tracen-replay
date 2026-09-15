"""Validate and normalize source-bound cached replay inputs.

The full-recording worker has historically consumed a few fixed cache names,
while the independent replay configurations can keep an inspection below a
separate directory.  This module is the small contract between those layouts.
It validates paths and hashes without opening an accepted report or copying
any of its values.  A later producer can use the normalized paths to invoke
the existing parsers in each entry's declared ``own_root``.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SCHEMA = "tracen-replay/replay-input-manifest-v1"
MERGE_KINDS = frozenset({"training", "receipt"})
RECOVERY_KINDS = frozenset({
    "numeric_receipt",
    "training_gain",
    "boundary_state",
    "occluded_receipt",
})
SUPPLEMENT_KINDS = frozenset({"song_symbols", "currency_regions", "race_identity", "raw_sidecars"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "source_sha256", "base", "inspections", "recovery", "supplements", "reference"}
)


class ReplayInputError(ValueError):
    """A cache manifest failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ReplayInputError(code, message)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_manifest", f"{field} must be an object.")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("invalid_manifest", f"{field} must be an array.")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        _fail("invalid_manifest", f"Unsupported {field} keys: {', '.join(sorted(map(str, unknown)))}")


def _hash_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail("invalid_hash", f"{field} must be a lowercase SHA-256 digest.")
    return value


def _canonical_hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("invalid_json", f"Could not fingerprint JSON value: {exc}")
    return hashlib.sha256(encoded).hexdigest()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        _fail("file_unreadable", f"Could not read {path}: {exc}")
    return digest.hexdigest()


def _relative(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        _fail("invalid_path", f"{field} must be a relative path.")
    if not value:
        return ""
    if "\x00" in value:
        _fail("invalid_path", f"{field} contains an invalid path character.")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or ".." in posix.parts
    ):
        _fail("path_outside_root", f"{field} must stay below the cache root.")
    return posix.as_posix()


def _join(*parts: str) -> str:
    values = [part.strip("/") for part in parts if part]
    return "/".join(values)


def _root_path(root: Path | str) -> Path:
    try:
        candidate = Path(root).expanduser()
        cursor = candidate
        while True:
            info = cursor.lstat()
            if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
                _fail("reparse_point", "Cache root cannot contain a symlink or other reparse point.")
            if cursor == cursor.parent:
                break
            cursor = cursor.parent
        resolved = candidate.resolve()
    except ReplayInputError:
        raise
    except FileNotFoundError as exc:
        _fail("missing_root", f"Cache root does not exist: {exc}")
    except (OSError, RuntimeError, ValueError) as exc:
        _fail("invalid_root", f"Cache root is not resolvable: {exc}")
    if not resolved.is_dir():
        _fail("missing_root", "Cache root must be an existing directory.")
    return resolved


def _assert_no_reparse(root: Path, path: Path, field: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        _fail("path_outside_root", f"{field} leaves the cache root.")
    cursor = root
    for component in relative.parts:
        cursor /= component
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            # The caller reports the more useful file_missing or
            # directory_missing error after this structural check.
            return
        except OSError as exc:
            _fail("file_unreadable", f"Could not inspect {field}: {exc}")
        if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            _fail("reparse_point", f"{field} contains a symlink or other reparse point.")


def _file(root: Path, relative: str, field: str) -> tuple[Path, str]:
    normalized = _relative(relative, field)
    path = root / Path(normalized)
    _assert_no_reparse(root, path, field)
    if not path.is_file():
        _fail("file_missing", f"{field} must identify an existing file.")
    return path, normalized


def _directory(root: Path, relative: str, field: str) -> tuple[Path, str]:
    normalized = _relative(relative, field, allow_empty=True)
    path = root / Path(normalized)
    if normalized:
        _assert_no_reparse(root, path, field)
    if not path.is_dir():
        _fail("directory_missing", f"{field} must identify an existing directory.")
    return path, normalized


def _json_file(path: Path, field: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", f"{field} is not readable JSON: {exc}")
    return _mapping(value, field)


def _checked_hash(path: Path, expected: Any, field: str) -> str:
    value = _hash_value(expected, field)
    actual = _digest(path)
    if actual != value:
        _fail("hash_mismatch", f"{field} does not match {path}.")
    return value


def _source(payload: Mapping[str, Any], source_sha256: str, field: str) -> None:
    declared = payload.get("source_sha256")
    if declared != source_sha256:
        _fail("source_mismatch", f"{field}.source_sha256 does not match the manifest source.")


def _load_bound_json(root: Path | str, relative: str, expected: str, field: str) -> dict[str, Any]:
    cache_root = _root_path(root)
    path, _ = _file(cache_root, relative, field)
    expected = _hash_value(expected, field + "_sha256")
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        _fail("file_unreadable", f"Could not read {field}: {exc}")
    if hashlib.sha256(encoded).hexdigest() != expected:
        _fail("hash_mismatch", f"{field}_sha256 does not match the registered file.")
    try:
        return dict(_mapping(json.loads(encoded.decode("utf-8")), field))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", f"{field} is not readable JSON: {exc}")


def load_bound_inspection_manifest(root: Path | str, manifest: str, manifest_sha256: str) -> dict[str, Any]:
    """Recheck inspection bytes at consumption, including direct helper calls."""
    payload = _load_bound_json(root, manifest, manifest_sha256, "inspection.manifest")
    _list(payload.get("readings"), "inspection.manifest.readings")
    return payload


def load_bound_recovery_plan(
    root: Path | str, plan: str, plan_sha256: str,
    *, source_sha256: str, manifest_sha256: str,
) -> dict[str, Any]:
    """Read the exact recovery plan registered beside its inspection proof.

    The inspection hash binds captured rows, while this separate hash binds
    the trigger geometry and ownership scope used to select those rows.
    Validate again at consumption so edits after manifest normalization fail.
    """
    source_sha256 = _hash_value(source_sha256, "recovery.source_sha256")
    manifest_sha256 = _hash_value(manifest_sha256, "recovery.manifest_sha256")
    payload = _load_bound_json(root, plan, plan_sha256, "recovery.plan")
    _source(payload, source_sha256, "recovery.plan")
    if payload.get("manifest_sha256") != manifest_sha256:
        _fail("hash_mismatch", "Recovery plan belongs to another inspection manifest.")
    return dict(payload)


def _timestamp(value: Any, field: str, duration_ms: int | None) -> int:
    if type(value) is not int or value < 0:
        _fail("timestamp_invalid", f"{field} must be a non-negative integer.")
    if duration_ms is not None and value > duration_ms:
        _fail("timestamp_out_of_range", f"{field} is outside the source duration.")
    return value


def _windows(payload: Mapping[str, Any], field: str, duration_ms: int | None) -> list[dict[str, Any]] | None:
    value = payload.get("windows")
    if value is None:
        return None
    windows = _list(value, field + ".windows")
    normalized: list[dict[str, Any]] = []
    for index, window_value in enumerate(windows):
        window = _mapping(window_value, f"{field}.windows[{index}]")
        start = _timestamp(window.get("start_ms"), f"{field}.windows[{index}].start_ms", duration_ms)
        end = _timestamp(window.get("end_ms"), f"{field}.windows[{index}].end_ms", duration_ms)
        if end <= start:
            _fail("timestamp_invalid", f"{field}.windows[{index}] must end after it starts.")
        fps = window.get("fps")
        if fps is not None and (type(fps) is not int or fps <= 0):
            _fail("invalid_manifest", f"{field}.windows[{index}].fps must be positive when present.")
        normalized_window = {"start_ms": start, "end_ms": end}
        if fps is not None:
            normalized_window["fps"] = fps
        normalized.append(normalized_window)
    return normalized


def _safe_basename(value: Any, field: str) -> str:
    result = _relative(value, field)
    if "/" in result:
        _fail("invalid_path", f"{field} must be a basename.")
    return result


def _normalize_base(spec: Mapping[str, Any], root: Path, source_sha256: str) -> tuple[dict[str, Any], int | None]:
    _only_keys(spec, {"folder", "capture", "neural", "capture_sha256", "frame_count"}, "base")
    folder = _relative(spec.get("folder", ""), "base.folder", allow_empty=True)
    capture_rel = _relative(spec.get("capture"), "base.capture")
    neural_rel = _relative(spec.get("neural"), "base.neural")
    capture_path, capture_name = _file(root, _join(folder, capture_rel), "base.capture")
    _, neural_name = _directory(root, _join(folder, neural_rel), "base.neural")
    capture_hash = _checked_hash(capture_path, spec.get("capture_sha256"), "base.capture_sha256")
    capture = _json_file(capture_path, "base.capture")
    source = _mapping(capture.get("source"), "base.capture.source")
    if source.get("sha256") != source_sha256:
        _fail("source_mismatch", "base.capture.source.sha256 does not match the manifest source.")
    duration = source.get("duration_ms")
    if duration is not None and (type(duration) is not int or duration <= 0):
        _fail("invalid_manifest", "base.capture.source.duration_ms must be positive when present.")
    frames = _list(capture.get("frames"), "base.capture.frames")
    expected_count = spec.get("frame_count", len(frames))
    if type(expected_count) is not int or expected_count != len(frames):
        _fail("count_mismatch", "base.frame_count does not match capture.frames.")
    seen_ids: set[str] = set()
    seen_times: set[int] = set()
    normalized_frames: list[dict[str, Any]] = []
    for index, frame_value in enumerate(frames):
        frame = _mapping(frame_value, f"base.capture.frames[{index}]")
        frame_id = _safe_basename(frame.get("id"), f"base.capture.frames[{index}].id")
        timestamp = _timestamp(frame.get("source_timestamp_ms"), f"base.capture.frames[{index}].source_timestamp_ms", duration)
        if frame_id in seen_ids or timestamp in seen_times:
            _fail("duplicate_frame", f"base.capture.frames[{index}] duplicates a frame identity or timestamp.")
        seen_ids.add(frame_id)
        seen_times.add(timestamp)
        frame_evidence = _relative(frame.get("evidence"), f"base.capture.frames[{index}].evidence")
        frame_evidence_path, frame_evidence_name = _file(root, _join(folder, frame_evidence), f"base.capture.frames[{index}].evidence")
        raw_rel = _join(folder, neural_rel, frame_id + ".json")
        raw_path, raw_name = _file(root, raw_rel, f"base.neural/{frame_id}.json")
        raw = _json_file(raw_path, f"base.neural/{frame_id}.json")
        if raw.get("source_timestamp_ms") != timestamp:
            _fail("timestamp_mismatch", f"base.neural/{frame_id}.json timestamp differs from capture.")
        raw_evidence = _relative(raw.get("evidence"), f"base.neural/{frame_id}.json.evidence")
        raw_evidence_path, raw_evidence_name = _file(root, _join(folder, raw_evidence), f"base.neural/{frame_id}.json.evidence")
        raw_source = raw.get("source_sha256")
        if raw_source is not None and raw_source != source_sha256:
            _fail("source_mismatch", f"base.neural/{frame_id}.json.source_sha256 does not match the manifest source.")
        source_frame_hash = _hash_value(
            raw.get("source_frame_sha256"), f"base.neural/{frame_id}.json.source_frame_sha256"
        )
        if source_frame_hash != _digest(frame_evidence_path):
            _fail("hash_mismatch", f"base.neural/{frame_id}.json.source_frame_sha256 does not match the capture frame.")
        normalized_frames.append(
            {
                "id": frame_id,
                "source_timestamp_ms": timestamp,
                "evidence": frame_evidence_name,
                "raw": raw_name,
                "raw_evidence": raw_evidence_name,
            }
        )
    return (
        {
            "folder": folder,
            "capture": capture_name,
            "capture_sha256": capture_hash,
            "neural": neural_name,
            "frame_count": len(normalized_frames),
            "frames": normalized_frames,
        },
        duration,
    )


def _frame_manifest(root: Path, evidence_path: Path, field: str, timestamp: int) -> str:
    frames_path = evidence_path.parent / "frames.json"
    # Inspection frame manifests are arrays, unlike capture.json.  Keep this
    # parser separate from _json_file, whose object-only return type is useful
    # for the surrounding manifests and sidecars.
    if not frames_path.exists():
        _assert_no_reparse(root, frames_path, field + ".frames.json")
        _fail("file_missing", f"{field}.frames.json is missing.")
    _assert_no_reparse(root, frames_path, field + ".frames.json")
    if not frames_path.is_file():
        _fail("file_missing", f"{field}.frames.json is not a file.")
    try:
        raw_value = json.loads(frames_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", f"{field}.frames.json is not readable JSON: {exc}")
    frames = _list(raw_value, field + ".frames.json")
    stem = evidence_path.stem
    matches = [frame for frame in frames if isinstance(frame, Mapping) and frame.get("id") == stem]
    if len(matches) != 1:
        _fail("frame_missing", f"{field} has no unique source frame for {stem}.")
    frame = matches[0]
    if frame.get("source_timestamp_ms") != timestamp:
        _fail("timestamp_mismatch", f"{field}.frames.json timestamp differs from the reading.")
    source_frame = _relative(frame.get("evidence"), field + ".frames.json.evidence")
    source_path = evidence_path.parent / Path(source_frame)
    _assert_no_reparse(root, source_path, field + ".frames.json.evidence")
    if not source_path.is_file():
        _fail("file_missing", f"{field}.frames.json.evidence is missing.")
    return source_path.relative_to(root).as_posix()


def _normalize_rows(
    readings: Sequence[Any],
    *,
    root: Path,
    source_sha256: str,
    duration_ms: int | None,
    folder: str,
    own_root: str,
    row_evidence_root: str,
    field: str,
    allow_timestamp_views: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    seen_timestamps: set[int] = set()
    for index, row_value in enumerate(readings):
        row = _mapping(row_value, f"{field}.readings[{index}]")
        timestamp = _timestamp(row.get("source_timestamp_ms"), f"{field}.readings[{index}].source_timestamp_ms", duration_ms)
        evidence = _relative(row.get("evidence"), f"{field}.readings[{index}].evidence")
        evidence_rel = evidence if row_evidence_root == "root" else _join(folder, evidence)
        evidence_path, evidence_name = _file(root, evidence_rel, f"{field}.readings[{index}].evidence")
        key = (timestamp, evidence_name)
        if key in seen or (timestamp in seen_timestamps and not allow_timestamp_views):
            _fail("duplicate_reading", f"{field}.readings[{index}] duplicates a reading.")
        seen.add(key)
        seen_timestamps.add(timestamp)
        raw_candidate = evidence_path.with_suffix(".v2.json")
        if not raw_candidate.is_file():
            raw_candidate = evidence_path.with_suffix(".json")
        raw_path, raw_name = _file(root, raw_candidate.relative_to(root).as_posix(), f"{field}.readings[{index}].raw")
        raw = _json_file(raw_path, f"{field}.readings[{index}].raw")
        if raw.get("source_timestamp_ms") != timestamp:
            _fail("timestamp_mismatch", f"{field}.readings[{index}] and its raw sidecar have different timestamps.")
        raw_source = _hash_value(raw.get("source_sha256"), f"{field}.readings[{index}].raw.source_sha256")
        if raw_source != source_sha256:
            _fail("source_mismatch", f"{field}.readings[{index}] raw sidecar belongs to another source.")
        raw_evidence = _relative(raw.get("evidence"), f"{field}.readings[{index}].raw.evidence")
        raw_evidence_path, raw_evidence_name = _file(root, _join(own_root, raw_evidence), f"{field}.readings[{index}].raw.evidence")
        if raw_evidence_path != evidence_path:
            _fail("path_mismatch", f"{field}.readings[{index}] raw evidence does not match its manifest evidence.")
        source_frame = _frame_manifest(root, evidence_path, f"{field}.readings[{index}]", timestamp)
        source_frame_hash = _hash_value(
            raw.get("source_frame_sha256"), f"{field}.readings[{index}].raw.source_frame_sha256"
        )
        if source_frame_hash != _digest(root / Path(source_frame)):
            _fail("hash_mismatch", f"{field}.readings[{index}].raw.source_frame_sha256 does not match its source frame.")
        result.append(
            {
                "source_timestamp_ms": timestamp,
                "evidence": evidence_name,
                "raw": raw_name,
                "own_root": own_root,
                "raw_evidence": raw_evidence_name,
                "source_frame": source_frame,
            }
        )
    return result


def _normalize_manifest_group(
    spec: Mapping[str, Any],
    *,
    root: Path,
    source_sha256: str,
    duration_ms: int | None,
    field: str,
    require_merge: bool,
) -> dict[str, Any]:
    _only_keys(
        spec,
        {"id", "kind", "folder", "own_root", "manifest", "manifest_sha256", "row_evidence_root", "row_count", "row_indices", "merge", "plan", "plan_sha256"},
        field,
    )
    folder = _relative(spec.get("folder", ""), field + ".folder", allow_empty=True)
    own_root = _relative(spec.get("own_root", folder), field + ".own_root", allow_empty=True)
    manifest = _relative(spec.get("manifest"), field + ".manifest")
    manifest_path, manifest_name = _file(root, _join(folder, manifest), field + ".manifest")
    manifest_sha256 = _checked_hash(manifest_path, spec.get("manifest_sha256"), field + ".manifest_sha256")
    payload = _json_file(manifest_path, field + ".manifest")
    _source(payload, source_sha256, field + ".manifest")
    readings = _list(payload.get("readings"), field + ".manifest.readings")
    windows = _windows(payload, field + ".manifest", duration_ms)
    row_indices_value = spec.get("row_indices")
    if row_indices_value is None:
        row_indices = list(range(len(readings)))
    else:
        row_indices = []
        for index, value in enumerate(_list(row_indices_value, field + ".row_indices")):
            if type(value) is not int or value < 0 or value >= len(readings):
                _fail("invalid_manifest", f"{field}.row_indices[{index}] is outside the manifest readings.")
            if value in row_indices:
                _fail("duplicate_reading", f"{field}.row_indices[{index}] duplicates a manifest reading.")
            row_indices.append(value)
        if not row_indices:
            _fail("invalid_manifest", f"{field}.row_indices must not be empty.")
    selected_readings = [readings[index] for index in row_indices]
    row_count = spec.get("row_count", len(selected_readings))
    if type(row_count) is not int or row_count != len(selected_readings):
        _fail("count_mismatch", f"{field}.row_count does not match its selected manifest readings.")
    row_evidence_root = spec.get("row_evidence_root", "folder")
    if row_evidence_root not in {"folder", "root"}:
        _fail("invalid_manifest", f"{field}.row_evidence_root must be folder or root.")
    merge = spec.get("merge")
    if require_merge:
        if merge not in MERGE_KINDS:
            _fail("invalid_merge_kind", f"{field}.merge must be training or receipt.")
    elif merge is not None and merge not in MERGE_KINDS:
        _fail("invalid_merge_kind", f"{field}.merge must be training or receipt when present.")
    normalized_rows = _normalize_rows(
        selected_readings,
        root=root,
        source_sha256=source_sha256,
        duration_ms=duration_ms,
        folder=folder,
        own_root=own_root,
        row_evidence_root=row_evidence_root,
        field=field,
        # Capped recovery windows can overlap and decode the same timestamp
        # into distinct source-bound views. Keep those views for scoped
        # receipt merging; exact repeated evidence remains invalid. Other
        # input groups retain their one-reading-per-timestamp contract.
        allow_timestamp_views=not require_merge and spec.get("kind") == "occluded_receipt",
    )
    result = {
        "folder": folder,
        "own_root": own_root,
        "manifest": manifest_name,
        "manifest_sha256": manifest_sha256,
        "row_evidence_root": row_evidence_root,
        "row_count": len(normalized_rows),
        "row_indices": row_indices,
        "rows": normalized_rows,
    }
    if windows is not None:
        result["windows"] = windows
    if merge is not None:
        result["merge"] = merge
    if spec.get("kind") == "occluded_receipt":
        plan = _relative(spec.get("plan"), field + ".plan")
        plan_name = _join(folder, plan)
        plan_sha256 = _hash_value(spec.get("plan_sha256"), field + ".plan_sha256")
        load_bound_recovery_plan(
            root, plan_name, plan_sha256,
            source_sha256=source_sha256, manifest_sha256=manifest_sha256,
        )
        result.update(plan=plan_name, plan_sha256=plan_sha256)
    elif "plan" in spec or "plan_sha256" in spec:
        _fail("invalid_manifest", "Bound recovery plans require an occluded_receipt group.")
    return result


def _normalize_supplement(
    spec: Mapping[str, Any], *, root: Path, source_sha256: str, duration_ms: int | None, field: str
) -> dict[str, Any]:
    _only_keys(spec, {"kind", "name", "folder", "entries"}, field)
    kind = spec.get("kind")
    if kind not in SUPPLEMENT_KINDS:
        _fail("invalid_supplement_kind", f"{field}.kind is unsupported.")
    name = spec.get("name")
    if kind == "raw_sidecars":
        if not isinstance(name, str) or not name:
            _fail("invalid_manifest", f"{field}.name is required for raw_sidecars.")
        _safe_basename(name, field + ".name")
    folder = _relative(spec.get("folder", ""), field + ".folder")
    entries = _list(spec.get("entries"), field + ".entries")
    normalized_entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry_value in enumerate(entries):
        entry = _mapping(entry_value, f"{field}.entries[{index}]")
        _only_keys(
            entry,
            {"path", "sidecar_sha256", "raw_path", "raw_sha256", "evidence_path", "evidence_sha256", "source_timestamp_ms"},
            f"{field}.entries[{index}]",
        )
        path_rel = _relative(entry.get("path"), f"{field}.entries[{index}].path")
        sidecar_path, sidecar_name = _file(root, _join(folder, path_rel), f"{field}.entries[{index}].path")
        if sidecar_name in seen:
            _fail("duplicate_sidecar", f"{field}.entries[{index}] duplicates a sidecar path.")
        seen.add(sidecar_name)
        sidecar_sha256 = _checked_hash(sidecar_path, entry.get("sidecar_sha256"), f"{field}.entries[{index}].sidecar_sha256")
        sidecar = _json_file(sidecar_path, f"{field}.entries[{index}].path")
        declared_source = sidecar.get("source_sha256")
        if declared_source is not None and declared_source != source_sha256:
            _fail("source_mismatch", f"{field}.entries[{index}] sidecar belongs to another source.")
        raw_path, raw_name = _file(root, entry.get("raw_path"), f"{field}.entries[{index}].raw_path")
        raw = _json_file(raw_path, f"{field}.entries[{index}].raw_path")
        raw_source = raw.get("source_sha256")
        if raw_source is not None and raw_source != source_sha256:
            _fail("source_mismatch", f"{field}.entries[{index}] raw input belongs to another source.")
        timestamp = _timestamp(entry.get("source_timestamp_ms"), f"{field}.entries[{index}].source_timestamp_ms", duration_ms)
        sidecar_timestamp = sidecar.get("source_timestamp_ms")
        if sidecar_timestamp is not None:
            if _timestamp(sidecar_timestamp, f"{field}.entries[{index}].sidecar.source_timestamp_ms", duration_ms) != timestamp:
                _fail("timestamp_mismatch", f"{field}.entries[{index}] sidecar timestamp differs from the entry.")
        if raw.get("source_timestamp_ms") != timestamp:
            _fail("timestamp_mismatch", f"{field}.entries[{index}] timestamp differs from raw input.")
        raw_fingerprint = _hash_value(entry.get("raw_sha256"), f"{field}.entries[{index}].raw_sha256")
        if sidecar.get("raw_sha256") != raw_fingerprint or _canonical_hash(raw) != raw_fingerprint:
            _fail("hash_mismatch", f"{field}.entries[{index}].raw_sha256 does not match the raw input.")
        evidence_path, evidence_name = _file(root, entry.get("evidence_path"), f"{field}.entries[{index}].evidence_path")
        evidence_sha256 = _hash_value(entry.get("evidence_sha256"), f"{field}.entries[{index}].evidence_sha256")
        if sidecar.get("evidence_sha256") != evidence_sha256 or _digest(evidence_path) != evidence_sha256:
            _fail("hash_mismatch", f"{field}.entries[{index}].evidence_sha256 does not match the evidence file.")
        raw_evidence = _relative(raw.get("evidence"), f"{field}.entries[{index}].raw.evidence")
        raw_evidence_path = raw_path.parent.parent / Path(raw_evidence)
        _assert_no_reparse(root, raw_evidence_path, f"{field}.entries[{index}].raw.evidence")
        if raw_evidence_path != evidence_path:
            _fail("path_mismatch", f"{field}.entries[{index}] raw evidence does not match its manifest evidence.")
        normalized_entries.append(
            {
                "path": sidecar_name,
                "sidecar_sha256": sidecar_sha256,
                "raw_path": raw_name,
                "raw_sha256": raw_fingerprint,
                "evidence_path": evidence_name,
                "evidence_sha256": evidence_sha256,
                "source_timestamp_ms": timestamp,
            }
        )
    result = {"kind": kind, "folder": folder, "entries": normalized_entries}
    if name is not None:
        result["name"] = name
    return result


def normalize_manifest(
    manifest: Mapping[str, Any],
    root: Path | str,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate and return root-relative replay inputs.

    ``accepted_report`` and accepted report values are intentionally not part
    of this schema.  A caller may retain ``reference.accepted_report_sha256``
    as comparison metadata, but this function never reads it.
    """
    payload = _mapping(manifest, "manifest")
    unknown = set(payload) - _TOP_LEVEL_KEYS
    if unknown:
        _fail("invalid_manifest", f"Unsupported manifest keys: {', '.join(sorted(map(str, unknown)))}")
    if payload.get("schema_version") != SCHEMA:
        _fail("invalid_schema", f"manifest.schema_version must be {SCHEMA}.")
    source_sha256 = _hash_value(payload.get("source_sha256"), "manifest.source_sha256")
    if expected_source_sha256 is not None:
        _hash_value(expected_source_sha256, "expected_source_sha256")
        if source_sha256 != expected_source_sha256:
            _fail("source_mismatch", "manifest.source_sha256 does not match the requested source.")
    cache_root = _root_path(root)
    base, duration_ms = _normalize_base(_mapping(payload.get("base"), "manifest.base"), cache_root, source_sha256)
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA,
        "source_sha256": source_sha256,
        "base": base,
        "inspections": [],
        "recovery": [],
        "supplements": [],
    }
    seen_groups: set[tuple[str, str, tuple[int, ...]]] = set()
    for index, value in enumerate(_list(payload.get("inspections", []), "manifest.inspections")):
        spec = _mapping(value, f"manifest.inspections[{index}]")
        identifier = spec.get("id")
        if not isinstance(identifier, str) or not identifier:
            _fail("invalid_manifest", f"manifest.inspections[{index}].id must be non-empty.")
        group = _normalize_manifest_group(
            spec,
            root=cache_root,
            source_sha256=source_sha256,
            duration_ms=duration_ms,
            field=f"manifest.inspections[{index}]",
            require_merge=True,
        )
        key = (group["folder"], group["manifest"], tuple(group["row_indices"]))
        if key in seen_groups:
            _fail("duplicate_manifest", f"manifest.inspections[{index}] duplicates a manifest.")
        seen_groups.add(key)
        group["id"] = identifier
        normalized["inspections"].append(group)
    for index, value in enumerate(_list(payload.get("recovery", []), "manifest.recovery")):
        spec = _mapping(value, f"manifest.recovery[{index}]")
        kind = spec.get("kind")
        if kind not in RECOVERY_KINDS:
            _fail("invalid_recovery_kind", f"manifest.recovery[{index}].kind is unsupported.")
        group = _normalize_manifest_group(
            spec,
            root=cache_root,
            source_sha256=source_sha256,
            duration_ms=duration_ms,
            field=f"manifest.recovery[{index}]",
            require_merge=False,
        )
        key = (group["folder"], group["manifest"], tuple(group["row_indices"]))
        if key in seen_groups:
            _fail("duplicate_manifest", f"manifest.recovery[{index}] duplicates a manifest.")
        seen_groups.add(key)
        group["kind"] = kind
        normalized["recovery"].append(group)
    for index, value in enumerate(_list(payload.get("supplements", []), "manifest.supplements")):
        normalized["supplements"].append(
            _normalize_supplement(
                _mapping(value, f"manifest.supplements[{index}]"),
                root=cache_root,
                source_sha256=source_sha256,
                duration_ms=duration_ms,
                field=f"manifest.supplements[{index}]",
            )
        )
    if "reference" in payload:
        reference = _mapping(payload["reference"], "manifest.reference")
        if set(reference) != {"accepted_report_sha256"}:
            _fail("accepted_report_values_forbidden", "manifest.reference may contain only accepted_report_sha256.")
        normalized["reference"] = {"accepted_report_sha256": _hash_value(reference["accepted_report_sha256"], "manifest.reference.accepted_report_sha256")}
    return normalized


def load_manifest(
    path: Path | str,
    root: Path | str | None = None,
    *,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Read, validate and normalize a replay-input manifest from disk."""
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", f"Replay input manifest is not readable JSON: {exc}")
    return normalize_manifest(payload, root or manifest_path.parent, expected_source_sha256=expected_source_sha256)


__all__ = ["SCHEMA", "ReplayInputError", "load_manifest", "normalize_manifest"]
