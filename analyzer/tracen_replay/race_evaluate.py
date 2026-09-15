"""Evaluate source-reviewed race result references in a bounded time window.

The reference format is ``tracen-replay/race-reference-v1``::

    {
      "schema_version": "tracen-replay/race-reference-v1",
      "source_sha256": "...",
      "scope": "one reviewed race-result interval",
      "start_ms": 150000,
      "end_ms": 160000,
      "races": [{
        "start_ms": 157000,
        "end_ms": 159250,
        "expected": {
          "race_name": "Junior Make Debut",
          "placing": 1,
          "fans": 1244,
          "fans_gained": 1243,
          "course": {
            "venue": "Hanshin",
            "surface": "turf",
            "distance_m": 1600,
            "distance_category": "mile",
            "direction": "right",
            "variant": "outer"
          }
        },
        "proofs": [{"source_timestamp_ms": 158000,
                     "evidence": "gameplay/frame.png",
                     "sha256": "..."}],
        "item_snapshots": [{
          "source_timestamp_ms": 158000,
          "quantities": [200],
          "proofs": [{"source_timestamp_ms": 158000,
                      "evidence": "gameplay/frame.png",
                      "sha256": "..."}]
        }]
      }]
    }

Each race is matched to at most one report race using only the time-window
overlap.  Expected values are compared only after that assignment, so a
candidate with the right text cannot win over a second candidate in the same
window.  A field set to ``{"unknown": true}`` (``null`` is accepted as a
short form) is explicitly unscored; an observed zero remains a real integer
and is never treated as unknown.

``item_snapshots`` compare the quantities visible in one reading.  They do
not add quantities across frames, read the inventory panel, infer item names,
or claim that the visible list is complete.  A reference with no races is
allowed only when ``no_races`` and ``independently_reviewed`` are both true
and it contains at least one top-level proof.

The result denominators include every reference race and item snapshot.
Unmatched or ambiguous races contribute to ``unobserved_fields`` and
``unobserved_items`` instead of disappearing from the score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "tracen-replay/race-reference-v1"
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_RACE_FIELDS = ("race_name", "placing", "fans", "fans_gained")
_COURSE_FIELDS = (
    "venue",
    "surface",
    "distance_m",
    "distance_category",
    "direction",
    "variant",
    "condition",
)


def _mapping(value: Any, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    return value


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _interval(value: Mapping[str, Any], label: str) -> tuple[int, int]:
    start, end = value.get("start_ms"), value.get("end_ms")
    if not _integer(start) or not _integer(end) or start < 0 or start >= end:
        raise ValueError(f"Invalid {label} interval.")
    return start, end


def _unknown(value: Any) -> bool:
    return value is None or value == {"unknown": True}


def _typed_expected(field: str, value: Any) -> bool:
    if _unknown(value):
        return True
    if field == "race_name":
        return isinstance(value, str) and bool(value)
    if field in ("placing", "fans", "fans_gained", "distance_m"):
        return _integer(value) and value >= 0
    return isinstance(value, str) and bool(value)


def _proofs(
    proofs: Any,
    label: str,
    outer: tuple[int, int],
    inner: tuple[int, int] | None = None,
) -> list[Mapping[str, Any]]:
    if not isinstance(proofs, list) or not proofs:
        raise ValueError(f"{label} requires at least one timestamped proof.")
    seen: set[int] = set()
    result: list[Mapping[str, Any]] = []
    for index, raw in enumerate(proofs):
        proof = _mapping(raw, f"Malformed {label} proof {index}.")
        timestamp = proof.get("source_timestamp_ms")
        evidence = proof.get("evidence")
        digest = proof.get("sha256")
        if not _integer(timestamp) or not outer[0] <= timestamp < outer[1]:
            raise ValueError(f"{label} proof timestamp lies outside scope.")
        if inner is not None and not inner[0] <= timestamp < inner[1]:
            raise ValueError(f"{label} proof timestamp lies outside race window.")
        if timestamp in seen:
            raise ValueError(f"Duplicate {label} proof timestamps.")
        if not isinstance(evidence, str) or not evidence:
            raise ValueError(f"{label} proof requires an evidence path.")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError(f"{label} proof requires a SHA-256 hash.")
        seen.add(timestamp)
        result.append(proof)
    return result


def _expected_fields(expected: Any, label: str) -> list[tuple[str, Any]]:
    expected = _mapping(expected, f"{label} expected fields must be an object.")
    unknown_keys = set(expected) - set(_RACE_FIELDS) - {"course"}
    if unknown_keys:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown_keys)}.")

    flattened: list[tuple[str, Any]] = []
    concrete = 0
    for field in _RACE_FIELDS:
        if field not in expected:
            continue
        value = expected[field]
        if not _typed_expected(field, value):
            raise ValueError(f"Invalid expected type for {label}.{field}.")
        flattened.append((field, value))
        concrete += not _unknown(value)

    if "course" in expected:
        course = expected["course"]
        if _unknown(course):
            flattened.append(("course", course))
            concrete += not _unknown(course)
        else:
            course = _mapping(course, f"{label}.course must be an object.")
            unknown_keys = set(course) - set(_COURSE_FIELDS)
            if unknown_keys:
                raise ValueError(f"Unknown {label}.course fields: {sorted(unknown_keys)}.")
            course_values = {}
            for field in _COURSE_FIELDS:
                if field not in course:
                    continue
                value = course[field]
                if not _typed_expected(field, value):
                    raise ValueError(f"Invalid expected type for {label}.course.{field}.")
                course_values[field] = value
                concrete += not _unknown(value)
            flattened.extend((f"course.{field}", value) for field, value in course_values.items())

    if not flattened or concrete == 0:
        raise ValueError(f"{label} must contain at least one concrete expected field.")
    return flattened


def _validate_reference(reference: Mapping[str, Any]) -> tuple[tuple[int, int], list[Mapping[str, Any]], bool]:
    if reference.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported race reference schema.")
    source_hash = reference.get("source_sha256")
    if not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash):
        raise ValueError("Race reference requires a source SHA-256 hash.")
    scope = reference.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("Race reference requires a scope description.")
    outer = _interval(reference, "reference scope")
    races = reference.get("races")
    if not isinstance(races, list):
        raise ValueError("Race reference requires a races list.")
    negative = reference.get("no_races") is True
    if reference.get("no_races") not in (None, False, True):
        raise ValueError("no_races must be boolean.")
    if reference.get("independently_reviewed") not in (None, False, True):
        raise ValueError("independently_reviewed must be boolean.")
    if negative and races:
        raise ValueError("Negative race reference cannot contain races.")
    if not races and not negative:
        raise ValueError("Empty race references require an explicit reviewed negative.")

    race_windows: list[tuple[int, int, int]] = []
    for index, raw in enumerate(races):
        race = _mapping(raw, f"Malformed race reference {index}.")
        start, end = _interval(race, f"race reference {index}")
        if start < outer[0] or end > outer[1]:
            raise ValueError(f"Race reference {index} lies outside scope.")
        race_windows.append((start, end, index))
        _expected_fields(race.get("expected"), f"race reference {index}")
        _proofs(race.get("proofs"), f"race reference {index}", outer, (start, end))
        snapshots = race.get("item_snapshots", [])
        if not isinstance(snapshots, list):
            raise ValueError(f"Race reference {index} item_snapshots must be a list.")
        for item_index, raw_item in enumerate(snapshots):
            item = _mapping(raw_item, f"Malformed item snapshot {index}/{item_index}.")
            timestamp = item.get("source_timestamp_ms")
            if not _integer(timestamp) or not start <= timestamp < end:
                raise ValueError(f"Item snapshot {index}/{item_index} lies outside race window.")
            quantities = item.get("quantities")
            if not isinstance(quantities, list) or any(
                not _integer(quantity) or quantity < 0 for quantity in quantities
            ):
                raise ValueError(f"Item snapshot {index}/{item_index} requires typed quantities.")
            sections = item.get("sections")
            if "sections" in item:
                if (not isinstance(sections, dict) or not sections
                    or any(key not in ("items", "bonus") for key in sections)
                    or any(not isinstance(values, list) or any(not _integer(v) or v < 0 for v in values)
                           for values in sections.values())
                    or sorted(v for values in sections.values() for v in values) != sorted(quantities)):
                    raise ValueError("Reward sections must partition the typed snapshot quantities.")
            _proofs(item.get("proofs"), f"Item snapshot {index}/{item_index}", outer, (timestamp, timestamp + 1))
            if any(proof["source_timestamp_ms"] != timestamp for proof in item["proofs"]):
                raise ValueError(f"Item snapshot {index}/{item_index} proofs must use its timestamp.")

    race_windows.sort()
    for previous, current in zip(race_windows, race_windows[1:]):
        if current[0] < previous[1]:
            raise ValueError("Overlapping race reference windows are ambiguous.")

    if negative:
        if reference.get("independently_reviewed") is not True:
            raise ValueError("Negative race references require independent review.")
        _proofs(reference.get("proofs"), "Negative race reference", outer)
    elif "proofs" in reference:
        raise ValueError("Top-level proofs are reserved for negative references.")
    return outer, races, negative


def _actual(candidate: Mapping[str, Any], path: str) -> Any:
    current: Any = candidate
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _actual_matches(field: str, expected: Any, actual: Any) -> bool:
    """Compare a concrete field without Python's bool/int coercion."""

    if field in ("placing", "fans", "fans_gained", "course.distance_m"):
        return _integer(actual) and actual == expected
    return isinstance(actual, str) and actual == expected


def _overlap(first: int, last: int, start: int, end: int) -> bool:
    """Return whether an observed race span belongs to a half-open window.

    Reference scopes and race windows use ``[start, end)`` semantics.  A
    report span includes the sampled frames at both ``first`` and ``last``.
    Therefore a result observed before a boundary and still visible at exactly
    ``start`` is carry-in owned by the preceding window, while a result first
    observed at ``start`` belongs to the new window.  This excludes boundary-only
    carry-in without dropping a result that genuinely straddles a boundary or
    begins on it. Spans crossing a boundary can still overlap both windows.
    """

    return first < end and (first == start or last > start)


def _verify_proofs(
    proofs: Sequence[Mapping[str, Any]],
    readings: Mapping[int, list[Mapping[str, Any]]],
    root: Path | None,
    label: str,
) -> list[str]:
    errors: list[str] = []
    for proof in proofs:
        timestamp = proof["source_timestamp_ms"]
        rows = readings.get(timestamp, [])
        if not rows:
            errors.append(f"{label}: missing annotated sample {timestamp}")
        elif len(rows) != 1:
            errors.append(f"{label}: ambiguous annotated sample {timestamp}")
        elif rows[0].get("evidence") != proof["evidence"]:
            errors.append(f"{label}: annotated evidence mismatch at {timestamp}")
        if root is None:
            continue
        path = (root / proof["evidence"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Race evidence path leaves run directory.")
        if not path.is_file():
            errors.append(f"proof changed: {proof['evidence']}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest().lower() != proof["sha256"].lower():
            errors.append(f"proof changed: {proof['evidence']}")
    return errors


def _check_fields(
    expected: Mapping[str, Any], candidate: Mapping[str, Any], race_index: int
) -> tuple[list[dict[str, Any]], list[str]]:
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    for field, wanted in _expected_fields(expected, f"race reference {race_index}"):
        actual = _actual(candidate, field)
        if _unknown(wanted):
            status = "unknown" if actual is None else "unscored"
        elif actual is None:
            status = "missing"
            errors.append(f"missing field: {field}")
        elif _actual_matches(field, wanted, actual):
            status = "matched"
        else:
            status = "incorrect"
            errors.append(f"incorrect field: {field}")
        checks.append(
            {
                "field": field,
                "expected": wanted,
                "actual": actual,
                "status": status,
            }
        )
    return checks, errors


def _check_items(
    snapshots: Sequence[Mapping[str, Any]],
    readings: Mapping[int, list[Mapping[str, Any]]],
    root: Path | None,
    race_index: int,
    candidate: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for item_index, item in enumerate(snapshots):
        timestamp = item["source_timestamp_ms"]
        rows = readings.get(timestamp, [])
        actual_quantities: list[int] | None = None
        item_errors = _verify_proofs(item["proofs"], readings, root, f"item {race_index}/{item_index}")
        if len(rows) == 1:
            facts = rows[0].get("facts")
            visible = facts.get("visible_item_quantities") if isinstance(facts, Mapping) else None
            if isinstance(visible, list) and all(
                isinstance(entry, Mapping) and _integer(entry.get("quantity")) and entry["quantity"] >= 0
                for entry in visible
            ):
                actual_quantities = sorted(entry["quantity"] for entry in visible)
            elif visible is not None:
                item_errors.append(f"item {race_index}/{item_index}: malformed visible quantities")
        if not rows:
            item_errors.append(f"item {race_index}/{item_index}: missing annotated sample {timestamp}")
        elif len(rows) != 1:
            item_errors.append(f"item {race_index}/{item_index}: ambiguous annotated sample {timestamp}")
        wanted = sorted(item["quantities"])
        if actual_quantities is None:
            status = "missing"
            item_errors.append(f"missing visible item quantities at {timestamp}")
        elif actual_quantities == wanted:
            status = "matched"
        else:
            status = "incorrect"
            item_errors.append(f"incorrect item quantities at {timestamp}")
        section_result = {}
        if "sections" in item:
            from .race_section_layout import _box as valid_reward_box
            wanted_sections = {k: sorted(v) for k, v in item["sections"].items()}
            observations = []
            if len(rows) == 1 and actual_quantities is not None:
                for snapshot in (candidate or {}).get("visible_item_reward_snapshots", []):
                    for observation in snapshot.get("item_observations", []):
                        if (observation.get("source_timestamp_ms") == timestamp
                            and observation.get("evidence") == rows[0].get("evidence")):
                            observations.append(observation)
            actual_sections = None
            distributions = []
            for observation in observations:
                entries = observation.get("items", [])
                visible = rows[0].get("facts", {}).get("visible_item_quantities", [])
                # A section label must belong to the same observed quantity
                # and position, not merely another frame with the same total.
                if (not isinstance(entries, list) or len(entries) != len(visible)
                    or any(not isinstance(a, Mapping) or valid_reward_box(a.get("box")) is None
                           or a.get("quantity") != b.get("quantity")
                           or a.get("box") != b.get("box") for a, b in zip(entries, visible))):
                    continue
                distribution = {}
                for entry in entries:
                    section = entry.get("section")
                    if section not in ("items", "bonus"):
                        section = "unresolved"
                    distribution.setdefault(section, []).append(entry["quantity"])
                distribution = {k: sorted(v) for k, v in distribution.items()}
                if distribution not in distributions:
                    distributions.append(distribution)
            if len(distributions) == 1:
                actual_sections = distributions[0]
            if actual_sections != wanted_sections:
                item_errors.append(f"incorrect or unresolved reward sections at {timestamp}")
                status = "incorrect"
            section_result = dict(expected_sections=wanted_sections, actual_sections=actual_sections)
        errors.extend(item_errors)
        results.append(
            {
                "source_timestamp_ms": timestamp,
                "expected_quantities": wanted,
                "actual_quantities": actual_quantities,
                "status": status,
                "passed": not item_errors and status == "matched",
                "errors": item_errors,
                **section_result,
            }
        )
    return results, errors


def evaluate(reference: Mapping[str, Any], report: Mapping[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    """Evaluate one typed race reference against a gameplay-only report."""

    reference = _mapping(reference, "Race reference must be an object.")
    report = _mapping(report, "Race report must be an object.")
    scope, expected_races, negative = _validate_reference(reference)
    source = _mapping(report.get("source"), "Race report source is missing.")
    report_hash = source.get("sha256")
    if not isinstance(report_hash, str) or report_hash.lower() != reference["source_sha256"].lower():
        raise ValueError("Different source recordings.")
    duration = source.get("duration_ms")
    if duration is not None and (not _integer(duration) or duration < 0 or scope[1] > duration):
        raise ValueError("Race reference scope exceeds report source duration.")
    data = _mapping(report.get("gameplay_tracking"), "Gameplay tracking is missing.")
    if data.get("auxiliary_log_used") is not False:
        raise ValueError("Only gameplay-only reports are eligible.")
    report_races = data.get("races")
    readings_raw = data.get("readings")
    if not isinstance(report_races, list) or not isinstance(readings_raw, list):
        raise ValueError("Race reports require races and readings lists.")

    readings: dict[int, list[Mapping[str, Any]]] = {}
    reading_errors: list[str] = []
    for row in readings_raw:
        if not isinstance(row, Mapping) or not _integer(row.get("source_timestamp_ms")):
            reading_errors.append("malformed report reading")
            continue
        readings.setdefault(row["source_timestamp_ms"], []).append(row)

    candidates: list[tuple[int, Mapping[str, Any]]] = []
    invalid_predictions: list[Any] = []
    for index, raw in enumerate(report_races):
        if not isinstance(raw, Mapping) or not _integer(raw.get("first_seen_ms")) or not _integer(raw.get("last_seen_ms")):
            invalid_predictions.append(raw)
            continue
        first, last = raw["first_seen_ms"], raw["last_seen_ms"]
        if first > last:
            invalid_predictions.append(raw)
            continue
        if _overlap(first, last, scope[0], scope[1]):
            candidates.append((index, raw))

    expected_field_counts: list[int] = []
    expected_unscored_field_counts: list[int] = []
    expected_item_counts: list[int] = []
    for expected_index, expected_raw in enumerate(expected_races):
        expected = _mapping(expected_raw, f"Malformed race reference {expected_index}.")
        fields = _expected_fields(expected["expected"], f"race reference {expected_index}")
        expected_field_counts.append(sum(not _unknown(value) for _, value in fields))
        expected_unscored_field_counts.append(sum(_unknown(value) for _, value in fields))
        snapshots = expected.get("item_snapshots", [])
        expected_item_counts.append(len(snapshots))

    base = Path(root).resolve() if root is not None else None
    all_errors = reading_errors[:]
    race_results: list[dict[str, Any]] = []
    used: set[int] = set()
    ambiguous_races: list[dict[str, Any]] = []
    missing_races: list[int] = []
    unobserved_races: set[int] = set()
    ambiguous_candidate_indices: set[int] = set()

    if not negative:
        for candidate_index, candidate in candidates:
            reference_indices = [
                expected_index
                for expected_index, expected_raw in enumerate(expected_races)
                if _overlap(
                    candidate["first_seen_ms"],
                    candidate["last_seen_ms"],
                    expected_raw["start_ms"],
                    expected_raw["end_ms"],
                )
            ]
            if len(reference_indices) > 1:
                ambiguous_candidate_indices.add(candidate_index)
                ambiguous_races.append(
                    {
                        "candidate_index": candidate_index,
                        "reference_indices": reference_indices,
                    }
                )

    if negative:
        proof_errors = _verify_proofs(_proofs(reference["proofs"], "Negative race reference", scope), readings, base, "negative")
        all_errors.extend(proof_errors)
    else:
        for expected_index, expected_raw in enumerate(expected_races):
            expected = _mapping(expected_raw, f"Malformed race reference {expected_index}.")
            start, end = expected["start_ms"], expected["end_ms"]
            matches = [
                (index, candidate)
                for index, candidate in candidates
                if index not in used
                and index not in ambiguous_candidate_indices
                and _overlap(candidate["first_seen_ms"], candidate["last_seen_ms"], start, end)
            ]
            if len(matches) != 1:
                if len(matches) > 1:
                    ambiguous_races.append(
                        {
                            "reference_index": expected_index,
                            "candidate_indices": [index for index, _ in matches],
                        }
                    )
                else:
                    missing_races.append(expected_index)
                unobserved_races.add(expected_index)
                race_results.append(
                    {
                        "reference_index": expected_index,
                        "candidate_indices": [index for index, _ in matches],
                        "expected_field_count": expected_field_counts[expected_index],
                        "expected_unscored_field_count": expected_unscored_field_counts[expected_index],
                        "expected_item_count": expected_item_counts[expected_index],
                        "unobserved_field_count": expected_field_counts[expected_index],
                        "unobserved_item_count": expected_item_counts[expected_index],
                        "passed": False,
                        "errors": ["ambiguous race candidates" if len(matches) > 1 else "missing race candidate"],
                    }
                )
                continue

            candidate_index, candidate = matches[0]
            used.add(candidate_index)
            checks, field_errors = _check_fields(expected["expected"], candidate, expected_index)
            proof_errors = _verify_proofs(
                _proofs(expected["proofs"], f"race reference {expected_index}", scope, (start, end)),
                readings,
                base,
                f"race {expected_index}",
            )
            item_results, item_errors = _check_items(expected.get("item_snapshots", []), readings, base, expected_index, candidate)
            errors = field_errors + proof_errors + item_errors
            if candidate.get("conflicting_readings"):
                errors.append("candidate has conflicting readings")
            race_results.append(
                {
                    "reference_index": expected_index,
                    "actual_id": candidate.get("id"),
                    "expected_field_count": expected_field_counts[expected_index],
                    "expected_unscored_field_count": expected_unscored_field_counts[expected_index],
                    "expected_item_count": expected_item_counts[expected_index],
                    "actual_window": {
                        "start_ms": candidate["first_seen_ms"],
                        "end_ms": candidate["last_seen_ms"],
                    },
                    "checks": checks,
                    "item_snapshots": item_results,
                    "passed": not errors,
                    "errors": errors,
                }
            )
            all_errors.extend(errors)

    unexpected = [candidate for index, candidate in candidates if index not in used]
    if unexpected:
        all_errors.append("unexpected race candidates in scope")
    if invalid_predictions:
        all_errors.append("malformed race candidates")
    if ambiguous_races:
        all_errors.append("ambiguous race candidates")
    if missing_races:
        all_errors.append("missing race candidates")

    concrete_checks = [
        check
        for result in race_results
        for check in result.get("checks", [])
        if check["status"] not in ("unknown", "unscored")
    ]
    matched_fields = sum(check["status"] == "matched" for check in concrete_checks)
    missing_fields = sum(check["status"] == "missing" for check in concrete_checks)
    incorrect_fields = sum(check["status"] == "incorrect" for check in concrete_checks)
    unknown_fields = sum(check["status"] == "unknown" for result in race_results for check in result.get("checks", []))
    item_results = [item for result in race_results for item in result.get("item_snapshots", [])]
    matched_races = sum("actual_id" in result for result in race_results)
    expected_fields = sum(expected_field_counts)
    expected_unscored_fields = sum(expected_unscored_field_counts)
    unobserved_fields = sum(expected_field_counts[index] for index in unobserved_races)
    expected_items = sum(expected_item_counts)
    unobserved_items = sum(expected_item_counts[index] for index in unobserved_races)
    return {
        "schema_version": "tracen-replay/race-evaluation-v1",
        "source_sha256": reference["source_sha256"],
        "scope": reference["scope"],
        "start_ms": scope[0],
        "end_ms": scope[1],
        "expected_races": len(expected_races),
        "predicted_races": len(candidates),
        "matched_races": matched_races,
        "expected_fields": expected_fields,
        "matched_fields": matched_fields,
        "missing_fields": missing_fields,
        "incorrect_fields": incorrect_fields,
        "unknown_fields": unknown_fields,
        "unscored_fields": expected_unscored_fields,
        "expected_unscored_fields": expected_unscored_fields,
        "unobserved_fields": unobserved_fields,
        "matched_items": sum(item["passed"] for item in item_results),
        "expected_items": expected_items,
        "unobserved_items": unobserved_items,
        "missing_races": missing_races,
        "unobserved_races": sorted(unobserved_races),
        "ambiguous_races": ambiguous_races,
        "unexpected_races": unexpected,
        "invalid_predictions": invalid_predictions,
        "race_results": race_results,
        "errors": all_errors,
        "proof_hashes_checked": base is not None,
        "annotated_samples_checked": True,
        "independently_reviewed": reference.get("independently_reviewed", False),
        "negative_reference": negative,
        "full_recording_race_recall_measured": False,
        "complete_race_validation": False,
        "passed": not all_errors and not unexpected and not invalid_predictions and not ambiguous_races and not missing_races,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = evaluate(reference, report, args.evidence_root)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"race_results", "unexpected_races", "invalid_predictions"}}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
