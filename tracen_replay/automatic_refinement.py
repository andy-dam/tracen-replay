"""Bounded source-driven refinement for a fresh full-recording run.

The base OCR pass owns frame selection.  This module only discovers weak
observations already present in that pass, then asks the existing refinement
readers to reread those source frames.  It never receives frame ids, times,
expected values, or accepted-report values from an evaluation fixture.

``allow_ocr=False`` is the cache replay mode: existing sidecars are left for
``cached_readings`` to validate and apply, while unresolved candidates are
reported instead of triggering a new OCR request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA = "tracen-replay/automatic-refinement-v1"
DEFAULT_MAX_FRAMES = 512
DEFAULT_MAX_STATUS_FRAMES = 512
DEFAULT_MAX_WEAK_FRAMES = 512
DEFAULT_MAX_NUMERIC_FRAMES = 512
PANEL_RESOLVED_STATUSES = frozenset(
    {
        "resolved_merged_panel_value",
        "resolved_separate_component_panel_values",
        "resolved_separate_panel_values",
        "resolved_current_panel_value",
    }
)


def _frame_key(frame: dict[str, Any]) -> tuple[int, str]:
    return frame["source_timestamp_ms"], frame.get("id", frame.get("frame_id", ""))


def _safe_frame_id(value: Any) -> bool:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    path = Path(value)
    return not path.is_absolute() and len(path.parts) == 1 and path.parts[0] == value


def _raw_path(root: Path, frame: dict[str, Any]) -> Path:
    return root / "neural" / f"{frame['id']}.json"


def _root_file(root: Path, value: Any) -> Path | None:
    """Resolve one raw evidence path without permitting reparse escapes."""

    if not isinstance(value, str) or not value:
        return None
    try:
        candidate = (root / Path(value)).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _sidecar_path(root: Path, folder: str, frame: dict[str, Any]) -> Path:
    return root / folder / f"{frame['id']}.json"


def _capture_source_frame(root: Path, frame: dict[str, Any]) -> tuple[Path | None, str | None]:
    """Return the decoded source frame path used by refinement provenance."""

    candidates: list[tuple[Path, str]] = []
    capture_path = root / "capture.json"
    if capture_path.is_file():
        try:
            payload = json.loads(capture_path.read_text(encoding="utf-8"))
            for item in payload.get("frames", []):
                if isinstance(item, dict) and item.get("id") == frame.get("id"):
                    evidence = item.get("evidence")
                    if isinstance(evidence, str):
                        candidates.append((root / Path(evidence), evidence))
                    break
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            pass
    evidence = frame.get("evidence")
    if isinstance(evidence, str):
        candidates.append((root / Path(evidence), evidence))
    for path, declared in candidates:
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            return resolved, declared.replace("\\", "/")
    return None, None


def _read_raw(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Automatic refinement source JSON is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Automatic refinement source JSON must be an object: {path}")
    return raw


def discover(report: dict[str, Any], root: str | Path) -> dict[str, Any]:
    """Find weak panel/badge observations from the immutable base OCR cache.

    Discovery is deliberately conservative.  Panel candidates must already
    have a strict panel identity and a pair of localized component regions;
    status candidates must satisfy the existing anchor and confidence rules.
    A valid sidecar is counted as existing and is never scheduled for a
    replacement write.
    """

    from .performance_panel_refinement import PANEL_FIELDS, _field_from_reader
    from .status_badge_refinement import _candidate_records
    from .vision import _PERFORMANCE_PANEL_ROWS, _performance_panel_field, _performance_panel_identity

    root = Path(root).resolve()
    frames = report.get("frames") if isinstance(report, dict) else None
    if not isinstance(frames, list):
        raise ValueError("Automatic refinement requires the capture frame list.")
    panel: list[dict[str, Any]] = []
    status: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    numeric: list[dict[str, Any]] = []
    auxiliary_errors: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for frame in frames:
        if not isinstance(frame, dict) or not _safe_frame_id(frame.get("id")):
            malformed.append({"reason": "invalid_frame_identity"})
            continue
        timestamp = frame.get("source_timestamp_ms")
        if type(timestamp) is not int:
            malformed.append({"frame_id": frame["id"], "reason": "invalid_frame_timestamp"})
            continue
        path = _raw_path(root, frame)
        try:
            path.resolve().relative_to((root / "neural").resolve())
        except ValueError:
            malformed.append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": timestamp,
                    "reason": "raw_path_outside_root",
                }
            )
            continue
        if not path.is_file():
            missing.append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": timestamp,
                    "reason": "missing_raw_observation",
                }
            )
            continue
        try:
            raw = _read_raw(path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            malformed.append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": timestamp,
                    "reason": "raw_observation_unreadable",
                    "detail": str(exc),
                }
            )
            continue
        if raw.get("source_timestamp_ms") != timestamp:
            malformed.append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": timestamp,
                    "reason": "raw_timestamp_mismatch",
                }
            )
            continue
        if _root_file(root, raw.get("evidence")) is None:
            malformed.append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": timestamp,
                    "reason": "raw_evidence_missing_or_outside_root",
                }
            )
            continue

        panel_fields: list[str] = []
        # Candidate discovery must stay cheap enough for a full fresh run.
        # Grid flags and the fixed panel identity are already present in the
        # immutable base OCR record, so do not run the full semantic parser a
        # second time merely to decide which panes deserve a reread.
        lines = raw.get("lines", [])
        identity = _performance_panel_identity(lines) if (
            raw.get("current_grid") or raw.get("result_grid")
        ) else None
        if identity is not None:
            rows_by_field = {field: label_y for field, _label, label_y, _cap_y in _PERFORMANCE_PANEL_ROWS}
            for field in PANEL_FIELDS:
                field_proof = _performance_panel_field(
                    lines, field, rows_by_field[field], regions=raw.get("regions", {})
                )
                if field_proof.get("status") in PANEL_RESOLVED_STATUSES:
                    continue
                # The base reader already asked for same-row component crops
                # when it saw a merged panel line.  Only schedule a reread if
                # both components are actually represented in that source.
                if _field_from_reader(raw, raw, field) is not None:
                    panel_fields.append(field)
        if panel_fields:
            target = _sidecar_path(root, "performance-panel-refinement", frame)
            item = {
                "frame_id": frame["id"],
                "source_timestamp_ms": timestamp,
                "fields": panel_fields,
                "status": "existing" if target.is_file() else "candidate",
                "sidecar": target.relative_to(root).as_posix(),
            }
            panel.append(item)

        badge_candidates = _candidate_records(raw)
        if badge_candidates:
            target = _sidecar_path(root, "status-badge-refinement", frame)
            status.append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": timestamp,
                    "kinds": sorted({candidate["kind"] for candidate in badge_candidates}),
                    "status": "existing" if target.is_file() else "candidate",
                    "sidecar": target.relative_to(root).as_posix(),
                }
            )

        # Auxiliary candidates are derived only from this immutable raw
        # observation.  They carry no expected values and never consult an
        # accepted report.  Keep candidate failures structured so a malformed
        # auxiliary record cannot silently become a successful supplement.
        try:
            from .weak_state_recovery import discover as discover_weak
            weak_result = discover_weak(raw, max_requests=16)
            weak_requests = weak_result.get("requests", [])
        except (KeyError, TypeError, ValueError) as exc:
            weak_requests = []
            auxiliary_errors.append({
                "frame_id": frame["id"],
                "source_timestamp_ms": timestamp,
                "reason": "weak_state_candidate_invalid",
                "detail": str(exc),
            })
        if weak_requests:
            target = _sidecar_path(root, "weak-state-recovery", frame)
            weak.append({
                "frame_id": frame["id"],
                "source_timestamp_ms": timestamp,
                "requests": [item.get("id") for item in weak_requests if isinstance(item, dict)],
                "status": "existing" if target.is_file() else "candidate",
                "sidecar": target.relative_to(root).as_posix(),
            })
        try:
            from .numeric_cap_refinement import candidate_fields
            numeric_fields = candidate_fields(raw)
        except (KeyError, TypeError, ValueError) as exc:
            numeric_fields = []
            auxiliary_errors.append({
                "frame_id": frame["id"],
                "source_timestamp_ms": timestamp,
                "reason": "numeric_candidate_invalid",
                "detail": str(exc),
            })
        if numeric_fields:
            target = _sidecar_path(root, "numeric-cap-refinement", frame)
            numeric.append({
                "frame_id": frame["id"],
                "source_timestamp_ms": timestamp,
                "fields": sorted({item.get("field") for item in numeric_fields
                                   if isinstance(item, dict) and item.get("field")}),
                "status": "existing" if target.is_file() else "candidate",
                "sidecar": target.relative_to(root).as_posix(),
            })

    panel.sort(key=_frame_key)
    status.sort(key=_frame_key)
    weak.sort(key=_frame_key)
    numeric.sort(key=_frame_key)
    return {
        "panel": panel,
        "status": status,
        "weak": weak,
        "numeric": numeric,
        "auxiliary_errors": auxiliary_errors,
        "missing": missing,
        "malformed": malformed,
    }


def _select(items: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if type(limit) is not int or limit < 0:
        raise ValueError("Automatic refinement frame budget must be a non-negative integer.")
    candidates = [item for item in items if item.get("status") == "candidate"]
    return candidates[:limit], candidates[limit:]


def _range_for(items: list[dict[str, Any]]) -> tuple[int, int] | None:
    if not items:
        return None
    start = min(item["source_timestamp_ms"] for item in items)
    end = max(item["source_timestamp_ms"] for item in items) + 1
    return start, end


def _group_panel(items: list[dict[str, Any]]) -> list[tuple[tuple[str, ...], list[dict[str, Any]]]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in items:
        key = tuple(sorted(item["fields"]))
        groups.setdefault(key, []).append(item)
    return sorted(groups.items(), key=lambda pair: (pair[0], _frame_key(pair[1][0])))


def run(
    report: dict[str, Any],
    root: str | Path,
    *,
    allow_ocr: bool,
    model_dir: str | Path = ".local/models/rapidocr",
    max_panel_frames: int = DEFAULT_MAX_FRAMES,
    max_status_frames: int = DEFAULT_MAX_STATUS_FRAMES,
    max_weak_frames: int = DEFAULT_MAX_WEAK_FRAMES,
    max_numeric_frames: int = DEFAULT_MAX_NUMERIC_FRAMES,
    reader=None,
) -> dict[str, Any]:
    """Discover and, when allowed, publish bounded refinement sidecars.

    The returned audit is safe to persist in the report.  It records counts,
    source frame identities, and unresolved reasons, but never copies a
    balance or value from another report.
    """

    root = Path(root).resolve()
    discovered = discover(report, root)
    panel_existing = [item for item in discovered["panel"] if item["status"] == "existing"]
    status_existing = [item for item in discovered["status"] if item["status"] == "existing"]
    weak_items = discovered.get("weak", [])
    numeric_items = discovered.get("numeric", [])
    weak_existing = [item for item in weak_items if item["status"] == "existing"]
    numeric_existing = [item for item in numeric_items if item["status"] == "existing"]
    panel_selected, panel_budget_excluded = _select(discovered["panel"], max_panel_frames)
    status_selected, status_budget_excluded = _select(discovered["status"], max_status_frames)
    weak_selected, weak_budget_excluded = _select(weak_items, max_weak_frames)
    numeric_selected, numeric_budget_excluded = _select(numeric_items, max_numeric_frames)
    audit: dict[str, Any] = {
        "schema_version": SCHEMA,
        "source_sha256": report.get("source", {}).get("sha256"),
        "allow_ocr": bool(allow_ocr),
        "budget": {
            "max_panel_frames": max_panel_frames,
            "max_status_frames": max_status_frames,
            "max_weak_frames": max_weak_frames,
            "max_numeric_frames": max_numeric_frames,
            "panel_budget_excluded": len(panel_budget_excluded),
            "status_budget_excluded": len(status_budget_excluded),
            "weak_budget_excluded": len(weak_budget_excluded),
            "numeric_budget_excluded": len(numeric_budget_excluded),
        },
        "discovered": {
            "panel_frames": len(discovered["panel"]),
            "status_frames": len(discovered["status"]),
            "weak_frames": len(weak_items),
            "numeric_frames": len(numeric_items),
            "missing_raw": len(discovered["missing"]),
            "malformed_raw": len(discovered["malformed"]),
            "auxiliary_errors": len(discovered.get("auxiliary_errors", [])),
        },
        "existing_sidecars": {
            "performance_panel": len(panel_existing),
            "status_badge": len(status_existing),
            "weak_state_recovery": len(weak_existing),
            "numeric_cap_refinement": len(numeric_existing),
        },
        "selected": {
            "panel_frames": [item["frame_id"] for item in panel_selected],
            "status_frames": [item["frame_id"] for item in status_selected],
            "weak_frames": [item["frame_id"] for item in weak_selected],
            "numeric_frames": [item["frame_id"] for item in numeric_selected],
        },
        "generated": {
            "performance_panel": {"written": 0, "unresolved": []},
            "status_badge": {"written": 0, "unresolved": [], "unresolved_count": 0},
            "weak_state_recovery": {"written": 0, "unresolved": [], "unresolved_count": 0},
            "numeric_cap_refinement": {"written": 0, "unresolved": [], "unresolved_count": 0},
        },
        "unresolved": (
            list(discovered["missing"])
            + list(discovered["malformed"])
            + list(discovered.get("auxiliary_errors", []))
        ),
    }
    for item in panel_budget_excluded:
        audit["unresolved"].append(dict(item, reason="panel_refinement_budget_exhausted"))
    for item in status_budget_excluded:
        audit["unresolved"].append(dict(item, reason="status_badge_refinement_budget_exhausted"))
    for item in weak_budget_excluded:
        audit["unresolved"].append(dict(item, reason="weak_state_recovery_budget_exhausted"))
    for item in numeric_budget_excluded:
        audit["unresolved"].append(dict(item, reason="numeric_cap_refinement_budget_exhausted"))

    if not allow_ocr:
        for item in panel_selected:
            audit["unresolved"].append(dict(item, reason="ocr_disabled_existing_sidecar_required"))
        for item in status_selected:
            audit["unresolved"].append(dict(item, reason="ocr_disabled_existing_sidecar_required"))
        for item in weak_selected:
            audit["unresolved"].append(dict(item, reason="ocr_disabled_existing_sidecar_required"))
        for item in numeric_selected:
            audit["unresolved"].append(dict(item, reason="ocr_disabled_existing_sidecar_required"))
        audit["status"] = "passed_with_unresolved" if audit["unresolved"] else "passed"
        return audit

    if (panel_selected or status_selected or weak_selected or numeric_selected) and reader is None:
        from .vision import NeuralReader

        reader = NeuralReader(model_dir)

    from .performance_panel_refinement import generate as generate_panel
    from .status_badge_refinement import generate as generate_status

    # A generator accepts one field set for a selected frame list.  Grouping
    # by the discovered field set avoids rereading or publishing a field that
    # was already source-resolved on an individual frame.
    for fields, items in _group_panel(panel_selected):
        bounds = _range_for(items)
        if bounds is None:
            continue
        try:
            result = generate_panel(
                root,
                start_ms=bounds[0],
                end_ms=bounds[1],
                frame_ids=[item["frame_id"] for item in items],
                fields=list(fields),
                reader=reader,
                model_dir=model_dir,
                output_dir=root / "performance-panel-refinement",
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            audit["generated"]["performance_panel"].setdefault("errors", []).append(str(exc))
            for item in items:
                audit["unresolved"].append(dict(item, reason="panel_refinement_failed", detail=str(exc)))
            continue
        audit["generated"]["performance_panel"]["written"] += result.get("written", 0)
        audit["generated"]["performance_panel"]["unresolved"].extend(result.get("unresolved_fields", []))
    if status_selected:
        bounds = _range_for(status_selected)
        if bounds is not None:
            try:
                result = generate_status(
                    root,
                    start_ms=bounds[0],
                    end_ms=bounds[1],
                    frame_ids=[item["frame_id"] for item in status_selected],
                    reader=reader,
                    model_dir=model_dir,
                    output_dir=root / "status-badge-refinement",
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                audit["generated"]["status_badge"]["errors"] = [str(exc)]
                for item in status_selected:
                    audit["unresolved"].append(dict(item, reason="status_badge_refinement_failed", detail=str(exc)))
            else:
                audit["generated"]["status_badge"]["written"] += result.get("written", 0)
                audit["generated"]["status_badge"]["unresolved_count"] += int(result.get("unresolved", 0))
                audit["generated"]["status_badge"]["unresolved"].extend(
                    result.get("unresolved_frames", [])
                )

    # Weak-state recovery consumes the exact raw/evidence/source-frame triplet
    # and writes one sidecar per selected frame.  Reuse the reader constructed
    # above so a fresh run does not create a second model session.
    from .weak_state_recovery import generate as generate_weak
    for item in weak_selected:
        frame = next((candidate for candidate in report.get("frames", [])
                      if isinstance(candidate, dict) and candidate.get("id") == item["frame_id"]), None)
        if frame is None:
            audit["generated"]["weak_state_recovery"]["unresolved"].append(
                dict(item, reason="frame_missing"))
            continue
        raw_path = _raw_path(root, frame)
        raw = _read_raw(raw_path)
        evidence_path = _root_file(root, raw.get("evidence"))
        source_frame_path, source_frame_evidence = _capture_source_frame(root, frame)
        if evidence_path is None or source_frame_path is None:
            audit["generated"]["weak_state_recovery"]["unresolved"].append(
                dict(item, reason="source_evidence_missing"))
            continue
        try:
            generate_weak(
                raw_path,
                evidence_path,
                _sidecar_path(root, "weak-state-recovery", frame),
                reader=reader,
                model_dir=model_dir,
                source_frame_path=source_frame_path,
                source_frame_evidence=source_frame_evidence,
                source_frame_id=frame["id"],
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            audit["generated"]["weak_state_recovery"]["unresolved"].append(
                dict(item, reason="weak_state_recovery_failed", detail=str(exc)))
        else:
            audit["generated"]["weak_state_recovery"]["written"] += 1

    # Numeric rereads are grouped into one bounded call.  The generator still
    # derives fields from each immutable raw frame, so the union below is only
    # a field allowlist and cannot supply a value or an expected total.
    if numeric_selected:
        from .numeric_cap_refinement import generate as generate_numeric
        fields = sorted({field for item in numeric_selected for field in item.get("fields", [])})
        bounds = _range_for(numeric_selected)
        try:
            result = generate_numeric(
                root,
                start_ms=bounds[0],
                end_ms=bounds[1],
                frame_ids=[item["frame_id"] for item in numeric_selected],
                fields=fields or None,
                reader=reader,
                model_dir=model_dir,
                output_dir=root / "numeric-cap-refinement",
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            audit["generated"]["numeric_cap_refinement"]["unresolved"].append(
                {"reason": "numeric_cap_refinement_failed", "detail": str(exc)})
        else:
            audit["generated"]["numeric_cap_refinement"]["written"] += result.get("written", 0)
            audit["generated"]["numeric_cap_refinement"]["unresolved"].extend(
                result.get("unresolved", []))
            audit["generated"]["numeric_cap_refinement"]["unresolved_count"] += len(result.get("unresolved", []))

    generated_unresolved = (
        audit["generated"]["performance_panel"].get("unresolved", [])
        + audit["generated"]["status_badge"].get("unresolved", [])
        + audit["generated"]["weak_state_recovery"].get("unresolved", [])
        + audit["generated"]["numeric_cap_refinement"].get("unresolved", [])
    )
    audit["unresolved"].extend(generated_unresolved)
    audit["status"] = "passed_with_unresolved" if audit["unresolved"] else "passed"
    return audit


__all__ = [
    "SCHEMA",
    "DEFAULT_MAX_FRAMES",
    "DEFAULT_MAX_STATUS_FRAMES",
    "DEFAULT_MAX_WEAK_FRAMES",
    "DEFAULT_MAX_NUMERIC_FRAMES",
    "discover",
    "run",
]
