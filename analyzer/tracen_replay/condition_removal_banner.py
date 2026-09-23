"""Read the source-backed ``CONDITION CURED!`` result banner.

The ordinary receipt parser requires a complete ``Recovered from NAME.`` line.
The game also renders a centered banner with the condition name, and that name
can remain readable when the receipt line is clipped by the typewriter
animation or cursor.  This module treats the banner as an observation whose
meaning comes from its geometry and heading, not from a condition catalog.

The reader only consumes existing OCR lines.  It does not run OCR, complete a
truncated name, or infer a condition from an infirmary action.  A caller should
merge its result with the same-frame receipt effects using
``merge_condition_removal_effects`` so one visual occurrence remains one
effect while both proofs stay available.  The event-level
``normalize_condition_removal_event`` pass handles later receipt frames only
when the event supplies bounded source timestamps and one canonical banner.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Iterable, Mapping

from .layout import place
from .source_clock import elapsed


HEADING_CONFIDENCE = 95.0
NAME_CONFIDENCE = 97.0

# OCR sidecar boxes use the reader's coordinates: the gameplay crop starts at
# x=148, and ``vision.NeuralReader`` adds that offset back to detected boxes.
# Y coordinates already use the crop's rows.  The regions are the PC pane's.
# The banner is centered in the lower half; the receipt block below it is
# deliberately outside the name band so a clipped ``Recovered from ...`` line
# cannot become the banner's identity.
HEADING_REGION = (300.0, 555.0, 900.0, 665.0)
NAME_REGION = (300.0, 635.0, 900.0, 745.0)
RECEIPT_REGION = (250.0, 760.0, 900.0, 1010.0)

# A banner and its fading receipt are normally sampled every 250 ms.  Keep
# the temporal bound explicit so a similarly named condition in a later
# recovery cannot be merged just because it shares a result event.
MAX_CONDITION_VARIANT_GAP_MS = 1000

_RECEIPT_RE = re.compile(r"Recovered from ([^.!?]+\S)[.!]$", re.IGNORECASE)
_TRAILING_FRAGMENT_RE = re.compile(r"(?:\.\.\.|…|[-/:;,])$")


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized or None


def _line_box(line: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    box = line.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        values = tuple(float(item) for item in box)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in values):
        return None
    left, top, right, bottom = values
    if not left < right or not top < bottom:
        return None
    return values


def _line_confidence(line: Mapping[str, Any]) -> float | None:
    try:
        value = float(line.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _inside(line: Any, region: tuple[float, float, float, float], minimum: float) -> bool:
    if not isinstance(line, Mapping) or not _text(line.get("text")):
        return False
    box = _line_box(line)
    confidence = _line_confidence(line)
    if box is None or confidence is None or confidence < minimum:
        return False
    left, top, right, bottom = box
    x1, y1, x2, y2 = region
    # Require the complete OCR box to fit.  A center-only check can accept a
    # line whose omitted suffix or prefix crosses a banner/crop boundary.
    return x1 <= left and right <= x2 and y1 <= top and bottom <= y2


def _heading_text(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    # The OCR source can omit the space in this all-caps display heading.  The
    # words themselves and the optional terminal exclamation mark remain fixed
    # UI grammar; arbitrary nearby narrative never matches.
    compact = re.sub(r"\s+", "", text).casefold()
    compact = compact.rstrip("!")
    return "condition cured" if compact == "conditioncured" else None


def _valid_condition_name(value: Any) -> str | None:
    # A name that has simply lost an invisible suffix is indistinguishable
    # from a legitimate short condition without a catalog or another frame.
    # Reject only visible truncation markers and receipt/narrative prefixes;
    # the full-box geometry check below handles spatial clipping.
    text = _text(value)
    if not text or len(text) < 2 or not any(character.isalpha() for character in text):
        return None
    if _TRAILING_FRAGMENT_RE.search(text):
        return None
    lowered = text.casefold()
    if lowered.startswith("recovered from") or lowered.startswith("condition cured"):
        return None
    if any(character in text for character in "?!"):
        return None
    return text


def _name_aligned(heading: Mapping[str, Any], name: Mapping[str, Any]) -> bool:
    heading_box = _line_box(heading)
    name_box = _line_box(name)
    if heading_box is None or name_box is None:
        return False
    h_left, h_top, h_right, h_bottom = heading_box
    n_left, n_top, n_right, n_bottom = name_box
    heading_center = (h_left + h_right) / 2
    name_center = (n_left + n_right) / 2
    # The condition title and name share the banner's centered column.  Keep
    # the horizontal tolerance generous for short names while requiring the
    # name to be below the heading and close enough to be one rendered card.
    return (abs(heading_center - name_center) <= 180
            and n_top >= h_bottom - 8
            and n_top <= h_bottom + 70
            and n_bottom > n_top)


def _unique_lines(lines: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for line in lines:
        text = _text(line.get("text")) if isinstance(line, Mapping) else None
        box = _line_box(line) if isinstance(line, Mapping) else None
        if text is None or box is None:
            continue
        key = (text.casefold(), tuple(round(value, 2) for value in box))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(line))
    return result


def _complete_receipt_proof(
    lines: Iterable[Mapping[str, Any]],
    name: str,
) -> list[dict[str, Any]]:
    proofs = []
    region = place(RECEIPT_REGION, "mc", "sc")
    for line in lines:
        if not _inside(line, region, NAME_CONFIDENCE):
            continue
        text = _text(line.get("text"))
        match = _RECEIPT_RE.fullmatch(text or "")
        if (match and _text(match.group(1))
                and _text(match.group(1)).casefold() == name.casefold()):
            proofs.append(deepcopy(dict(line)))
    return proofs


def read_condition_cured_banner(
    lines: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    source_timestamp_ms: int | None = None,
    evidence: str | None = None,
) -> dict[str, Any] | None:
    """Return one condition-removal effect from a verified banner, if present.

    ``lines`` may be the raw line list or a raw sidecar mapping containing a
    ``lines`` list.  A mapping also supplies the source timestamp and evidence
    when the keyword arguments are omitted.  The source proof contains the
    heading and condition-name lines, plus a complete same-frame receipt when
    one is visible.
    """

    raw = lines if isinstance(lines, Mapping) else None
    if raw is not None:
        lines = raw.get("lines", [])
        if source_timestamp_ms is None:
            source_timestamp_ms = raw.get("source_timestamp_ms")
        if evidence is None:
            evidence = raw.get("evidence")
    if not isinstance(lines, Iterable) or isinstance(lines, (str, bytes)):
        return None
    normalized = _unique_lines(line for line in lines if isinstance(line, Mapping))
    # The banner is centred; the bands cover the safe-area and screen centres.
    headings = [line for line in normalized
                if _inside(line, place(HEADING_REGION, "mc", "sc"), HEADING_CONFIDENCE)
                and _heading_text(line.get("text")) is not None]
    if len(headings) != 1:
        return None
    names = [line for line in normalized
             if _inside(line, place(NAME_REGION, "mc", "sc"), NAME_CONFIDENCE)
             and _valid_condition_name(line.get("text")) is not None]
    if len(names) != 1 or not _name_aligned(headings[0], names[0]):
        return None
    name = _valid_condition_name(names[0].get("text"))
    if name is None:
        return None
    proof = {
        "heading": deepcopy(headings[0]),
        "condition_name": deepcopy(names[0]),
    }
    receipts = _complete_receipt_proof(normalized, name)
    if receipts:
        proof["complete_receipt"] = receipts
    result = {
        "kind": "condition_removed",
        "name": name,
        "raw_text": f"{_text(headings[0].get('text'))} {name}",
        "mechanical_effect": None,
        "confidence": round(min(_line_confidence(headings[0]),
                                _line_confidence(names[0])), 4),
        "observation_basis": "visible_condition_cured_banner",
        "source_proof": proof,
        "inferred_numeric_effects": False,
    }
    if type(source_timestamp_ms) is int and source_timestamp_ms >= 0:
        result["source_timestamp_ms"] = source_timestamp_ms
    if isinstance(evidence, str) and evidence.strip():
        result["evidence"] = evidence.strip()
    return result


def _same_name(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_name, right_name = _text(left.get("name")), _text(right.get("name"))
    return bool(left_name and right_name and left_name.casefold() == right_name.casefold())


def _name_tokens(value: Any) -> list[str]:
    text = _text(value)
    return re.findall(r"[a-z0-9]+", text.casefold()) if text else []


def _name_variant(candidate: Any, canonical: Any) -> bool:
    """Return whether a receipt name is a conservative OCR variant.

    This deliberately handles only multi-word names whose first word is
    stable and whose final word contains only missing banner letters.
    It does not use a condition catalog and therefore
    cannot turn an arbitrary name into a known condition.  Requiring a banner
    elsewhere in the same bounded occurrence is what makes this useful for a
    fading receipt without treating two ordinary receipts as one.
    """

    candidate_tokens = _name_tokens(candidate)
    canonical_tokens = _name_tokens(canonical)
    if (len(candidate_tokens) < 2 or len(candidate_tokens) != len(canonical_tokens)
            or candidate_tokens[:-1] != canonical_tokens[:-1]):
        return False
    candidate_tail, canonical_tail = candidate_tokens[-1], canonical_tokens[-1]
    if not candidate_tail or len(canonical_tail) < 2:
        return False
    # A clipped terminal word is the common receipt failure (``Night O``).
    if (canonical_tail.startswith(candidate_tail)
            and len(candidate_tail) >= max(1, len(canonical_tail) - 2)):
        return True
    # Internal missing letters also occur (``Ol`` from ``Owl``). A substituted
    # or inserted letter could instead name a different condition; spelling
    # similarity alone does not justify discarding that observation.
    if not 1 <= len(canonical_tail) - len(candidate_tail) <= 2:
        return False
    remaining = iter(canonical_tail)
    return all(any(letter == candidate for letter in remaining)
               for candidate in candidate_tail)


def _is_banner_effect(effect: Mapping[str, Any]) -> bool:
    if not isinstance(effect, Mapping) or effect.get("kind") != "condition_removed":
        return False
    basis = effect.get("observation_basis")
    if isinstance(basis, str) and "condition_cured_banner" in basis:
        return True
    proof = effect.get("source_proof")
    return (isinstance(proof, Mapping)
            and isinstance(proof.get("heading"), Mapping)
            and isinstance(proof.get("condition_name"), Mapping))


def _is_complete_receipt_effect(effect: Mapping[str, Any]) -> bool:
    raw_text = _text(effect.get("raw_text")) if isinstance(effect, Mapping) else None
    return bool(raw_text and _RECEIPT_RE.fullmatch(raw_text))


def _variant_record(primary: Mapping[str, Any], variant: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "canonical_name": _text(primary.get("name")),
        "observed_name": _text(variant.get("name")),
        "raw_text": variant.get("raw_text"),
        "confidence": variant.get("confidence"),
        "evidence": variant.get("evidence"),
        "source_timestamp_ms": variant.get("source_timestamp_ms"),
        "basis": "bounded_banner_receipt_ocr_variant",
    }


def _effect_key(effect: Mapping[str, Any]) -> str:
    return "|".join(str(effect.get(key) or "")
                    for key in ("kind", "field", "name"))


def _effect_evidence(
    event: Mapping[str, Any],
    effect: Mapping[str, Any],
) -> list[str]:
    evidence = []
    field_evidence = event.get("field_evidence")
    if isinstance(field_evidence, Mapping):
        values = field_evidence.get(_effect_key(effect), [])
        if isinstance(values, str):
            values = [values]
        if isinstance(values, Iterable):
            evidence.extend(value for value in values if isinstance(value, str) and value)
    value = effect.get("evidence")
    if isinstance(value, str) and value:
        evidence.append(value)
    return list(dict.fromkeys(evidence))


def _source_namespace(evidence: Any) -> str | None:
    if not isinstance(evidence, str) or not evidence.strip():
        return None
    value = evidence.replace("\\", "/").strip().casefold()
    marker = "/gameplay/"
    if marker in value:
        return value.split(marker, 1)[0]
    return value.rsplit("/", 1)[0]


def _source_times(
    event: Mapping[str, Any],
    effect: Mapping[str, Any],
    rows_by_evidence: Mapping[str, Any] | None,
) -> tuple[list[int], list[str]]:
    evidence = _effect_evidence(event, effect)
    times = []
    value = effect.get("source_timestamp_ms")
    if type(value) is int and value >= 0:
        times.append(value)
    if isinstance(rows_by_evidence, Mapping):
        for path in evidence:
            row = rows_by_evidence.get(path)
            if not isinstance(row, Mapping):
                continue
            value = row.get("source_timestamp_ms")
            if type(value) is int and value >= 0:
                times.append(value)
    return sorted(set(times)), evidence


def _same_bounded_source_occurrence(
    event: Mapping[str, Any],
    primary: Mapping[str, Any],
    candidate: Mapping[str, Any],
    rows_by_evidence: Mapping[str, Any] | None,
    *,
    max_gap_ms: int = MAX_CONDITION_VARIANT_GAP_MS,
) -> tuple[bool, list[int], list[str]]:
    primary_times, primary_evidence = _source_times(event, primary, rows_by_evidence)
    candidate_times, candidate_evidence = _source_times(event, candidate, rows_by_evidence)
    if (not primary_times or not candidate_times
            or not primary_evidence or not candidate_evidence):
        # A name match without independent source timing is not enough to
        # rewrite an event.  This keeps the helper from becoming a catalog or
        # amount-driven deduplicator when called on partial projections.
        return False, candidate_times, candidate_evidence
    first = event.get("first_seen_ms")
    last = event.get("last_seen_ms")
    if type(first) is int and type(last) is int and (
            any(time < first or time > last for time in primary_times + candidate_times)):
        return False, candidate_times, candidate_evidence
    primary_namespaces = {_source_namespace(path) for path in primary_evidence}
    candidate_namespaces = {_source_namespace(path) for path in candidate_evidence}
    primary_namespaces.discard(None)
    candidate_namespaces.discard(None)
    if (primary_namespaces and candidate_namespaces
            and primary_namespaces.isdisjoint(candidate_namespaces)):
        return False, candidate_times, candidate_evidence
    gap = min(abs(elapsed(left, right)) for left in primary_times for right in candidate_times)
    return gap <= max_gap_ms, candidate_times, candidate_evidence


def _conflict_names(effect: Mapping[str, Any]) -> list[str]:
    names = effect.get("names")
    if not isinstance(names, Iterable) or isinstance(names, (str, bytes)):
        return []
    return list(dict.fromkeys(name for name in names if isinstance(name, str) and name.strip()))


def _conflict_banner(effect: Mapping[str, Any]) -> Mapping[str, Any] | None:
    proof = effect.get("source_proof")
    banner = proof.get("banner") if isinstance(proof, Mapping) else None
    return banner if isinstance(banner, Mapping) and banner.get("kind") == "condition_removed" else None


def _merge_proofs(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(left)) if isinstance(left, Mapping) else {}
    for key, value in (right.items() if isinstance(right, Mapping) else ()):
        if key not in merged:
            merged[key] = deepcopy(value)
            continue
        current = merged[key]
        if isinstance(current, list) and isinstance(value, list):
            merged[key] = current + [item for item in deepcopy(value) if item not in current]
        elif current != value:
            merged.setdefault("conflicts", {}).setdefault(key, [deepcopy(current)]).append(deepcopy(value))
    return merged


def merge_condition_removal_effects(
    effects: Iterable[Mapping[str, Any]],
    banner: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Deduplicate a banner with complete same-frame receipt effects.

    This function is intentionally scoped to one parsed source frame.  It
    preserves the original receipt effect and adds the banner proof to it; a
    banner with no matching receipt is appended as a separate source-backed
    effect.  Duplicate same-name receipt rows are coalesced while their raw
    effect objects are retained in ``same_frame_effects``.
    """

    result = [deepcopy(dict(effect)) for effect in effects
              if isinstance(effect, Mapping)]
    if not isinstance(banner, Mapping) or banner.get("kind") != "condition_removed":
        return result
    condition_rows = [row for row in result
                      if row.get("kind") == "condition_removed"]
    matches = [row for row in condition_rows if _same_name(row, banner)]
    variants = [row for row in condition_rows
                if not _same_name(row, banner)
                and _name_variant(row.get("name"), banner.get("name"))]
    conflicting = [row for row in condition_rows
                   if not _same_name(row, banner)
                   and row not in variants]
    if conflicting:
        # A receipt and a banner from the same frame are independent semantic
        # witnesses.  If they name different conditions, retaining both as
        # ordinary removals would create two unqualified transactions.  Keep a
        # structured conflict record with every raw proof and let accounting
        # abstain until the source is adjudicated.
        non_condition = [row for row in result if row.get("kind") != "condition_removed"]
        conflict = {
            "kind": "condition_removal_conflict",
            "names": list(dict.fromkeys(
                [_text(row.get("name")) for row in (conflicting + variants)
                 if _text(row.get("name"))]
                + ([_text(matches[0].get("name"))] if matches and _text(matches[0].get("name")) else [])
                + ([_text(banner.get("name"))] if _text(banner.get("name")) else [])
            )),
            "observation_basis": "conflicting_same_frame_condition_proofs",
            "source_proof": {
                "receipt_effects": deepcopy(conflicting + variants + matches),
                "banner": deepcopy(dict(banner)),
            },
            "inferred_numeric_effects": False,
        }
        for key in ("source_timestamp_ms", "evidence"):
            if key in banner:
                conflict[key] = deepcopy(banner[key])
        return non_condition + [conflict]
    if not matches:
        primary = deepcopy(dict(banner))
        if variants:
            primary["same_frame_effects"] = [deepcopy(variant) for variant in variants]
            primary["resolved_condition_name_variants"] = [
                _variant_record(primary, variant) for variant in variants
            ]
            primary["observation_basis"] = (
                "visible_condition_cured_banner_with_ocr_receipt_variants"
            )
            result = [row for row in result if row not in variants]
        result.append(primary)
        return result
    primary = matches[0]
    primary["source_proof"] = _merge_proofs(primary.get("source_proof", {}),
                                             banner.get("source_proof", {}))
    primary["condition_banner_proof"] = deepcopy(banner.get("source_proof", {}))
    primary["observation_basis"] = "receipt_and_visible_condition_cured_banner"
    for key in ("source_timestamp_ms", "evidence"):
        if key not in primary and key in banner:
            primary[key] = deepcopy(banner[key])
    for duplicate in matches[1:]:
        primary.setdefault("same_frame_effects", []).append(deepcopy(duplicate))
        result.remove(duplicate)
    if variants:
        primary.setdefault("same_frame_effects", []).extend(
            deepcopy(variant) for variant in variants
        )
        primary.setdefault("resolved_condition_name_variants", []).extend(
            _variant_record(primary, variant) for variant in variants
        )
        result = [row for row in result if row not in variants]
    return result


def normalize_condition_removal_event(
    event: Mapping[str, Any],
    rows_by_evidence: Mapping[str, Any] | None = None,
    *,
    max_gap_ms: int = MAX_CONDITION_VARIANT_GAP_MS,
) -> dict[str, Any]:
    """Collapse receipt-name variants under one source-backed banner.

    ``vision.parse`` can only merge observations that share one frame.  A
    fading receipt can expose ``Night O`` and ``Night Ol`` on later frames,
    however, after the banner has already established ``Night Owl``.  This
    event-level pass is deliberately separate from ordinary receipt parsing:
    it needs the event's evidence map and source timestamps before it can
    decide that those rows are one occurrence.

    Only a single canonical banner name can authorize this pass.  A candidate
    must be a conservative final-word OCR variant, have source timestamps in
    the event, and share the source namespace within the bounded interval.
    Conflicts naming a different condition remain untouched.  The returned
    event is a deep copy, and discarded observations remain attached to the
    canonical effect as audit metadata without remaining countable effects.
    """

    result = deepcopy(dict(event)) if isinstance(event, Mapping) else {}
    effects = [deepcopy(dict(effect)) for effect in result.get("effects", [])
               if isinstance(effect, Mapping)]
    banners = [effect for effect in effects if _is_banner_effect(effect)]
    banner_names = list(dict.fromkeys(_text(effect.get("name")) for effect in banners
                                     if _text(effect.get("name"))))
    if len(banner_names) != 1:
        result["effects"] = effects
        return result
    primary = next(effect for effect in effects
                   if _is_banner_effect(effect)
                   and _text(effect.get("name")) == banner_names[0])
    primary_name = banner_names[0]
    removed_ids: set[int] = set()
    removed_evidence: set[str] = set()
    removed_evidence_by_key: dict[str, set[str]] = {}
    variant_records = []
    conflict_records = []

    for effect in effects:
        if effect is primary or effect.get("kind") != "condition_removed":
            continue
        name = _text(effect.get("name"))
        if not name or not _name_variant(name, primary_name):
            continue
        if not _is_complete_receipt_effect(effect):
            continue
        if _is_banner_effect(effect):
            # Two independent banner names are never reconciled by a fuzzy
            # receipt rule.  The multiple-banner guard above normally handles
            # this branch, but keep it explicit for malformed projections.
            continue
        bounded, times, evidence = _same_bounded_source_occurrence(
            result, primary, effect, rows_by_evidence, max_gap_ms=max_gap_ms)
        if not bounded:
            continue
        record = _variant_record(primary, effect)
        record["evidence"] = evidence
        record["source_timestamps_ms"] = times
        variant_records.append(record)
        removed_ids.add(id(effect))
        removed_evidence.update(evidence)
        removed_evidence_by_key.setdefault(_effect_key(effect), set()).update(evidence)

    for effect in effects:
        if effect.get("kind") != "condition_removal_conflict":
            continue
        names = _conflict_names(effect)
        other_names = [name for name in names
                       if name.casefold() != primary_name.casefold()]
        if not other_names or not all(_name_variant(name, primary_name)
                                      for name in other_names):
            continue
        banner = _conflict_banner(effect)
        if banner is not None and _text(banner.get("name")) != primary_name:
            continue
        bounded, times, evidence = _same_bounded_source_occurrence(
            result, primary, effect, rows_by_evidence, max_gap_ms=max_gap_ms)
        if not bounded:
            continue
        conflict_records.append({
            "names": names,
            "evidence": evidence,
            "source_timestamps_ms": times,
            "basis": "bounded_banner_receipt_ocr_variant_conflict",
        })
        removed_ids.add(id(effect))
        removed_evidence.update(evidence)
        removed_evidence_by_key.setdefault(_effect_key(effect), set()).update(evidence)

    if not removed_ids:
        result["effects"] = effects
        return result

    removed_keys = {
        _effect_key(effect) for effect in effects if id(effect) in removed_ids
    }
    if variant_records:
        primary.setdefault("resolved_condition_name_variants", []).extend(variant_records)
    if conflict_records:
        primary.setdefault("resolved_condition_name_conflicts", []).extend(conflict_records)
    effects = [effect for effect in effects if id(effect) not in removed_ids]
    result["effects"] = effects

    field_evidence = result.get("field_evidence")
    if isinstance(field_evidence, Mapping):
        updated = {}
        for key, values in field_evidence.items():
            if isinstance(values, str):
                values = [values]
            blocked = removed_evidence_by_key.get(key, set())
            if (blocked and isinstance(values, Iterable)
                    and not isinstance(values, (str, bytes))):
                retained = [value for value in values
                            if not (isinstance(value, str) and value in removed_evidence)]
                if retained:
                    updated[key] = retained
            else:
                updated[key] = deepcopy(values)
        result["field_evidence"] = updated

    # A conflict row may also have been mirrored in ``conflicting_readings``.
    # Remove only entries whose field/evidence belong to a discarded variant;
    # unrelated condition conflicts remain visible to accounting.
    conflicts = result.get("conflicting_readings")
    if isinstance(conflicts, list):
        retained_conflicts = []
        for conflict in conflicts:
            if not isinstance(conflict, Mapping):
                retained_conflicts.append(conflict)
                continue
            field = conflict.get("field")
            evidence = conflict.get("evidence")
            if (field in removed_keys and isinstance(evidence, str)
                    and evidence in removed_evidence):
                continue
            retained_conflicts.append(conflict)
        result["conflicting_readings"] = retained_conflicts
    return result


__all__ = [
    "SCHEMA",
    "HEADING_REGION",
    "NAME_REGION",
    "MAX_CONDITION_VARIANT_GAP_MS",
    "read_condition_cured_banner",
    "read_banner",
    "merge_condition_removal_effects",
    "merge_effects",
    "normalize_condition_removal_event",
    "normalize_event",
]
