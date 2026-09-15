"""Source-only training gain phase and stable-result helpers.

The transaction layer sees several OCR views of one training animation.  This
module keeps the two independent questions separate:

* a repeated gain badge may identify a direct amount, including a complete
  badge and a bounded component phase in either source order; and
* a trailing result counter may support an amount derived from a before state.

Neither helper receives a balance or a claimed amount while selecting its
observations.  Balances are used by the caller only after an observation set
has been selected, to recompute and label the resulting change.
"""

import math
import posixpath
import re
from collections.abc import Mapping

from .crop_provenance import (
    CROP_FAMILY_RULES,
    crop_family,
    source_refinement_candidate_is_bound,
)
from .ocr_confidence import confidence_percent


_SOURCE_CROP_BASES = frozenset(
    {
        "source_crop_family_geometry",
        "source_crop_family_geometry_prefix_resolution",
        "same_family_scaled_crop_prefix_consensus",
        "source_pixel_localized_training_badge_with_tight_agreement",
        "source_crop_pixel_localized_training_badge_agreement",
    }
)
_SOURCE_CROP_STATES = frozenset(
    {
        "resolved_same_amount_across_source_crops",
        "resolved_broader_prefix",
        "resolved_same_family_prefix",
        "resolved_source_pixel_localized",
    }
)
_SOURCE_HASH_KEYS = (
    "source_frame_sha256",
    "capture_sha256",
    "image_sha256",
)
_SOURCE_PATH_KEYS = ("path", "evidence", "source_path")
_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_GLOBAL_GAMEPLAY_PANE = (148.0, 0.0, 958.0, 1080.0)


def is_committed_training_result_row(row):
    """Return whether a result row is eligible for successful gain proofs.

    A failed result can still carry legitimate failure effects, so this helper
    is scoped to signed gain and performance proof channels.  It rejects only
    explicit preview or failure markers; an ordinary result row without an
    outcome marker remains eligible for the legacy direct parser.
    """

    if not isinstance(row, Mapping) or row.get("screen") != "training_result":
        return False
    stats = row.get("stats")
    if not isinstance(stats, Mapping):
        stats = {}
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return False
    if stats.get("training_preview") is True:
        return False
    if any(
        facts.get(key) is True
        for key in (
            "preview",
            "preview_overlay_proven",
            "preview_modifier_proven",
            "training_preview",
        )
    ):
        return False
    outcome = facts.get("training_outcome")
    if isinstance(outcome, str) and outcome.strip().casefold() in {"failure", "failed"}:
        return False
    if facts.get("failure_banner"):
        return False
    explicit = row.get("phase")
    if explicit is None:
        explicit = row.get("training_phase")
    if explicit is None:
        for key in ("phase", "training_phase", "source_phase"):
            if facts.get(key) is not None:
                explicit = facts[key]
                break
    if isinstance(explicit, str):
        marker = explicit.strip().casefold().replace("-", "_").replace(" ", "_")
        if marker in {"preview", "training_preview", "projected", "projection"}:
            return False
    return True


def _finite_positive_box(value):
    """Return one finite, non-empty global gameplay crop box, or ``None``."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(part, bool) or not isinstance(part, (int, float)) for part in value):
        return None
    try:
        box = tuple(float(part) for part in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(part) for part in box):
        return None
    left, top, right, bottom = box
    pane_left, pane_top, pane_right, pane_bottom = _GLOBAL_GAMEPLAY_PANE
    if not (
        pane_left <= left < right <= pane_right
        and pane_top <= top < bottom <= pane_bottom
    ):
        return None
    return box

def _row_time(row):
    value = row.get("source_timestamp_ms")
    # Timestamps are source identity metadata, not an OCR value.  Reject
    # negative and non-integer forms before any chronological arithmetic.  In
    # particular, accepting ``-1`` would let a malformed row become the first
    # member of an otherwise valid repeated-gain proof.
    return value if type(value) is int and value >= 0 else None


def _normalize_evidence_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized)
    normalized = posixpath.normpath(normalized)
    if normalized in {"", "."}:
        return None
    return normalized.casefold()


def _evidence_identity(value):
    """Return a hashable identity for string or structured proof references."""

    if isinstance(value, str):
        return _normalize_evidence_path(value)
    if isinstance(value, dict):
        # Prefer the physical capture identity when a sidecar supplies it. A
        # copied path can name the same frame, while a source-frame hash still
        # lets the suffix proof reject that duplicate.
        for key in (
            "source_frame_sha256",
            "capture_sha256",
            "image_sha256",
        ):
            candidate = value.get(key)
            if candidate is not None and (
                not isinstance(candidate, str)
                or _HEX_SHA256.fullmatch(candidate.strip()) is None
            ):
                # A declared but malformed hash must not silently downgrade
                # to an unrelated path alias.  Treat the source identity as
                # untrusted and let the caller abstain.
                return None
            if isinstance(candidate, str) and _HEX_SHA256.fullmatch(candidate.strip()):
                return f"{key}:{candidate.strip().casefold()}"
        for key in ("path", "evidence", "source_path"):
            candidate = _normalize_evidence_path(value.get(key))
            if candidate:
                return f"path:{candidate}"
    return None


def _row_context(row):
    """Return source-phase context used to reject conflicting duplicates."""

    stats = row.get("stats") if isinstance(row, Mapping) else None
    facts = row.get("facts") if isinstance(row, Mapping) else None
    if not isinstance(stats, Mapping):
        stats = {}
    if not isinstance(facts, Mapping):
        facts = {}
    explicit = row.get("phase") if isinstance(row, Mapping) else None
    if explicit is None and isinstance(row, Mapping):
        explicit = row.get("training_phase", facts.get("phase", facts.get("training_phase")))
    return (
        row.get("screen") if isinstance(row, Mapping) else None,
        row.get("training_option") if isinstance(row, Mapping) else None,
        stats.get("training_preview") is True
        or facts.get("preview") is True
        or facts.get("preview_overlay_proven") is True
        or facts.get("preview_modifier_proven") is True
        or facts.get("training_preview") is True
        or (
            isinstance(facts.get("training_outcome"), str)
            and facts.get("training_outcome").strip().casefold() in {"failure", "failed"}
        )
        or bool(facts.get("failure_banner")),
        explicit,
    )


def _source_identity_tokens(row):
    """Return physical source identity tokens, or ``None`` if absent.

    A timestamp is metadata, not a physical frame identity.  Repeated paths
    and repeated frame hashes therefore cannot inflate a source proof even if
    the OCR rows were copied into different timestamps.  Keeping every valid
    token lets the caller reject a path/hash disagreement as well as a direct
    duplicate.
    """

    def normalize_path(value):
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("\\", "/")
        normalized = re.sub(r"/+", "/", normalized)
        normalized = posixpath.normpath(normalized)
        return None if normalized in {"", "."} else normalized.casefold()

    evidence = row.get("evidence")
    if isinstance(evidence, str):
        text = normalize_path(evidence)
        return {f"path:{text}"} if text else None
    if not isinstance(evidence, dict):
        return None
    tokens = set()
    for key in _SOURCE_HASH_KEYS:
        value = evidence.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value.strip()):
            return None
        tokens.add(f"{key}:{value.strip().casefold()}")
    for key in _SOURCE_PATH_KEYS:
        value = evidence.get(key)
        normalized = normalize_path(value)
        if normalized:
            tokens.add(f"path:{normalized}")
    return tokens or None


def _unique_physical_source_rows(rows):
    """Reject missing or repeated physical identities in source observations."""

    seen = set()
    for row, _value, _shape in rows:
        tokens = _source_identity_tokens(row)
        if not tokens or seen.intersection(tokens):
            return None
        seen.update(tokens)
    return rows


def _row_shape(row):
    facts = row.get("facts", {})
    explicit = facts.get("observed_training_gain_fields")
    if isinstance(explicit, (list, tuple, set)):
        return frozenset(value for value in explicit if isinstance(value, str))
    return frozenset(
        field
        for field, amount in facts.get("training_gains", {}).items()
        if type(amount) is int
    )


def _deduplicate_observations(observations):
    """Return one physical observation per timestamp, or ``None`` on conflict.

    The specialized reader can contribute several views for one frame.  They
    are useful in the retained proof, but they cannot inflate repetition
    counts.  A timestamp carrying different values or shapes is ambiguous.
    """

    by_time = {}
    for row, value in observations:
        if not isinstance(row, Mapping):
            return None
        timestamp = _row_time(row)
        if timestamp is None or type(value) is not int or value < 0:
            return None
        shape = _row_shape(row)
        prior = by_time.setdefault(timestamp, [])
        prior.append((row, value, shape))
        if {item[1] for item in prior} != {value}:
            return None
        if len({item[2] for item in prior}) != 1:
            return None
        identity = _evidence_identity(row.get("evidence"))
        if identity is not None:
            for previous, _previous_value, _previous_shape in prior[:-1]:
                if _evidence_identity(previous.get("evidence")) == identity:
                    if _row_context(previous) != _row_context(row):
                        return None
    return [items[-1] for _, items in sorted(by_time.items())]


def distinct_gain_observations(observations):
    """Return unique physical gain observations for direct proofs.

    A copied row with the same normalized evidence path is one source frame
    even when a malformed sidecar assigns it a different timestamp.  Requiring
    a usable source identity here keeps the ordinary direct-value fallback
    subject to the same source boundary as the specialized phase resolvers.
    """

    rows = _deduplicate_observations(observations)
    if rows is None:
        return []
    seen = set()
    result = []
    for row, value, _shape in rows:
        identity = _evidence_identity(row.get("evidence"))
        if identity is None or identity in seen:
            return []
        seen.add(identity)
        result.append((row, value))
    return result


def source_gain_observations(rows, field):
    """Return gain observations retained by the source crop resolver.

    ``inspect_training.merge`` deliberately removes a field when two source
    views disagree.  Its per-field crop provenance still records a resolved
    canonical amount when the disagreement is a clipped component versus a
    broader readable badge.  Reintroduce that amount only from the provenance
    itself; this helper never consults a balance, expected amount, or result
    counter.  A row that already has a canonical ``training_gains`` value is
    left untouched so an explicit merged value remains the observation being
    audited.
    """

    result = []
    for row in rows:
        if not is_committed_training_result_row(row):
            continue
        facts = row.get("facts", {})
        gains = facts.get("training_gains", {})
        value = gains.get(field)
        if type(value) is int and value >= 0:
            result.append((row, value))
            continue
        provenance = facts.get("training_gain_crop_provenance", {}).get(field)
        if not isinstance(provenance, dict):
            continue
        value = provenance.get("canonical_amount")
        candidate = provenance.get("canonical_candidate")
        basis = provenance.get("canonical_basis")
        state = provenance.get("conflict_state")
        if (
            type(value) is int
            and value >= 0
            and isinstance(candidate, dict)
            and candidate.get("canonical_eligible") is True
            and isinstance(basis, str)
            and basis.startswith("source_crop_")
            and isinstance(state, str)
            and state.startswith("resolved_")
        ):
            result.append((row, value))
    return result


def _source_phase_proof(row, value, shape, field):
    facts = row.get("facts", {})
    provenance = facts.get("training_gain_crop_provenance", {}).get(field)
    candidate = provenance.get("canonical_candidate", {}) if isinstance(provenance, dict) else {}
    proof = dict(
        source_timestamp_ms=row["source_timestamp_ms"],
        evidence=row.get("evidence"),
        value=value,
        shape=sorted(shape),
        crop_basis=provenance.get("canonical_basis") if isinstance(provenance, dict) else None,
        crop_conflict_state=provenance.get("conflict_state") if isinstance(provenance, dict) else None,
        crop_region=candidate.get("region"),
        crop_candidate_amounts=provenance.get("candidate_amounts", []) if isinstance(provenance, dict) else [],
    )
    source_proof = _source_bound_proof(row, value, field)
    if source_proof is not None:
        proof["source_proof_kind"] = source_proof.get("kind")
        if source_proof.get("kind") == "bounded_training_gain_recovery":
            proof["recovery_owner_id"] = source_proof.get("owner_id")
            proof["recovery_requested_fields"] = source_proof.get("requested_fields")
    return proof


def _validated_source_crop_candidate(candidate, value, field):
    """Validate one canonical crop candidate and return family/box metadata."""

    if not isinstance(candidate, dict) or type(value) is not int or value < 0:
        return None
    region = candidate.get("region")
    if not isinstance(region, str) or not isinstance(field, str):
        return None
    recognized_family = crop_family(region)
    box = _finite_positive_box(candidate.get("box"))
    confidence = confidence_percent(candidate.get("confidence"))
    if (
        recognized_family is None
        or recognized_family == "result"
        or not region.endswith(f".{field}")
        or candidate.get("crop_family") != recognized_family
        or candidate.get("amount") != value
        or type(candidate.get("amount")) is not int
        or candidate.get("canonical_eligible") is not True
        or candidate.get("input_eligible") is not True
        or candidate.get("source_role") != "amount_crop_candidate"
        or box is None
        or confidence is None
        or confidence < CROP_FAMILY_RULES[recognized_family]["minimum_confidence"]
        or not source_refinement_candidate_is_bound(candidate)
    ):
        return None
    if recognized_family == "localized_gain":
        pixel_sha = candidate.get("pixel_rgb_sha256")
        if (
            candidate.get("source_pixel_verified") is not True
            or candidate.get("source_observation_basis")
            != "source_pixel_localized_training_badge"
            or not isinstance(pixel_sha, str)
            or _HEX_SHA256.fullmatch(pixel_sha) is None
        ):
            return None
    return recognized_family, box


def _canonical_source_provenance(row, value, field):
    """Return strict crop provenance for a source-backed amount.

    A row can carry a numeric ``training_gains`` value while its crop reader
    only supplied a diagnostic or unresolved candidate.  Those values remain
    useful observations, but they are not independently source-proven full
    phase operands.  Keep this predicate in one place so phase selection does
    not accidentally treat presence of ``canonical_amount`` as proof.
    """

    provenance = row.get("facts", {}).get("training_gain_crop_provenance", {}).get(field)
    if (
        not isinstance(provenance, dict)
        or type(provenance.get("canonical_amount")) is not int
        or provenance.get("canonical_amount") != value
    ):
        return None
    candidate = provenance.get("canonical_candidate")
    basis = provenance.get("canonical_basis")
    state = provenance.get("conflict_state")
    candidates = provenance.get("candidates")
    if (
        not isinstance(candidates, list)
        or not any(isinstance(item, dict) and item == candidate for item in candidates)
    ):
        return None
    validated = _validated_source_crop_candidate(candidate, value, field)
    if validated is None:
        return None
    _candidate_family, _candidate_box = validated
    candidate_amounts = provenance.get("candidate_amounts")
    if (
        not isinstance(candidate_amounts, list)
        or any(type(amount) is not int or amount < 0 for amount in candidate_amounts)
        or value not in candidate_amounts
        or basis not in _SOURCE_CROP_BASES
        or state not in _SOURCE_CROP_STATES
    ):
        return None
    return provenance


def _training_gain_recovery_proof(row, value, field):
    """Validate one bounded recovery row as source-phase evidence.

    ``training_gain_recovery`` rows are produced by the source-bound receipt
    reread.  They do not carry the native crop provenance because the reread
    stores the accepted field directly, but the owner, requested field,
    source timestamp, and source evidence are still persisted on the row.
    Treating an arbitrary ``training_gains`` value as a phase proof would make
    the fallback unsafe, so only this complete metadata tuple is admitted.
    """

    if not is_committed_training_result_row(row):
        return None
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return None
    recovery = facts.get("training_gain_recovery")
    if not isinstance(recovery, Mapping):
        return None
    owner_id = recovery.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id.strip():
        return None
    requested_fields = recovery.get("requested_fields")
    if not isinstance(requested_fields, (list, tuple, set)):
        return None
    if (
        not requested_fields
        or any(not isinstance(name, str) or not name.strip() for name in requested_fields)
        or field not in requested_fields
    ):
        return None
    timestamp = row.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        return None
    if recovery.get("source_timestamp_ms") != timestamp:
        return None
    evidence = row.get("evidence")
    recovery_evidence = recovery.get("evidence")
    row_identity = _normalize_evidence_path(evidence)
    recovery_identity = _normalize_evidence_path(recovery_evidence)
    if (
        row_identity is None
        or recovery_identity is None
        or row_identity != recovery_identity
    ):
        return None
    gains = facts.get("training_gains")
    if not isinstance(gains, Mapping) or gains.get(field) != value:
        return None
    return {
        "kind": "bounded_training_gain_recovery",
        "owner_id": owner_id.strip(),
        "requested_fields": list(requested_fields),
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
    }


def _source_bound_proof(row, value, field):
    """Return the source proof kind for one phase observation, if any."""

    provenance = _canonical_source_provenance(row, value, field)
    if provenance is not None:
        return {
            "kind": "canonical_source_crop",
            "provenance": provenance,
        }
    return _training_gain_recovery_proof(row, value, field)


def source_result_projection_proof(row, value, field):
    """Validate one direct gain from a committed result reread.

    The normal repeated-badge resolver needs multiple physical observations.
    A bounded result reread has a different proof shape: its owner window was
    scheduled from the same committed result, and this row carries the
    selected option, success phase, source identity, and typed signed field.
    Admit one such row only with that complete projection metadata; an
    ordinary one-frame OCR value remains insufficient.
    """

    proof = _training_gain_recovery_proof(row, value, field)
    if proof is None:
        return None
    facts = row.get("facts")
    recovery = facts.get("training_gain_recovery") if isinstance(facts, Mapping) else None
    if not isinstance(recovery, Mapping):
        return None
    if recovery.get("projection_mode") != "committed_result_direct_fields":
        return None
    owner_interval = recovery.get("owner_interval_ms")
    if (
        not isinstance(owner_interval, (list, tuple))
        or len(owner_interval) != 2
        or any(type(item) is not int or item < 0 for item in owner_interval)
        or owner_interval[0] > owner_interval[1]
        or not owner_interval[0] <= row.get("source_timestamp_ms") <= owner_interval[1]
    ):
        return None
    option = row.get("training_option")
    if not isinstance(option, str) or not option.strip():
        return None
    if recovery.get("training_option") != option:
        return None
    if facts.get("training_outcome") != "success":
        return None
    for key in (
        "source_sha256", "source_frame_sha256", "source_frame_id",
        "engine_fingerprint", "model_sha256", "gameplay_sha256",
    ):
        if key in recovery and recovery.get(key) != row.get(key):
            return None
    return dict(
        kind="committed_result_direct_field",
        owner_id=proof["owner_id"],
        requested_fields=proof["requested_fields"],
        source_timestamp_ms=proof["source_timestamp_ms"],
        evidence=proof["evidence"],
        owner_interval_ms=list(owner_interval),
        training_option=option,
        **{
            key: row[key]
            for key in (
                "source_sha256", "source_frame_sha256", "source_frame_id",
                "engine_fingerprint", "model_sha256", "gameplay_sha256",
            )
            if key in row
        },
    )


def source_result_projection_belongs_to_group(proof, row, group):
    """Bind a reread projection to the assembled source-result interval.

    Recovery owner IDs come from a pre-assembly plan and may be renumbered
    when events are rebuilt.  The stable binding is the source interval and
    selected option, which must enclose the final group's observed interval.
    """

    if not isinstance(proof, Mapping) or not isinstance(row, Mapping):
        return False
    if not isinstance(group, Mapping):
        return False
    option = group.get("option")
    if not isinstance(option, str) or not option.strip():
        return False
    if proof.get("training_option") != option:
        return False
    interval = proof.get("owner_interval_ms")
    if (
        not isinstance(interval, (list, tuple))
        or len(interval) != 2
        or any(type(item) is not int or item < 0 for item in interval)
        or interval[0] > interval[1]
    ):
        return False
    first = group.get("first_seen_ms")
    last = group.get("last_seen_ms")
    timestamp = row.get("source_timestamp_ms")
    if (
        any(type(item) is not int or item < 0 for item in (first, last, timestamp))
        or first > last
        or not interval[0] <= first <= last <= interval[1]
        or not first <= timestamp <= last
    ):
        return False
    return True


def _observation_field(rows):
    """Infer the one field shared by every field-specific observation.

    ``resolve_full_component_phase`` intentionally receives pairs rather than
    a field argument because its public callers also use it for synthetic
    shape proofs.  The source-only component-first branch needs the field to
    validate crop provenance.  Infer it only when every row maps its observed
    value to the same field; a numeric coincidence in one row is insufficient.
    """

    fields = set()
    for row, _value, _shape in rows:
        facts = row.get("facts", {})
        gains = facts.get("training_gains", {}) if isinstance(facts, dict) else {}
        if isinstance(gains, dict):
            fields.update(name for name in gains if isinstance(name, str))
        provenance = facts.get("training_gain_crop_provenance", {}) if isinstance(facts, dict) else {}
        if isinstance(provenance, dict):
            fields.update(name for name in provenance if isinstance(name, str))

    def observed_value(row, field):
        facts = row.get("facts", {})
        gains = facts.get("training_gains", {}) if isinstance(facts, dict) else {}
        value = gains.get(field) if isinstance(gains, dict) else None
        if type(value) is int and value >= 0:
            return value
        provenance = facts.get("training_gain_crop_provenance", {}) if isinstance(facts, dict) else {}
        detail = provenance.get(field) if isinstance(provenance, dict) else None
        value = detail.get("canonical_amount") if isinstance(detail, dict) else None
        return value if type(value) is int and value >= 0 else None

    candidates = [
        field for field in sorted(fields)
        if all(observed_value(row, field) == value for row, value, _shape in rows)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _canonical_source_crop_families(row, value, field):
    """Return independently canonical crop families for one source reading.

    Family labels are untrusted copied metadata.  Count a family only when the
    region name, amount, eligibility, role, and finite crop geometry all agree
    with the selected candidate set.  A same-frame family agreement also needs
    physically different boxes; a duplicated box under two labels is one crop.
    """

    if _canonical_source_provenance(row, value, field) is None:
        return frozenset()
    facts = row.get("facts", {})
    provenance = facts.get("training_gain_crop_provenance", {}) if isinstance(facts, dict) else {}
    detail = provenance.get(field) if isinstance(provenance, dict) else None
    candidates = detail.get("candidates", []) if isinstance(detail, dict) else []
    families = set()
    boxes_by_family = {}
    for candidate in candidates:
        validated = _validated_source_crop_candidate(candidate, value, field)
        if validated is None:
            continue
        family, box = validated
        boxes_by_family.setdefault(family, set()).add(box)

    # A same-frame proof must contain independent physical crop boxes.  The
    # normal wide crop overlaps the tight crop, so reject exact duplicates
    # while retaining legitimate nested/overlapping crop geometry.
    for family, boxes in boxes_by_family.items():
        if any(
            box == other_box
            for other_family, other_boxes in boxes_by_family.items()
            if other_family != family
            for box in boxes
            for other_box in other_boxes
        ):
            return frozenset()
    families.update(boxes_by_family)
    return frozenset(families)


def _strict_decimal_prefix(full_value, component_value):
    """Return whether the component is a visible leading prefix of the full."""

    full_text = str(full_value)
    component_text = str(component_value)
    return (
        len(full_text) > len(component_text)
        and full_text.startswith(component_text)
    )


def resolve_source_temporal_phase(observations, field):
    """Resolve a source-proven complete badge before a component phase.

    Some reviewed captures expose the complete and component badges with the
    same visible field set.  The generic shape-superset rule intentionally
    keeps that pattern ambiguous.  A narrower source-aware rule is allowed
    only when the crop resolver retained a canonical source proof, and the
    shorter value is a strict decimal prefix of the longer value.  The prefix
    identifies a clipped digit phase; timing then establishes which value is
    complete.  A single unproven recovery outlier may be retained as a source
    diagnostic when both members of the pair have canonical crop proof.  It
    cannot participate in selection.  Balance or arbitrary numeric magnitude
    never selects the winner.  This leaves unproven equal-shape inputs and
    reversed or competing phases unresolved.
    """

    rows = _deduplicate_observations(observations)
    if rows is None:
        return None
    if _unique_physical_source_rows(rows) is None:
        return None
    by_value = {}
    for row, value, shape in rows:
        by_value.setdefault(value, []).append((row, shape))
    ignored_items = []
    ignored_source_refinement_values = set()
    if len(by_value) == 2:
        ordered = sorted(
            ((value, items) for value, items in by_value.items()),
            key=lambda item: min(_row_time(row) for row, _ in item[1]),
        )
        full_value, full_items = ordered[0]
        component_value, component_items = ordered[1]
    else:
        # A recovery reader can leave one weak, contradictory value in the
        # same bounded result window.  Find the phase pair from source proofs
        # first; never choose it by amount or by a checkpoint.  A bounded
        # training-gain reread carries an explicit owner/evidence tuple even
        # when it has no native crop provenance, so it is admitted alongside
        # canonical crop proof.  The selected full value must be the one
        # whose first source observation precedes the component phase.  This
        # chronology is what distinguishes T028's later expanded-crop
        # alternative from its earlier complete badge.
        source_values = {}
        for value, items in by_value.items():
            proven = [
                (row, shape)
                for row, shape in items
                if _source_bound_proof(row, value, field) is not None
            ]
            if proven:
                source_values[value] = proven
        pairs = []
        for full_candidate in source_values:
            for component_candidate in source_values:
                if full_candidate == component_candidate:
                    continue
                if not (
                    len(str(full_candidate)) > len(str(component_candidate))
                    and str(full_candidate).startswith(str(component_candidate))
                ):
                    continue
                full_times = [
                    _row_time(row) for row, _shape in by_value[full_candidate]
                ]
                component_times = [
                    _row_time(row)
                    for row, _shape in by_value[component_candidate]
                ]
                # A full badge seen only after the component began is not an
                # opening full phase.  Keeping this chronology in pair
                # construction prevents two strict-prefix candidates from
                # becoming a magnitude/confidence tie-break.
                if not full_times or not component_times or min(full_times) >= min(component_times):
                    continue
                pairs.append((full_candidate, component_candidate))
        if len(pairs) != 1:
            return None
        full_value, component_value = pairs[0]
        full_items = by_value[full_value]
        component_items = by_value[component_value]
        recovery_owners = {
            proof.get("owner_id")
            for value in (full_value, component_value)
            for row, _shape in by_value[value]
            for proof in [_source_bound_proof(row, value, field)]
            if proof is not None and proof.get("kind") == "bounded_training_gain_recovery"
        }
        if len(recovery_owners) > 1:
            return None
        for value, items in by_value.items():
            if value in (full_value, component_value):
                continue
            bound_items = source_values.get(value, [])
            if bound_items:
                # One complete expanded/refined crop can be a transient
                # animation alternative when it occurs after the selected
                # full phase has already appeared, after the component phase
                # has started, and before the selected full suffix ends.  It
                # remains in the returned diagnostic evidence.  A repeated
                # source crop, a recovery row from another owner, or an
                # out-of-order value remains a real contradiction.
                outlier_row, _outlier_shape = items[0] if len(items) == 1 else (None, None)
                outlier_time = _row_time(outlier_row) if outlier_row is not None else None
                refined = False
                if (
                    len(items) == 1
                    and len(bound_items) == 1
                    and outlier_time is not None
                    and min(_row_time(row) for row, _shape in component_items) <= outlier_time <= max(_row_time(row) for row, _shape in full_items)
                    and _strict_decimal_prefix(value, component_value)
                ):
                    outlier_provenance = _canonical_source_provenance(
                        outlier_row, value, field
                    )
                    outlier_candidate = (
                        outlier_provenance.get("canonical_candidate", {})
                        if isinstance(outlier_provenance, dict)
                        else {}
                    )
                    refined = bool(
                        outlier_candidate.get("source_refinement_schema")
                        and outlier_candidate.get("source_refinement_role") in {"inner", "outer"}
                        and outlier_candidate.get("source_pixel_verified") is True
                    )
                if not refined:
                    return None
                ignored_source_refinement_values.add(value)
            elif len(items) != 1:
                return None
            ignored_items.extend((row, value, shape) for row, shape in items)
        if len(ignored_items) > 1:
            # One unproven recovery row was historically retained as a
            # diagnostic.  Source-bound refinement alternatives follow the
            # same singleton rule above; two ignored values would make the
            # source phase underdetermined.
            if len(ignored_items) != 1:
                return None
    # The smaller observation must be a clipped decimal prefix of the
    # complete amount.  This is a digit/phase cue from the badge itself; an
    # arbitrary larger number, including an equal-shape alternative, remains
    # ambiguous.
    if not (
        len(str(full_value)) > len(str(component_value))
        and str(full_value).startswith(str(component_value))
    ):
        return None
    full_times = [_row_time(row) for row, _ in full_items]
    component_times = [_row_time(row) for row, _ in component_items]
    # A complete badge can reappear after the component animation.  The
    # source phase is still oriented by its first complete observation; a
    # component-first sequence remains unresolved.
    if min(full_times) >= min(component_times):
        return None

    # The complete candidate must be independently readable in its source
    # crop.  One high-confidence complete frame is acceptable only when the
    # later component phase supplies at least two distinct source frames; the
    # multi-crop provenance remains the source proof for that frame.
    source_full = [
        (row, shape)
        for row, shape in full_items
        if _source_bound_proof(row, full_value, field) is not None
    ]
    source_components = [
        (row, shape)
        for row, shape in component_items
        if _source_bound_proof(row, component_value, field) is not None
    ]
    if not source_full or len(full_items) < 2 and len(component_items) < 2:
        return None
    if len(component_items) < 1:
        return None
    if len(by_value) > 2 and (
        len(source_full) != len(full_items)
        or len(source_components) != len(component_items)
    ):
        return None

    # A source phase must be a bounded animation rather than two unrelated
    # source events accidentally grouped together.  This also keeps the
    # chronology rule conservative when a capture has a long gap.
    all_times = full_times + component_times + [
        _row_time(row) for row, _value, _shape in ignored_items
    ]
    if max(all_times) - min(all_times) > 500:
        return None

    full_shape = frozenset(
        field_name
        for row, shape in full_items
        for field_name in shape
    )
    if not full_shape:
        full_shape = frozenset({field})
    component_shapes = sorted(
        {tuple(sorted(shape)) for _, shape in component_items}, key=str
    )
    full_proof = [
        _source_phase_proof(row, full_value, shape, field)
        for row, shape in full_items
    ]
    component_proof = [
        _source_phase_proof(row, component_value, shape, field)
        for row, shape in component_items
    ]
    return dict(
        accepted_amount=full_value,
        observed_amounts=sorted(by_value),
        basis="source_temporal_full_before_component_phase",
        full_shape=sorted(full_shape),
        component_shapes=[list(shape) for shape in component_shapes],
        full_observations=full_proof,
        component_observations=component_proof,
        full_last_seen_ms=max(full_times),
        component_first_seen_ms=min(component_times),
        phase_order="full_before_component",
        source_field=field,
        source_full_observation_count=len(source_full),
        source_phase_rule=(
            "canonical_source_pair_with_isolated_late_refinement_diagnostic"
            if ignored_source_refinement_values
            else "canonical_source_pair_with_single_unproven_outlier_diagnostic"
            if ignored_items
            else "canonical_source_crop_then_later_component_only"
        ),
        ignored_unproven_observations=[
            _source_phase_proof(row, value, shape, field)
            for row, value, shape in ignored_items
        ],
        ignored_unproven_values=sorted({value for _row, value, _shape in ignored_items}),
        ignored_source_refinement_values=sorted(ignored_source_refinement_values),
        ignored_source_refinement_observations=[
            _source_phase_proof(row, value, shape, field)
            for row, value, shape in ignored_items
            if value in ignored_source_refinement_values
        ],
    )


def resolve_full_component_phase(observations):
    """Resolve a repeated complete gain from source timing and badge shape.

    The primary proof is a strict shape superset whose complete phase ends
    before the component phase begins.  Some captures instead contain a
    transient prefix, then a stable complete suffix, with OCR exposing a
    changing companion set along the way.  That narrower fallback requires a
    digit prefix relationship, a repeated multi-shape complete suffix, and
    no competing value after the suffix starts.  It remains source-only: no
    checkpoint, balance, or claimed amount is accepted as an input.
    """

    rows = _deduplicate_observations(observations)
    if rows is None or _unique_physical_source_rows(rows) is None:
        return None
    by_value = {}
    for row, value, shape in rows:
        by_value.setdefault(value, []).append((row, shape))
    if len(by_value) < 2:
        return None

    candidates = []
    for value, items in by_value.items():
        if len(items) < 2:
            continue
        shapes = {shape for _, shape in items}
        if len(shapes) != 1:
            continue
        full_shape = next(iter(shapes))
        competitors = [
            other_items
            for other_value, other_items in by_value.items()
            if other_value != value
        ]
        if any(len(other_items) < 2 for other_items in competitors):
            continue
        if not competitors or not all(
            all(other_shape < full_shape for _, other_shape in other_items)
            for other_items in competitors
        ):
            continue
        full_times = [_row_time(row) for row, _ in items]
        component_times = [
            _row_time(row)
            for other_items in competitors
            for row, _ in other_items
        ]
        if max(full_times) >= min(component_times):
            continue
        candidates.append((value, items, competitors, full_shape))

    def proof(row, amount, shape):
        return dict(
            source_timestamp_ms=row["source_timestamp_ms"],
            evidence=row.get("evidence"),
            value=amount,
            shape=sorted(shape),
        )

    def phase_result(value, full_items, component_groups, full_shape, *, basis,
                     phase_order="full_before_component"):
        component_items = [
            (row, other_value, shape)
            for other_value, items in component_groups
            for row, shape in items
        ]
        component_shapes = sorted({
            tuple(sorted(shape)) for _, _, shape in component_items
        })
        return dict(
            accepted_amount=value,
            observed_amounts=sorted(by_value),
            basis=basis,
            full_shape=sorted(full_shape),
            component_shapes=[list(shape) for shape in component_shapes],
            full_observations=[proof(row, value, shape) for row, shape in full_items],
            component_observations=[
                proof(row, other_value, shape)
                for row, other_value, shape in component_items
            ],
            full_last_seen_ms=max(_row_time(row) for row, _ in full_items),
            component_first_seen_ms=min(
                _row_time(item[0]) for item in component_items
            ),
            phase_order=phase_order,
        )

    if len(candidates) == 1:
        value, full_items, competitors, full_shape = candidates[0]
        component_groups = [
            (other_value, other_items)
            for other_value, other_items in by_value.items()
            if other_value != value
        ]
        return phase_result(
            value,
            full_items,
            component_groups,
            full_shape,
            basis="repeated_full_gain_before_repeated_component_phase",
        )

    # A source capture can expose a short component badge first and the
    # complete result only afterwards.  The generic shape branch above cannot
    # safely select that pattern when the complete rows have changing
    # companion fields (for example, Wit appears once with Speed and once with
    # Skill Pts).  Permit it only with a source crop proof for the requested
    # field, a strict digit-prefix relationship, and a bounded component-first
    # chronology.  A complete value needs either repeated physical rows or
    # two canonical crop families on its one row.  This keeps a singleton OCR
    # reading, a balance-derived number, or an equal-shape disagreement out of
    # the result resolver.
    if len(by_value) == 2:
        source_field = _observation_field(rows)
        if source_field is not None:
            prefix_pairs = [
                (full_value, component_value)
                for full_value in by_value
                for component_value in by_value
                if full_value != component_value
                and _strict_decimal_prefix(full_value, component_value)
            ]
            if len(prefix_pairs) == 1:
                full_value, component_value = prefix_pairs[0]
                full_items = by_value[full_value]
                component_items = by_value[component_value]
                component_times = [_row_time(row) for row, _ in component_items]
                full_times = [_row_time(row) for row, _ in full_items]
                if (
                    component_times
                    and full_times
                    and max(component_times) < min(full_times)
                    and max(full_times + component_times)
                    - min(full_times + component_times)
                    <= 500
                ):
                    component_shapes = {shape for _, shape in component_items}
                    full_shapes = {shape for _, shape in full_items}
                    # A shape change is required as an independent phase cue.
                    # Equal-shape alternatives remain ambiguous even when the
                    # later value has a larger number of digits.
                    shape_changed = not any(
                        component_shape == full_shape
                        for component_shape in component_shapes
                        for full_shape in full_shapes
                    )
                    source_components = [
                        (row, shape)
                        for row, shape in component_items
                        if _canonical_source_provenance(
                            row, component_value, source_field
                        ) is not None
                    ]
                    source_full = [
                        (row, shape)
                        for row, shape in full_items
                        if _canonical_source_provenance(
                            row, full_value, source_field
                        ) is not None
                    ]
                    if (
                        shape_changed
                        and len(source_components) == len(component_items)
                        and len(source_full) == len(full_items)
                        and _unique_physical_source_rows(rows) is not None
                    ):
                        if len(full_items) >= 2:
                            full_support = "repeated_source_frames"
                            full_supported = True
                        else:
                            families = _canonical_source_crop_families(
                                full_items[0][0], full_value, source_field
                            )
                            full_support = "same_frame_source_crop_family_agreement"
                            full_supported = len(families) >= 2
                        if full_supported:
                            full_span = max(full_times) - min(full_times)
                            if len(full_items) == 1 or full_span >= 30:
                                full_shape = frozenset(
                                    field_name
                                    for _row, shape in full_items
                                    for field_name in shape
                                )
                                return dict(
                                    accepted_amount=full_value,
                                    observed_amounts=sorted(by_value),
                                    basis=(
                                        "source_component_prefix_before_stable_full_suffix"
                                    ),
                                    full_shape=sorted(full_shape),
                                    full_shapes=[
                                        list(sorted(shape))
                                        for shape in sorted(full_shapes, key=sorted)
                                    ],
                                    component_shapes=[
                                        list(sorted(shape))
                                        for shape in sorted(component_shapes, key=sorted)
                                    ],
                                    full_observations=[
                                        _source_phase_proof(
                                            row, full_value, shape, source_field
                                        )
                                        for row, shape in full_items
                                    ],
                                    component_observations=[
                                        _source_phase_proof(
                                            row, component_value, shape, source_field
                                        )
                                        for row, shape in component_items
                                    ],
                                    full_last_seen_ms=max(full_times),
                                    component_first_seen_ms=min(component_times),
                                    phase_order=(
                                        "component_prefix_before_stable_full_suffix"
                                    ),
                                    source_field=source_field,
                                    source_full_observation_count=len(source_full),
                                    source_component_observation_count=len(
                                        source_components
                                    ),
                                    source_full_proof=full_support,
                                    source_phase_rule=(
                                        "canonical_component_prefix_then_source_full"
                                    ),
                                )

    # Recovery for the source pattern present in several recordings: a
    # one-digit component is visible briefly, followed by a stable complete
    # amount.  The strict branch above intentionally remains unchanged for
    # fixed-shape proofs and the fallback is kept narrow to avoid turning a
    # merely larger or later OCR reading into a gain.
    if len(by_value) != 2:
        return None
    value_lengths = {value: len(str(value)) for value in by_value}
    value = max(by_value, key=lambda candidate: (value_lengths[candidate], candidate))
    component_value = next(candidate for candidate in by_value if candidate != value)
    if (
        value_lengths[value] <= value_lengths[component_value]
        or not str(value).startswith(str(component_value))
    ):
        return None
    full_items = by_value[value]
    component_items = by_value[component_value]
    if len(full_items) < 3 or len(component_items) < 1:
        return None
    full_shapes = {shape for _, shape in full_items}
    # A changing complete shape is the source signal that distinguishes this
    # transient-prefix pattern from the frozen equal-shape/reversed cases.
    if len(full_shapes) < 2:
        return None
    last_component_ms = max(_row_time(row) for row, _ in component_items)
    suffix = [
        item for item in full_items
        if _row_time(item[0]) > last_component_ms
    ]
    if len(suffix) < 2:
        return None
    suffix_times = [_row_time(row) for row, _ in suffix]
    if suffix_times[-1] - suffix_times[0] < 30:
        return None
    # The source has to show a complete reading before the final suffix or a
    # brief prefix boundary.  This rejects a component-only phase followed by
    # a larger amount when the complete reading never has shape variation.
    first_full_ms = min(_row_time(row) for row, _ in full_items)
    if first_full_ms >= min(_row_time(row) for row, _ in component_items):
        if suffix_times[-1] - min(
            _row_time(row) for row, _ in component_items + suffix
        ) > 500:
            return None
    component_groups = [(component_value, component_items)]
    return phase_result(
        value,
        full_items,
        component_groups,
        suffix[-1][1],
        basis="repeated_full_gain_prefix_phase_with_stable_suffix",
        phase_order="component_prefix_before_stable_full_suffix",
    )


def stable_trailing_result_suffix(rows, field, *, minimum_observations=3,
                                  minimum_span_ms=60):
    """Select the last stable result total without an expected amount.

    The suffix is chosen from the chronologically last contiguous integer
    ``result_values[field]`` observations.  A missing result value before the
    suffix is a phase boundary; a missing value after a collected suffix is
    also a boundary.  Distinct timestamps and evidence identities are required
    so duplicate rows cannot masquerade as a multi-frame proof.
    """

    ordered = sorted(
        (
            row for row in rows
            if type(row.get("source_timestamp_ms")) is int
            and row.get("source_timestamp_ms") >= 0
        ),
        key=lambda row: row["source_timestamp_ms"],
    )
    if not ordered:
        return None

    suffix = []
    expected = None
    for row in reversed(ordered):
        value = row.get("facts", {}).get("result_values", {}).get(field)
        if type(value) is not int or value < 0:
            if suffix:
                break
            continue
        if expected is None:
            expected = value
        elif value != expected:
            break
        suffix.append((row, value))
    suffix.reverse()
    if len(suffix) < minimum_observations:
        return None
    timestamps = [row["source_timestamp_ms"] for row, _ in suffix]
    evidence = [_evidence_identity(row.get("evidence")) for row, _ in suffix]
    if (
        len(set(timestamps)) < minimum_observations
        or any(identity is None for identity in evidence)
        or len(set(evidence)) < minimum_observations
        or timestamps != sorted(timestamps)
        or timestamps[-1] - timestamps[0] < minimum_span_ms
    ):
        return None
    return dict(
        value=expected,
        observations=[
            dict(source_timestamp_ms=row["source_timestamp_ms"],
                 evidence=row.get("evidence"), value=value)
            for row, value in suffix
        ],
        first_seen_ms=timestamps[0],
        last_seen_ms=timestamps[-1],
        observation_count=len(suffix),
        distinct_timestamp_count=len(set(timestamps)),
        distinct_evidence_count=len(set(evidence)),
        basis="stable_trailing_result_suffix",
    )


def stable_trailing_result_counter_suffix(rows, field, *, minimum_observations=3,
                                          minimum_span_ms=50,
                                          require_complete_after_partial=False):
    """Select a stable complete/partial result counter suffix.

    A row contributes a result value only when its complete result value is an
    integer or its partial counter has exactly one integer candidate.  The
    suffix is selected before any training-gain candidate is examined, so a
    claimed amount cannot choose an intermediate counter.
    """

    ordered = sorted(
        (
            row for row in rows
            if type(row.get("source_timestamp_ms")) is int
            and row.get("source_timestamp_ms") >= 0
        ),
        key=lambda row: row["source_timestamp_ms"],
    )
    observed = []
    for row in ordered:
        facts = row.get("facts", {})
        values = set()
        complete = facts.get("result_values", {}).get(field)
        if type(complete) is int and complete >= 0:
            values.add(complete)
        candidates = facts.get("result_numerator_candidates", {}).get(field, [])
        if isinstance(candidates, (list, tuple, set)):
            values.update(value for value in candidates if type(value) is int and value >= 0)
        observed.append((row, next(iter(values)) if len(values) == 1 else None,
                         len(values) == 1, type(complete) is int and complete >= 0))

    suffix = []
    expected = None
    for row, value, valid, complete in reversed(observed):
        if not valid:
            if suffix:
                break
            continue
        if expected is None:
            expected = value
        elif value != expected:
            break
        suffix.append((row, value, complete))
    suffix.reverse()
    if len(suffix) < minimum_observations:
        return None
    timestamps = [row["source_timestamp_ms"] for row, _, _ in suffix]
    evidence = [_evidence_identity(row.get("supplemental_evidence", row.get("evidence")))
                for row, _, _ in suffix]
    if (
        len(set(timestamps)) < minimum_observations
        or any(identity is None for identity in evidence)
        or len(set(evidence)) < minimum_observations
        or timestamps[-1] - timestamps[0] < minimum_span_ms
    ):
        return None
    if require_complete_after_partial:
        complete_times = [row["source_timestamp_ms"] for row, _, complete in suffix if complete]
        partial_times = [row["source_timestamp_ms"] for row, _, complete in suffix if not complete]
        if not complete_times or (partial_times and max(partial_times) >= min(complete_times)):
            return None
    return dict(
        value=expected,
        observations=[
            dict(source_timestamp_ms=row["source_timestamp_ms"],
                 evidence=row.get("supplemental_evidence", row.get("evidence")),
                 value=value, complete=complete)
            for row, value, complete in suffix
        ],
        first_seen_ms=timestamps[0],
        last_seen_ms=timestamps[-1],
        observation_count=len(suffix),
        distinct_timestamp_count=len(set(timestamps)),
        distinct_evidence_count=len(set(evidence)),
        basis="stable_trailing_result_counter_suffix",
    )


def direct_gain_proof(value, observations, *, basis="repeated_training_gain_badge"):
    """Normalize direct gain observations for event-level provenance."""

    return dict(
        value=value,
        basis=basis,
        observations=[
            dict(source_timestamp_ms=row.get("source_timestamp_ms"),
                 evidence=row.get("evidence"), value=observed)
            for row, observed in observations
        ],
        source_timestamps_ms=[row.get("source_timestamp_ms") for row, _ in observations],
        evidence=[row.get("evidence") for row, _ in observations],
        observation_count=len(observations),
    )
