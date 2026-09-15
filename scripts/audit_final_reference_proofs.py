"""Audit frozen source-proof paths against preserved report/frame metadata.

The audit is mechanical: it checks explicit evidence paths, timestamps, image
bytes and source hashes.  It does not decide whether a reviewer transcribed a
visible effect correctly or whether the review is complete.  Frame numbers are
never converted to timestamps.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import posixpath
from typing import Any, Iterable

SCHEMA = "tracen-replay/final-reliability-reference-proof-audit-v1"
REFERENCE_SCHEMA = "final-reliability-source-reference-v1"


def _json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json_hashed(path: Path) -> tuple[Any, str]:
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def sha256(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _key(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().replace("\\", "/")
    if value.startswith("./"):
        value = value[2:]
    return value if value.startswith("/") else posixpath.normpath(value)


def _evidence(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [item for item in values if _key(item)]


def _time(value: Any) -> int | float | None:
    return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value)) else None


def _row_time(row: dict[str, Any]) -> int | float | None:
    for name in ("source_timestamp_ms", "timestamp_ms", "capture_timestamp_ms", "time_ms"):
        if name in row:
            return _time(row[name])
    return None


def _relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _inside(path: Path, root: Path) -> bool:
    return _relative(path, root) is not None


def _keys(value: Any, origin: Path, root: Path) -> set[str]:
    raw = _key(value)
    if raw is None:
        return set()
    path = Path(raw)
    if path.is_absolute():
        relative = _relative(path, root)
        return {relative} if relative else set()
    keys = {raw}
    for candidate in (root / path, origin / path):
        relative = _relative(candidate, root)
        if relative:
            keys.add(relative)
    return keys


def _add(index: dict[str, list[dict[str, Any]]], key: str, timestamp: Any,
        source: str, metadata_path: Path | None = None) -> None:
    timestamp = _time(timestamp)
    if timestamp is None:
        return
    row = {"timestamp_ms": timestamp, "source": source}
    if metadata_path is not None:
        row["metadata_path"] = str(metadata_path.resolve())
    if row not in index[key]:
        index[key].append(row)


def _walk(value: Any, origin: Path, root: Path, index: dict[str, list[dict[str, Any]]],
          source: str, metadata_path: Path | None = None,
          wanted: set[str] | None = None) -> None:
    if isinstance(value, dict):
        timestamp = _row_time(value)
        for evidence in _evidence(value.get("evidence")):
            raw = _key(evidence)
            if timestamp is None or (wanted is not None and raw not in wanted and not any(
                    item.endswith("/" + raw) for item in wanted if raw)):
                continue
            for key in _keys(evidence, origin, root):
                if wanted is None or key in wanted:
                    _add(index, key, timestamp, source, metadata_path)
        for child in value.values():
            _walk(child, origin, root, index, source, metadata_path, wanted)
    elif isinstance(value, list):
        for child in value:
            _walk(child, origin, root, index, source, metadata_path, wanted)


def _report_index(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = report.get("gameplay_tracking", {}).get("readings", [])
    rows = list(rows) if isinstance(rows, list) else []
    frames = report.get("frames", [])
    if isinstance(frames, list):
        rows.extend((row, "before_report.frames") for row in frames if isinstance(row, dict))
    for item in rows:
        source, row = "before_report.gameplay_tracking.readings", item
        if isinstance(item, tuple):
            row, source = item
        if not isinstance(row, dict):
            continue
        timestamp = _row_time(row)
        if timestamp is None:
            continue
        for evidence in _evidence(row.get("evidence")):
            key = _key(evidence)
            if key:
                _add(index, key, timestamp, source)
    return index


def _source_sha(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    if isinstance(source, dict) and isinstance(source.get("sha256"), str):
        return source["sha256"]
    return value.get("source_sha256") if isinstance(value.get("source_sha256"), str) else None


def _index_files(paths: Iterable[Path], root: Path, wanted: set[str]) -> tuple[
        dict[str, list[dict[str, Any]]], list[Path], list[str], list[str]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    included, source_hashes, errors = [], [], []
    for path in paths:
        path = Path(path).resolve()
        if not _inside(path, root):
            errors.append(f"metadata_outside_evidence_root:{path}")
            continue
        if not path.is_file():
            errors.append(f"metadata_missing:{path}")
            continue
        included.append(path)
        try:
            payload = _json(path)
        except (OSError, ValueError) as exc:
            errors.append(f"metadata_invalid:{path}:{exc}")
            continue
        digest = _source_sha(payload)
        if digest:
            source_hashes.append(digest)
        _walk(payload, path.parent, root, index,
              f"{path.name}:{_relative(path, root) or path.name}", path, wanted)
    return index, included, source_hashes, errors


def _dense_paths(root: Path, images: Iterable[Path]) -> list[Path]:
    found: set[Path] = set()
    root = root.resolve()
    for image in images:
        if not _inside(image, root):
            continue
        sidecar = image.with_suffix(".json")
        if sidecar.is_file() and _inside(sidecar, root):
            found.add(sidecar.resolve())
        cursor = image.parent.resolve()
        while cursor != root and _inside(cursor, root):
            aggregate = cursor.with_suffix(".json")
            if aggregate.is_file() and _inside(aggregate, root):
                found.add(aggregate.resolve())
            cursor = cursor.parent
    return sorted(found, key=lambda path: path.as_posix())


def _image_path(root: Path, evidence: str) -> tuple[Path | None, str | None]:
    raw = _key(evidence)
    if raw is None:
        return None, "invalid_evidence_path"
    path = Path(raw) if Path(raw).is_absolute() else root / raw
    path = path.resolve()
    if not _inside(path, root):
        return None, "evidence_outside_root"
    return path, None if path.is_file() else "image_missing"


def _verify_image(path: Path) -> dict[str, Any]:
    """Verify image bytes with Pillow; Pillow is an optional project extra."""
    try:
        from PIL import Image
    except ImportError as exc:
        return {"ok": False, "reason": "PIL_UNAVAILABLE", "error": str(exc)}
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return {"ok": True, "format": image.format, "size": list(image.size)}
    except Exception as exc:  # Pillow format readers use several exception types.
        return {"ok": False, "reason": "PIL_INVALID_IMAGE", "error": str(exc)}


def _proofs(reference: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"case_id": case.get("case_id"), "observation_id": observation.get("id"),
             "status": observation.get("status"),
             "expected_interval_ms": [observation.get("start_ms"), observation.get("end_ms")],
             "evidence": evidence}
            for case in reference.get("cases", []) if isinstance(case, dict)
            for observation in case.get("observations", []) if isinstance(observation, dict)
            for evidence in _evidence(observation.get("evidence"))]


def audit_reference_proofs(reference_path: str | Path, report_path: str | Path,
                           evidence_root: str | Path | None = None,
                           capture_paths: Iterable[str | Path] | None = None) -> dict[str, Any]:
    """Audit one source-reference document and return scoped proof findings."""
    reference_path, report_path = Path(reference_path).resolve(), Path(report_path).resolve()
    reference, reference_hash = _json_hashed(reference_path)
    report, report_hash = _json_hashed(report_path)
    if not isinstance(reference, dict) or reference.get("schema_version") != REFERENCE_SCHEMA:
        raise ValueError(f"Unsupported source-reference document: {reference_path}")
    if not isinstance(report, dict):
        raise ValueError(f"Expected report object: {report_path}")
    run = reference.get("run")
    if evidence_root is None:
        candidate = reference_path.parent.parent.parent / "full-recording" / str(run)
        evidence_root = candidate if candidate.exists() else reference_path.parent
    root = Path(evidence_root).resolve()
    if not root.is_dir():
        raise ValueError(f"Evidence root is missing: {root}")

    proofs = _proofs(reference)
    wanted = {_key(row["evidence"]) for row in proofs if _key(row["evidence"])}
    report_index = _report_index(report)
    if capture_paths is None:
        captures = sorted(root.rglob("capture.json"), key=lambda path: path.as_posix())
    else:
        if isinstance(capture_paths, (str, Path)):
            capture_paths = [capture_paths]
        captures = [Path(path).resolve() for path in capture_paths]
    capture_index, capture_metadata, capture_sources, errors = _index_files(captures, root, wanted)
    proof_images = [_image_path(root, row["evidence"])[0] for row in proofs]
    proof_images = [path for path in proof_images if path is not None]
    dense_metadata_paths = _dense_paths(root, proof_images)
    dense_index, dense_metadata, _, dense_errors = _index_files(dense_metadata_paths, root, wanted)
    errors.extend(dense_errors)

    image_map = reference.get("image_sha256")
    image_map = image_map if isinstance(image_map, dict) else {}
    expected_images = {_key(path) or path: digest for path, digest in image_map.items()}
    image_paths = {str(path): path for path in proof_images}
    image_checks: dict[str, dict[str, Any]] = {}
    manifest_paths = [reference_path, report_path, *capture_metadata, *dense_metadata,
                      *image_paths.values()]
    for key, path in image_paths.items():
        if not path.is_file():
            image_checks[key] = {"ok": False, "reason": "image_missing"}
            continue
        check = _verify_image(path)
        check["sha256"] = sha256(path)
        image_checks[key] = check

    hashes = {str(path.resolve()): sha256(path) for path in set(manifest_paths) if path.is_file()}
    hashes[str(reference_path)] = reference_hash
    hashes[str(report_path)] = report_hash
    mismatches = [{"kind": "metadata", "reason": error} for error in errors]
    source_hash = reference.get("source_sha256")
    report_source_hash = _source_sha(report)
    if source_hash != report_source_hash:
        mismatches.append({"kind": "source", "reason": "report_source_hash_mismatch",
                           "reference_source_sha256": source_hash,
                           "report_source_sha256": report_source_hash})
    for capture_source in sorted(set(capture_sources)):
        if capture_source != source_hash:
            mismatches.append({"kind": "source", "reason": "capture_source_hash_mismatch",
                               "reference_source_sha256": source_hash,
                               "capture_source_sha256": capture_source})
    reference_after = sha256(reference_path) if reference_path.is_file() else None
    report_after = sha256(report_path) if report_path.is_file() else None
    if reference_after != reference_hash:
        mismatches.append({"kind": "manifest", "reason": "reference_changed_during_audit",
                           "reference_sha256_before": reference_hash,
                           "reference_sha256_after": reference_after})
    if report_after != report_hash:
        mismatches.append({"kind": "manifest", "reason": "report_changed_during_audit",
                           "report_sha256_before": report_hash,
                           "report_sha256_after": report_after})

    image_failures: dict[str, list[str]] = defaultdict(list)
    for row in proofs:
        path, image_error = _image_path(root, row["evidence"])
        if image_error:
            image_failures[row["evidence"]].append(image_error)
        else:
            check = image_checks.get(str(path), {})
            if not check.get("ok"):
                image_failures[row["evidence"]].append(check.get("reason", "image_unverified"))
            expected = expected_images.get(_key(row["evidence"]) or row["evidence"])
            if expected and check.get("sha256") != expected:
                image_failures[row["evidence"]].append("image_hash_mismatch")
        if isinstance(reference.get("image_sha256"), dict) and (
                _key(row["evidence"]) or row["evidence"]) not in expected_images:
            image_failures[row["evidence"]].append("missing_declared_image_hash")
    for key in image_failures:
        image_failures[key] = list(dict.fromkeys(image_failures[key]))

    matched, unmapped = [], []
    proof_keys = {_key(row["evidence"]) for row in proofs}
    for row in proofs:
        key = _key(row["evidence"])
        report_rows = report_index.get(key, []) if key else []
        capture_rows = capture_index.get(key, []) if key else []
        dense_rows = dense_index.get(key, []) if key else []
        supplemental = capture_rows + [item for item in dense_rows if item not in capture_rows]
        chosen = report_rows or supplemental
        all_rows = report_rows + supplemental
        base = dict(row, mapped_from=sorted({item["source"] for item in chosen}),
                    timestamps_ms=sorted({item["timestamp_ms"] for item in chosen}),
                    checked_sources=sorted({item["source"] for item in all_rows}),
                    checked_timestamps_ms=sorted({item["timestamp_ms"] for item in all_rows}))
        if not chosen:
            base["reason"] = "no_explicit_report_or_capture_timestamp"
            if image_failures.get(row["evidence"]):
                base["image_issues"] = image_failures[row["evidence"]]
            unmapped.append(base)
            continue
        issues = []
        report_times = {item["timestamp_ms"] for item in report_rows}
        supplemental_times = {item["timestamp_ms"] for item in supplemental}
        if len(report_times) > 1:
            issues.append({"reason": "conflicting_report_timestamps",
                           "timestamps_ms": sorted(report_times)})
        if report_rows and supplemental and report_times != supplemental_times:
            issues.append({"reason": "report_capture_timestamp_conflict",
                           "report_timestamps_ms": sorted(report_times),
                           "capture_timestamps_ms": sorted(supplemental_times)})
        start, end = row["expected_interval_ms"]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   for value in (start, end)):
            issues.append({"reason": "invalid_observation_interval"})
        elif any(timestamp < start or timestamp > end for timestamp in
                 ({item["timestamp_ms"] for item in chosen})):
            issues.append({"reason": "timestamp_outside_observation_interval",
                           "expected_interval_ms": [start, end],
                           "timestamps_ms": sorted({item["timestamp_ms"] for item in chosen})})
        issues.extend({"reason": reason} for reason in image_failures.get(row["evidence"], []))
        if issues:
            mismatches.append(dict(base, issues=issues))
        else:
            matched.append(dict(base, image=image_checks.get(
                str(_image_path(root, row["evidence"])[0]), {})))

    manifest_paths = sorted({str(path.resolve()) for path in manifest_paths})
    image_verification = {
        "pil_available": not any(item.get("reason") == "PIL_UNAVAILABLE"
                                  for item in image_checks.values()),
        "files_checked": len(image_checks),
        "files_valid": sum(bool(item.get("ok")) for item in image_checks.values()),
        "files_invalid": sum(not bool(item.get("ok")) for item in image_checks.values()),
        "checks": image_checks,
    }
    result = {
        "schema_version": SCHEMA, "run": run,
        "reference_path": str(reference_path), "report_path": str(report_path),
        "evidence_root": str(root), "source_sha256": source_hash,
        "reference_sha256": reference_hash, "report_sha256": report_hash,
        "input_hashes": {"reference_before": reference_hash, "reference_after": reference_after,
                          "report_before": report_hash, "report_after": report_after},
        "source_manifest_paths": manifest_paths, "source_manifest_sha256": hashes,
        "source_manifest": {"paths": manifest_paths, "sha256": hashes},
        "matched": matched, "mismatches": mismatches, "unmapped": unmapped,
        "image_verification": image_verification,
        "summary": {
            "proof_occurrences": len(proofs), "matched": len(matched),
            "mismatches": len(mismatches), "unmapped": len(unmapped),
            "unique_evidence_paths": len(proof_keys),
            "report_mapped_paths": len(set(report_index) & proof_keys),
            "capture_mapped_paths": len(set(capture_index) & proof_keys),
            "dense_metadata_mapped_paths": len(set(dense_index) & proof_keys),
        },
        "limitations": [
            "Path, timestamp, source-hash and image checks do not establish semantic review correctness.",
            "Unmapped evidence is left unknown; no timestamp is inferred from a frame number.",
        ],
    }
    return result


audit = audit_reference_proofs
audit_run = audit_reference_proofs


def audit_all(reference_dir: str | Path, report_dir: str | Path,
              evidence_dir: str | Path) -> list[dict[str, Any]]:
    reference_dir, report_dir, evidence_dir = map(Path, (reference_dir, report_dir, evidence_dir))
    results = []
    for path in sorted(reference_dir.glob("*.json")):
        document = _json(path)
        run = document.get("run") if isinstance(document, dict) else None
        if run:
            results.append(audit_reference_proofs(
                path, report_dir / f"{run}-report.json", evidence_dir / str(run)))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", "--references", dest="reference", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--capture", action="append", dest="capture_paths", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_reference_proofs(args.reference, args.report, args.evidence_root,
                                    args.capture_paths)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if result["mismatches"] or result["unmapped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
