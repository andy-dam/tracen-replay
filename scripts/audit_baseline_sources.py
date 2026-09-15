"""Validate and seal source labels without loading analyzer predictions.

This verifies artifact integrity and reports declared review coverage. It cannot
prove a human viewed an image or that the labels exhaust every visible fact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def uncovered(start: int, end: int, intervals: list[dict]) -> list[list[int]]:
    cursor = start
    gaps = []
    for interval in sorted(intervals, key=lambda item: item["start_ms"]):
        left, right = max(start, interval["start_ms"]), min(end, interval["end_ms"])
        if right <= cursor:
            continue
        if left > cursor:
            gaps.append([cursor, left])
        cursor = right
    if cursor < end:
        gaps.append([cursor, end])
    return gaps


def audit_shard(root: Path, shard: str, seal: bool = False) -> dict:
    folder = root / shard
    source = read_json(folder / "labels.json")
    manifest = read_json(folder / "manifest.json")
    freeze = read_json(root / "freeze.json")
    frames = {str(Path(item["evidence"]).resolve()): item for item in manifest["frames"]}
    # Boundary evidence can belong to an adjacent source shard.
    for adjacent in sorted(root.glob("*/manifest.json")):
        for item in read_json(adjacent)["frames"]:
            frames.setdefault(str(Path(item["evidence"]).resolve()), item)
    errors = []
    if source["source_sha256"] != freeze["source_sha256"]:
        errors.append("Source video hash does not match freeze")
    start, end = source["start_ms"], source["end_ms"]
    labels = source["labels"]
    ids = [label["id"] for label in labels]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate label IDs")
    visual = {str(Path(path).resolve()) for path in source["visual_evidence"]}
    label_evidence = set()
    for label in labels:
        ident = label["id"]
        first, last = label["first_seen_ms"], label["last_seen_ms"]
        if not start <= first < end or last < first:
            errors.append(f"{ident}: invalid occurrence interval")
        if label.get("onset_after_ms", first - 1) >= first:
            errors.append(f"{ident}: onset bound must precede first positive")
        if label["category"] not in {"state", "effect", "action", "purchase", "concert_result"}:
            errors.append(f"{ident}: unsupported label category")
        if not label.get("expected") or not label.get("evidence"):
            errors.append(f"{ident}: missing expected values or evidence")
        for path in label.get("evidence", []):
            resolved = str(Path(path).resolve())
            label_evidence.add(resolved)
            if resolved not in visual:
                errors.append(f"{ident}: evidence absent from inspected-image list: {path}")
    evidence_hashes = {}
    for path in sorted(visual | label_evidence):
        if path not in frames:
            errors.append(f"Evidence absent from source manifests: {path}")
            continue
        p = Path(path)
        if not p.is_file():
            errors.append(f"Missing evidence image: {path}")
            continue
        actual = sha256(p)
        if actual != frames[path]["sha256"]:
            errors.append(f"Source evidence hash mismatch: {path}")
        evidence_hashes[path] = actual
    intervals = source["reviewed_intervals"]
    for item in intervals:
        if item["end_ms"] <= item["start_ms"] or item["sample_interval_ms"] <= 0:
            errors.append("Invalid declared review interval")
    gaps = uncovered(start, end, intervals)
    if gaps:
        errors.append(f"Chronological review has uncovered intervals: {gaps}")
    result = {
        "schema_version": "tracen-replay/source-label-audit-v1",
        "shard": shard,
        "source_sha256": source["source_sha256"],
        "labels_sha256": sha256(folder / "labels.json"),
        "manifest_sha256": sha256(folder / "manifest.json"),
        "freeze_sha256": sha256(root / "freeze.json"),
        "interval_ms": [start, end],
        "label_count": len(labels),
        "category_counts": dict(sorted(Counter(label["category"] for label in labels).items())),
        "kind_counts": dict(sorted(Counter(label["category"] + "/" + str(label["expected"].get("kind", label["expected"].get("channel"))) for label in labels).items())),
        "declared_review_intervals": intervals,
        "uncovered_intervals": gaps,
        "ungraded": source.get("ungraded", []),
        "unobservable": source.get("unobservable", []),
        "evidence_sha256": evidence_hashes,
        "errors": errors,
        "integrity_passed": not errors,
        "semantic_completeness_verified": False,
        "limitation": "Artifact and declared-coverage checks do not establish exhaustive source truth or native-frame recall. Read review methods and exclusions before using denominators.",
    }
    if seal:
        if errors:
            raise ValueError("Refusing to seal invalid labels: " + "; ".join(errors))
        seal_path = folder / "seal.json"
        if seal_path.exists():
            previous = read_json(seal_path)
            if previous != result:
                raise ValueError("Existing seal differs; preserve it and create a versioned correction")
        else:
            seal_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_dir", type=Path)
    parser.add_argument("--shard", required=True, choices=["source-a", "source-b", "source-c", "source-d", "tail-c", "tail-a", "middle-a"])
    parser.add_argument("--seal", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_shard(args.review_dir.resolve(), args.shard, args.seal)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
