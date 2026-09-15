"""Compare every stored JSON field in two same-source reports.

This is an additive diagnostic inventory.  It compares serialized report
content only; it does not recompute accounting or certify source correctness.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "tracen-replay/final-report-structure-comparison-v1"
_MISSING = object()


def _canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Report contains a value that cannot be canonically hashed.") from exc
    return encoded.encode("utf-8")


def canonical_value_sha256(value: Any) -> str:
    """Return the stable hash used for changed-value references."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _report_sha256(report: Any) -> str:
    return canonical_value_sha256(report)


def _source_sha256(report: dict[str, Any]) -> str | None:
    source = report.get("source")
    if isinstance(source, dict) and isinstance(source.get("sha256"), str):
        return source["sha256"]
    value = report.get("source_sha256")
    return value if isinstance(value, str) else None


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _pointer(parent: str, token: str | int) -> str:
    escaped = _escape_pointer_token(str(token))
    return f"/{escaped}" if not parent else f"{parent}/{escaped}"


def _type_name(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _value_ref(value: Any, pointer: str | None, report_sha256: str) -> dict[str, Any]:
    present = value is not _MISSING
    return {
        "pointer": pointer,
        "report_sha256": report_sha256,
        "present": present,
        "type": _type_name(value),
        "value_sha256": canonical_value_sha256(value) if present else None,
    }


def _same_value(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    return canonical_value_sha256(left) == canonical_value_sha256(right)


def _change(
    kind: str,
    pointer: str,
    before: Any,
    after: Any,
    before_sha256: str,
    after_sha256: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "pointer": pointer,
        # The top-level pointer identifies the changed location on the
        # present side.  An absent counterpart has no JSON location: reusing
        # the numeric list index could falsely point at a different row after
        # insertion or relocation.
        "before": _value_ref(
            before,
            pointer if before is not _MISSING else None,
            before_sha256,
        ),
        "after": _value_ref(
            after,
            pointer if after is not _MISSING else None,
            after_sha256,
        ),
    }


def _diff_value(
    before: Any,
    after: Any,
    pointer: str,
    before_sha256: str,
    after_sha256: str,
    changes: list[dict[str, Any]],
    ordering_changes: list[dict[str, Any]],
) -> None:
    if before is _MISSING or after is _MISSING:
        if before is after:
            return
        changes.append(_change(
            "added" if before is _MISSING else "removed",
            pointer,
            before,
            after,
            before_sha256,
            after_sha256,
        ))
        return
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after), key=lambda item: _escape_pointer_token(str(item)))
        for key in keys:
            child = _pointer(pointer, key)
            _diff_value(
                before.get(key, _MISSING),
                after.get(key, _MISSING),
                child,
                before_sha256,
                after_sha256,
                changes,
                ordering_changes,
            )
        return
    if isinstance(before, list) and isinstance(after, list):
        _diff_list(
            before,
            after,
            pointer,
            before_sha256,
            after_sha256,
            changes,
            ordering_changes,
        )
        return
    if not _same_value(before, after):
        changes.append(_change(
            "changed",
            pointer,
            before,
            after,
            before_sha256,
            after_sha256,
        ))


def _diff_list(
    before: list[Any],
    after: list[Any],
    pointer: str,
    before_sha256: str,
    after_sha256: str,
    changes: list[dict[str, Any]],
    ordering_changes: list[dict[str, Any]],
) -> None:
    """Diff list content with one-to-one exact-row matching.

    Matching exact canonical rows first prevents an insertion or relocation
    from turning all following rows into false changes.  Every relocation is
    still emitted as an ordering entry, and duplicate rows consume separate
    queue positions so count changes remain visible.
    """

    positions: dict[str, deque[int]] = defaultdict(deque)
    for index, value in enumerate(after):
        positions[canonical_value_sha256(value)].append(index)

    pairs: list[tuple[int, int]] = []
    matched_after: set[int] = set()
    for before_index, value in enumerate(before):
        queue = positions.get(canonical_value_sha256(value))
        if queue:
            after_index = queue.popleft()
            matched_after.add(after_index)
            pairs.append((before_index, after_index))
            if before_index != after_index:
                before_pointer = _pointer(pointer, before_index)
                after_pointer = _pointer(pointer, after_index)
                ordering_changes.append({
                    "kind": "relocated",
                    "list_pointer": pointer,
                    "before_index": before_index,
                    "after_index": after_index,
                    "before": _value_ref(value, before_pointer, before_sha256),
                    "after": _value_ref(after[after_index], after_pointer, after_sha256),
                })

    matched_before = {before_index for before_index, _ in pairs}
    unmatched_before = [index for index in range(len(before)) if index not in matched_before]
    unmatched_after = [index for index in range(len(after)) if index not in matched_after]

    # A same-index pair is a structural replacement, not an identity match.
    # Keep the complete row reachable through one hash-bound pair rather than
    # expanding every nested reading field; a row at a different index is
    # either a relocation (already matched) or a genuine add/remove.
    after_by_index = set(unmatched_after)
    paired_unmatched: set[int] = set()
    for before_index in unmatched_before:
        if before_index not in after_by_index:
            continue
        after_index = before_index
        paired_unmatched.add(after_index)
        child = _pointer(pointer, before_index)
        changes.append(_change(
            "replaced_at_index",
            child,
            before[before_index],
            after[after_index],
            before_sha256,
            after_sha256,
        ))

    for before_index in unmatched_before:
        if before_index in paired_unmatched:
            continue
        child = _pointer(pointer, before_index)
        changes.append(_change(
            "removed",
            child,
            before[before_index],
            _MISSING,
            before_sha256,
            after_sha256,
        ))
    for after_index in unmatched_after:
        if after_index in paired_unmatched:
            continue
        child = _pointer(pointer, after_index)
        changes.append(_change(
            "added",
            child,
            _MISSING,
            after[after_index],
            before_sha256,
            after_sha256,
        ))


def compare(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    before_sha256: str | None = None,
    after_sha256: str | None = None,
) -> dict[str, Any]:
    """Return a complete stored-JSON diff for two reports.

    The optional report hashes are the exact input-byte hashes supplied by the
    CLI.  Direct callers get canonical object hashes, which still bind every
    pointer to the precise in-memory report passed to this function.
    """

    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("Reports must be JSON objects.")
    before_source = _source_sha256(before)
    after_source = _source_sha256(after)
    if not before_source or not after_source or before_source != after_source:
        raise ValueError("Cannot compare reports from different source recordings.")
    before_report_sha = before_sha256 or _report_sha256(before)
    after_report_sha = after_sha256 or _report_sha256(after)
    changes: list[dict[str, Any]] = []
    ordering_changes: list[dict[str, Any]] = []
    _diff_value(
        before,
        after,
        "",
        before_report_sha,
        after_report_sha,
        changes,
        ordering_changes,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "additive_diagnostic_only",
        "source_sha256": before_source,
        "input_sha256": {
            "before": before_report_sha,
            "after": after_report_sha,
        },
        "complete_stored_json_diff": True,
        "changes": changes,
        "ordering_changes": ordering_changes,
        "changed_value_count": len(changes),
        "ordering_change_count": len(ordering_changes),
        "review_required": bool(changes or ordering_changes),
        "scope": "All stored report JSON fields, including states, purchases, inventories, and observation metadata.",
        "limits": [
            "This inventory does not verify that a changed value is source-correct.",
            "This comparison does not recompute a baseline or certify completeness.",
            "Object member order is not semantic; list relocation is reported explicitly.",
        ],
    }


def _load_input(path: Path) -> tuple[dict[str, Any], str]:
    try:
        data = path.read_bytes()
        report = json.loads(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load report: {path}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"Report must be a JSON object: {path}")
    return report, hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, before_sha = _load_input(args.before)
    after, after_sha = _load_input(args.after)
    result = compare(
        before,
        after,
        before_sha256=before_sha,
        after_sha256=after_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"Refusing to overwrite diagnostic output: {args.output}") from exc
    print(json.dumps({
        "output": str(args.output),
        "source_sha256": result["source_sha256"],
        "changed_value_count": result["changed_value_count"],
        "ordering_change_count": result["ordering_change_count"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
