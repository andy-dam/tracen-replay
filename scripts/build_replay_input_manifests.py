"""Build deterministic source-bound replay manifests for the three recordings.

The builder reads cache metadata and raw OCR sidecars only.  It never opens an
accepted report and it does not start the producer or OCR.  The resulting
manifests are validated with :mod:`tracen_replay.replay_inputs` before they are
reported as usable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tracen_replay.replay_inputs import SCHEMA, ReplayInputError, normalize_manifest


BUILDER_SCHEMA = "tracen-replay/replay-input-manifest-builder-v1"
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / ".local/final-reliability-v1/replay-inputs"

RECORDINGS: dict[str, dict[str, Any]] = {
    "v1": {
        "cache_root": REPOSITORY_ROOT / ".local/full-recording/v1",
        "base_folder": "",
        "source_video": Path(r"C:\Users\andy-\Videos\2026-09-08 01-59-24.mp4"),
        "expected_source_sha256": "a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174",
        "inspections": (
            ("training", "training-inspection.json", "training"),
            ("native", "native-inspection.json", "training"),
            ("receipt", "receipt-inspection.json", "receipt"),
        ),
    },
    "independent-01": {
        "cache_root": REPOSITORY_ROOT / ".local/full-recording/independent-01",
        "base_folder": "",
        "source_video": Path(r"C:\Users\andy-\Videos\2026-09-08 12-21-21.mp4"),
        "expected_source_sha256": "24e000837fa4bba40d4e9e7c1c7d6121300a51d166424ea7bf5cfea24f521c18",
        "inspections": (
            ("training", "training-inspection.json", "training"),
            ("native", "native-inspection.json", "training"),
            ("receipt", "receipt-inspection.json", "receipt"),
        ),
    },
    "independent-02": {
        "cache_root": REPOSITORY_ROOT / ".local/full-recording/independent-02",
        "base_folder": "initial-baseline",
        "source_video": Path(r"C:\Users\andy-\Videos\2026-09-09 20-52-07.mp4"),
        "expected_source_sha256": "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313",
        "inspections": (
            ("training-recovery-v1", "training-inspection.json", "training"),
            ("native-recovery-v1", "native-inspection.json", "training"),
            ("missing-training-recovery-v4", "training-inspection.json", "training"),
            ("receipt", "receipt-inspection.json", "receipt"),
        ),
    },
}

AUTOMATIC_RECOVERIES = (
    ("numeric_receipt", "numeric-receipt-recovery", "receipt-inspection.json", "receipt"),
    ("training_gain", "training-gain-recovery", "receipt-inspection.json", "training"),
    ("boundary_state", "boundary-state-recovery", "receipt-inspection.json", "receipt"),
    ("occluded_receipt", "occluded-receipt-recovery", "receipt-inspection.json", "receipt"),
)

# These are the fixed per-frame sidecar directories consumed by
# full_recording.cached_readings.  A generic raw_sidecars group keeps their
# dispatch name explicit without pretending that this manifest implements the
# refinement semantics.
FIXED_RAW_SIDECARS = (
    ("outcome-refinement", "outcome-refinement"),
    ("currency-refinement", "currency_regions"),
    ("currency-padding-refinement", "currency_regions"),
    ("skill-points-refinement", "skill_points"),
    ("skill-variants", "skill_variants"),
    ("song-symbols", "song_symbols"),
    ("song-symbol-refinement", "song_symbol_refinement"),
    ("song-star-refinement", "song_star_refinement"),
    ("concert-panel-refinement", "concert_panel"),
    ("base-receipt-refinement", "base_receipt"),
    ("race-identity-refinement", "race_identity"),
    ("performance-panel-refinement", "performance_panel"),
    ("status-badge-refinement", "status_badge_refinement"),
    ("training-badge-localization", "training_badge_localization"),
    ("inventory-refinement", "inventory"),
    ("lesson-offer-refinement", "lesson_offer"),
    ("choice-refinement", "choice"),
    ("choice-card-refinement", "choice_cards"),
)

# These directories contain aggregate refinement plans rather than the
# per-frame raw/evidence/hash shape.  They are called out in the audit for a
# later specialized contract instead of being silently treated as sidecars.
AGGREGATE_REFINEMENTS = ("race-quantity-refinement",)

# The replay configurations identify these independent source supplements.
# The race entry is intentionally the raw crop-proof directory; the old
# race_identity_accepted mode read values from an accepted report and is not
# used here.
EXPLICIT_SUPPLEMENTS: dict[str, tuple[tuple[str, str, str | None], ...]] = {
    "v1": (),
    "independent-01": (),
    "independent-02": (
        ("song_symbols", "song-symbol-recovery-v1/song-symbols", None),
        ("currency_regions", "currency-recovery-v1", None),
        ("race_identity", "race-identity-crop-proof-v1/validated-v2", None),
    ),
}


class ManifestBuildError(ValueError):
    """A reproducible input-manifest construction failure."""

    def __init__(self, code: str, path: Path | str, message: str):
        super().__init__(message)
        self.code = code
        self.path = str(path)


class DigestCache:
    def __init__(self) -> None:
        self._values: dict[Path, str] = {}

    def file(self, path: Path) -> str:
        path = path.resolve()
        if path not in self._values:
            try:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise ManifestBuildError("file_unreadable", path, str(exc)) from exc
            self._values[path] = digest.hexdigest()
        return self._values[path]


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestBuildError("invalid_json", path, str(exc)) from exc


def _mapping(value: Any, path: Path | str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestBuildError("invalid_json", path, "expected a JSON object")
    return value


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
        return "" if relative == Path(".") else relative.as_posix()
    except ValueError as exc:
        raise ManifestBuildError("path_outside_root", path, "path is outside the cache root") from exc


def _raw_candidate(evidence_path: Path) -> Path:
    versioned = evidence_path.with_suffix(".v2.json")
    return versioned if versioned.is_file() else evidence_path.with_suffix(".json")


def _raw_evidence_relative(evidence_path: Path, raw: dict[str, Any]) -> PurePosixPath:
    value = raw.get("evidence")
    if not isinstance(value, str) or not value:
        raise ManifestBuildError("invalid_path", evidence_path, "raw evidence must be a non-empty path")
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if relative.is_absolute() or windows.is_absolute() or windows.drive or windows.root or ".." in relative.parts:
        raise ManifestBuildError("path_outside_root", evidence_path, "raw evidence escapes its own root")
    if not relative.parts:
        raise ManifestBuildError("invalid_path", evidence_path, "raw evidence must be a non-empty path")
    return relative


def _raw_evidence_path(evidence_path: Path, raw: dict[str, Any]) -> tuple[Path, Path, PurePosixPath]:
    """Resolve raw evidence from the sidecar's declared own root.

    Inspection manifests can be stored below a recovery directory while the
    raw sidecar keeps an evidence path relative to that recovery directory.
    Walking up once per relative component recovers that own root without
    flattening or guessing a cache namespace.
    """

    relative = _raw_evidence_relative(evidence_path, raw)
    own_root_path = evidence_path
    for _ in relative.parts:
        own_root_path = own_root_path.parent
    return own_root_path / Path(relative.as_posix()), own_root_path, relative


def _safe_relative(value: str, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ManifestBuildError("invalid_path", field, "path must be a relative path")
    if not value:
        return ""
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if "\x00" in value:
        raise ManifestBuildError("invalid_path", field, "path contains a null character")
    if relative.is_absolute() or windows.is_absolute() or windows.drive or windows.root or ".." in relative.parts:
        raise ManifestBuildError("path_outside_root", field, "path must stay below the cache root")
    return relative.as_posix()


def _issue_path(root: Path, path: Path | str) -> str:
    try:
        return _relative(root, Path(path))
    except ManifestBuildError:
        return str(path)


def _partition_group(
    *,
    root: Path,
    folder: str,
    manifest: str,
    merge: str,
    source_sha256: str,
    duration_ms: int | None,
    identifier: str,
    recovery: bool,
    issues: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = root / Path(folder) / manifest if folder else root / manifest
    if not manifest_path.is_file():
        issues.append({"code": "manifest_missing", "path": _relative(root, manifest_path), "message": "manifest is missing"})
        return [], {"id": identifier, "manifest": manifest, "rows": 0, "partitions": []}
    payload = _mapping(_json(manifest_path), manifest_path)
    readings = payload.get("readings")
    if not isinstance(readings, list):
        issues.append({"code": "readings_invalid", "path": _relative(root, manifest_path), "message": "manifest readings must be an array"})
        return [], {"id": identifier, "manifest": manifest, "rows": 0, "partitions": []}
    partitions: dict[str, list[int]] = defaultdict(list)
    row_evidence_root = "folder" if folder else "root"
    for index, value in enumerate(readings):
        row_path = f"{_relative(root, manifest_path)}.readings[{index}]"
        row = value if isinstance(value, dict) else None
        evidence_value = row.get("evidence") if row else None
        try:
            evidence_relative = _safe_relative(evidence_value, f"{row_path}.evidence")
        except ManifestBuildError as exc:
            issues.append({"code": exc.code, "path": exc.path, "message": str(exc)})
            continue
        if not evidence_relative:
            issues.append({"code": "invalid_path", "path": row_path, "message": "reading evidence is missing"})
            continue
        try:
            folder_relative = _safe_relative(folder, f"{row_path}.folder", allow_empty=True)
        except ManifestBuildError as exc:
            issues.append({"code": exc.code, "path": exc.path, "message": str(exc)})
            continue
        evidence_path = root / Path("/".join(part for part in (folder_relative, evidence_relative) if part))
        if not evidence_path.is_file():
            issues.append({"code": "evidence_missing", "path": _issue_path(root, evidence_path), "message": "reading evidence is missing"})
            continue
        raw_path = _raw_candidate(evidence_path)
        if not raw_path.is_file():
            issues.append({"code": "raw_missing", "path": _issue_path(root, raw_path), "message": "reading raw sidecar is missing"})
            continue
        try:
            raw = _mapping(_json(raw_path), raw_path)
            raw_evidence, own_root_path, relative_raw = _raw_evidence_path(evidence_path, raw)
        except ManifestBuildError as exc:
            issues.append({"code": exc.code, "path": exc.path, "message": str(exc)})
            continue
        if not raw_evidence.is_file():
            issues.append({"code": "raw_evidence_missing", "path": _issue_path(root, raw_evidence), "message": "raw evidence is missing"})
            continue
        if raw.get("source_sha256") != source_sha256:
            issues.append({"code": "source_mismatch", "path": _issue_path(root, raw_path), "message": "raw sidecar source differs"})
        if raw.get("source_timestamp_ms") != (row or {}).get("source_timestamp_ms"):
            issues.append({"code": "timestamp_mismatch", "path": _issue_path(root, raw_path), "message": "raw sidecar timestamp differs"})
        try:
            own_root = _relative(root, own_root_path)
            if own_root_path / Path(relative_raw.as_posix()) != evidence_path:
                raise ValueError("raw and manifest evidence paths disagree")
        except (ManifestBuildError, ValueError) as exc:
            issues.append({"code": "path_mismatch", "path": _issue_path(root, raw_path), "message": str(exc)})
            continue
        partitions[own_root].append(index)
    specs: list[dict[str, Any]] = []
    manifest_hash = DigestCache().file(manifest_path)
    for own_root, indices in sorted(partitions.items()):
        suffix = own_root.replace("/", "-") or "root"
        spec: dict[str, Any] = {
            "folder": folder,
            "own_root": own_root,
            "manifest": manifest,
            "manifest_sha256": manifest_hash,
            "row_evidence_root": row_evidence_root,
            "row_count": len(indices),
            "row_indices": indices,
            "merge": merge,
        }
        if not recovery:
            spec["id"] = f"{identifier}--{suffix}"
        specs.append(spec)
    return specs, {
        "id": identifier,
        "manifest": _relative(root, manifest_path),
        "rows": len(readings),
        "partitions": [{"own_root": key, "rows": len(value)} for key, value in sorted(partitions.items())],
    }


def _build_base(root: Path, base_folder: str, source_sha256: str, digests: DigestCache) -> tuple[dict[str, Any], int | None, dict[str, Any]]:
    base = root / Path(base_folder) if base_folder else root
    capture_path = base / "capture.json"
    payload = _mapping(_json(capture_path), capture_path)
    source = _mapping(payload.get("source"), f"{capture_path}.source")
    if source.get("sha256") != source_sha256:
        raise ManifestBuildError("source_mismatch", capture_path, "capture source hash differs from expected source")
    frames = payload.get("frames")
    if not isinstance(frames, list):
        raise ManifestBuildError("invalid_json", capture_path, "capture frames must be an array")
    duration_ms = source.get("duration_ms")
    if type(duration_ms) is not int or duration_ms <= 0:
        raise ManifestBuildError("invalid_manifest", capture_path, "capture duration must be a positive integer")
    neural = base / "neural"
    if not neural.is_dir():
        raise ManifestBuildError("directory_missing", neural, "base neural directory is missing")
    spec = {
        "folder": base_folder,
        "capture": "capture.json",
        "neural": "neural",
        "capture_sha256": digests.file(capture_path),
        "frame_count": len(frames),
    }
    return spec, duration_ms, {"capture": _relative(root, capture_path), "frames": len(frames), "duration_ms": duration_ms}


def _build_supplement(
    *,
    root: Path,
    base_folder: str,
    folder: str,
    kind: str,
    name: str | None,
    source_sha256: str,
    issues: list[dict[str, str]],
    digests: DigestCache,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    directory = root / Path(folder)
    if not directory.is_dir():
        issues.append({"code": "supplement_missing", "path": folder, "message": "supplement directory is missing"})
        return None, {"kind": kind, "folder": folder, "entries": 0, "ignored": 0}
    entries: list[dict[str, Any]] = []
    ignored = 0
    for sidecar_path in sorted(directory.glob("*.json")):
        try:
            value = _json(sidecar_path)
        except ManifestBuildError as exc:
            issues.append({"code": exc.code, "path": _issue_path(root, sidecar_path), "message": str(exc)})
            ignored += 1
            continue
        if not isinstance(value, dict) or not {"raw_sha256", "evidence_sha256"} <= set(value):
            ignored += 1
            continue
        raw_path = root / Path(base_folder) / "neural" / f"{sidecar_path.stem}.json" if base_folder else root / "neural" / f"{sidecar_path.stem}.json"
        if not raw_path.is_file():
            issues.append({"code": "raw_missing", "path": _relative(root, raw_path), "message": "supplement raw neural sidecar is missing"})
            continue
        raw = _mapping(_json(raw_path), raw_path)
        evidence_value = raw.get("evidence")
        if not isinstance(evidence_value, str) or not evidence_value:
            issues.append({"code": "invalid_path", "path": _relative(root, raw_path), "message": "supplement raw evidence is missing"})
            continue
        evidence_path = root / Path(base_folder) / Path(evidence_value) if base_folder else root / Path(evidence_value)
        if not evidence_path.is_file():
            issues.append({"code": "evidence_missing", "path": _relative(root, evidence_path), "message": "supplement evidence is missing"})
            continue
        timestamp = raw.get("source_timestamp_ms")
        if type(timestamp) is not int:
            issues.append({"code": "timestamp_invalid", "path": _relative(root, raw_path), "message": "supplement raw timestamp is invalid"})
            continue
        try:
            sidecar_rel = sidecar_path.relative_to(directory).as_posix()
            entry = {
                "path": sidecar_rel,
                "sidecar_sha256": digests.file(sidecar_path),
                "raw_path": _relative(root, raw_path),
                "raw_sha256": value["raw_sha256"],
                "evidence_path": _relative(root, evidence_path),
                "evidence_sha256": value["evidence_sha256"],
                "source_timestamp_ms": timestamp,
            }
        except ManifestBuildError as exc:
            issues.append({"code": exc.code, "path": exc.path, "message": str(exc)})
            continue
        if value.get("source_sha256") not in (None, source_sha256):
            issues.append({"code": "source_mismatch", "path": _relative(root, sidecar_path), "message": "supplement sidecar source differs"})
        entries.append(entry)
    if not entries:
        return None, {"kind": kind, "name": name, "folder": folder, "entries": 0, "ignored": ignored}
    spec: dict[str, Any] = {"kind": kind, "folder": folder, "entries": entries}
    if name is not None:
        spec["name"] = name
    return spec, {"kind": kind, "name": name, "folder": folder, "entries": len(entries), "ignored": ignored}


def _recording_manifest(name: str, details: dict[str, Any], *, verify_video: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(details["cache_root"]).resolve()
    expected_source = details["expected_source_sha256"]
    issues: list[dict[str, str]] = []
    digests = DigestCache()
    source_video = Path(details["source_video"])
    source_video_sha256 = None
    if verify_video:
        if not source_video.is_file():
            issues.append({"code": "source_missing", "path": str(source_video), "message": "source video is missing"})
        else:
            source_video_sha256 = digests.file(source_video)
            if source_video_sha256 != expected_source:
                issues.append({"code": "source_mismatch", "path": str(source_video), "message": "source video hash differs from expected source"})
    base, duration_ms, base_audit = _build_base(root, details["base_folder"], expected_source, digests)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA,
        "source_sha256": expected_source,
        "base": base,
        "inspections": [],
        "recovery": [],
        "supplements": [],
    }
    inspection_audit: list[dict[str, Any]] = []
    for identifier, manifest_name, merge in details["inspections"]:
        folder = identifier if name == "independent-02" and identifier in {
            "training-recovery-v1",
            "native-recovery-v1",
            "missing-training-recovery-v4",
        } else ""
        specs, audit = _partition_group(
            root=root,
            folder=folder,
            manifest=manifest_name,
            merge=merge,
            source_sha256=expected_source,
            duration_ms=duration_ms,
            identifier=identifier,
            recovery=False,
            issues=issues,
        )
        manifest["inspections"].extend(specs)
        inspection_audit.append(audit)
    recovery_audit: list[dict[str, Any]] = []
    for kind, folder, manifest_name, merge in AUTOMATIC_RECOVERIES:
        if not (root / folder / manifest_name).is_file():
            continue
        specs, audit = _partition_group(
            root=root,
            folder=folder,
            manifest=manifest_name,
            merge=merge,
            source_sha256=expected_source,
            duration_ms=duration_ms,
            identifier=kind,
            recovery=True,
            issues=issues,
        )
        for spec in specs:
            spec["kind"] = kind
        manifest["recovery"].extend(specs)
        recovery_audit.append(audit)
    supplement_audit: list[dict[str, Any]] = []
    seen_supplement_folders: set[str] = set()
    for folder, kind_name in FIXED_RAW_SIDECARS:
        full_folder = f"{details['base_folder']}/{folder}" if details["base_folder"] else folder
        if full_folder in seen_supplement_folders or not (root / full_folder).is_dir():
            continue
        seen_supplement_folders.add(full_folder)
        semantic_kinds = {"currency_regions", "song_symbols", "race_identity"}
        kind = kind_name if kind_name in semantic_kinds else "raw_sidecars"
        display_name = None if kind != "raw_sidecars" else kind_name
        spec, audit = _build_supplement(
            root=root,
            base_folder=details["base_folder"],
            folder=full_folder,
            kind=kind,
            name=display_name,
            source_sha256=expected_source,
            issues=issues,
            digests=digests,
        )
        if spec is not None:
            manifest["supplements"].append(spec)
        supplement_audit.append(audit)
    for kind, folder, name_override in EXPLICIT_SUPPLEMENTS[name]:
        if folder in seen_supplement_folders:
            continue
        seen_supplement_folders.add(folder)
        spec, audit = _build_supplement(
            root=root,
            base_folder=details["base_folder"],
            folder=folder,
            kind=kind,
            name=name_override,
            source_sha256=expected_source,
            issues=issues,
            digests=digests,
        )
        if spec is not None:
            manifest["supplements"].append(spec)
        supplement_audit.append(audit)
    aggregate_audit: list[dict[str, Any]] = []
    for folder in AGGREGATE_REFINEMENTS:
        full_folder = f"{details['base_folder']}/{folder}" if details["base_folder"] else folder
        directory = root / full_folder
        if directory.is_dir():
            aggregate_audit.append({"folder": full_folder, "json_files": len(list(directory.glob("*.json")),)})
    audit: dict[str, Any] = {
        "builder_schema": BUILDER_SCHEMA,
        "manifest_schema": SCHEMA,
        "recording": name,
        "cache_root": _relative(REPOSITORY_ROOT, root),
        "base": base_audit,
        "source_video": str(source_video),
        "source_video_sha256": source_video_sha256,
        "expected_source_sha256": expected_source,
        "inspections": inspection_audit,
        "recovery": recovery_audit,
        "supplements": supplement_audit,
        "aggregate_refinements": aggregate_audit,
        "issues": issues,
    }
    return manifest, audit


def build(output_dir: Path, names: list[str], *, verify_video: bool, overwrite: bool) -> int:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    failed = False
    for name in names:
        details = RECORDINGS[name]
        manifest, audit = _recording_manifest(name, details, verify_video=verify_video)
        manifest_path = output_dir / f"{name}-replay-input-manifest.json"
        audit_path = output_dir / f"{name}-build-audit.json"
        for path in (manifest_path, audit_path):
            if path.exists() and not overwrite:
                raise ManifestBuildError("output_exists", path, "refusing to overwrite an existing manifest; use --overwrite")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            normalized = normalize_manifest(manifest, Path(details["cache_root"]), expected_source_sha256=details["expected_source_sha256"])
            audit["validation"] = {
                "status": "passed_with_issues" if audit["issues"] else "passed",
                "base_frames": normalized["base"]["frame_count"],
                "inspection_rows": sum(group["row_count"] for group in normalized["inspections"]),
                "recovery_rows": sum(group["row_count"] for group in normalized["recovery"]),
                "supplement_entries": sum(len(group["entries"]) for group in normalized["supplements"]),
            }
        except (ReplayInputError, ManifestBuildError) as exc:
            failed = True
            audit["validation"] = {"status": "failed", "code": getattr(exc, "code", "build_error"), "message": str(exc)}
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        results.append({"recording": name, "manifest": str(manifest_path), "validation": audit["validation"], "issues": len(audit["issues"])})
        print(json.dumps(results[-1], sort_keys=True), flush=True)
    summary_path = output_dir / "build-summary.json"
    if summary_path.exists() and not overwrite:
        raise ManifestBuildError("output_exists", summary_path, "refusing to overwrite an existing summary; use --overwrite")
    summary_path.write_text(
        json.dumps({"builder_schema": BUILDER_SCHEMA, "manifest_schema": SCHEMA, "recordings": results}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recording", choices=sorted(RECORDINGS), action="append")
    parser.add_argument("--skip-source-video", action="store_true", help="Do not hash the source videos before cache validation")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    names = args.recording or list(RECORDINGS)
    try:
        return build(args.output_dir, names, verify_video=not args.skip_source_video, overwrite=args.overwrite)
    except ManifestBuildError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "path": exc.path, "message": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
