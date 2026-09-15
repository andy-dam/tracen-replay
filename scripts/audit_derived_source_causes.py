"""Audit source causes of state-derived numeric contributions.

This is a report-only G5 audit. It follows each canonical state-derived
contribution exactly once, binds its amount to a pre-action stats.values
reading and a post-action facts.result_values reading, then classifies the
direct gain observations found in the same training-result window.

The transition pointers retained by causal accounting compare whole turn
openings and closings. They are kept as context, but are not treated as a
per-contribution proof when a turn contains sibling effects. The per-event
source operands below are the arithmetic proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPORT_DIR = Path(".local/final-reliability-v1/before")
AUDIT_PATH = Path(".local/final-reliability-v1/derived-audit.json")
SELECTION_PATH = Path(".local/final-reliability-v1/source-case-selection.json")
DEFAULT_OUTPUT = Path(".local/final-reliability-v1/derived-source-causes.json")
DEFAULT_MARKDOWN_OUTPUT = Path(".local/final-reliability-v1/derived-source-causes.md")
REPORT_SPECS = (
    ("v1", "v1-report.json"),
    ("independent-01", "independent-01-report.json"),
    ("independent-02", "independent-02-report.json"),
)
FIELDS = ("speed", "stamina", "power", "guts", "wit", "skill_points")


# Bounded, manually inspected source windows. All paths are resolved below
# the report's evidence_root. A row not listed here remains unknown: parser
# non-acceptance does not establish that a badge was absent.
MANUAL_CASES = (
    {
        "id": "v1-turn-028-training-0031-prefix",
        "recording": "v1",
        "event_id": "training-0031",
        "turn_id": "turn-028",
        "status": "reviewed_prefix_conflict",
        "badge_visibility": "visible_complete_after_transient_prefix",
        "source_frames": (
            (
                "training-inspection/531000/frame-000015.png",
                531467,
                "Transient phase: +4 Speed, +2 Wit, and +1 Skill Pts are visible.",
            ),
            (
                "training-inspection/531000/frame-000016.png",
                531500,
                "Stable phase: +4 Speed and +21 Wit are visible; Skill Pts is still animating.",
            ),
            (
                "training-inspection/531000/frame-000017.png",
                531533,
                "Stable phase: +21 Wit and +13 Skill Pts are visible.",
            ),
        ),
        "fields": ("speed", "skill_points"),
        "observation": (
            "The selected +4 Speed and +13 Skill Pts are source-visible across "
            "adjacent animation phases; the +1 Skill Pts prefix is not a second award."
        ),
    },
    {
        "id": "v1-turn-019-training-0020-speed-candidate",
        "recording": "v1",
        "event_id": "training-0020",
        "turn_id": "turn-019",
        "status": "reviewed_readable_but_rejected",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "training-inspection/262000/frame-000018.png",
                262567,
                "The +3 Speed badge is visible while the large SUCCESS grade overlaps the result area; +10 Wit and +7 Skill Pts are also visible.",
            ),
        ),
        "fields": ("speed",),
        "observation": (
            "The report keeps Speed +3 as a high-confidence candidate but does "
            "not promote it to training_gains in this short window."
        ),
    },
    {
        "id": "v1-turn-024-training-0026-wit",
        "recording": "v1",
        "event_id": "training-0026",
        "turn_id": "turn-024",
        "status": "reviewed_positive_visible",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "gameplay/part-003-frame-000333.png",
                443000,
                "The +14 Wit badge is visible at the lower result card; the frame also carries final result counters.",
            ),
        ),
        "fields": ("wit",),
        "observation": (
            "The direct +14 badge is readable in the gameplay frame, although "
            "the OCR line is lower confidence than the repeated state counter."
        ),
    },
    {
        "id": "independent-01-turn-016-training-0017",
        "recording": "independent-01",
        "event_id": "training-0017",
        "turn_id": "turn-016",
        "status": "reviewed_positive_visible",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "training-inspection/217500/frame-000019.png",
                218100,
                "The result screen visibly shows +5 Speed, +3 Power, +11 Guts, and +6 Skill Pts.",
            ),
        ),
        "fields": ("speed", "power", "guts", "skill_points"),
        "observation": (
            "All four selected state-derived fields have a readable direct badge "
            "in this frozen source case."
        ),
    },
    {
        "id": "independent-01-turn-063-training-0059-prefix",
        "recording": "independent-01",
        "event_id": "training-0059",
        "turn_id": "turn-063",
        "status": "reviewed_prefix_conflict",
        "badge_visibility": "visible_complete_after_transient_prefix",
        "source_frames": (
            (
                "training-inspection/1347500/frame-000017.png",
                1348033,
                "Transient phase: +1 Speed, +2 Wit, and +1 Skill Pts are visible.",
            ),
            (
                "training-inspection/1347500/frame-000018.png",
                1348067,
                "Stable phase: +12 Speed, +20 Wit, and +14 Skill Pts are visible.",
            ),
        ),
        "fields": ("wit", "skill_points"),
        "observation": (
            "The source window shows the leading-digit animation phase before "
            "the complete +20 Wit and +14 Skill Pts badges."
        ),
    },
    {
        "id": "independent-02-turn-053-training-0083",
        "recording": "independent-02",
        "event_id": "training-0083",
        "turn_id": "turn-053",
        "status": "reviewed_prefix_conflict",
        "badge_visibility": "visible_complete_after_transient_prefix",
        "source_frames": (
            (
                "training-recovery-v1/training-inspection/1301500/frame-000014.png",
                1301933,
                "The result screen visibly shows +6 Speed, +6 Power, +22 Guts, and +14 Skill Pts.",
            ),
            (
                "initial-baseline/gameplay/part-010-frame-000409.png",
                1302000,
                "A later animation phase exposes a transient +2 Guts and +1 Skill Pts reading.",
            ),
        ),
        "fields": ("skill_points",),
        "observation": (
            "The complete +14 Skill Pts badge is visible before the transient "
            "single-digit component reading; state-derived does not mean absent."
        ),
    },
    {
        "id": "independent-02-turn-036-training-0055-candidate",
        "recording": "independent-02",
        "event_id": "training-0055",
        "turn_id": "turn-036",
        "status": "reviewed_readable_but_rejected",
        "badge_visibility": "visible_then_other_field_later",
        "source_frames": (
            (
                "training-recovery-v1/training-inspection/830250/frame-000015.png",
                830717,
                "The +13 Wit badge is visible in the Wit card and +6 Skill Pts is visible at the same result screen.",
            ),
            (
                "training-recovery-v1/training-inspection/830250/frame-000018.png",
                830817,
                "A later frame shows +5 Speed while the Wit card has no overlay; this is a later phase, not evidence the badge was absent from the window.",
            ),
        ),
        "fields": ("wit",),
        "observation": (
            "Wit +13 is a source-visible candidate with no accepted direct "
            "training_gains value; the later +5 Speed frame demonstrates why a "
            "single sampled phase can be a wrong-field crop."
        ),
    },
    {
        "id": "independent-02-turn-020-training-0031",
        "recording": "independent-02",
        "event_id": "training-0031",
        "turn_id": "turn-020",
        "status": "reviewed_positive_visible",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "training-recovery-v1/training-inspection/414500/frame-000010.png",
                414800,
                "The result screen visibly shows +13 Stamina, +13 Guts, and +6 Skill Pts.",
            ),
        ),
        "fields": ("stamina", "guts", "skill_points"),
        "observation": (
            "All three state-derived fields in this independent-02 event have a readable direct badge in one source frame."
        ),
    },
    {
        "id": "v1-turn-038-training-0042-direct-single",
        "recording": "v1",
        "event_id": "training-0042",
        "turn_id": "turn-038",
        "status": "reviewed_positive_visible",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "training-inspection/743750/frame-000018.png",
                744317,
                "The Wit result card visibly shows +32; the same frame also shows the other training-result badges and the SUCCESS animation.",
            ),
        ),
        "fields": ("wit",),
        "observation": (
            "The accepted direct Wit value is source-readable in a single sampled frame; "
            "sparse direct repetition does not establish badge absence."
        ),
    },
    {
        "id": "independent-01-turn-015-training-0016-direct-single",
        "recording": "independent-01",
        "event_id": "training-0016",
        "turn_id": "turn-015",
        "status": "reviewed_positive_visible",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "gameplay/part-001-frame-000346.png",
                206250,
                "The training result frame visibly shows +3 Speed, +9 Wit, and +6 Skill Pts while the SUCCESS grade overlaps the cards.",
            ),
        ),
        "fields": ("speed", "wit", "skill_points"),
        "observation": (
            "All three direct-single fields are readable in the same source frame; "
            "the one-frame direct status reflects sparse accepted observations, not a missing badge."
        ),
    },
    {
        "id": "v1-turn-068-training-0070-prefix",
        "recording": "v1",
        "event_id": "training-0070",
        "turn_id": "turn-068",
        "status": "reviewed_prefix_conflict",
        "badge_visibility": "visible_complete_after_transient_prefix",
        "source_frames": (
            (
                "training-inspection/1405000/frame-000016.png",
                1405500,
                "Transient result components show +4 Stamina, +2 Guts, and +2 Skill Pts under the SUCCESS animation.",
            ),
            (
                "training-inspection/1405000/frame-000017.png",
                1405533,
                "The next phase visibly resolves to +44 Stamina, +22 Guts, and +22 Skill Pts.",
            ),
        ),
        "fields": ("stamina", "guts", "skill_points"),
        "observation": (
            "The frozen v1 case directly shows the leading single-digit component "
            "readings followed by the complete repeated badges; the prefix is not a second award."
        ),
    },
    {
        "id": "independent-01-turn-053-training-0049",
        "recording": "independent-01",
        "event_id": "training-0049",
        "turn_id": "turn-053",
        "status": "reviewed_positive_visible",
        "badge_visibility": "visible",
        "source_frames": (
            (
                "training-inspection/1064250/frame-000009.png",
                1064517,
                "The result screen visibly shows +38 Speed and +18 Skill Pts, with +12 Power also readable in the same frame.",
            ),
        ),
        "fields": ("speed", "skill_points"),
        "observation": (
            "Both frozen state-derived fields have a repeated direct badge in the source window, "
            "supporting the canonical basis-selection collision mechanism."
        ),
    },
)


CAUSE_INFO = {
    "canonical_state_derived_overrides_direct_badge": {
        "priority": 1,
        "ownership": "tracen_replay/transactions.py and tracen_replay/causal_accounting.py",
        "suggested_action": (
            "Preserve a repeated accepted training_gains value as the canonical "
            "basis before appending result_state_derived_fields, or make basis "
            "selection prefer the direct event observation."
        ),
    },
    "animation_component_prefix_conflict": {
        "priority": 2,
        "ownership": (
            "tracen_replay/vision.py and the training-gain phase resolver in "
            "tracen_replay/transactions.py"
        ),
        "suggested_action": (
            "Keep the complete repeated badge and retain the leading-digit "
            "reading as an animation conflict; do not count the prefix separately."
        ),
    },
    "short_or_low_confidence_direct_window": {
        "priority": 3,
        "ownership": "tracen_replay/vision.py training-result crop and confidence policy",
        "suggested_action": (
            "Review short result windows and crop confidence thresholds while "
            "requiring source evidence before promoting a one-frame badge."
        ),
    },
    "candidate_without_direct_promotion": {
        "priority": 4,
        "ownership": (
            "tracen_replay/vision.py candidate extraction and "
            "tracen_replay/transactions.py promotion policy"
        ),
        "suggested_action": (
            "Keep candidates separate from accepted gains, but preserve their "
            "source path and consider a bounded complete-badge promotion rule."
        ),
    },
    "direct_conflicting_other": {
        "priority": 5,
        "ownership": (
            "tracen_replay/vision.py crop/field association and "
            "tracen_replay/transactions.py conflict handling"
        ),
        "suggested_action": (
            "Inspect field association before allowing arithmetic or balance "
            "constraints to choose an unverified alternative."
        ),
    },
    "direct_no_matching_value": {
        "priority": 6,
        "ownership": "tracen_replay/vision.py training-gain reader",
        "suggested_action": (
            "Review the source window before assigning a cause; this category "
            "does not imply an absent badge."
        ),
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _int_values(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    return [value for value in values if type(value) is int]


def _window(values: list[int]) -> list[int] | None:
    values = sorted(set(value for value in values if type(value) is int))
    return [values[0], values[-1]] if values else None


def _counter(counter: Counter[Any]) -> dict[str, int]:
    return {
        str(key): value
        for key, value in sorted(counter.items(), key=lambda item: str(item[0]))
    }


def _pointer_tokens(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer!r}")
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    ]


def _resolve(data: Any, pointer: str) -> Any:
    current = data
    for token in _pointer_tokens(pointer):
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]
    return current


def _reading_stats_values(row: dict[str, Any]) -> dict[str, Any]:
    stats = row.get("stats")
    if not isinstance(stats, dict):
        return {}
    values = stats.get("values")
    return values if isinstance(values, dict) else {}


def _reading_evidence(row: dict[str, Any]) -> list[str]:
    value = row.get("evidence")
    if isinstance(value, str):
        return [value]
    return [item for item in value or [] if isinstance(item, str)]


def _evidence_intersects(row: dict[str, Any], paths: list[str]) -> bool:
    return bool(set(_reading_evidence(row)).intersection(paths))


def _source_paths(root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    absolute = []
    missing = []
    for path in _unique(paths):
        target = Path(path) if Path(path).is_absolute() else root / path
        target = target.resolve()
        absolute.append(str(target))
        if not target.is_file():
            missing.append(path)
    return absolute, missing


def _reading_pointer(index: int, suffix: str) -> str:
    return f"/gameplay_tracking/readings/{index}/{suffix}"


def _event_rows(
    readings: list[dict[str, Any]], event: dict[str, Any]
) -> list[tuple[int, dict[str, Any]]]:
    start = event.get("first_seen_ms")
    end = event.get("last_seen_ms")
    if type(start) is not int or type(end) is not int:
        return []
    return [
        (index, row)
        for index, row in enumerate(readings)
        if start <= row.get("source_timestamp_ms", -1) <= end
        and row.get("screen") == "training_result"
    ]


def _before_candidates(
    readings: list[dict[str, Any]], event: dict[str, Any], field: str
) -> list[tuple[int, dict[str, Any]]]:
    start = event.get("first_seen_ms")
    if type(start) is not int:
        return []
    return sorted(
        [
        (index, row)
        for index, row in enumerate(readings)
        if row.get("source_timestamp_ms", -1) < start
        and type(_reading_stats_values(row).get(field)) is int
        ],
        key=lambda item: (item[1].get("source_timestamp_ms", -1), item[0]),
    )


def _recorded_before_candidates(
    readings: list[dict[str, Any]],
    event: dict[str, Any],
    field: str,
    evidence_paths: list[str],
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, row)
        for index, row in _before_candidates(readings, event, field)
        if _evidence_intersects(row, evidence_paths)
    ]


def _before_reading(
    readings: list[dict[str, Any]], event: dict[str, Any], field: str
) -> tuple[int, dict[str, Any]] | None:
    candidates = _before_candidates(readings, event, field)
    return candidates[-1] if candidates else None


def _legacy_compatible_after_matches(
    event_rows: list[tuple[int, dict[str, Any]]],
    field: str,
    expected: int | None,
) -> list[tuple[int, dict[str, Any]]]:
    if type(expected) is not int:
        return []
    return sorted(
        [
            (index, row)
            for index, row in event_rows
            if ((row.get("facts") or {}).get("result_values") or {}).get(field)
            == expected
        ],
        key=lambda item: (item[1].get("source_timestamp_ms", -1), item[0]),
    )


def _stable_result_suffix(
    event_rows: list[tuple[int, dict[str, Any]]], field: str
) -> list[tuple[int, dict[str, Any]]]:
    """Return the final repeated integer result counter, without a claim amount."""
    ordered = sorted(
        event_rows,
        key=lambda item: (item[1].get("source_timestamp_ms", -1), item[0]),
    )
    suffix: list[tuple[int, dict[str, Any]]] = []
    for index, row in reversed(ordered):
        value = ((row.get("facts") or {}).get("result_values") or {}).get(field)
        if type(value) is not int:
            if suffix:
                break
            continue
        if suffix:
            previous = ((suffix[-1][1].get("facts") or {}).get("result_values") or {}).get(
                field
            )
            if value != previous:
                break
        suffix.append((index, row))
    suffix.reverse()
    if len(suffix) < 3:
        return []
    timestamps = [
        row.get("source_timestamp_ms")
        for _, row in suffix
        if type(row.get("source_timestamp_ms")) is int
    ]
    if not timestamps or max(timestamps) - min(timestamps) < 60:
        return []
    return suffix


def _run_focused_operand_tests() -> dict[str, str]:
    """Guard that stable-suffix selection never consumes the claimed amount."""
    rows = [
        (
            index,
            {
                "source_timestamp_ms": timestamp,
                "facts": {"result_values": {"speed": value}},
            },
        )
        for index, (timestamp, value) in enumerate(
            ((100, 10), (120, 12), (160, 12), (200, 12), (240, 14), (280, 14), (320, 14))
        )
    ]
    suffix = _stable_result_suffix(rows, "speed")
    assert [
        ((row.get("facts") or {}).get("result_values") or {}).get("speed")
        for _, row in suffix
    ] == [14, 14, 14]
    assert suffix[0][1]["source_timestamp_ms"] == 240
    claimed_amount = 2
    selected_amount = suffix[0][1]["facts"]["result_values"]["speed"] - 10
    assert selected_amount == 4
    assert selected_amount != claimed_amount
    legacy_match = _legacy_compatible_after_matches(rows, "speed", 10 + claimed_amount)
    assert legacy_match[0][1]["facts"]["result_values"]["speed"] == 12
    return {
        "intermediate_total_is_not_selected": "passed",
        "conflicting_last_stable_total_is_selected": "passed",
    }


def _transition_operand(
    report: dict[str, Any], pointer: str | None, field: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if not pointer:
        return None, [{"kind": "missing_transition_pointer"}]
    try:
        values = _resolve(report, pointer)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return None, [
            {"kind": "transition_pointer_unresolved", "pointer": pointer, "error": str(exc)}
        ]
    if not isinstance(values, dict):
        errors.append({"kind": "transition_pointer_not_value_mapping", "pointer": pointer})
        return None, errors
    if type(values.get(field)) is not int:
        errors.append(
            {
                "kind": "transition_pointer_field_not_integer",
                "pointer": pointer,
                "field": field,
                "value": values.get(field),
            }
        )
    parent_pointer = pointer.rsplit("/", 1)[0]
    try:
        parent = _resolve(report, parent_pointer)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        errors.append(
            {
                "kind": "transition_parent_unresolved",
                "pointer": parent_pointer,
                "error": str(exc),
            }
        )
        parent = {}
    tokens = _pointer_tokens(pointer)
    source: dict[str, Any] = {
        "pointer": pointer,
        "parent_pointer": parent_pointer,
        "field": field,
        "field_value": values.get(field),
        "value_shape": (
            "stats_values"
            if tokens[-2:] == ["stats", "values"]
            else "checkpoint_values"
        ),
    }
    if len(tokens) >= 3 and tokens[0:2] == ["gameplay_tracking", "readings"]:
        try:
            reading_index = int(tokens[2])
            reading = _resolve(report, f"/gameplay_tracking/readings/{reading_index}")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            errors.append(
                {"kind": "reading_source_unresolved", "pointer": pointer, "error": str(exc)}
            )
            reading_index = None
            reading = {}
        source.update(
            {
                "reading_index": reading_index,
                "source_timestamp_ms": reading.get("source_timestamp_ms"),
                "screen": reading.get("screen"),
                "evidence": _reading_evidence(reading),
                "source_kind": "reading",
            }
        )
    elif len(tokens) >= 3 and tokens[0:2] == ["gameplay_tracking", "checkpoints"]:
        source.update(
            {
                "first_seen_ms": parent.get("first_seen_ms")
                if isinstance(parent, dict)
                else None,
                "last_seen_ms": parent.get("last_seen_ms")
                if isinstance(parent, dict)
                else None,
                "evidence": _reading_evidence(parent)
                if isinstance(parent, dict)
                else [],
                "supporting_frames": (
                    list(parent.get("supporting_frames") or [])
                    if isinstance(parent, dict)
                    else []
                ),
                "source_kind": "checkpoint",
            }
        )
    else:
        errors.append({"kind": "transition_pointer_unexpected_root", "pointer": pointer})
    return source, errors


def _direct_observation(
    event_rows: list[tuple[int, dict[str, Any]]], field: str, amount: int
) -> dict[str, Any]:
    direct: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    crosscheck_times: list[int] = []
    for index, row in event_rows:
        facts = row.get("facts") or {}
        gains = facts.get("training_gains") or {}
        gain_candidates = facts.get("training_gain_candidates") or {}
        timestamp = row.get("source_timestamp_ms")
        evidence = _reading_evidence(row)
        value = gains.get(field)
        if type(value) is int:
            direct.append(
                {
                    "value": value,
                    "source_timestamp_ms": timestamp,
                    "reading_index": index,
                    "evidence": evidence,
                }
            )
        for candidate in _int_values(gain_candidates.get(field)):
            candidates.append(
                {
                    "value": candidate,
                    "source_timestamp_ms": timestamp,
                    "reading_index": index,
                    "evidence": evidence,
                }
            )
        if field in (facts.get("gain_digit_crosschecks") or []):
            if type(timestamp) is int:
                crosscheck_times.append(timestamp)

    direct_values = sorted({item["value"] for item in direct})
    candidate_values = sorted({item["value"] for item in candidates})
    target_direct = [item for item in direct if item["value"] == amount]
    target_candidates = [item for item in candidates if item["value"] == amount]

    if len(direct_values) == 1 and direct_values[0] == amount:
        status = "direct_repeated_unique" if len(target_direct) >= 2 else "direct_single"
    elif len(direct_values) > 1 and amount in direct_values:
        alternate_values = [value for value in direct_values if value != amount]
        if all(str(amount).startswith(str(value)) for value in alternate_values):
            status = "direct_conflicting_prefix"
        else:
            status = "direct_conflicting_other"
    elif not direct_values and amount in candidate_values:
        status = "candidate_only"
    else:
        status = "direct_no_matching_value"

    cause = {
        "direct_repeated_unique": "canonical_state_derived_overrides_direct_badge",
        "direct_conflicting_prefix": "animation_component_prefix_conflict",
        "direct_conflicting_other": "direct_conflicting_other",
        "direct_single": "short_or_low_confidence_direct_window",
        "candidate_only": "candidate_without_direct_promotion",
        "direct_no_matching_value": "direct_no_matching_value",
    }[status]

    def grouped(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_value: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            by_value[item["value"]].append(item)
        return [
            {
                "value": value,
                "count": len(rows),
                "timestamps_ms": sorted(
                    {
                        row["source_timestamp_ms"]
                        for row in rows
                        if type(row.get("source_timestamp_ms")) is int
                    }
                ),
                "reading_indices": [row["reading_index"] for row in rows],
                "evidence": _unique(
                    [path for row in rows for path in row.get("evidence", [])]
                ),
            }
            for value, rows in sorted(by_value.items())
        ]

    direct_timestamps = sorted(
        {
            item["source_timestamp_ms"]
            for item in target_direct
            if type(item.get("source_timestamp_ms")) is int
        }
    )
    candidate_timestamps = sorted(
        {
            item["source_timestamp_ms"]
            for item in target_candidates
            if type(item.get("source_timestamp_ms")) is int
        }
    )
    return {
        "status": status,
        "cause_group": cause,
        "direct_values": direct_values,
        "direct_observation_count": len(direct),
        "direct_target_count": len(target_direct),
        "direct_target_timestamps_ms": direct_timestamps,
        "direct_target_timestamp_count": len(direct_timestamps),
        "direct_value_groups": grouped(direct),
        "candidate_values": candidate_values,
        "candidate_observation_count": len(candidates),
        "candidate_target_count": len(target_candidates),
        "candidate_target_timestamps_ms": candidate_timestamps,
        "candidate_target_timestamp_count": len(candidate_timestamps),
        "candidate_value_groups": grouped(candidates),
        "extra_candidate_values": [value for value in candidate_values if value != amount],
        "gain_digit_crosscheck_timestamps_ms": sorted(set(crosscheck_times)),
        "all_direct_observations": direct,
        "all_candidate_observations": candidates,
        "prefix_alternates": (
            [value for value in direct_values if value != amount]
            if status == "direct_conflicting_prefix"
            else []
        ),
    }


def _manual_case_index() -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    result: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in MANUAL_CASES:
        for field in case["fields"]:
            result[(case["recording"], case["event_id"], field)].append(case)
    return dict(result)


def _attach_manual_case(
    case_index: dict[tuple[str, str, str], list[dict[str, Any]]],
    recording: str,
    event_id: str,
    field: str,
    evidence_root: Path,
    readings: list[dict[str, Any]],
) -> dict[str, Any]:
    cases = case_index.get((recording, event_id, field), [])
    attached = []
    for case in cases:
        frames = []
        for relative, timestamp, note in case["source_frames"]:
            absolute, missing = _source_paths(evidence_root, [relative])
            report_readings = []
            for index, row in enumerate(readings):
                if relative not in _reading_evidence(row):
                    continue
                facts = row.get("facts") or {}
                report_readings.append(
                    {
                        "reading_index": index,
                        "source_timestamp_ms": row.get("source_timestamp_ms"),
                        "screen": row.get("screen"),
                        "training_option": row.get("training_option"),
                        "facts": {
                            "training_gains": facts.get("training_gains"),
                            "training_gain_candidates": facts.get(
                                "training_gain_candidates"
                            ),
                            "result_values": facts.get("result_values"),
                        },
                    }
                )
            frames.append(
                {
                    "relative_path": relative,
                    "absolute_path": (
                        absolute[0] if absolute else str(evidence_root / relative)
                    ),
                    "source_timestamp_ms": timestamp,
                    "visual_observation": note,
                    "report_readings": report_readings,
                    "missing": missing,
                }
            )
        attached.append(
            {
                "id": case["id"],
                "status": case["status"],
                "badge_visibility": case["badge_visibility"],
                "observation": case["observation"],
                "source_frames": frames,
            }
        )
    if attached:
        return {
            "status": attached[0]["status"],
            "badge_visibility": attached[0]["badge_visibility"],
            "cases": attached,
        }
    return {
        "status": "not_individually_reviewed",
        "badge_visibility": "unknown_not_individually_reviewed",
        "cases": [],
        "review_limit": (
            "No badge-absence conclusion is made for this row because its "
            "source frame was not individually reviewed."
        ),
    }


def _row_for_contribution(
    root: Path,
    recording: str,
    report: dict[str, Any],
    audit_row: dict[str, Any],
    case_index: dict[tuple[str, str, str], list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    event_id = audit_row.get("event_id")
    events = (report.get("gameplay_tracking") or {}).get("events") or []
    event = next((item for item in events if item.get("id") == event_id), None)
    if event is None:
        return (
            {
                "recording": recording,
                "source_sha256": audit_row.get("source_sha256"),
                "id": audit_row.get("id"),
                "event_id": event_id,
                "field": audit_row.get("field"),
                "amount": audit_row.get("amount"),
                "cause_group": "missing_event",
            },
            [{"kind": "missing_event", "event_id": event_id}],
        )

    field = audit_row["field"]
    amount = audit_row["amount"]
    readings = (report.get("gameplay_tracking") or {}).get("readings") or []
    event_rows = _event_rows(readings, event)
    event_field_evidence = _unique(
        [
            path
            for path in (event.get("field_evidence") or {}).get(field, [])
            if isinstance(path, str)
        ]
    )
    before_candidates = _before_candidates(readings, event, field)
    before = before_candidates[-1] if before_candidates else None
    recorded_before_candidates = _recorded_before_candidates(
        readings, event, field, event_field_evidence
    )
    recorded_before = (
        recorded_before_candidates[-1] if recorded_before_candidates else None
    )
    if before is None:
        errors.append({"kind": "missing_source_bound_before_reading", "field": field})
    before_index, before_row = before if before else (None, {})
    recorded_before_index, recorded_before_row = (
        recorded_before if recorded_before else (None, {})
    )
    before_values = _reading_stats_values(before_row)
    before_value = before_values.get(field)
    expected_after = (
        before_value + amount
        if type(before_value) is int and type(amount) is int
        else None
    )
    stable_suffix = _stable_result_suffix(event_rows, field)
    after = stable_suffix[0] if stable_suffix else None
    # Keep the former amount-driven lookup only as a historical compatibility
    # metric. It must never select the source-bound operand or prove a row.
    legacy_after_candidates = _legacy_compatible_after_matches(
        event_rows, field, expected_after
    )
    legacy_after = legacy_after_candidates[0] if legacy_after_candidates else None
    if after is None:
        errors.append(
            {
                "kind": "missing_source_bound_after_result_reading",
                "field": field,
                "selection": "stable_trailing_result_suffix",
            }
        )
    after_index, after_row = after if after else (None, {})
    after_values = (after_row.get("facts") or {}).get("result_values") or {}
    after_value = after_values.get(field)
    recomputed = (
        after_value - before_value
        if type(after_value) is int and type(before_value) is int
        else None
    )
    amount_match = recomputed == amount
    before_timestamp = before_row.get("source_timestamp_ms")
    after_timestamp = after_row.get("source_timestamp_ms")
    event_start = event.get("first_seen_ms")
    event_end = event.get("last_seen_ms")
    source_time_order_ok = (
        type(before_timestamp) is int
        and type(after_timestamp) is int
        and type(event_start) is int
        and type(event_end) is int
        and before_timestamp < event_start <= after_timestamp <= event_end
    )
    before_is_latest = bool(before) and before == before_candidates[-1]
    after_is_first_stable = bool(after) and after == stable_suffix[0]
    recorded_before_value = (
        _reading_stats_values(recorded_before[1]).get(field)
        if recorded_before
        else None
    )
    before_matches_recorded_proof = (
        type(before_value) is int
        and type(recorded_before_value) is int
        and before_value == recorded_before_value
    )
    stable_suffix_provenance_ok = bool(stable_suffix) and all(
        _evidence_intersects(row, event_field_evidence)
        for _, row in stable_suffix
    )
    after_stable_repetition_ok = len(stable_suffix) >= 3
    if not before_matches_recorded_proof:
        errors.append(
            {
                "kind": "source_bound_before_disagrees_with_recorded_field_proof",
                "field": field,
                "current_before_value": before_value,
                "recorded_before_value": recorded_before_value,
            }
        )
    if not stable_suffix_provenance_ok:
        errors.append(
            {
                "kind": "source_bound_after_not_in_recorded_field_proof",
                "field": field,
                "stable_suffix_length": len(stable_suffix),
            }
        )
    if not after_stable_repetition_ok:
        errors.append(
            {
                "kind": "source_bound_after_stable_suffix_too_short",
                "field": field,
                "stable_suffix_length": len(stable_suffix),
            }
        )
    if not before_is_latest:
        errors.append(
            {
                "kind": "source_bound_before_not_latest_pre_event_reading",
                "field": field,
            }
        )
    if not after_is_first_stable:
        errors.append(
            {
                "kind": "source_bound_after_not_first_stable_suffix_reading",
                "field": field,
            }
        )
    if not source_time_order_ok:
        errors.append(
            {
                "kind": "source_bound_timestamp_window_invalid",
                "field": field,
                "before_timestamp_ms": before_timestamp,
                "after_timestamp_ms": after_timestamp,
                "event_window_ms": [event_start, event_end],
            }
        )
    if not amount_match:
        errors.append(
            {
                "kind": "source_bound_amount_mismatch",
                "field": field,
                "amount": amount,
                "before_value": before_value,
                "after_value": after_value,
                "recomputed_amount": recomputed,
            }
        )

    direct = _direct_observation(event_rows, field, amount)
    evidence_root_value = (report.get("evaluation_context") or {}).get("evidence_root")
    evidence_root = Path(evidence_root_value) if evidence_root_value else root
    event_absolute, event_missing = _source_paths(evidence_root, event_field_evidence)
    source_bound_paths = []
    if before_row:
        source_bound_paths.extend(_reading_evidence(before_row))
    if recorded_before_row:
        source_bound_paths.extend(_reading_evidence(recorded_before_row))
    for _, suffix_row in stable_suffix:
        source_bound_paths.extend(_reading_evidence(suffix_row))
    source_absolute, source_missing = _source_paths(evidence_root, source_bound_paths)
    if event_missing:
        errors.append(
            {"kind": "missing_event_field_evidence_paths", "paths": event_missing}
        )
    if source_missing:
        errors.append(
            {"kind": "missing_source_bound_evidence_paths", "paths": source_missing}
        )

    transition = audit_row.get("transition") or {}
    actual_transition = None
    actual_transition_field = None
    for candidate_transition in (report.get("causal_accounting") or {}).get(
        "turn_transitions", []
    ):
        if candidate_transition.get("turn_id") != transition.get("turn_id"):
            continue
        if candidate_transition.get("channel") != audit_row.get("channel"):
            continue
        for candidate_field in candidate_transition.get("fields", []):
            if candidate_field.get("field") != field:
                continue
            if audit_row.get("id") in (candidate_field.get("contribution_refs") or []):
                actual_transition = candidate_transition
                actual_transition_field = candidate_field
                break
        if actual_transition is not None:
            break
    if actual_transition is None:
        errors.append(
            {
                "kind": "canonical_contribution_missing_source_transition_field",
                "field": field,
                "transition_turn_id": transition.get("turn_id"),
            }
        )
    transition_before, transition_errors_before = _transition_operand(
        report, transition.get("before_state_ref"), field
    )
    transition_after, transition_errors_after = _transition_operand(
        report, transition.get("after_state_ref"), field
    )
    errors.extend(transition_errors_before)
    errors.extend(transition_errors_after)
    transition_pointers_verified = not (
        transition_errors_before or transition_errors_after
    )
    if field not in (event.get("result_state_derived_fields") or []):
        errors.append(
            {
                "kind": "state_derived_field_tag_missing_from_event",
                "field": field,
                "event_id": event_id,
            }
        )
    endpoint_delta = None
    if (
        transition_before
        and transition_after
        and type(transition_before.get("field_value")) is int
        and type(transition_after.get("field_value")) is int
    ):
        endpoint_delta = (
            transition_after["field_value"] - transition_before["field_value"]
        )

    row = {
        "recording": recording,
        "source_sha256": audit_row.get("source_sha256"),
        "id": audit_row.get("id"),
        "event_id": event_id,
        "event_kind": event.get("kind"),
        "event_training_option": event.get("training_option"),
        "turn_id": audit_row.get("turn_id"),
        "candidate_turn_ids": list(audit_row.get("candidate_turn_ids") or []),
        "channel": audit_row.get("channel"),
        "field": field,
        "amount": amount,
        "basis": audit_row.get("basis"),
        "state_derived_tag": field in (event.get("result_state_derived_fields") or []),
        "frozen_case_ids": [],
        "source_bound_operands": {
            "event_window_ms": [event.get("first_seen_ms"), event.get("last_seen_ms")],
            "before": {
                "selection": "latest_integer_stats_values_before_event_start",
                "candidate_count": len(before_candidates),
                "is_latest_pre_event_reading": before_is_latest,
                "recorded_field_proof_candidate_count": len(recorded_before_candidates),
                "recorded_field_proof_pointer": (
                    _reading_pointer(recorded_before_index, "stats/values")
                    if recorded_before_index is not None
                    else None
                ),
                "recorded_field_proof_value": recorded_before_value,
                "matches_recorded_field_proof": before_matches_recorded_proof,
                "pointer": (
                    _reading_pointer(before_index, "stats/values")
                    if before_index is not None
                    else None
                ),
                "reading_index": before_index,
                "source_timestamp_ms": before_row.get("source_timestamp_ms"),
                "screen": before_row.get("screen"),
                "field_value": before_value,
                "evidence": _reading_evidence(before_row),
                "absolute_evidence": _source_paths(
                    evidence_root, _reading_evidence(before_row)
                )[0],
            },
            "after": {
                "selection": "first_reading_of_stable_trailing_result_suffix",
                "stable_suffix_count": len(stable_suffix),
                "stable_suffix_repeated": after_stable_repetition_ok,
                "stable_suffix_provenance_matches_field_proof": stable_suffix_provenance_ok,
                "is_first_stable_suffix_reading": after_is_first_stable,
                "pointer": (
                    _reading_pointer(after_index, "facts/result_values")
                    if after_index is not None
                    else None
                ),
                "reading_index": after_index,
                "source_timestamp_ms": after_row.get("source_timestamp_ms"),
                "screen": after_row.get("screen"),
                "field_value": after_value,
                "evidence": _reading_evidence(after_row),
                "absolute_evidence": _source_paths(
                    evidence_root, _reading_evidence(after_row)
                )[0],
            },
            "stable_result_suffix": [
                {
                    "pointer": _reading_pointer(index, "facts/result_values"),
                    "reading_index": index,
                    "source_timestamp_ms": suffix_row.get("source_timestamp_ms"),
                    "field_value": (
                        ((suffix_row.get("facts") or {}).get("result_values") or {}).get(field)
                    ),
                    "evidence": _reading_evidence(suffix_row),
                    "in_recorded_field_proof": _evidence_intersects(
                        suffix_row, event_field_evidence
                    ),
                }
                for index, suffix_row in stable_suffix
            ],
            "legacy_expected_value_compatibility": {
                "expected_after_value": expected_after,
                "matching_result_count": len(legacy_after_candidates),
                "compatible": bool(legacy_after_candidates),
                "first_matching_pointer": (
                    _reading_pointer(legacy_after[0], "facts/result_values")
                    if legacy_after
                    else None
                ),
                "first_matching_timestamp_ms": (
                    legacy_after[1].get("source_timestamp_ms") if legacy_after else None
                ),
            },
            "recomputed_amount": recomputed,
            "amount_match": amount_match,
            "constraints": {
                "expected_after_value": expected_after,
                "observed_after_value": after_value,
                "post_result_equals_before_plus_amount": (
                    type(expected_after) is int
                    and after_value == expected_after
                ),
                "before_value_is_integer": type(before_value) is int,
                "after_value_is_integer": type(after_value) is int,
                "source_timestamp_order_valid": source_time_order_ok,
                "before_matches_recorded_field_proof": before_matches_recorded_proof,
                "before_is_latest_pre_event_reading": before_is_latest,
                "after_stable_suffix_repeated": after_stable_repetition_ok,
                "after_stable_suffix_provenance_matches_field_proof": stable_suffix_provenance_ok,
                "after_is_first_stable_suffix_reading": after_is_first_stable,
                "result_reading_is_inside_event_window": (
                    type(after_timestamp) is int
                    and type(event_start) is int
                    and type(event_end) is int
                    and event_start <= after_timestamp <= event_end
                ),
            },
            "source_bound_arithmetic_verified": (
                amount_match
                and not source_missing
                and not event_missing
                and before_index is not None
                and after_index is not None
                and source_time_order_ok
                and before_matches_recorded_proof
                and before_is_latest
                and after_stable_repetition_ok
                and stable_suffix_provenance_ok
                and after_is_first_stable
                and transition_pointers_verified
            ),
            "source_window_ms": _window(
                [
                    value
                    for value in (
                        before_row.get("source_timestamp_ms"),
                        after_row.get("source_timestamp_ms"),
                    )
                    if type(value) is int
                ]
            ),
        },
        "event_source_evidence": {
            "relative_paths": event_field_evidence,
            "absolute_paths": event_absolute,
            "missing_paths": event_missing,
        },
        "source_bound_evidence": {
            "relative_paths": _unique(source_bound_paths),
            "absolute_paths": source_absolute,
            "missing_paths": source_missing,
        },
        "direct_reader_observation": direct,
        "cause_group": direct["cause_group"],
        "structural_transition_context": {
            "turn_id": transition.get("turn_id"),
            "status": transition.get("status"),
            "transition_before_state_ref": transition.get("before_state_ref"),
            "transition_after_state_ref": transition.get("after_state_ref"),
            "transition_before": transition.get("before"),
            "transition_after": transition.get("after"),
            "observed_change": transition.get("observed_change"),
            "direct_change": transition.get("direct_change"),
            "derived_or_summary_change": transition.get("derived_or_summary_change"),
            "unresolved_change": transition.get("unresolved_change"),
            "endpoint_recomputed_amount": endpoint_delta,
            "endpoint_amount_match": endpoint_delta == amount,
            "endpoint_note": (
                "This transition is a whole-turn opening/closing comparison and "
                "can include sibling contribution refs. It is context, not the "
                "per-contribution arithmetic proof."
            ),
            "before_operand": transition_before,
            "after_operand": transition_after,
        },
        "transition_reference": {
            "selected_transition_turn_id": transition.get("turn_id"),
            "canonical_id_in_transition": actual_transition is not None,
            "transition_contribution_refs": (
                list(actual_transition_field.get("contribution_refs") or [])
                if actual_transition_field
                else []
            ),
            "transition_reference_count": audit_row.get("transition_reference_count"),
        },
        "source_review": _attach_manual_case(
            case_index, recording, event_id, field, evidence_root, readings
        ),
        "report_structural_flags": {
            "event_field_evidence_matches_canonical": event_field_evidence
            == list(audit_row.get("evidence") or []),
            "event_result_state_derived_fields": list(
                event.get("result_state_derived_fields") or []
            ),
            "event_conflicting_readings": event.get("conflicting_readings") or {},
            "event_repeated_fields": list(event.get("repeated_fields") or []),
        },
    }
    return row, errors


def _frozen_cases(root: Path, selection_path: Path = SELECTION_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    path = selection_path if selection_path.is_absolute() else root / selection_path
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (case.get("run"), case.get("turn_id")): case
        for case in data.get("cases", [])
        if case.get("run") and case.get("turn_id")
    }


def _cause_group(rows: list[dict[str, Any]], cause: str) -> dict[str, Any]:
    info = CAUSE_INFO.get(cause, {})
    recordings = Counter(row.get("recording") for row in rows)
    fields = Counter(row.get("field") for row in rows)
    event_kinds = Counter(row.get("event_kind") for row in rows)
    statuses = Counter(
        (row.get("direct_reader_observation") or {}).get("status") for row in rows
    )
    source_review_statuses = Counter(
        (row.get("source_review") or {}).get("status") for row in rows
    )
    badge_visibility = Counter(
        (row.get("source_review") or {}).get("badge_visibility") for row in rows
    )
    frozen = [row for row in rows if row.get("frozen_case_ids")]
    reviewed = [
        row
        for row in rows
        if (row.get("source_review") or {}).get("status")
        != "not_individually_reviewed"
    ]
    return {
        "cause_group": cause,
        "priority": info.get("priority"),
        "contribution_count": len(rows),
        "amount_sum": sum(
            row.get("amount", 0) for row in rows if type(row.get("amount")) is int
        ),
        "recording_counts": _counter(recordings),
        "field_counts": _counter(fields),
        "event_kind_counts": _counter(event_kinds),
        "direct_status_counts": _counter(statuses),
        "source_review_status_counts": _counter(source_review_statuses),
        "badge_visibility_counts": _counter(badge_visibility),
        "frozen_case_contribution_count": len(frozen),
        "individually_reviewed_contribution_count": len(reviewed),
        "ownership": info.get("ownership"),
        "suggested_action": info.get("suggested_action"),
        "example_contribution_ids": [
            f"{row.get('recording')}:{row.get('id')}" for row in rows[:8]
        ],
    }


def _bucket(count: int) -> str:
    return "3_or_more" if count >= 3 else str(count)


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    scope = result.get("scope", {})
    current_count = summary.get("contribution_count", 0)
    historical_count = summary.get("historical_compatibility_count", 0)
    historical_baseline = summary.get("historical_baseline", {})
    input_directory = scope.get("input_directory", "the report directory")
    lines = [
        "# Derived source causes",
        "",
        f"Generated by scripts/audit_derived_source_causes.py from reports in `{input_directory}` and the canonical G5 inventory.",
        "",
        f"This bounded audit covers {current_count} current `state_derived` contributions once. The historical compatibility artifact retains {historical_count} prior rows separately. Current rows bind each amount to a pre-action `stats.values` source reading and a post-action `facts.result_values` source reading in the same training-result event window. This validates arithmetic operands only; basis legitimacy is not asserted. Full-history accuracy remains unmeasured.",
        "",
        "## Coverage and source-bound arithmetic",
        "",
        f"- Contributions: {summary['contribution_count']} across "
        f"{summary['recording_counts']}.",
        f"- Amount recomputations from the amount-independent stable result suffix: "
        f"{summary['source_bound_amount_match_count']} matches; "
        f"{summary['source_bound_amount_mismatch_count']} mismatches.",
        f"- Historical amount-driven compatibility lookup: "
        f"{summary['legacy_expected_value_compatibility_match_count']} matches; "
        f"{summary['legacy_expected_value_compatibility_mismatch_count']} "
        "mismatches (retained for comparison, not used as proof).",
        f"- Source-bound post-result constraints match: "
        f"{summary['source_bound_constraint_match_count']}; verified rows: "
        f"{summary['source_bound_verified_row_count']}; unsupported or unproven: "
        f"{summary['unsupported_or_unproven_source_bound_rows']}.",
        f"- Operand selection checks: latest pre-event stats reading matching the "
        f"recorded field proof for {summary['source_bound_before_latest_count']} "
        f"rows and first reading of the stable trailing result suffix for "
        f"{summary['source_bound_after_first_match_count']} "
        f"rows; event-window durations (ms): "
        f"{summary['source_bound_event_window_duration_ms']}; pre-event gaps (ms): "
        f"{summary['source_bound_pre_event_gap_ms']}.",
        f"- Focused operand-selection tests: {summary['focused_operand_tests']}.",
        f"- Source pointer errors: {summary['source_pointer_error_count']}; "
        f"missing evidence files: {summary['missing_source_evidence_path_count']}.",
        f"- Whole-turn transition endpoint deltas match the individual amount for "
        f"{summary['transition_endpoint_amount_match_count']} rows and differ for "
        f"{summary['transition_endpoint_amount_mismatch_count']} rows. The latter "
        "is expected when sibling effects share the turn opening/closing window.",
        f"- Frozen source selection contains {summary['frozen_contribution_count']} "
        f"rows; individually reviewed rows: "
        f"{summary['individually_reviewed_contribution_count']}.",
        "",
        "## Channel, field, and event-kind coverage",
        "",
        "| Dimension | Counts |",
        "| --- | --- |",
        f"| Channel | {summary['channel_counts']} |",
        f"| Field | {summary['field_counts']} |",
        f"| Event kind | {summary['event_kind_counts']} |",
        "",
        "Rows in this input are stats fields from training events. Historical field counts, when present in the source-cause baseline, describe the prior 264-row compatibility artifact and are not merged into the current count.",
        "",
        "## Direct reader causes",
        "",
        "| Cause group | Count | Direct observation | Source review | Frozen rows | Reviewed rows |",
        "| --- | ---: | --- | --- | ---: | ---: |",
    ]
    for cause, group in sorted(
        result["cause_groups"].items(),
        key=lambda item: (item[1].get("priority", 99), item[0]),
    ):
        lines.append(
            f"| {cause} | {group['contribution_count']} | "
            f"{group['direct_status_counts']} | "
            f"{group['source_review_status_counts']} | "
            f"{group['frozen_case_contribution_count']} | "
            f"{group['individually_reviewed_contribution_count']} |"
        )
    lines.extend(
        [
            "",
            f"The historical baseline recorded these separate counts: direct causes {historical_baseline.get('direct_cause_counts', {})}; source statuses {historical_baseline.get('source_operand_audit_status_counts', {})}. In particular, the pre-change audit had 179 canonical direct collisions, 76 prefix conflicts, and 17 purchase ownership conflicts. Those baseline counts are kept separate from the current input rows above.",
            "",
            "| Direct reader status | Count | Target timestamps |",
            "| --- | ---: | --- |",
        ]
    )
    for status, count in sorted(summary["direct_status_counts"].items()):
        buckets = summary["direct_target_timestamp_buckets"].get(status, {})
        lines.append(f"| {status} | {count} | {buckets} |")
    lines.extend(
        [
            "",
            "In the historical baseline, the 179 repeated-unique rows have an accepted direct gain repeated in the report, yet remain tagged state_derived because the result-counter suffix path also tagged the field. This is a basis selection collision, not evidence that the badge was missing.",
            "",
            "In the historical baseline, the 76 conflicting rows all have an accepted value equal to the source-bound amount and alternate values that are leading prefixes of that amount. This matches animated badge phases.",
            "",
            "Historical direct-single and candidate-only cases remain in the reviewed-case appendix. No row is labeled badge_absent; parser non-acceptance is never treated as badge absence.",
            "",
        "## Reviewed source cases (frozen cases prioritized)",
            "",
            "| Case | Event | Source-backed observation |",
            "| --- | --- | --- |",
        ]
    )
    for case in result["reviewed_cases"]:
        paths = "<br>".join(frame["absolute_path"] for frame in case["source_frames"])
        notes = " ".join(frame["visual_observation"] for frame in case["source_frames"])
        lines.append(
            f"| {case['id']} | {case['recording']} / {case['event_id']} | "
            f"{notes} Source: {paths} |"
        )
    lines.extend(
        [
            "",
            "The independent-02 training-0055 window is an explicit "
            "readable-but-rejected example: frame 830717 shows Wit +13, "
            "while a later 830817 frame shows a Speed +5 overlay and no Wit "
            "overlay. The later frame is a different animation phase and does "
            "not support a badge-absence claim.",
            "",
            "## Implementation ownership",
            "",
        ]
    )
    for cause, group in sorted(
        result["cause_groups"].items(),
        key=lambda item: (item[1].get("priority", 99), item[0]),
    ):
        if not group["contribution_count"]:
            continue
        lines.append(
            f"- {cause} ({group['contribution_count']}): "
            f"{group.get('ownership')}. {group.get('suggested_action')}"
        )
    lines.extend(
        [
            "",
            "## Reproduction and limitations",
            "",
            "Run `python scripts/audit_derived_source_causes.py` from the repository root. The script reads only the selected report directory, the canonical derived audit, and the frozen source-case selection. It does not edit recognizers or rerun recognition.",
            "",
            "- source_bound_arithmetic_verified means the report's actual "
            "pre-action and post-action operands recompute the contribution "
            "amount exactly. It does not prove complete event history.",
            "- structural_transition_context retains the causal-accounting "
            "opening/closing comparison for traceability. It is not substituted "
            "for the source-bound event operands.",
            "- Source frames outside the bounded reviewed cases remain "
            "not_individually_reviewed; parser non-acceptance is not badge absence.",
            "- Full-history accuracy remains null.",
        ]
    )
    return "\n".join(lines) + "\n"


def _bind_external_evidence_root(report, report_path, recording, bindings):
    """Bind relocated worker evidence without editing the preserved report."""
    if bindings is None:
        return
    entry = bindings.get(recording)
    if not isinstance(entry, dict):
        raise ValueError(f"Missing evidence-root binding for {recording}")
    if entry.get("report_sha256") != _sha256(report_path):
        raise ValueError(f"Evidence-root report hash mismatch for {recording}")
    if entry.get("source_sha256") != report.get("source", {}).get("sha256"):
        raise ValueError(f"Evidence-root source hash mismatch for {recording}")
    value = entry.get("evidence_root")
    if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_dir():
        raise ValueError(f"Invalid absolute evidence root for {recording}")
    context = dict(report.get("evaluation_context") or {})
    existing = context.get("evidence_root")
    if existing and Path(existing).resolve() != Path(value).resolve():
        raise ValueError(f"Conflicting evidence root for {recording}")
    context["evidence_root"] = str(Path(value).resolve())
    report["evaluation_context"] = context


def audit(
    root: Path,
    report_dir: Path = REPORT_DIR,
    audit_path: Path = AUDIT_PATH,
    selection_path: Path = SELECTION_PATH,
    evidence_roots: dict | None = None,
) -> dict[str, Any]:
    focused_operand_tests = _run_focused_operand_tests()
    audit_file = audit_path if audit_path.is_absolute() else root / audit_path
    audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
    frozen = _frozen_cases(root, selection_path)
    case_index = _manual_case_index()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    report_summaries = []
    reports_by_recording: dict[str, dict[str, Any]] = {}

    for recording, report_name in REPORT_SPECS:
        report_path = report_dir / report_name if report_dir.is_absolute() else root / report_dir / report_name
        report = json.loads(report_path.read_text(encoding="utf-8"))
        _bind_external_evidence_root(report, report_path, recording, evidence_roots)
        reports_by_recording[recording] = report
        source_sha256 = (report.get("source") or {}).get("sha256")
        report_rows = [
            row
            for row in audit_data.get("contributions", [])
            if row.get("recording") == recording and row.get("basis") == "state_derived"
        ]
        seen_ids: set[tuple[str, str, str]] = set()
        recording_errors_before = len(errors)
        for audit_row in report_rows:
            key = (recording, source_sha256, audit_row.get("id"))
            if key in seen_ids:
                errors.append(
                    {
                        "recording": recording,
                        "kind": "duplicate_canonical_contribution",
                        "id": audit_row.get("id"),
                    }
                )
                continue
            seen_ids.add(key)
            row, row_errors = _row_for_contribution(
                root, recording, report, audit_row, case_index
            )
            row["source_sha256"] = source_sha256
            if (row.get("recording"), row.get("turn_id")) in frozen:
                row["frozen_case_ids"] = [
                    frozen[(row.get("recording"), row.get("turn_id"))]["id"]
                ]
            rows.append(row)
            for error in row_errors:
                errors.append({"recording": recording, "id": audit_row.get("id"), **error})
        report_summaries.append(
            {
                "recording": recording,
                "report": str((report_dir / report_name).as_posix()),
                "source_sha256": source_sha256,
                "contribution_count": len(report_rows),
                "error_count": len(errors) - recording_errors_before,
                "evidence_root": (report.get("evaluation_context") or {}).get(
                    "evidence_root"
                ),
            }
        )

    reviewed_cases = []
    for case in MANUAL_CASES:
        report = reports_by_recording[case["recording"]]
        evidence_root = Path(
            (report.get("evaluation_context") or {}).get("evidence_root") or root
        )
        readings = (report.get("gameplay_tracking") or {}).get("readings") or []
        frames = []
        for relative, timestamp, note in case["source_frames"]:
            absolute, missing = _source_paths(evidence_root, [relative])
            report_readings = []
            for index, reading in enumerate(readings):
                if relative not in _reading_evidence(reading):
                    continue
                facts = reading.get("facts") or {}
                report_readings.append(
                    {
                        "reading_index": index,
                        "source_timestamp_ms": reading.get("source_timestamp_ms"),
                        "screen": reading.get("screen"),
                        "training_option": reading.get("training_option"),
                        "facts": {
                            "training_gains": facts.get("training_gains"),
                            "training_gain_candidates": facts.get(
                                "training_gain_candidates"
                            ),
                            "result_values": facts.get("result_values"),
                        },
                    }
                )
            frames.append(
                {
                    "relative_path": relative,
                    "absolute_path": (
                        absolute[0] if absolute else str(evidence_root / relative)
                    ),
                    "source_timestamp_ms": timestamp,
                    "visual_observation": note,
                    "report_readings": report_readings,
                    "missing": missing,
                }
            )
            if missing:
                errors.append(
                    {
                        "recording": case["recording"],
                        "kind": "manual_review_source_path_missing",
                        "case_id": case["id"],
                        "path": relative,
                    }
                )
        reviewed_cases.append(
            {
                "id": case["id"],
                "recording": case["recording"],
                "event_id": case["event_id"],
                "turn_id": case["turn_id"],
                "status": case["status"],
                "badge_visibility": case["badge_visibility"],
                "fields": list(case["fields"]),
                "observation": case["observation"],
                "source_frames": frames,
            }
        )

    cause_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cause_rows[row.get("cause_group", "unknown")].append(row)
    cause_groups = {
        cause: _cause_group(group_rows, cause)
        for cause, group_rows in sorted(cause_rows.items())
    }
    for cause in CAUSE_INFO:
        cause_groups.setdefault(cause, _cause_group([], cause))

    direct_status_counts = Counter(
        (row.get("direct_reader_observation") or {}).get("status") for row in rows
    )
    direct_target_buckets: defaultdict[str, Counter[str]] = defaultdict(Counter)
    candidate_target_buckets: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        direct = row.get("direct_reader_observation") or {}
        status = direct.get("status")
        direct_target_buckets[status][
            _bucket(direct.get("direct_target_timestamp_count", 0))
        ] += 1
        candidate_target_buckets[status][
            _bucket(direct.get("candidate_target_timestamp_count", 0))
        ] += 1

    transition_endpoint_matches = Counter(
        bool(
            (row.get("structural_transition_context") or {}).get(
                "endpoint_amount_match"
            )
        )
        for row in rows
    )
    amount_matches = Counter(
        bool((row.get("source_bound_operands") or {}).get("amount_match"))
        for row in rows
    )
    source_pointer_error_count = sum(
        1
        for error in errors
        if error.get("kind", "").startswith("transition_")
        or error.get("kind")
        in {
            "missing_source_bound_before_reading",
            "missing_source_bound_after_result_reading",
        }
    )
    missing_evidence_path_count = sum(
        1
        for error in errors
        if error.get("kind")
        in {
            "missing_event_field_evidence_paths",
            "missing_source_bound_evidence_paths",
            "manual_review_source_path_missing",
        }
    )
    channel_counts = Counter(row.get("channel") for row in rows)
    field_counts = Counter(row.get("field") for row in rows)
    event_kind_counts = Counter(row.get("event_kind") for row in rows)
    recording_counts = Counter(row.get("recording") for row in rows)
    frozen_rows = [row for row in rows if row.get("frozen_case_ids")]
    reviewed_rows = [
        row
        for row in rows
        if (row.get("source_review") or {}).get("status")
        != "not_individually_reviewed"
    ]
    source_review_statuses = Counter(
        (row.get("source_review") or {}).get("status") for row in rows
    )
    badge_visibility = Counter(
        (row.get("source_review") or {}).get("badge_visibility") for row in rows
    )
    extra_candidate_count = sum(
        bool((row.get("direct_reader_observation") or {}).get("extra_candidate_values"))
        for row in rows
    )
    crosscheck_count = sum(
        bool(
            (row.get("direct_reader_observation") or {}).get(
                "gain_digit_crosscheck_timestamps_ms"
            )
        )
        for row in rows
    )
    structural_tag_count = sum(bool(row.get("state_derived_tag")) for row in rows)
    event_window_durations = []
    pre_event_gaps = []
    for row in rows:
        source_bound = row.get("source_bound_operands") or {}
        event_window = source_bound.get("event_window_ms") or []
        before_timestamp = (source_bound.get("before") or {}).get(
            "source_timestamp_ms"
        )
        if (
            len(event_window) == 2
            and type(event_window[0]) is int
            and type(event_window[1]) is int
            and event_window[1] >= event_window[0]
        ):
            event_window_durations.append(event_window[1] - event_window[0])
            if type(before_timestamp) is int and event_window[0] >= before_timestamp:
                pre_event_gaps.append(event_window[0] - before_timestamp)

    summary = {
        "contribution_count": len(rows),
        "historical_compatibility_count": (
            (audit_data.get("summary") or {})
            .get("historical_state_derived_compatibility", {})
            .get("historical_summary", {})
            .get("contribution_count", 0)
        ),
        "historical_baseline": (audit_data.get("summary") or {}).get(
            "historical_baseline", {}
        ),
        "recording_counts": _counter(recording_counts),
        "channel_counts": _counter(channel_counts),
        "field_counts": _counter(field_counts),
        "event_kind_counts": _counter(event_kind_counts),
        "source_bound_amount_match_count": amount_matches[True],
        "source_bound_amount_mismatch_count": amount_matches[False],
        "legacy_expected_value_compatibility_match_count": sum(
            bool(
                ((row.get("source_bound_operands") or {}).get(
                    "legacy_expected_value_compatibility"
                ) or {}).get("compatible")
            )
            for row in rows
        ),
        "legacy_expected_value_compatibility_mismatch_count": sum(
            not bool(
                ((row.get("source_bound_operands") or {}).get(
                    "legacy_expected_value_compatibility"
                ) or {}).get("compatible")
            )
            for row in rows
        ),
        "source_bound_constraint_match_count": sum(
            bool(
                (row.get("source_bound_operands") or {})
                .get("constraints", {})
                .get("post_result_equals_before_plus_amount")
            )
            for row in rows
        ),
        "source_bound_verified_row_count": sum(
            bool(
                (row.get("source_bound_operands") or {}).get(
                    "source_bound_arithmetic_verified"
                )
            )
            for row in rows
        ),
        "source_bound_before_latest_count": sum(
            bool(
                (((row.get("source_bound_operands") or {}).get("constraints") or {}).get(
                    "before_is_latest_pre_event_reading"
                ))
            )
            for row in rows
        ),
        "source_bound_after_first_match_count": sum(
            bool(
                (((row.get("source_bound_operands") or {}).get("constraints") or {}).get(
                    "after_is_first_stable_suffix_reading"
                ))
            )
            for row in rows
        ),
        "focused_operand_tests": focused_operand_tests,
        "source_bound_event_window_duration_ms": _window(event_window_durations),
        "source_bound_pre_event_gap_ms": _window(pre_event_gaps),
        "unsupported_or_unproven_source_bound_rows": sum(
            not bool(
                (row.get("source_bound_operands") or {}).get(
                    "source_bound_arithmetic_verified"
                )
            )
            for row in rows
        ),
        "source_pointer_error_count": source_pointer_error_count,
        "missing_source_evidence_path_count": missing_evidence_path_count,
        "transition_endpoint_amount_match_count": transition_endpoint_matches[True],
        "transition_endpoint_amount_mismatch_count": transition_endpoint_matches[False],
        "direct_status_counts": _counter(direct_status_counts),
        "source_review_status_counts": _counter(source_review_statuses),
        "badge_visibility_counts": _counter(badge_visibility),
        "direct_target_timestamp_buckets": {
            status: _counter(bucket_counts)
            for status, bucket_counts in sorted(direct_target_buckets.items())
        },
        "candidate_target_timestamp_buckets": {
            status: _counter(bucket_counts)
            for status, bucket_counts in sorted(candidate_target_buckets.items())
        },
        "candidate_target_seen_count": sum(
            bool(
                (row.get("direct_reader_observation") or {}).get(
                    "candidate_target_timestamps_ms"
                )
            )
            for row in rows
        ),
        "direct_target_seen_count": sum(
            bool(
                (row.get("direct_reader_observation") or {}).get(
                    "direct_target_timestamps_ms"
                )
            )
            for row in rows
        ),
        "rows_with_extra_candidate_values": extra_candidate_count,
        "rows_with_gain_digit_crosschecks": crosscheck_count,
        "state_derived_tag_count": structural_tag_count,
        "frozen_contribution_count": len(frozen_rows),
        "individually_reviewed_contribution_count": len(reviewed_rows),
        "reviewed_case_count": len(reviewed_cases),
        "badge_absence_inferred_from_parser": False,
        "full_history_accuracy_measured": None,
        "deduplication_key": "(recording, source_sha256, canonical contribution id)",
    }

    return {
        "schema_version": "tracen-replay/derived-source-causes-v1",
        "scope": {
            "checklist_gate": "G5",
            "input_directory": str(report_dir.as_posix()),
            "reports": [
                str((report_dir / name).as_posix()) for _, name in REPORT_SPECS
            ],
            "canonical_inventory": str(audit_path.as_posix()),
            "frozen_source_selection": str(selection_path.as_posix()),
            "selected_basis": "state_derived",
            "contribution_count_is_unique": True,
            "basis_legitimacy_proven": False,
            "operand_arithmetic_status": "independently_recomputed_from_recorded_operands",
            "recognizer_edits": False,
        },
        "inputs": report_summaries,
        "summary": summary,
        "cause_groups": cause_groups,
        "reviewed_cases": reviewed_cases,
        "contributions": rows,
        "errors": errors,
        "limitations": [
            "Source-bound arithmetic validates the per-event operands available in the preserved report; it does not establish complete event history.",
            "Source-bound arithmetic does not prove that state_derived was the only legitimate basis; direct-badge collisions are classified separately.",
            "A state-derived endpoint difference remains source-linked arithmetic evidence, not independent receipt evidence.",
            "Whole-turn transition endpoints can include sibling effects, so endpoint mismatch is not a per-contribution arithmetic error.",
            "A parser non-acceptance or a later animation frame without a badge does not establish badge absence.",
            "Only bounded reviewed cases have an explicit visual visibility statement; all other rows remain unreviewed.",
            "Full-history accuracy remains unmeasured.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="Repository root.")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR,
                        help="Directory containing the three report files.")
    parser.add_argument("--audit-path", type=Path, default=AUDIT_PATH,
                        help="Canonical derived audit JSON used to select rows.")
    parser.add_argument("--selection-path", type=Path, default=SELECTION_PATH,
                        help="Frozen source-case selection manifest.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--evidence-roots", type=Path,
                        help="Per-recording evidence roots bound to report and source SHA-256 hashes.")
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    args = parser.parse_args()
    root = args.root.resolve()
    bindings = (json.loads(args.evidence_roots.read_text(encoding="utf-8"))
                if args.evidence_roots else None)
    result = audit(root, args.report_dir, args.audit_path, args.selection_path, bindings)
    if args.evidence_roots:
        result["evidence_root_bindings"] = {
            "path": str(args.evidence_roots.resolve()),
            "sha256": _sha256(args.evidence_roots), "bindings": bindings}
    result["generator"] = {
        "script": str(Path(__file__).resolve().relative_to(root).as_posix()),
        "script_sha256": _sha256(Path(__file__).resolve()),
    }
    output = args.output if args.output.is_absolute() else root / args.output
    markdown_output = (
        args.markdown_output
        if args.markdown_output.is_absolute()
        else root / args.markdown_output
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown_output.write_text(_render_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "markdown_output": str(markdown_output),
                "summary": result["summary"],
                "error_count": len(result["errors"]),
            },
            indent=2,
        )
    )
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
