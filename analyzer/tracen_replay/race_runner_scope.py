"""Keep race Attributes observations runner-scoped until ownership is proven.

The race entry card is a selectable runner view.  A readable name, bib,
favorite label, portrait, expected statistics, or the default carousel
position therefore cannot identify the trainee.  This module is deliberately
an input adapter rather than an OCR reader: callers supply values that were
already read from a gameplay crop, and the adapter preserves them even when
the owner is unknown.

Only explicit gameplay evidence can verify ownership.  A direct marker,
stable card identity, or an owner-exclusive control needs source-reviewed
evidence from at least two frames.  The green ``Change`` control seen in the
preserved race cards is represented as a candidate affordance and never
passes that guard by itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import re
from typing import Any


SCHEMA = "tracen-replay/race-runner-scope-v1"
EVENT = "race_runner_attributes"
STAT_FIELDS = ("speed", "stamina", "power", "guts", "wit")
APTITUDE_FIELDS = ("turf", "medium", "pace")
STRATEGY_FIELDS = ("end", "late", "pace", "front")
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class RunnerScopeError(ValueError):
    """Raised when an explicit runner observation has an invalid shape."""


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _integer(value: Any) -> int | None:
    """Return a nonnegative integer, excluding bool and numeric strings."""

    if type(value) is int and value >= 0:
        return value
    return None


def _timestamp(value: Any) -> int | None:
    value = _integer(value)
    return value


def _hash(value: Any) -> str | None:
    if isinstance(value, str) and _SHA256.fullmatch(value):
        return value.lower()
    return None


def _evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item.strip() for item in value
                             if isinstance(item, str) and item.strip()))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _frame_tokens(value: Any) -> list[str]:
    """Extract one canonical token per source-frame observation.

    A frame record commonly carries both a timestamp and an evidence path.
    Those are two identifiers for one frame, not two frames.  Prefer the
    stable frame id, then timestamp, and use evidence only when neither is
    present.  Grouped positive/negative frame lists are still expanded so
    each child observation contributes exactly one token.
    """

    if isinstance(value, Mapping):
        grouped = []
        for key in ("frames", "source_frames", "observations", "positive_frames",
                    "target_frames", "alternate_frames", "negative_frames"):
            if key in value:
                grouped.extend(_frame_tokens(value[key]))
        if grouped:
            return list(dict.fromkeys(grouped))
        frame_id = _first(value, "frame_id", "id")
        if isinstance(frame_id, str) and frame_id.strip():
            return [f"frame:{frame_id.strip()}"]
        timestamp = _first(value, "source_timestamp_ms", "timestamp_ms")
        if type(timestamp) is int and timestamp >= 0:
            return [f"timestamp:{timestamp}"]
        evidence = _evidence(_first(value, "evidence", "source_evidence"))
        if evidence:
            return ["evidence:" + "|".join(evidence)]
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        tokens = []
        for item in value:
            tokens.extend(_frame_tokens(item))
        return list(dict.fromkeys(tokens))
    if type(value) is int:
        return [f"timestamp:{value}"]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _state(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "unknown"
    explicit = _text(_first(value, "state", "settled_state"))
    if explicit in ("bright_green_enabled", "dim_or_entering", "unknown"):
        return explicit
    visible = value.get("visible")
    enabled = value.get("enabled")
    if enabled is None:
        enabled = value.get("settled_enabled")
    if visible is None and type(enabled) is bool:
        visible = enabled
    if visible is True and enabled is True:
        return "bright_green_enabled"
    if visible is False or enabled is False:
        return "dim_or_entering"
    return "unknown"


def _bool_or_none(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _normalize_controls(value: Any) -> dict[str, Any]:
    """Copy only gameplay card controls and label Change as a candidate.

    ``controls`` may contain OCR or UI metadata from a caller.  The output
    deliberately keeps the small, stable shape used by the source review and
    never trusts an input owner status without separate owner evidence.
    """

    source = _mapping(value)
    change_value = _first(source, "change", "Change")
    change_source = _mapping(change_value)
    if not change_source and any(key in source for key in (
            "change_label", "settled_enabled", "settled_state", "visible", "enabled")):
        change_source = source
    change: dict[str, Any] = {
        "label": _text(_first(change_source, "label", "change_label")) or "Change",
        "visible": _bool_or_none(_first(change_source, "visible", "settled_visible")),
        "enabled": _bool_or_none(_first(change_source, "enabled", "settled_enabled")),
        "state": _state(change_source),
        "owner_affordance_status": "unknown",
    }
    if (change["state"] == "bright_green_enabled"
            or change["visible"] is True or change["enabled"] is True):
        change["owner_affordance_status"] = "candidate_only"

    navigation_source = _mapping(_first(source, "navigation", "card_navigation"))
    navigation = {
        "left_arrow_visible": _bool_or_none(
            _first(navigation_source, "left_arrow_visible", "left_arrow")),
        "right_arrow_visible": _bool_or_none(
            _first(navigation_source, "right_arrow_visible", "right_arrow")),
        "runners_button_visible": _bool_or_none(
            _first(navigation_source, "runners_button_visible", "runners_button",
                   "runners")),
    }
    if not navigation_source:
        for key in navigation:
            navigation[key] = _bool_or_none(source.get(key))
    result = {"change": change, "navigation": navigation}
    # Preserve a gameplay crop region when supplied, but do not manufacture a
    # region from a name or a screen's default layout.
    region = _first(change_source, "gameplay_crop_region", "region_in_gameplay_crop",
                    "region_in_gameplay_crop", "region")
    if isinstance(region, (list, tuple)) and len(region) == 4:
        result["change"]["gameplay_crop_region"] = list(region)
    return result


def _normalize_owner_evidence(value: Any) -> dict[str, Any]:
    """Validate an explicit gameplay-only owner proof.

    The returned object is safe to put in a report.  Arbitrary input fields,
    including auxiliary panel text, are not copied into the ownership proof.
    """

    source = _mapping(value)
    kind = _text(_first(source, "kind", "basis", "type"))
    gameplay_only = source.get("gameplay_only") is True
    excluded = source.get("excluded_auxiliary_context") is True
    verified_flag = source.get("verified") is True
    tokens = _frame_tokens(source)
    result: dict[str, Any] = {
        "kind": kind,
        "gameplay_only": gameplay_only,
        "verified": False,
        "source_frame_count": len(tokens),
        "frame_tokens": tokens,
        "owner_affordance_status": "unknown",
        "reason": "No explicit gameplay-only owner proof was supplied.",
    }
    source_evidence = _evidence(_first(source, "source_evidence", "evidence"))
    if source_evidence:
        result["source_evidence"] = source_evidence
    if not source:
        return result
    if excluded or not gameplay_only:
        result["reason"] = "Auxiliary or non-gameplay evidence cannot bind a runner card."
        return result
    if len(tokens) < 2:
        result["reason"] = "Owner proof requires at least two distinct source frames."
        return result

    if kind in ("direct_marker", "gameplay_marker", "own_marker"):
        marker = _text(_first(source, "marker", "label", "text", "token"))
        if not marker:
            result["reason"] = "A gameplay owner marker must contain readable marker text."
            return result
        result.update(marker=marker)
        if not verified_flag:
            result["reason"] = "The gameplay marker was not marked source-reviewed."
            return result
        result.update(verified=True, owner_affordance_status="verified_owner_exclusive",
                      reason="Source-reviewed gameplay owner marker.")
        return result

    if kind in ("stable_identity_link", "stable_card_identity", "card_identity"):
        stable_id = _text(_first(source, "stable_id", "card_id", "identity"))
        if not stable_id:
            result["reason"] = "A stable gameplay card identity is missing."
            return result
        result["stable_id"] = stable_id
        if not verified_flag:
            result["reason"] = "The stable card identity was not marked source-reviewed."
            return result
        result.update(verified=True, owner_affordance_status="verified_owner_exclusive",
                      reason="Source-reviewed gameplay stable card identity.")
        return result

    if kind in ("exclusive_control", "owner_exclusive_control"):
        control = _text(_first(source, "control", "control_label", "label"))
        exclusive = source.get("exclusive_to_trainee_proven") is True
        positive = _frame_tokens(_first(source, "positive_frames", "target_frames",
                                        "owner_frames"))
        negative = _frame_tokens(_first(source, "negative_frames", "alternate_frames",
                                        "non_owner_frames"))
        result.update(control=control, positive_frame_count=len(set(positive)),
                      negative_frame_count=len(set(negative)))
        if not control:
            result["reason"] = "An owner-exclusive control proof must name the control."
            return result
        if set(positive) & set(negative):
            result["reason"] = "Target and alternate control proof frames overlap."
            return result
        if not exclusive:
            result["reason"] = "Control exclusivity was not source-proven."
            return result
        if len(set(positive)) < 2 or len(set(negative)) < 2:
            result["reason"] = "Control proof needs two target and two alternate frames."
            return result
        if not verified_flag:
            result["reason"] = "The control comparison was not marked source-reviewed."
            return result
        result.update(verified=True, owner_affordance_status="verified_owner_exclusive",
                      reason="Source-reviewed gameplay control is owner-exclusive.")
        return result

    result["reason"] = "Owner evidence kind is unsupported; name, default, and stat agreement are not owner proof."
    return result


def evaluate_owner_evidence(value: Any) -> dict[str, Any]:
    """Return a conservative, sanitized evaluation of explicit owner proof."""

    return _normalize_owner_evidence(value)


def _result_corroborates(card: Mapping[str, Any], value: Any) -> bool:
    """Check continuity without treating it as ownership proof."""

    source = _mapping(value)
    if source.get("gameplay_only") is not True or source.get("observed") is not True:
        return False
    if any(card.get(field) is None for field in ("race_name", "runner_name", "bib_number")):
        return False
    if _text(source.get("race_name")) != card.get("race_name"):
        return False
    if _text(source.get("runner_name")) != card.get("runner_name"):
        return False
    return (_integer(source.get("bib_number")) == card.get("bib_number")
            and len(_frame_tokens(source)) >= 1)


def _field_value(raw: Mapping[str, Any], facts: Mapping[str, Any], *keys: str) -> Any:
    value = _first(raw, *keys)
    if value is not None:
        return value
    return _first(facts, *keys)


def _stats(raw: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[dict[str, int | None], list[str]]:
    nested = _field_value(raw, facts, "stats", "attributes", "race_runner_attributes")
    nested = _mapping(nested)
    nested_values = _mapping(_first(nested, "values", "stats", "attributes"))
    values: dict[str, int | None] = {}
    unknown: list[str] = []
    for field in STAT_FIELDS:
        value = _first(nested, field)
        if value is None:
            value = nested_values.get(field)
        if value is None:
            value = _field_value(raw, facts, field)
        normalized = _integer(value)
        values[field] = normalized
        if normalized is None:
            unknown.append(field)
    return values, unknown


def _aptitude(raw: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    value = _mapping(_field_value(raw, facts, "aptitude", "aptitudes"))
    result: dict[str, Any] = {}
    unknown: list[str] = []
    for field in APTITUDE_FIELDS:
        normalized = _text(value.get(field))
        if normalized is None:
            normalized = _text(_field_value(raw, facts, field + "_aptitude"))
        result[field] = normalized
        if normalized is None:
            unknown.append(f"aptitude.{field}")
    return result, unknown


def _strategy(raw: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[dict[str, int | None], list[str]]:
    value = _mapping(_field_value(raw, facts, "strategy_counts", "strategy"))
    result: dict[str, int | None] = {}
    unknown: list[str] = []
    for field in STRATEGY_FIELDS:
        normalized = _integer(value.get(field))
        if normalized is None:
            normalized = _integer(_field_value(raw, facts, field + "_strategy"))
        result[field] = normalized
        if normalized is None:
            unknown.append(f"strategy_counts.{field}")
    return result, unknown


def _source_metadata(raw: Mapping[str, Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    timestamp = _timestamp(_first(kwargs, "source_timestamp_ms", "timestamp_ms"))
    if timestamp is None:
        timestamp = _timestamp(_first(raw, "source_timestamp_ms", "timestamp_ms"))
    evidence = _first(kwargs, "source_evidence", "evidence")
    if evidence is None:
        evidence = _first(raw, "source_evidence", "evidence")
    frame_hash = _hash(_first(kwargs, "source_frame_sha256", "frame_sha256"))
    if frame_hash is None:
        frame_hash = _hash(_first(raw, "source_frame_sha256", "frame_sha256"))
    source_hash = _hash(_first(kwargs, "source_sha256"))
    if source_hash is None:
        source_hash = _hash(_first(raw, "source_sha256"))
    return {
        "source_timestamp_ms": timestamp,
        "source_evidence": _evidence(evidence),
        "source_frame_sha256": frame_hash,
        "source_sha256": source_hash,
    }


def read_runner_facts(
    raw: Mapping[str, Any],
    *,
    source_timestamp_ms: int | None = None,
    source_frame_sha256: str | None = None,
    source_sha256: str | None = None,
    evidence: str | Sequence[str] | None = None,
    owner_evidence: Mapping[str, Any] | None = None,
    result_corroboration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one gameplay-card observation with explicit ownership status.

    ``raw`` can be a parsed reading with a ``facts`` object or a compact card
    mapping.  Missing and malformed visual values become ``None`` and are
    listed in ``visibility_limit``.  The function never examines or promotes
    a right-hand profile/trainee panel; only the supplied gameplay-card fields
    and explicit gameplay owner proof participate.
    """

    if not isinstance(raw, Mapping):
        raise RunnerScopeError("Runner card observation must be a mapping.")
    facts = _mapping(raw.get("facts"))
    metadata = _source_metadata(
        raw,
        {
            "source_timestamp_ms": source_timestamp_ms,
            "source_frame_sha256": source_frame_sha256,
            "source_sha256": source_sha256,
            "evidence": evidence,
        },
    )

    race_value = _field_value(raw, facts, "race_name", "race")
    race_mapping = _mapping(race_value)
    race_name = _text(race_mapping.get("name")) if race_mapping else _text(race_value)
    race_grade = _text(_field_value(raw, facts, "race_grade", "grade"))
    if race_grade is None and race_mapping:
        race_grade = _text(_first(race_mapping, "grade", "race_grade"))
    runner_value = _field_value(raw, facts, "runner_name", "runner")
    runner_mapping = _mapping(runner_value)
    runner_name = (_text(_first(runner_mapping, "name", "runner_name"))
                   if runner_mapping else _text(runner_value))
    bib_number = _integer(_field_value(raw, facts, "bib_number", "bib", "runner_number"))
    stats, unknown_stats = _stats(raw, facts)
    aptitude, unknown_aptitude = _aptitude(raw, facts)
    mood = _text(_field_value(raw, facts, "mood", "condition"))
    strategy, unknown_strategy = _strategy(raw, facts)

    controls_value = _field_value(raw, facts, "selected_card_controls", "controls")
    controls = _normalize_controls(controls_value)

    card = {
        "race_name": race_name,
        "race_grade": race_grade,
        "runner_name": runner_name,
        "bib_number": bib_number,
    }
    explicit_owner = owner_evidence
    if explicit_owner is None:
        candidate = raw.get("owner_evidence")
        explicit_owner = candidate if isinstance(candidate, Mapping) else None
    # A caller can explicitly identify a full-frame/side-log input.  Preserve
    # its card values if supplied, but never let even otherwise well-shaped
    # owner evidence bind that non-gameplay input.
    non_gameplay_input = (
        raw.get("gameplay_only") is False
        or raw.get("auxiliary_log_used") is True
        or raw.get("evidence_scope") == "auxiliary"
    )
    owner = _normalize_owner_evidence(
        {"gameplay_only": False, "excluded_auxiliary_context": True}
        if non_gameplay_input else explicit_owner
    )
    continuity = result_corroboration
    if continuity is None:
        candidate = raw.get("result_corroboration")
        continuity = candidate if isinstance(candidate, Mapping) else None
    continuity_verified = _result_corroborates(card, continuity)
    owner_verified = owner["verified"] is True
    if owner_verified:
        identity_status = "trainee_owner_verified"
    elif continuity_verified:
        identity_status = "run_continuity_owner_unverified"
    else:
        identity_status = "visible_identity_unverified"

    unknown_fields = []
    if race_name is None:
        unknown_fields.append("race_name")
    if race_grade is None:
        unknown_fields.append("race_grade")
    if runner_name is None:
        unknown_fields.append("runner_name")
    if bib_number is None:
        unknown_fields.append("bib_number")
    unknown_fields.extend(unknown_stats)
    unknown_fields.extend(unknown_aptitude)
    if mood is None:
        unknown_fields.append("mood")
    unknown_fields.extend(unknown_strategy)

    # Keep the non-visual limits explicit.  These values are not supplied by
    # an Attributes card and must not be filled from defaults or another panel.
    not_visible_fields = ["skill_points", "performance_points"]
    visibility_limit = {
        "scope": "gameplay_crop_only",
        "card_fields_complete": not unknown_fields,
        "unknown_fields": list(dict.fromkeys(unknown_fields)),
        "missing_fields": list(dict.fromkeys(unknown_fields)),
        "not_visible_fields": not_visible_fields,
        "reason": (
            "Only values visible on the gameplay runner card are emitted. "
            "Unknown fields remain null; the auxiliary profile panel, "
            "default card position, runner name, favorite label, and expected "
            "statistics cannot fill them."
        ),
    }

    # A missing hash is a provenance limitation, not a reason to discard a
    # readable runner card.  Keep it visible to the caller for later binding.
    provenance_limit = []
    if metadata["source_timestamp_ms"] is None:
        provenance_limit.append("source_timestamp_ms")
    if not metadata["source_evidence"]:
        provenance_limit.append("source_evidence")
    if metadata["source_frame_sha256"] is None:
        provenance_limit.append("source_frame_sha256")
    if provenance_limit:
        visibility_limit["provenance_limits"] = provenance_limit

    if owner_verified:
        promotion = {
            "to_trainee": True,
            "allowed": True,
            "basis": owner["reason"],
        }
    else:
        blockers = ["gameplay_owner_marker_or_owner_exclusive_control"]
        if owner["reason"] != "No explicit gameplay-only owner proof was supplied.":
            blockers.append(owner["reason"])
        promotion = {
            "to_trainee": False,
            "allowed": False,
            "basis": "runner_scoped_only",
            "blockers": list(dict.fromkeys(blockers)),
        }

    continuity_output = {
        "observed": continuity_verified,
        "promotes_owner": False,
    }
    if isinstance(continuity, Mapping):
        for key in ("source_timestamp_ms", "race_name", "runner_name", "bib_number"):
            if key in continuity:
                continuity_output[key] = deepcopy(continuity[key])
        evidence = _evidence(_first(continuity, "source_evidence", "evidence"))
        if evidence:
            continuity_output["source_evidence"] = evidence

    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "event": EVENT,
        "runner_scoped": True,
        "scope": "runner",
        "gameplay_only": not non_gameplay_input,
        "evidence_scope": "gameplay_crop_only" if not non_gameplay_input else "non_gameplay_rejected",
        "identity_status": identity_status,
        "owner_status": identity_status,
        "ownership_status": identity_status,
        "owner_verified": owner_verified,
        "source_timestamp_ms": metadata["source_timestamp_ms"],
        "source_evidence": metadata["source_evidence"],
        "source_frame_sha256": metadata["source_frame_sha256"],
        "source_sha256": metadata["source_sha256"],
        "race_name": race_name,
        "race_grade": race_grade,
        "runner_name": runner_name,
        "bib_number": bib_number,
        "stats": stats,
        "attributes": deepcopy(stats),
        "aptitude": aptitude,
        "mood": mood,
        "strategy_counts": strategy,
        "selected_card_controls": controls,
        "owner_binding": owner,
        "result_corroboration": continuity_output,
        "visibility_limit": visibility_limit,
        "promotion": promotion,
        "excluded_auxiliary_context": {
            "status": "excluded_auxiliary_context",
            "used_for_binding": False,
            "read_or_promoted": False,
        },
    }
    # Flat aliases make the observation convenient for report adapters while
    # retaining the canonical ``stats`` object for runner-scoped consumers.
    result.update(stats)
    return result


def runner_scoped_stats(raw: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Compatibility entry point for numeric-owner integrations."""

    return read_runner_facts(raw, **kwargs)


__all__ = [
    "APTITUDE_FIELDS",
    "EVENT",
    "IDENTITY_STATUSES",
    "OWNER_AFFORDANCE_STATUSES",
    "RunnerScopeError",
    "SCHEMA",
    "STAT_FIELDS",
    "STRATEGY_FIELDS",
    "build_runner_observation",
    "evaluate_owner_evidence",
    "owner_status",
    "read_runner_card",
    "read_runner_facts",
    "runner_facts",
    "runner_scoped_observation",
    "runner_scoped_stats",
]
