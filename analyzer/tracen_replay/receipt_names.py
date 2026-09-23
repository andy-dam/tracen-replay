"""Retain receipt identity uncertainty and resolve evidence-backed variants."""

from copy import deepcopy
import hashlib
import math
import re

from .gameplay import receipt_rows
from .layout import inside_pane, pane_box
from .source_clock import elapsed


_SOURCE_BOUND_IDENTITY_BASIS = "source_bound_clean_adjacent_receipt_line"
_FRIENDSHIP_EFFECT_KINDS = frozenset(("friendship_change", "friendship_status"))


def _identity_normal_text(value):
    return " ".join(str(value or "").split())


def _identity_digest(value):
    return hashlib.sha256(_identity_normal_text(value).encode("utf-8")).hexdigest()


def _identity_single_glyph_variant(first, second):
    """Recognize one OCR insertion, deletion, or substitution in a name.

    This is only used after a source-bound clean receipt proof has matched the
    same physical slot.  It is deliberately stricter than sentence-level
    similarity so two different recipients such as ``Alpha Support`` and
    ``Beta Support`` remain unresolved.
    """

    left = _identity_normal_text(first).casefold()
    right = _identity_normal_text(second).casefold()
    if not left or not right or left == right or abs(len(left) - len(right)) > 1:
        return False
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1] == 1


def _identity_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if any(type(item) not in (int, float) or isinstance(item, bool)
           or not math.isfinite(float(item)) for item in value):
        return False
    left, top, right, bottom = (float(item) for item in value)
    pane_left, _, pane_right, _ = pane_box()
    band_top, band_bottom = receipt_rows()
    return pane_left <= left < right <= pane_right and band_top <= top < bottom <= band_bottom


def _identity_boxes_intersect(first, second):
    """Return true only when two validated boxes share source pixels."""

    if not (_identity_box(first) and _identity_box(second)):
        return False
    first = tuple(float(item) for item in first)
    second = tuple(float(item) for item in second)
    return (
        max(first[0], second[0]) < min(first[2], second[2])
        and max(first[1], second[1]) < min(first[3], second[3])
    )


def _identity_source_overlay_boxes(row):
    """Collect immutable overlay boxes recorded for one source row."""

    if not isinstance(row, dict):
        return []
    boxes = []

    def collect(value):
        if not isinstance(value, list):
            return
        for box in value:
            if _identity_box(box):
                normalized = list(box)
                if normalized not in boxes:
                    boxes.append(normalized)

    for key in ("overlay_boxes", "animated_overlay_boxes"):
        collect(row.get(key))
    facts = row.get("facts")
    if not isinstance(facts, dict):
        return boxes
    occluded_lines = facts.get("occluded_receipt_lines")
    if isinstance(occluded_lines, list):
        for candidate in occluded_lines:
            if not isinstance(candidate, dict):
                continue
            for key in ("overlay_boxes", "animated_overlay_boxes"):
                collect(candidate.get(key))
    receipt_overlay = facts.get("receipt_overlay_evidence")
    if isinstance(receipt_overlay, dict):
        for key in ("overlay_boxes", "animated_overlay_boxes"):
            collect(receipt_overlay.get(key))
    return boxes


def _identity_overlay_alignment(row):
    """Return source-bound overlay alignments retained by receipt parsing."""

    if not isinstance(row, dict):
        return []
    values = []
    top_level = row.get("overlay_alignment")
    if isinstance(top_level, list):
        values.extend(top_level)
    facts = row.get("facts")
    receipt_overlay = facts.get("receipt_overlay_evidence") if isinstance(facts, dict) else None
    if isinstance(receipt_overlay, dict) and isinstance(receipt_overlay.get("alignments"), list):
        values.extend(receipt_overlay["alignments"])
    return [item for item in values if isinstance(item, dict)]


def _identity_source_binding_matches(row, binding):
    """Require proof provenance to remain equal to its clean source row."""

    if not isinstance(row, dict) or not isinstance(binding, dict) or not binding:
        return False
    required = {"source_sha256", "source_frame_sha256", "engine_fingerprint", "model_sha256"}
    if not required.issubset(binding):
        return False
    return all(row.get(key) == value for key, value in binding.items())


def _identity_anchor_overlay_intersection(proof, anchor_row, target_box):
    """Validate recorded anchor overlays when no alignment sidecar exists.

    The fallback is intentionally narrow.  It consumes only overlay boxes
    copied from the validated source trigger, requires exact membership in the
    anchor row's overlay evidence, and is used only when that row has no
    alignment records at all.  It does not estimate a recipient region or
    create one from sentence length.
    """

    if _identity_overlay_alignment(anchor_row):
        return False
    declared = proof.get("anchor_overlay_boxes") if isinstance(proof, dict) else None
    if not isinstance(declared, list) or not declared:
        return False
    available = _identity_source_overlay_boxes(anchor_row)
    if not available:
        return False
    for box in declared:
        if not _identity_box(box) or list(box) not in available:
            return False
    return any(
        _identity_boxes_intersect(box, target_box) for box in declared
    )


def _identity_alignment_name_region(row, target_box):
    """Validate an existing alignment and return its recipient region.

    The region comes from the same alignment helpers used by the source
    occlusion detector.  No horizontal position is estimated from sentence
    length or from a character-name catalog.
    """

    if not _identity_box(target_box):
        return []
    try:
        from .receipt_occlusion import (
            _friendship_name_glyph_spans,
            friendship_name_bounds,
        )
    except (ImportError, AttributeError):
        return []
    line_text = _identity_normal_text(
        next(
            (
                line.get("text")
                for line in _identity_ocr_lines(row)
                if isinstance(line, dict)
                and _identity_same_slot(line.get("box"), target_box)
            ),
            "",
        )
    )
    regions = []
    for alignment in _identity_overlay_alignment(row):
        alignment_box = alignment.get("line_box")
        if not _identity_same_slot(alignment_box, target_box):
            continue
        # ``annotate`` only treats an alignment as source geometry when its
        # OCR confidence meets the shared receipt proof threshold.  Identity
        # recovery must apply the same contract; a digest-shaped alignment
        # with confidence zero is not evidence for a recipient region.
        alignment_confidence = alignment.get("confidence")
        if (type(alignment_confidence) not in (int, float)
                or isinstance(alignment_confidence, bool)
                or not math.isfinite(float(alignment_confidence))
                or float(alignment_confidence) < 95):
            continue
        recognized = _identity_normal_text(alignment.get("recognized_text"))
        if not line_text or not recognized or recognized != line_text:
            continue
        try:
            name_bounds = friendship_name_bounds(
                alignment_box,
                alignment.get("words"),
                alignment.get("columns"),
                alignment.get("line_length"),
            )
            glyph_spans = _friendship_name_glyph_spans(alignment)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if not _identity_box(name_bounds) or not glyph_spans:
            continue
        if any(
            not isinstance(span, (list, tuple))
            or len(span) != 2
            or any(type(value) not in (int, float) or isinstance(value, bool)
                   or not math.isfinite(float(value)) for value in span)
            or float(span[0]) >= float(span[1])
            for span in glyph_spans
        ):
            continue
        regions.append((list(name_bounds), [list(span) for span in glyph_spans]))
    return regions


def _identity_recipient_obstruction(row, target_box):
    """Prove that a source overlay crossed the recipient glyph region."""

    overlays = _identity_source_overlay_boxes(row)
    if not overlays:
        return False
    for name_bounds, glyph_spans in _identity_alignment_name_region(row, target_box):
        if any(
            _identity_boxes_intersect(overlay, name_bounds)
            and any(
                max(float(span[0]), float(overlay[0]))
                < min(float(span[1]), float(overlay[2]))
                for span in glyph_spans
            )
            for overlay in overlays
        ):
            return True
    return False


def _identity_same_slot(first, second):
    if not (_identity_box(first) and _identity_box(second)):
        return False
    first = tuple(float(item) for item in first)
    second = tuple(float(item) for item in second)
    width = max(0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = width * height
    union = ((first[2] - first[0]) * (first[3] - first[1])
             + (second[2] - second[0]) * (second[3] - second[1])
             - intersection)
    return union > 0 and intersection / union >= 0.8 and abs(
        (first[1] + first[3] - second[1] - second[3]) / 2
    ) <= 3


def _identity_adjacent_slot(first, second):
    """Match the bounded receipt-row geometry used by recovery."""

    if not (_identity_box(first) and _identity_box(second)):
        return False
    if _identity_same_slot(first, second):
        return False
    first = tuple(float(item) for item in first)
    second = tuple(float(item) for item in second)
    upper, lower = sorted((first, second), key=lambda box: (box[1] + box[3]) / 2)
    upper_center = (upper[1] + upper[3]) / 2
    lower_center = (lower[1] + lower[3]) / 2
    return (
        0 < lower_center - upper_center <= 35
        and lower[1] - upper[3] <= 6
        and abs(upper[0] - lower[0]) <= 16
    )


def _identity_semantics(effect):
    return tuple(effect.get(key) for key in (
        "kind", "field", "amount", "direction", "value",
    ))


def _identity_field_key(effect):
    """Use the canonical transaction key for source evidence lookups."""

    return "|".join(str(effect.get(key) or "") for key in (
        "kind", "field", "name",
    ))


def _identity_exact_effect(first, second):
    if _identity_semantics(first) != _identity_semantics(second):
        return False
    first_name, second_name = first.get("name"), second.get("name")
    if first_name is None or second_name is None:
        return first_name is None and second_name is None
    return _identity_normal_text(first_name).casefold() == _identity_normal_text(second_name).casefold()


def _identity_contexts(value):
    if not isinstance(value, dict):
        return set()
    return {
        item.strip() for key in ("context_title", "context_title_candidate")
        for item in (value.get(key),)
        if isinstance(item, str) and item.strip()
    }


def _identity_ocr_lines(row):
    if not isinstance(row, dict):
        return []
    ocr = row.get("ocr")
    lines = ocr.get("neural") if isinstance(ocr, dict) else None
    return lines if isinstance(lines, list) else []


def _validated_source_identity_proof(effect, event, rows_by_evidence):
    """Validate one clear adjacent-line proof against the actual source row.

    The proof is produced by the bounded occluded-receipt reread.  Recheck its
    row, exact OCR text, parsed semantics, geometry, anchor overlay, owner,
    and recovery window here before it can resolve an existing spelling.  A
    metadata flag alone is never sufficient.
    """

    proof = effect.get("source_bound_identity_proof") if isinstance(effect, dict) else None
    if not isinstance(proof, dict) or proof.get("basis") != _SOURCE_BOUND_IDENTITY_BASIS:
        return None
    required = (
        "source_timestamp_ms", "evidence", "line_box", "source_line_text_sha256",
        "owner_ref", "owner_start_ms", "owner_end_ms", "owner_context_title",
        "anchor_source_timestamp_ms", "anchor_evidence", "anchor_line_box",
        "anchor_source_line_text_sha256", "anchor_window_start_ms",
        "anchor_window_end_ms", "confidence", "source_binding",
    )
    if any(key not in proof for key in required):
        return None
    source_timestamp = proof.get("source_timestamp_ms")
    anchor_timestamp = proof.get("anchor_source_timestamp_ms")
    owner_start, owner_end = proof.get("owner_start_ms"), proof.get("owner_end_ms")
    window_start, window_end = (
        proof.get("anchor_window_start_ms"), proof.get("anchor_window_end_ms")
    )
    if any(type(value) is not int or value < 0 for value in (
        source_timestamp, anchor_timestamp, owner_start, owner_end,
        window_start, window_end,
    )):
        return None
    if owner_end < owner_start or window_end <= window_start:
        return None
    if not window_start <= anchor_timestamp < window_end:
        return None
    if not window_start <= source_timestamp < window_end:
        return None
    if not owner_start <= anchor_timestamp <= owner_end:
        return None
    owner_ref = proof.get("owner_ref")
    if not isinstance(owner_ref, str) or not owner_ref.strip():
        return None
    context = proof.get("owner_context_title")
    if not isinstance(context, str) or not context.strip():
        return None
    evidence = proof.get("evidence")
    anchor_evidence = proof.get("anchor_evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        return None
    if not isinstance(anchor_evidence, str) or not anchor_evidence.strip():
        return None
    if not _identity_box(proof.get("line_box")) or not _identity_box(proof.get("anchor_line_box")):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(proof.get("source_line_text_sha256"))):
        # A digest-shaped value is not enough; the exact source line below
        # must produce it. This branch also rejects non-string values.
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(proof.get("anchor_source_line_text_sha256"))):
        return None
    confidence = proof.get("confidence")
    if (type(confidence) not in (int, float) or isinstance(confidence, bool)
            or not math.isfinite(float(confidence)) or float(confidence) < 95):
        return None
    event_bounds = (event.get("first_seen_ms"), event.get("last_seen_ms"))
    if (type(event_bounds[0]) is not int or type(event_bounds[1]) is not int
            or event_bounds[1] < event_bounds[0]
            or not event_bounds[0] <= anchor_timestamp <= event_bounds[1]
            or not event_bounds[0] <= source_timestamp <= event_bounds[1]):
        return None
    event_contexts = _identity_contexts(event)
    if event_contexts and context.strip() not in event_contexts:
        return None
    # Bind the proof to the source-owned recovery row that produced it.  The
    # owner reference is useful only when it agrees with the validated
    # recovery metadata; accepting a caller-supplied string here would let a
    # forged proof attach a clean line to an unrelated outcome event.
    row = rows_by_evidence.get(evidence) if isinstance(rows_by_evidence, dict) else None
    anchor_row = rows_by_evidence.get(anchor_evidence) if isinstance(rows_by_evidence, dict) else None
    if not isinstance(row, dict) or not isinstance(anchor_row, dict):
        return None
    if not _identity_source_binding_matches(row, proof.get("source_binding")):
        return None
    if row.get("source_timestamp_ms") != source_timestamp or row.get("evidence") != evidence:
        return None
    if anchor_row.get("source_timestamp_ms") != anchor_timestamp or anchor_row.get("evidence") != anchor_evidence:
        return None
    if row.get("screen") not in ("unknown", "event_outcome"):
        return None
    if anchor_row.get("screen") not in ("unknown", "event_outcome"):
        return None
    field_evidence = event.get("field_evidence")
    field_key = _identity_field_key(effect)
    if not isinstance(field_evidence, dict):
        return None
    field_paths = field_evidence.get(field_key)
    if (not isinstance(field_paths, list) or evidence not in field_paths
            or not any(
                isinstance(paths, list) and anchor_evidence in paths
                for paths in field_evidence.values()
            )):
        return None
    recovery = row.get("facts", {}).get("occluded_receipt_recovery") \
        if isinstance(row.get("facts"), dict) else None
    if not isinstance(recovery, dict):
        return None
    if (recovery.get("source_timestamp_ms") != source_timestamp
            or recovery.get("evidence") != evidence
            or recovery.get("requested_owner_ref") != owner_ref
            or recovery.get("owner_basis") != "outcome_event"):
        return None
    trigger_sources = recovery.get("trigger_sources")
    if (not isinstance(trigger_sources, list) or not any(
        isinstance(trigger, dict)
        and trigger.get("source_timestamp_ms") == anchor_timestamp
        and trigger.get("evidence") == anchor_evidence
        and trigger.get("line_box") == proof.get("anchor_line_box")
        and trigger.get("source_line_text_sha256")
            == proof.get("anchor_source_line_text_sha256")
        # The recovery row must retain the complete owner binding selected
        # from the validated outcome plan.  Matching only the requested owner
        # field would allow a caller to mutate that field and the proof
        # together while leaving no source-bound record of which outcome
        # actually owned the obstructed line.
        and trigger.get("owner_ref") == owner_ref
        and trigger.get("owner_start_ms") == owner_start
        and trigger.get("owner_end_ms") == owner_end
        and trigger.get("owner_context_title") == context
        for trigger in trigger_sources
    )):
        return None
    if _identity_contexts(row) and context.strip() not in _identity_contexts(row):
        return None
    if _identity_contexts(anchor_row) and context.strip() not in _identity_contexts(anchor_row):
        return None
    lines = []
    for line in _identity_ocr_lines(row):
        if not isinstance(line, dict) or line.get("box") != proof.get("line_box"):
            continue
        if _identity_normal_text(line.get("text")) != _identity_normal_text(effect.get("raw_text")):
            continue
        if line.get("overlay_occluded") is True:
            continue
        overlays = line.get("overlay_boxes")
        if isinstance(overlays, list) and overlays:
            return None
        line_confidence = line.get("confidence")
        if (type(line_confidence) not in (int, float) or isinstance(line_confidence, bool)
                or not math.isfinite(float(line_confidence)) or float(line_confidence) < 95):
            return None
        lines.append(line)
    if len(lines) != 1:
        return None
    line = lines[0]
    if _identity_digest(line.get("text")) != proof.get("source_line_text_sha256"):
        return None
    try:
        from .gameplay import effects_from_lines
        parsed = effects_from_lines([line])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if not any(_identity_exact_effect(effect, candidate) for candidate in parsed):
        return None
    if any(proof.get(key) != effect.get(key) for key in (
        "kind", "field", "amount", "direction", "value",
    )):
        return None
    if proof.get("owner_context_title", "").strip() != context.strip():
        return None

    anchor_lines = []
    anchor_facts = anchor_row.get("facts")
    if not isinstance(anchor_facts, dict):
        return None
    for line in anchor_facts.get("occluded_receipt_lines", []):
        if not isinstance(line, dict) or line.get("box") != proof.get("anchor_line_box"):
            continue
        if _identity_digest(line.get("text")) != proof.get("anchor_source_line_text_sha256"):
            continue
        overlays = line.get("overlay_boxes")
        if not isinstance(overlays, list) or not overlays:
            continue
        if any(_identity_boxes_intersect(box, proof.get("line_box")) for box in overlays):
            anchor_lines.append(line)
    if len(anchor_lines) != 1 or not _identity_adjacent_slot(
        proof.get("anchor_line_box"), proof.get("line_box")
    ):
        return None
    # The anchor's obstruction normally must cross the recipient region of the
    # displaced friendship line.  A small set of legacy source rows has the
    # validated overlay components but no alignment sidecar.  In that case
    # the source-bound recovery proof may use the exact copied overlay boxes
    # only after the resolver applies its one-glyph OCR-variant guard.  The
    # fallback never estimates a name region or treats a neighboring overlay
    # as identity evidence by itself.
    recipient_obstructed = _identity_recipient_obstruction(
        anchor_row, proof.get("line_box")
    )
    if not recipient_obstructed and not _identity_anchor_overlay_intersection(
        proof, anchor_row, proof.get("line_box")
    ):
        return None
    return dict(
        proof=proof,
        row=row,
        anchor_row=anchor_row,
        line=line,
        source_timestamp_ms=source_timestamp,
        anchor_timestamp_ms=anchor_timestamp,
        line_box=list(proof["line_box"]),
        owner_ref=owner_ref.strip(),
        proof_mode=(
            "source_bound_recipient_overlay"
            if recipient_obstructed
            else "source_bound_anchor_overlay_intersection"
        ),
    )


def _effect_source_observations(effect, event, rows_by_evidence):
    """Find exact OCR observations for same-occurrence geometry checks."""

    field = _identity_field_key(effect)
    evidence = event.get("field_evidence", {}).get(field, [])
    if not isinstance(evidence, list):
        return []
    texts = {
        _identity_normal_text(value) for value in (
            effect.get("raw_text"), effect.get("normalized_text"), effect.get("original_text")
        ) if isinstance(value, str) and _identity_normal_text(value)
    }
    result = []
    for path in dict.fromkeys(item for item in evidence if isinstance(item, str)):
        row = rows_by_evidence.get(path) if isinstance(rows_by_evidence, dict) else None
        if not isinstance(row, dict) or type(row.get("source_timestamp_ms")) is not int:
            continue
        matches = [
            line for line in _identity_ocr_lines(row)
            if isinstance(line, dict)
            and _identity_normal_text(line.get("text")) in texts
            and _identity_box(line.get("box"))
            and type(line.get("confidence")) in (int, float)
            and not isinstance(line.get("confidence"), bool)
            and math.isfinite(float(line.get("confidence")))
            and float(line.get("confidence")) >= 95
        ]
        if len(matches) == 1:
            result.append(dict(
                source_timestamp_ms=row["source_timestamp_ms"],
                evidence=path,
                line=matches[0],
                row=row,
            ))
    return result


def _identity_observation_has_degradation(observation):
    """Require source geometry proving why an identity may be displaced.

    A high-confidence OCR spelling remains authoritative unless the same
    source row contains an obstruction crossing that spelling's own receipt
    box.  Obstruction on a neighboring line is insufficient.
    """

    if not isinstance(observation, dict):
        return False
    row = observation.get("row")
    line = observation.get("line")
    if not isinstance(row, dict) or not isinstance(line, dict):
        return False
    line_box = line.get("box")
    if not _identity_box(line_box):
        return False
    return _identity_recipient_obstruction(row, line_box)


def _identity_observations_all_degraded(effect, event, rows_by_evidence, observations):
    """Return true only when every displaced source observation is degraded."""

    if not observations:
        return False
    return all(_identity_observation_has_degradation(observation)
               for observation in observations)


def _same_identity_occurrence(clean, other_observations):
    for observation in other_observations:
        if abs(elapsed(clean["source_timestamp_ms"], observation["source_timestamp_ms"])) > 250:
            continue
        if _identity_same_slot(clean["line_box"], observation["line"].get("box")):
            return True
    return False


def _source_bound_variant_observations(
    clean_effect, clean_record, other_effect, other_observations,
):
    """Treat one OCR glyph variant as degraded under a validated proof.

    Alignment-backed proofs already establish recipient obstruction directly.
    For legacy rows without alignment sidecars, the recovery proof still
    carries the exact source overlay boxes and clean source binding.  Permit
    that narrow fallback only when the competing name differs by one glyph and
    every competing observation is the same physical receipt slot.  Different
    names, rows, or source overlays remain unresolved.
    """

    if not isinstance(clean_effect, dict) or not isinstance(other_effect, dict):
        return False
    if not isinstance(clean_record, dict) or clean_record.get("proof_mode") != (
        "source_bound_anchor_overlay_intersection"
    ):
        return False
    if not _identity_single_glyph_variant(
        clean_effect.get("name"), other_effect.get("name")
    ):
        return False
    proof = clean_record.get("proof")
    anchor_row = clean_record.get("anchor_row")
    if not isinstance(proof, dict) or not isinstance(anchor_row, dict):
        return False
    if _identity_overlay_alignment(anchor_row):
        return False
    if not _identity_anchor_overlay_intersection(
        proof, anchor_row, clean_record.get("line_box")
    ):
        return False
    if not isinstance(other_observations, list) or not other_observations:
        return False
    clean_timestamp = clean_record.get("source_timestamp_ms")
    for observation in other_observations:
        if not isinstance(observation, dict):
            return False
        other_row = observation.get("row")
        other_line = observation.get("line")
        if not isinstance(other_row, dict) or not isinstance(other_line, dict):
            return False
        if type(clean_timestamp) is not int or type(
            other_row.get("source_timestamp_ms")
        ) is not int:
            return False
        if abs(elapsed(clean_timestamp, other_row["source_timestamp_ms"])) > 250:
            return False
        if not _identity_same_slot(
            clean_record.get("line_box"), other_line.get("box")
        ):
            return False
        if not _identity_single_glyph_variant(
            clean_effect.get("raw_text"), other_effect.get("raw_text")
        ):
            return False
    return True


def _resolve_source_bound_friendship_identities(event, rows_by_evidence):
    """Prefer one strictly proven clean name over an obstructed spelling.

    This resolution is limited to the same kind/amount/direction/value and a
    physically matching receipt occurrence.  A second clean name, an
    ownerless proof, or a source row outside the event remains unresolved for
    the existing conservative conflict handler.
    """

    if not isinstance(event, dict) or not isinstance(event.get("effects"), list):
        return
    effects = [
        effect for effect in event["effects"]
        if isinstance(effect, dict)
        and effect.get("kind") in _FRIENDSHIP_EFFECT_KINDS
        and isinstance(effect.get("name"), str)
        and effect.get("name").strip()
    ]
    validated = {
        id(effect): _validated_source_identity_proof(effect, event, rows_by_evidence)
        for effect in effects
    }
    removed = set()
    resolutions = []
    for clean in effects:
        clean_record = validated.get(id(clean))
        if clean_record is None or id(clean) in removed:
            continue
        clean_observation = dict(
            source_timestamp_ms=clean_record["source_timestamp_ms"],
            line_box=clean_record["line_box"],
        )
        related = []
        for other in effects:
            if other is clean or id(other) in removed:
                continue
            if other.get("name") == clean.get("name") or _identity_semantics(other) != _identity_semantics(clean):
                continue
            observations = _effect_source_observations(other, event, rows_by_evidence)
            if _same_identity_occurrence(clean_observation, observations):
                related.append((other, observations))
        if not related:
            continue
        # A source-observed clean spelling is a competing identity even when
        # it has no recovery proof.  Only an observation with positive
        # obstruction geometry may be displaced by the newly proven clean
        # adjacent line.  This prevents a neighboring overlay from silently
        # replacing an ordinary clean line in the same physical slot.
        competing_clean = [
            other for other, observations in related
            if validated.get(id(other)) is not None
            or not _identity_observations_all_degraded(
                other, event, rows_by_evidence, observations
            )
        ]
        if competing_clean and clean_record.get("proof_mode") == (
            "source_bound_anchor_overlay_intersection"
        ):
            observation_by_id = {
                id(other): observations for other, observations in related
            }
            competing_clean = [
                other for other in competing_clean
                if not _source_bound_variant_observations(
                    clean,
                    clean_record,
                    other,
                    observation_by_id.get(id(other), []),
                )
            ]
        if competing_clean:
            continue
        removed.update(id(other) for other, _ in related)
        resolutions.append(dict(
            kind=clean.get("kind"),
            field=f"{clean.get('kind')}||{clean.get('name')}",
            accepted_name=clean.get("name"),
            rejected_name_candidates=sorted({other.get("name") for other, _ in related}),
            evidence=[clean_record["proof"].get("evidence")],
            source_timestamp_ms=clean_record["source_timestamp_ms"],
            source_line_box=clean_record["line_box"],
            basis="source_bound_clean_same_occurrence",
        ))
    if removed:
        event["effects"] = [effect for effect in event["effects"] if id(effect) not in removed]
    if resolutions:
        event.setdefault("resolved_identity_readings", []).extend(resolutions)


def _dialogue_text_moved(first,second):
    """A shared line changing row disproves a stationary receipt-slot match.

    This only vetoes identity inference. It does not accept either recipient
    or promote an OCR reading, and it does not need a character-name catalog.
    """
    top,bottom=receipt_rows(780,960)
    def lines(row):
        found={}
        for line in row.get('ocr',{}).get('neural',[]):
            box=line.get('box',[])
            if len(box)!=4 or line.get('confidence',0)<95:continue
            if not top<=(box[1]+box[3])/2<=bottom:continue
            found.setdefault(line.get('text'),[]).append(box)
        return found
    a,b=lines(first),lines(second)
    for text in a.keys()&b.keys():
        if not text or len(a[text])!=1 or len(b[text])!=1:continue
        left,right=a[text][0],b[text][0]
        dy=(right[1]+right[3]-left[1]-left[3])/2
        if 8<=abs(dy)<=80 and abs(left[0]-right[0])<=5 and abs(left[2]-right[2])<=5:
            return True
    return False


def _same_line_slot(ba,bb):
    """Two line boxes in one receipt slot: nearly the same box, on the same row."""
    if len(ba)!=4 or len(bb)!=4:return False
    width=max(0,min(ba[2],bb[2])-max(ba[0],bb[0]))
    height=max(0,min(ba[3],bb[3])-max(ba[1],bb[1]))
    intersection=width*height
    union=(ba[2]-ba[0])*(ba[3]-ba[1])+(bb[2]-bb[0])*(bb[3]-bb[1])-intersection
    return union>0 and intersection/union>=.8 and abs((ba[1]+ba[3]-bb[1]-bb[3])/2)<=3


def flag_friendship_identity_conflicts(event,rows_by_evidence):
    """Abstain when adjacent views of one receipt slot disagree on a name.

    No spelling is selected. Simultaneous recipients and separate receipt
    positions remain distinct, even when their names and gains are similar.
    """
    _resolve_source_bound_friendship_identities(event, rows_by_evidence)
    effects=[e for e in event['effects'] if e['kind'] in ('friendship_change','friendship_status')]
    same_slot=_same_line_slot
    def occluded_bridge(ta,tb,pa,pb,ba,bb,effect):
        if abs(ta-tb)!=500:return False
        middle=(ta+tb)//2
        proofs={p for paths in event['field_evidence'].values() for p in paths}
        rows=[rows_by_evidence[p] for p in proofs if p in rows_by_evidence
              and rows_by_evidence[p]['source_timestamp_ms']==middle]
        if len(rows)!=1:return False
        row=rows[0]
        if _dialogue_text_moved(rows_by_evidence[pa],row) or _dialogue_text_moved(row,rows_by_evidence[pb]):return False
        from .gameplay import effects_from_lines
        for line in row.get('facts',{}).get('occluded_receipt_lines',[]):
            if line.get('recipient_name_occluded') is not True:continue
            if not same_slot(ba,line.get('box',[])) or not same_slot(bb,line.get('box',[])):continue
            # Read the retained pre-occlusion grammar only to identify this
            # unknown slot. Its recipient is never restored as an effect.
            parsed=effects_from_lines([line])
            if len(parsed)==1 and all(parsed[0].get(k)==effect.get(k) for k in ('kind','amount','value')):
                return row['evidence']
        return False
    def receipt_gap_bridge(ta,tb,pa,pb,ba,bb,effect):
        # Dense and base samples need not land at the same cadence. An
        # explicitly anchored OCR gap can connect a stationary unknown slot
        # without treating its corrupted recipient as a new person.
        if not 250<abs(elapsed(ta,tb))<=500 or effect['kind']!='friendship_change':return False
        start,end=sorted((ta,tb))
        for gap in event.get('receipt_continuity_evidence',[]):
            if gap.get('basis')!='matching_named_amount_receipt_ocr_gap' or gap.get('accepted_as_effect') is not False:continue
            proof=gap.get('evidence');row=rows_by_evidence.get(proof)
            if not row or not start<row['source_timestamp_ms']<end:continue
            if row['source_timestamp_ms']!=gap.get('source_timestamp_ms'):continue
            if row.get('effects') or row.get('facts',{}).get('effect_candidates'):continue
            if row.get('screen') not in ('unknown','event_outcome'):continue
            if _dialogue_text_moved(rows_by_evidence[pa],row) or _dialogue_text_moved(row,rows_by_evidence[pb]):continue
            for line in row.get('ocr',{}).get('neural',[]):
                if line.get('confidence',0)<95 or line.get('text') not in gap.get('raw_texts',[]):continue
                if not same_slot(ba,line.get('box',[])) or not same_slot(bb,line.get('box',[])):continue
                match=re.fullmatch(r'friendship with .+? [went ]{1,5}up by (\d+)\.',
                                   ' '.join(line.get('text','').casefold().split()))
                if match and int(match[1])==effect.get('amount'):return proof
        return False
    def identity(effect):return (effect['kind'],effect['name'])
    def field(effect):return effect['kind']+'||'+effect['name']
    def observations(effect):
        key=field(effect);result=[]
        for proof in event['field_evidence'].get(key,[]):
            row=rows_by_evidence.get(proof)
            if row is None:continue
            texts={effect.get('raw_text'),effect.get('original_text')}-{None}
            lines=[l for l in row.get('ocr',{}).get('neural',[])
                   if l.get('confidence',0)>=95 and l.get('text') in texts]
            if len(lines)==1:
                result.append((row['source_timestamp_ms'],proof,lines[0]['box']))
        return result
    observed={identity(e):observations(e) for e in effects}
    disputed=set()
    for index,left in enumerate(effects):
        for right in effects[index+1:]:
            a,b=left['name'],right['name']
            # Geometry and time identify the disputed slot. OCR can lose many
            # characters under an overlay; edit distance cannot establish that
            # the changing text describes separate people.
            if left['kind']!=right['kind'] or a==b:continue
            if left.get('amount')!=right.get('amount') or left.get('value')!=right.get('value'):continue
            first,second=observed[identity(left)],observed[identity(right)]
            if {x[0] for x in first}&{x[0] for x in second}:continue
            pairs=[];bridges=[];gap_bridges=[]
            for ta,pa,ba in first:
                for tb,pb,bb in second:
                    bridge=occluded_bridge(ta,tb,pa,pb,ba,bb,left) if abs(elapsed(ta,tb))>250 else False
                    gap_bridge=receipt_gap_bridge(ta,tb,pa,pb,ba,bb,left) if abs(elapsed(ta,tb))>250 else False
                    if not (0<abs(elapsed(ta,tb))<=250 or bridge or gap_bridge):continue
                    if _dialogue_text_moved(rows_by_evidence[pa],rows_by_evidence[pb]):continue
                    if same_slot(ba,bb):
                        pairs.append([pa,pb])
                        if bridge:bridges.append(bridge)
                        if gap_bridge:gap_bridges.append(gap_bridge)
            if not pairs:continue
            disputed.update((identity(left),identity(right)))
            for effect in (left,right):
                event['conflicting_readings'].append(dict(field=field(effect),
                    reason='recipient_name_changes_in_adjacent_same_slot_receipt',
                    name_candidates=[a,b],evidence_pairs=pairs,
                    **({'occluded_bridge_evidence':sorted(set(bridges))} if bridges else {}),
                    **({'receipt_gap_bridge_evidence':sorted(set(gap_bridges))} if gap_bridges else {})))
    if disputed:
        event.setdefault('ambiguous_effect_candidates',[]).extend(
            dict(effect=e,reason='unresolved_recipient_identity',
                 evidence=event['field_evidence'].get(field(e),[]))
            for e in effects if identity(e) in disputed)
        event['effects']=[e for e in event['effects'] if not
            (e.get('name') and identity(e) in disputed)]


def flag_inheritance_identity_conflicts(event,rows_by_evidence):
    """Abstain when adjacent views disagree within one scrolling receipt.

    Inheritance names are deliberately never normalized here.  Two accepted
    names become ambiguous only when their source rows are adjacent,
    event-outcome rows share a title context, and their line geometry proves
    one receipt slot.  A moving slot also needs one unique exact neighboring
    OCR line with the same upward motion.  This keeps separate visible
    inspiration lines distinct and does not depend on a name catalog,
    similarity score, cursor interpretation, or animation effects.
    """
    if not isinstance(event,dict) or not isinstance(rows_by_evidence,dict):return
    effects=[e for e in event.get('effects',[]) if isinstance(e,dict)
             and e.get('kind')=='inheritance_inspiration'
             and isinstance(e.get('name'),str) and e.get('name')]
    field_evidence=event.get('field_evidence',{})
    if len(effects)<2 or not isinstance(field_evidence,dict):return

    def valid_box(box):
        return (isinstance(box,(list,tuple)) and len(box)==4
                and all(type(value) in (int,float) and math.isfinite(value) for value in box)
                and inside_pane(box))

    band_top,band_bottom=receipt_rows(780,960)

    def center(box):return (box[1]+box[3])/2

    def height(box):return box[3]-box[1]

    def row_lines(row):
        if not isinstance(row,dict) or row.get('screen')!='event_outcome':return []
        ocr=row.get('ocr',{})
        if not isinstance(ocr,dict) or not isinstance(ocr.get('neural'),list):return []
        result=[]
        for line in ocr['neural']:
            box=line.get('box',[]) if isinstance(line,dict) else []
            confidence=line.get('confidence',0) if isinstance(line,dict) else 0
            if (not isinstance(line,dict) or not valid_box(box) or
                type(confidence) not in (int,float) or not math.isfinite(confidence) or
                confidence<95 or line.get('overlay_occluded') is True or
                not band_top<=center(box)<=band_bottom or not isinstance(line.get('text'),str)):
                continue
            result.append(line)
        return result

    def titles(value):
        if not isinstance(value,dict):return set()
        return {item.strip() for key in ('context_title','context_title_candidate')
                for item in [value.get(key)] if isinstance(item,str) and item.strip()}

    event_titles=titles(event)

    def same_context(left,right):
        left_titles,right_titles=titles(left),titles(right)
        return left_titles==right_titles and len(event_titles|left_titles)<=1

    def field(effect):return effect['kind']+'||'+effect['name']

    def texts(effect):
        return {item for item in (effect.get('raw_text'),effect.get('original_text'))
                if isinstance(item,str) and item}

    def observations(effect):
        proofs=field_evidence.get(field(effect),[])
        if not isinstance(proofs,list):return []
        result=[]
        for proof in dict.fromkeys(item for item in proofs if isinstance(item,str)):
            row=rows_by_evidence.get(proof)
            matches=[line for line in row_lines(row) if line.get('text') in texts(effect)]
            if len(matches)!=1 or not isinstance(row,dict):continue
            timestamp=row.get('source_timestamp_ms')
            if type(timestamp) is not int:continue
            result.append(dict(timestamp=timestamp,evidence=proof,row=row,line=matches[0]))
        return result

    def adjacent(left,right):
        if left['timestamp']==right['timestamp']:return False
        start,end=sorted((left['timestamp'],right['timestamp']))
        if end-start>250:return False
        # The accepted observations must be consecutive source rows.  A row
        # between them would make a two-point identity pairing ambiguous.
        return not any(isinstance(row,dict) and type(row.get('source_timestamp_ms')) is int
                       and start<row['source_timestamp_ms']<end
                       for row in rows_by_evidence.values())

    def stable_geometry(left,right):
        return (valid_box(left) and valid_box(right)
                and abs(left[0]-right[0])<=5
                # Keep the x origin, width, and height stable while allowing
                # a modest OCR box change for a different spelling.
                and abs((left[2]-left[0])-(right[2]-right[0]))<=35
                and abs(height(left)-height(right))<=8)

    def inspiration_lines(row):
        """Retain even weak Inspired-by boxes as topology evidence only."""
        if not isinstance(row,dict):return []
        ocr=row.get('ocr',{})
        if not isinstance(ocr,dict) or not isinstance(ocr.get('neural'),list):return []
        return [line for line in ocr['neural']
                if isinstance(line,dict) and valid_box(line.get('box',[]))
                and band_top<=center(line['box'])<=band_bottom
                and isinstance(line.get('text'),str)
                and re.match(r'^\s*Inspired\s+by\b',line['text'],re.I)]

    def competing_inspiration(row,target_box,left,right):
        """Reject a target slot with another nearby Inspired-by candidate.

        A low-confidence neighboring name is not accepted as an effect.  Its
        box still prevents a generic spark line from being mistaken for the
        same physical receipt occurrence.
        """
        target_texts=texts(left)|texts(right)
        for line in inspiration_lines(row):
            if line.get('text') in target_texts and line.get('box')==target_box:continue
            if abs(center(line['box'])-center(target_box))<=35:return True
        return False

    def simultaneous(row,left,right):
        left_texts,right_texts=texts(left),texts(right)
        found={line.get('text') for line in row_lines(row)}
        return bool(found&left_texts) and bool(found&right_texts)

    def moving_anchor(first,second,target_first,target_second,left,right):
        target_dy=center(target_second)-center(target_first)
        if not -80<=target_dy<=-8:return None
        first_lines=row_lines(first);second_lines=row_lines(second)
        first_by={};second_by={}
        for line in first_lines:first_by.setdefault(line['text'],[]).append(line)
        for line in second_lines:second_by.setdefault(line['text'],[]).append(line)
        excluded=texts(left)|texts(right);candidates=[]
        for text in sorted(first_by.keys()&second_by.keys()):
            if text in excluded:continue
            # A repeated line cannot identify one physical neighboring slot,
            # even when another line in the frame would otherwise qualify.
            if len(first_by[text])!=1 or len(second_by[text])!=1:return None
            anchor_first,anchor_second=first_by[text][0],second_by[text][0]
            box_first,box_second=anchor_first['box'],anchor_second['box']
            if not stable_geometry(box_first,box_second):return None
            anchor_dy=center(box_second)-center(box_first)
            if abs(anchor_dy)<8:continue
            # Any other clearly moving shared line that disagrees with the
            # target motion is contradictory evidence for this receipt track.
            if not -80<=anchor_dy<=-8 or abs(target_dy-anchor_dy)>8:return None
            # The target-to-anchor spacing must survive the scroll.  This
            # rejects a name that merely moved into another receipt slot.
            first_spacing=center(target_first)-center(box_first)
            second_spacing=center(target_second)-center(box_second)
            if abs(first_spacing-second_spacing)>8:return None
            candidates.append(dict(text=text,first=box_first,second=box_second,
                                   delta_y=anchor_dy))
        if not candidates:return None
        # More than one independently unique anchor is stronger evidence when
        # all anchors agree on the same rigid motion. Contradictory anchors
        # must leave the names unresolved.
        first_delta=candidates[0]['delta_y']
        if any(abs(item['delta_y']-first_delta)>8 for item in candidates):return None
        return dict(anchor=candidates[0],anchors=candidates)

    def track(left,right,first,second):
        if not adjacent(first,second):return None
        earlier,later=(first,second) if first['timestamp']<second['timestamp'] else (second,first)
        if not same_context(earlier['row'],later['row']):return None
        if simultaneous(earlier['row'],left,right) or simultaneous(later['row'],left,right):return None
        first_box,later_box=earlier['line']['box'],later['line']['box']
        if not stable_geometry(first_box,later_box):return None
        # A stationary slot alone cannot distinguish a replacement receipt
        # from an OCR variant. The cursor gate handles the known Seiun case;
        # this generic guard requires source-backed scrolling motion.
        if competing_inspiration(earlier['row'],first_box,left,right) or competing_inspiration(later['row'],later_box,left,right):return None
        anchor=moving_anchor(earlier['row'],later['row'],first_box,later_box,left,right)
        if anchor is None:return None
        return dict(mode='upward_scroll',evidence_pair=[earlier['evidence'],later['evidence']],
                    timestamp_pair=[earlier['timestamp'],later['timestamp']],
                    target_boxes=[list(first_box),list(later_box)],
                    anchor=anchor['anchor'],anchors=anchor['anchors'])

    def combine_tracks(pairs):
        """Combine adjacent supporting windows only when they form one track."""
        unique={tuple(item['evidence_pair']):item for item in pairs}
        tracks=sorted(unique.values(),key=lambda item:(tuple(item['timestamp_pair']),tuple(item['evidence_pair'])))
        if not tracks:return None
        if len(tracks)==1:return tracks[0]
        if {item['mode'] for item in tracks}!={'upward_scroll'}:return None
        times=sorted({time for item in tracks for time in item['timestamp_pair']})
        if any(elapsed(earlier,later)>250 for earlier,later in zip(times,times[1:])):return None
        links={evidence:set() for item in tracks for pair in [item['evidence_pair']]
               for evidence in pair}
        for item in tracks:
            first,second=item['evidence_pair'];links[first].add(second);links[second].add(first)
        pending=[sorted(links)[0]];seen=set()
        while pending:
            evidence=pending.pop()
            if evidence in seen:continue
            seen.add(evidence);pending.extend(links[evidence]-seen)
        if len(seen)!=len(links):return None
        common={anchor['text'] for anchor in tracks[0]['anchors']}
        for item in tracks[1:]:common &= {anchor['text'] for anchor in item['anchors']}
        if not common:return None
        # A connected evidence graph is necessary but not sufficient: the
        # target and each shared anchor must also continue through the joins
        # in timestamp order.  This rejects two unrelated receipt windows
        # that happen to have the same neighboring text.
        for previous,current in zip(tracks,tracks[1:]):
            previous_target=previous['target_boxes'][1]
            current_target=current['target_boxes'][0]
            if not stable_geometry(previous_target,current_target):return None
            target_join=center(current_target)-center(previous_target)
            if not -80<=target_join<=8:return None
            for anchor_text in sorted(common):
                previous_anchor=next(anchor for anchor in previous['anchors']
                                     if anchor['text']==anchor_text)
                current_anchor=next(anchor for anchor in current['anchors']
                                    if anchor['text']==anchor_text)
                if not stable_geometry(previous_anchor['second'],current_anchor['first']):return None
        anchors=[]
        for text in sorted(common):
            anchors.append(next(anchor for anchor in tracks[0]['anchors'] if anchor['text']==text))
        return dict(mode='upward_scroll',evidence_pair=tracks[0]['evidence_pair'],
                    timestamp_pair=tracks[0]['timestamp_pair'],
                    evidence_pairs=[item['evidence_pair'] for item in tracks],
                    timestamp_pairs=[item['timestamp_pair'] for item in tracks],
                    target_boxes=[item['target_boxes'] for item in tracks],
                    anchor=anchors[0],anchors=anchors,
                    supporting_tracks=tracks)

    observed={id(effect):observations(effect) for effect in effects}
    disputed=[]
    for index,left in enumerate(effects):
        for right in effects[index+1:]:
            if left.get('name')==right.get('name'):continue
            if any(left.get(key)!=right.get(key) for key in ('field','amount','direction','value')):continue
            left_observed,right_observed=observed[id(left)],observed[id(right)]
            if not left_observed or not right_observed:continue
            if any(simultaneous(item['row'],left,right)
                   for item in left_observed+right_observed):continue
            pairs=[]
            for first in left_observed:
                for second in right_observed:
                    candidate=track(left,right,first,second)
                    if candidate is not None:pairs.append(candidate)
            # Only one pair or one connected, geometrically continuous track
            # can establish the disputed slot. Otherwise preserve both names.
            continuity=combine_tracks(pairs)
            if continuity is None:continue
            disputed.extend((left,right))
            for effect in (left,right):
                event.setdefault('conflicting_readings',[]).append(dict(
                    field=field(effect),
                    reason='inheritance_inspiration_identity_changes_in_tracked_receipt',
                    name_candidates=[left['name'],right['name']],
                    evidence_pairs=continuity.get('evidence_pairs', [continuity['evidence_pair']]),
                    continuity=deepcopy(continuity)))

    disputed_ids={id(effect) for effect in disputed}
    if not disputed_ids:return
    existing=set()
    for candidate in event.get('ambiguous_effect_candidates',[]):
        if (isinstance(candidate,dict) and isinstance(candidate.get('effect'),dict)
                and isinstance(candidate['effect'].get('name'),str)):
            existing.add(field(candidate['effect']))
    for effect in effects:
        if id(effect) not in disputed_ids or field(effect) in existing:continue
        event.setdefault('ambiguous_effect_candidates',[]).append(dict(
            effect=deepcopy(effect),reason='unresolved_inheritance_inspiration_identity',
            evidence=list(field_evidence.get(field(effect),[]))))
    event['effects']=[effect for effect in event.get('effects',[])
                      if id(effect) not in disputed_ids]


def _occluded_wrapped_hint_bridge(start,end,effect,rows_by_evidence):
    """Prove continuity across one obscured first line, without reading its name."""
    if end-start!=500:return None
    selected=[]
    for time in (start,start+250,end):
        rows=[r for r in rows_by_evidence.values() if r['source_timestamp_ms']==time]
        if len(rows)!=1:return None
        selected.append(rows[0])
    before,middle,after=selected
    if _dialogue_text_moved(before,middle) or _dialogue_text_moved(middle,after):return None
    prefix=f"Gained {effect['amount']} hint level(s) for "
    top,bottom=receipt_rows(780,960)
    def parts(row):
        lines=row.get('ocr',{}).get('neural',[])
        matches=[]
        for a,b in zip(lines,lines[1:]):
            if min(a.get('confidence',0),b.get('confidence',0))<95:continue
            if a.get('text','')+' '+b.get('text','')!=effect.get('raw_text'):continue
            ba,bb=a.get('box',[]),b.get('box',[])
            if len(ba)!=4 or len(bb)!=4:continue
            if not top<=ba[1]<bb[1]<=bottom or bb[1]-ba[1]>=40 or abs(ba[0]-bb[0])>15:continue
            matches.append((a,b))
        return matches
    a,b=parts(before),parts(after)
    if len(a)!=1 or len(b)!=1:return None
    def near(x,y):
        if len(x)!=4 or len(y)!=4:return False
        intersection=max(0,min(x[2],y[2])-max(x[0],y[0]))*max(0,min(x[3],y[3])-max(x[1],y[1]))
        union=(x[2]-x[0])*(x[3]-x[1])+(y[2]-y[0])*(y[3]-y[1])-intersection
        return union>0 and intersection/union>=.8 and abs(x[0]-y[0])<=3 and abs(x[2]-y[2])<=3 and abs(x[1]+x[3]-y[1]-y[3])<=6
    if any(x['text']!=y['text'] or not near(x['box'],y['box']) for x,y in zip(a[0],b[0])):return None
    occluded=[l for l in middle.get('facts',{}).get('occluded_receipt_lines',[])
              if l.get('text','').startswith(prefix) and l.get('overlay_boxes')
              and near(l.get('box',[]),a[0][0]['box']) and near(l.get('box',[]),b[0][0]['box'])]
    tails=[l for l in middle.get('ocr',{}).get('neural',[]) if l.get('confidence',0)>=95
           and l.get('text')==a[0][1]['text'] and near(l.get('box',[]),a[0][1]['box'])
           and near(l.get('box',[]),b[0][1]['box'])]
    if len(occluded)!=1 or len(tails)!=1:return None
    return middle['evidence']


def collapse_visual_hint_variants(event,timestamps,rows_by_evidence=None):
    """Keep one award when contiguous receipt frames lose a proven marker.

    Only the exact original OCR sentence of a pixel-corrected observation may
    be folded into it. Uncorrected frames remain alternate evidence, never
    proof of the corrected symbol. This operates within one receipt event.
    """
    hints=[e for e in event['effects'] if e['kind']=='skill_hint_change']
    removed=[]
    for weak in hints:
        if weak.get('visual_symbol_observation'):continue
        weak_key='skill_hint_change||'+weak['name']
        weak_proofs=event['field_evidence'].get(weak_key,[])
        weak_times=sorted({timestamps[p] for p in weak_proofs if p in timestamps})
        candidates=[]
        for strong in hints:
            symbol=strong.get('visual_symbol_observation',{})
            if symbol.get('method')!='strict_terminal_ring_geometry':continue
            if strong.get('original_text')!=weak.get('raw_text') or strong.get('amount')!=weak.get('amount'):continue
            strong_key='skill_hint_change||'+strong['name']
            if any(c.get('field') in (weak_key,strong_key) for c in event.get('conflicting_readings',[])):continue
            strong_times=sorted({timestamps[p] for p in event['field_evidence'].get(strong_key,[]) if p in timestamps})
            if len(strong_times)<2 or not weak_times:continue
            combined=sorted(set(strong_times+weak_times))
            bridges=[];unresolved=False
            for a,b in zip(combined,combined[1:]):
                if b-a<=250:continue
                bridge=_occluded_wrapped_hint_bridge(a,b,weak,rows_by_evidence or {})
                if bridge is None:unresolved=True;break
                bridges.append(bridge)
            if unresolved:continue
            candidates.append((strong,bridges))
        if len(candidates)!=1:continue
        target,bridges=candidates[0]
        target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
        target.setdefault('alternate_name_evidence',[]).append(dict(name=weak['name'],evidence=list(weak_proofs)))
        target['name_resolution']='exact_original_text_in_contiguous_pixel_verified_hint_receipt'
        if bridges:target['occluded_continuity_evidence']=bridges
        removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]


def collapse_punctuated_hint_variants(event,rows_by_evidence):
    """Resolve a lost name terminator using repeated same-frame skill labels.

    A longer spelling alone is insufficient: the shorter receipt must overlap
    two independent frames displaying the complete skill label. Keep its
    evidence separate from the observations that actually read both marks.
    """
    hints=[e for e in event['effects'] if e['kind']=='skill_hint_change']
    removed=[]
    # The receipt rows, and the skill label drawn above the text box.
    receipt_top,receipt_bottom=receipt_rows(780,960)
    label_top,label_bottom=receipt_rows(600,780)
    def observations(effect):
        key='skill_hint_change||'+effect['name']
        result=[]
        for proof in event['field_evidence'].get(key,[]):
            row=rows_by_evidence.get(proof)
            if row is None:continue
            lines=row.get('ocr',{}).get('neural',[])
            matches=[l for l in lines if l.get('text')==effect.get('raw_text')
                     and l.get('confidence',0)>=95 and len(l.get('box',[]))==4
                     and receipt_top<=(l['box'][1]+l['box'][3])/2<=receipt_bottom]
            if len(matches)==1:result.append((row['source_timestamp_ms'],proof,lines))
        return result
    for weak in hints:
        candidates=[]
        first=observations(weak)
        for strong in hints:
            if strong['name']!=weak['name']+'!':continue
            if strong.get('amount')!=weak.get('amount'):continue
            if strong.get('raw_text')!=weak.get('raw_text','')+'.':continue
            keys={'skill_hint_change||'+e['name'] for e in (weak,strong)}
            if any(c.get('field') in keys for c in event.get('conflicting_readings',[])):continue
            second=observations(strong)
            a={t for t,_,_ in first};b={t for t,_,_ in second}
            if not a or len(b)<2 or a&b:continue
            times=sorted(a|b)
            if any(elapsed(x,y)>250 for x,y in zip(times,times[1:])):continue
            labels=[]
            for t,proof,lines in first:
                matches=[l for l in lines if l.get('text')==strong['name']
                         and l.get('confidence',0)>=95 and len(l.get('box',[]))==4
                         and label_top<=(l['box'][1]+l['box'][3])/2<=label_bottom]
                if len(matches)==1:labels.append((t,proof))
            if len({t for t,_ in labels})<2:continue
            candidates.append((strong,labels))
        if len(candidates)!=1:continue
        target,labels=candidates[0]
        target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
        target.setdefault('alternate_name_evidence',[]).append(dict(
            name=weak['name'],evidence=[p for _,p,_ in first]))
        target['name_resolution']='repeated_same_frame_label_and_contiguous_punctuated_receipt'
        target['name_label_evidence']=[p for _,p in labels]
        removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]


def _recovered_reading(evidence):
    """Whether a proof came from a recovery pass rather than the base one."""
    return isinstance(evidence,str) and not evidence.startswith('gameplay/')


def _nearest_hint_award(name,candidates):
    """Which held award a garbled spelling belongs to, when one stands out.

    Distance cannot say whether a spelling is garbled: ``Sp.unner`` is far
    from the name it damages, while ``Medium Corners`` is close to a
    different skill.  Corroboration decides that.  Distance is asked only
    which of the awards the event already holds at that amount the garble
    belongs to, and it answers only when one of them is strictly nearer than
    every other.
    """
    from .gameplay import _edit_distance
    scored=sorted((_edit_distance(str(name or ''),str(item.get('name') or '')),index,item)
                  for index,item in enumerate(candidates))
    if not scored or (len(scored)>1 and scored[0][0]==scored[1][0]):return None
    return scored[0][2]


def collapse_uncorroborated_hint_variants(event,hint_name_sightings):
    """Fold a hint spelling that one recovered frame is the only sighting of.

    The receipt log scrolls while an event runs, so a receipt the recovery
    reads again sits at a different height on every frame and a garbled
    re-read shares no slot with the clean one.  The distance between the two
    settles nothing either: ``Sp.unner`` is far from ``Spring Runner`` while
    ``Medium Corners`` is close to ``Medium Straightaways``.  What separates
    them is corroboration -- a real award's name recurs through the run, and
    a garbled re-read is one recovered frame's spelling and appears nowhere
    else.  Such a spelling joins an award the event already holds at the same
    amount whose own name was read more often, so the award keeps its count
    and gains the frame as evidence.

    An award nothing else corroborates keeps whatever the recovery read, a
    skill the run saw more than once stays its own award at any amount, and
    two equally uncorroborated candidates leave each other alone.
    """
    hints=[e for e in event['effects'] if e['kind']=='skill_hint_change']
    removed=[]
    for weak in hints:
        if hint_name_sightings.get(weak.get('name'))!=1:continue
        key='skill_hint_change||'+str(weak.get('name') or '')
        proofs=event['field_evidence'].get(key) or []
        if not proofs or not all(_recovered_reading(proof) for proof in proofs):continue
        targets=[held for held in hints
                 if held is not weak and held.get('amount')==weak.get('amount')
                 and (hint_name_sightings.get(held.get('name')) or 0)>1]
        target=_nearest_hint_award(weak.get('name'),targets)
        if target is None:continue
        keys={'skill_hint_change||'+str(effect.get('name') or '') for effect in (weak,target)}
        if any(item.get('field') in keys for item in event.get('conflicting_readings',[])):continue
        target.setdefault('alternate_name_evidence',[]).append(dict(
            name=weak.get('name'),evidence=list(proofs)))
        target['name_resolution']='uncorroborated_recovered_spelling'
        event['field_evidence'].setdefault(
            'skill_hint_change||'+str(target.get('name') or ''),[]).extend(proofs)
        removed.append(weak)
    event['effects']=[effect for effect in event['effects'] if effect not in removed]


def _candidate_frames(candidate):
    return [proof for proof in (candidate.get('evidence') or []) if isinstance(proof, str)]


def _drop_conflicts(event, fields, reasons):
    event['conflicting_readings'] = [
        item for item in event.get('conflicting_readings', [])
        if not (item.get('field') in fields and item.get('reason') in reasons)]


def collapse_uncorroborated_recipient_variants(event, name_sightings, vocabulary=None, rows_by_evidence=None):
    """Fold a recipient spelling the run produced only here into the one it knows.

    Adjacent views of one receipt slot can disagree on the recipient's name
    (``Agnes Tachyon`` and ``\u00c1gnes Tachyon``, ``Etsuko Otonashi`` and
    ``Etsuk Otonashi``), and the conflict handler rightly keeps both as
    candidates: distance cannot say which is damaged.  Corroboration can.  A
    supporter's name recurs through the run on every receipt that names them,
    while a damaged spelling is read on the frames of this one receipt and
    nowhere else.  When exactly one candidate of a disputed slot recurs
    elsewhere and every other candidate is known only here, the recurring
    spelling is the receipt and the others are its evidence.  When none
    recurs, the spellings may still repair to one known name.  Failing both,
    the slot's own views may settle it: a spelling read only between two
    reads of another, in unbroken runs of the slot's views, is a misread of
    it.  Otherwise the slot stays as undecided as the conflict handler left
    it.
    """
    candidates = event.get('ambiguous_effect_candidates')
    if not isinstance(candidates, list):
        return
    groups = {}
    for candidate in candidates:
        if (isinstance(candidate, dict) and candidate.get('reason') == 'unresolved_recipient_identity'
                and isinstance(candidate.get('effect'), dict)):
            effect = candidate['effect']
            groups.setdefault((effect.get('kind'), effect.get('amount'), effect.get('direction'), effect.get('value')),
                              []).append(candidate)

    def elsewhere(candidate):
        effect = candidate['effect']
        return (name_sightings.get((effect.get('kind'), effect.get('name'))) or 0) - len(_candidate_frames(candidate))

    folded = []
    for members in groups.values():
        recurring = [candidate for candidate in members if elsewhere(candidate) > 0]
        if len(members) < 2:
            continue
        known = None
        target = None
        if len(recurring) == 1:
            target = recurring[0]
            resolution = 'uncorroborated_spelling_joins_recurring_recipient'
        elif not recurring and vocabulary is not None:
            # No spelling recurs, but each is a glyph or two from one name the
            # run knows well ("Agies Tachyon" and "Aghes Tachyon" beside many
            # "Agnes Tachyon"): the cursor crossed a different letter on each
            # frame, and the damaged spellings are that recipient.
            from .name_vocabulary import _group, repair
            repaired = set()
            for candidate in members:
                name = candidate['effect'].get('name')
                repaired.add(repair(name, vocabulary.get('supporter') or {}, 'supporter',
                                    (vocabulary.get('together') or {}).get(name, ()))
                             if _group(candidate['effect']) == 'supporter' else None)
            if len(repaired) == 1 and None not in repaired:
                known = repaired.pop()
                target = members[0]
                resolution = 'disputed_spellings_repaired_to_known_recipient'
        if target is None:
            # The run cannot settle it, but the slot's own views can: its line
            # cannot change and change back, so a spelling read only between
            # two reads of another is a misread of that one, even a misread
            # the run happens to produce elsewhere too.
            target = _slot_misread_target(members, rows_by_evidence)
            resolution = 'misread_between_reads_of_one_name_in_one_slot'
        if target is None:
            continue
        effect = deepcopy(target['effect'])
        if known is not None:
            effect['name'] = known
        effect['name_resolution'] = resolution
        key = str(effect.get('kind')) + '||' + str(effect.get('name') or '')
        proofs = event.setdefault('field_evidence', {}).setdefault(key, [])
        proofs.extend(proof for proof in _candidate_frames(target) if proof not in proofs)
        for other in members:
            if other is target and known is None:
                continue
            effect.setdefault('alternate_name_evidence', []).append(dict(
                name=other['effect'].get('name'), evidence=_candidate_frames(other)))
            proofs.extend(proof for proof in _candidate_frames(other) if proof not in proofs)
        event.setdefault('effects', []).append(effect)
        _drop_conflicts(event, {str(c['effect'].get('kind')) + '||' + str(c['effect'].get('name') or '') for c in members},
                        {'recipient_name_changes_in_adjacent_same_slot_receipt'})
        folded.extend(members)
    if folded:
        event['ambiguous_effect_candidates'] = [c for c in candidates if not any(c is f for f in folded)]


def _slot_misread_target(members, rows_by_evidence):
    """The candidate every other candidate of a disputed slot misreads, or None.

    The slot's views a sampling step apart at most, with the dialogue not
    moving, form unbroken runs. The target starts and ends every run another
    candidate is read in, so each other spelling is read only between two
    reads of it. A view that cannot be placed settles nothing.
    """
    if not rows_by_evidence:
        return None
    views = []
    for candidate in members:
        effect = candidate['effect']
        texts = {effect.get('raw_text'), effect.get('original_text')} - {None}
        for proof in _candidate_frames(candidate):
            row = rows_by_evidence.get(proof)
            lines = [line for line in (row or {}).get('ocr', {}).get('neural', [])
                     if line.get('confidence', 0) >= 95 and line.get('text') in texts]
            if len(lines) != 1:
                return None
            views.append((row['source_timestamp_ms'], proof, lines[0]['box'], candidate))
    views.sort(key=lambda view: (view[0], view[1]))
    if not views or not all(_same_line_slot(views[0][2], view[2]) for view in views):
        return None
    runs = [[views[0]]]
    for view in views[1:]:
        last = runs[-1][-1]
        if (elapsed(last[0], view[0]) <= 250
                and not _dialogue_text_moved(rows_by_evidence[last[1]], rows_by_evidence[view[1]])):
            runs[-1].append(view)
        else:
            runs.append([view])
    starts = {id(run[0][3]): run[0][3] for run in runs if any(view[3] is not run[0][3] for view in run)}
    if len(starts) != 1:
        return None
    target = next(iter(starts.values()))
    if any(any(view[3] is not target for view in run) and (run[0][3] is not target or run[-1][3] is not target)
           for run in runs):
        return None
    return target


_CIRCLE_MARKERS = '\u25cb\u25ce'


def collapse_uncorroborated_circle_base_variants(event, name_sightings):
    """Fold a bare spelling read nowhere else into its circle-proven award.

    A receipt's circle marker is a small glyph at the end of the line, and
    the recognizer drops it on some frames, so one line reads as ``Corner
    Recovery \u25cb`` and as ``Corner Recovery``.  The circle fallback keeps the
    proven circle award and holds the bare spelling as a possible duplicate
    or additional effect, because it could not show the two readings to be
    one slot.  Corroboration decides the rest.  A skill the run knows without
    a circle is read that way on its own receipts; a bare spelling read only
    on this receipt's frames, beside the proven circle award at the same
    amount, is that award with its glyph unread, and becomes its evidence.  A
    bare spelling read anywhere else in the run stays a candidate.
    """
    candidates = event.get('ambiguous_effect_candidates')
    if not isinstance(candidates, list):
        return
    effects_by_key = {str(effect.get('kind')) + '||' + str(effect.get('name') or ''): effect
                      for effect in event.get('effects', []) if isinstance(effect, dict) and effect.get('name')}
    folded = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get('reason') != 'unresolved_circle_base_variant':
            continue
        weak = candidate.get('effect') or {}
        strong_ref = candidate.get('possible_duplicate_of') or {}
        strong = effects_by_key.get(strong_ref.get('field'))
        if strong is None or strong.get('kind') != weak.get('kind') or strong.get('amount') != weak.get('amount'):
            continue
        strong_name = str(strong.get('name') or '').strip()
        if (not strong_name or strong_name[-1] not in _CIRCLE_MARKERS
                or strong_name[:-1].strip() != str(weak.get('name') or '').strip()):
            continue
        frames = _candidate_frames(candidate)
        if (name_sightings.get((weak.get('kind'), weak.get('name'))) or 0) > len(frames):
            continue
        strong.setdefault('alternate_name_evidence', []).append(dict(name=weak.get('name'), evidence=list(frames)))
        strong['name_resolution'] = 'circle_glyph_unread_on_uncorroborated_base_reading'
        proofs = event.setdefault('field_evidence', {}).setdefault(strong_ref['field'], [])
        proofs.extend(proof for proof in frames if proof not in proofs)
        _drop_conflicts(event, {candidate.get('field'), strong_ref.get('field')}, {'unresolved_circle_variant_relation'})
        folded.append(candidate)
    if folded:
        event['ambiguous_effect_candidates'] = [c for c in candidates if not any(c is f for f in folded)]


def _list_item_base(name):
    """A hint name without its rank mark, however the mark was read."""
    return re.sub(r'\s*[O0○◯◎]$', '', str(name or '').strip()).strip().casefold()


def _list_item_sightings(effect, event, rows_by_evidence, group_texts):
    """``(preceding line's text or None)`` for each frame that shows this reading.

    Returns ``None`` when a frame shows two lines of the group: those are two
    list items, and nothing may be folded.
    """
    key = 'skill_hint_change||' + str(effect.get('name') or '')
    proof = effect.get('source_bound_receipt_proof')
    proof_box = proof.get('line_box') if isinstance(proof, dict) else None
    sightings = []
    for evidence in dict.fromkeys(p for p in event.get('field_evidence', {}).get(key, []) if isinstance(p, str)):
        row = rows_by_evidence.get(evidence)
        lines = [line for line in ((row or {}).get('ocr') or {}).get('neural', [])
                 if isinstance(line, dict) and len(line.get('box', [])) == 4 and isinstance(line.get('text'), str)]
        if sum(1 for line in lines if line['text'] in group_texts) > 1:
            return None
        if isinstance(proof_box, list) and len(proof_box) == 4:
            own = [line for line in lines if all(abs(a - b) <= 4 for a, b in zip(line['box'], proof_box))]
        else:
            own = [line for line in lines if line.get('confidence', 0) >= 90
                   and line['text'] in (effect.get('raw_text'), effect.get('original_text'))]
        if len(own) != 1:
            continue
        left, top = own[0]['box'][0], own[0]['box'][1]
        above = [line for line in lines if line is not own[0] and line.get('confidence', 0) >= 95
                 and abs(line['box'][0] - left) <= 6 and 16 <= top - line['box'][1] <= 34]
        sightings.append(above[0]['text'] if len(above) == 1 else None)
    return sightings


def collapse_same_list_item_hint_variants(event, rows_by_evidence):
    """One line of a scrolling receipt list is one award, however it was read.

    The list scrolls while a cursor or a skill card crosses it, so one line
    can come out as ``Rainy Days ○`` (ring proven), ``Rainy Days O`` and,
    half covered, ``Rainy Days``, with gaps between the readings that the
    continuity rules cannot bridge.  Position in the list can: a list item
    keeps the same line above it on every frame.  Readings of one skill (rank
    mark aside) at one amount fold into the single ring-marked reading when
    every one of them was seen under the same preceding line, and no frame
    shows two of them at once (which would be two awards of the same skill).
    """
    hints = [e for e in event.get('effects', []) if isinstance(e, dict)
             and e.get('kind') == 'skill_hint_change' and e.get('name')]
    groups = {}
    for effect in hints:
        groups.setdefault((_list_item_base(effect['name']), effect.get('amount')), []).append(effect)
    removed = []
    for members in groups.values():
        if len(members) < 2:
            continue
        marked = [e for e in members if str(e['name']).strip()[-1] in _CIRCLE_MARKERS]
        if len(marked) != 1:
            continue
        target = marked[0]
        texts = {t for e in members for t in (e.get('raw_text'), e.get('original_text')) if isinstance(t, str)}
        above = set()
        for effect in members:
            sightings = _list_item_sightings(effect, event, rows_by_evidence, texts)
            seen = {text for text in sightings or [] if text}
            if sightings is None or not seen:
                above = None
                break
            above |= seen
        if not above or len(above) != 1 or above & texts:
            continue
        target_key = 'skill_hint_change||' + target['name']
        proofs = event.setdefault('field_evidence', {}).setdefault(target_key, [])
        fields = {target_key}
        for weak in members:
            if weak is target:
                continue
            weak_key = 'skill_hint_change||' + weak['name']
            weak_proofs = list(event['field_evidence'].get(weak_key, []))
            target.setdefault('observed_name_candidates', [target['name']]).append(weak['name'])
            target.setdefault('alternate_name_evidence', []).append(dict(name=weak['name'], evidence=weak_proofs))
            proofs.extend(proof for proof in weak_proofs if proof not in proofs)
            fields.add(weak_key)
            removed.append(weak)
        target['name_resolution'] = 'same_preceding_line_in_scrolling_receipt'
        _drop_conflicts(event, fields, {'unresolved_circle_variant_relation'})
    if removed:
        event['effects'] = [e for e in event['effects'] if not any(e is r for r in removed)]


_HINT_SEPARATOR_RE = re.compile(r"[\s\-\u2010-\u2015\u2212]+")


def _separator_identity(value):
    """Compare hint names while retaining every non-separator character.

    Spaces and hyphen-like separators are the only characters normalized here.
    In particular, trailing rank glyphs or OCR letters are retained as part of
    the identity; ``Skill ○`` and ``Skill O`` therefore remain different.
    """
    if not isinstance(value,str):return None
    return _HINT_SEPARATOR_RE.sub(' ',value.strip())


def _separator_hint_observations(effect,event,rows_by_evidence):
    """Return high-confidence source lines for one exact hint spelling."""
    raw_text=effect.get('raw_text')
    if not isinstance(raw_text,str) or not raw_text.strip():return []
    key='skill_hint_change||'+effect.get('name','')
    proofs=event.get('field_evidence',{}).get(key,[])
    if not isinstance(proofs,list):return []
    result=[]
    top,bottom=receipt_rows(780,960)
    for proof in dict.fromkeys(item for item in proofs if isinstance(item,str)):
        row=rows_by_evidence.get(proof)
        if not isinstance(row,dict) or type(row.get('source_timestamp_ms')) is not int:continue
        ocr=row.get('ocr',{})
        if not isinstance(ocr,dict) or not isinstance(ocr.get('neural'),list):continue
        lines=[]
        for line in ocr['neural']:
            if not isinstance(line,dict):continue
            box=line.get('box',[])
            confidence=line.get('confidence')
            line_text=line.get('text')
            if (not isinstance(line_text,str)
                    or (_separator_identity(line_text)!=_separator_identity(raw_text)
                        and line_text!=raw_text)
                    or line.get('overlay_occluded') is True
                    or type(confidence) not in (int,float) or not 0<=confidence<=100
                    or not math.isfinite(confidence)
                    or confidence<95 or not isinstance(box,(list,tuple)) or len(box)!=4
                    or not all(type(value) in (int,float) and -10000<=value<=10000
                               and math.isfinite(value) for value in box)
                    or not inside_pane(box)
                    or not top<=(box[1]+box[3])/2<=bottom):continue
            lines.append(line)
        if len(lines)==1:
            result.append(dict(timestamp=row['source_timestamp_ms'],evidence=proof,
                               row=row,line=lines[0]))
    return result


def _separator_hint_line_compatible(first,second):
    """Require one stationary receipt slot for two separator spellings."""
    if not isinstance(first,dict) or not isinstance(second,dict):return False
    a,b=first.get('box',[]),second.get('box',[])
    if not isinstance(a,(list,tuple)) or not isinstance(b,(list,tuple)) or len(a)!=4 or len(b)!=4:return False
    if not all(type(value) in (int,float) and -10000<=value<=10000 and math.isfinite(value)
               for value in (*a,*b)):return False
    return (abs(a[0]-b[0])<=5 and abs(a[2]-b[2])<=8
            and abs((a[1]+a[3])-(b[1]+b[3]))/2<=10
            and abs((a[3]-a[1])-(b[3]-b[1]))<=8)


def _separator_hint_pair_compatible(first,second):
    """Require adjacent, same-context source observations for one receipt."""
    if first['timestamp']==second['timestamp'] or abs(elapsed(first['timestamp'],second['timestamp']))>250:return False
    left,right=first['row'],second['row']
    if left.get('screen')!='event_outcome' or right.get('screen')!='event_outcome':
        return False
    left_title=left.get('context_title') or left.get('context_title_candidate')
    right_title=right.get('context_title') or right.get('context_title_candidate')
    if left_title and right_title and left_title!=right_title:return False
    return _separator_hint_line_compatible(first['line'],second['line'])


def collapse_separator_hint_variants(event,rows_by_evidence):
    """Collapse a same-receipt hint spelling that only changes separators.

    The helper preserves the selected OCR spelling and records the alternate
    evidence. It never strips or rewrites rank symbols, and it requires a
    same-amount, same-sentence, adjacent source line in one receipt slot.
    Separate events, differing amounts, differing suffixes, context changes,
    and moving line geometry remain separate or unresolved.
    """
    if not isinstance(event,dict) or not isinstance(rows_by_evidence,dict):return
    effects=[e for e in event.get('effects',[]) if isinstance(e,dict)
             and e.get('kind')=='skill_hint_change' and isinstance(e.get('name'),str)
             and e.get('name')]
    if len(effects)<2:return
    field_evidence=event.get('field_evidence',{})
    if not isinstance(field_evidence,dict):return

    def key(effect):return 'skill_hint_change||'+effect['name']
    def conflicts(effect):
        items=event.get('conflicting_readings',[])
        if not isinstance(items,list):return True
        return any(item.get('field')==key(effect)
                   for item in items if isinstance(item,dict))
    def score(effect,observations):
        by_timestamp={}
        for item in observations:
            timestamp=item['timestamp']
            by_timestamp[timestamp]=max(by_timestamp.get(timestamp,0),item['line'].get('confidence',0))
        confidences=list(by_timestamp.values())
        return (len(confidences),sum(confidences),max(confidences,default=0),effect['name'])

    removed=[]
    while True:
        merged_one=False
        active=[effect for effect in effects if effect not in removed]
        for index,left in enumerate(active):
            if conflicts(left):continue
            left_identity=_separator_identity(left['name'])
            if left_identity is None:continue
            for right in active[index+1:]:
                if conflicts(right) or left.get('name')==right.get('name'):continue
                if any(left.get(field)!=right.get(field) for field in ('field','amount','direction','value')):continue
                if _separator_identity(right['name'])!=left_identity:continue
                left_raw,right_raw=left.get('raw_text'),right.get('raw_text')
                if (not isinstance(left_raw,str) or not isinstance(right_raw,str)
                        or _separator_identity(left_raw)!=_separator_identity(right_raw)):continue
                left_observations=_separator_hint_observations(left,event,rows_by_evidence)
                right_observations=_separator_hint_observations(right,event,rows_by_evidence)
                pairs=[(a,b) for a in left_observations for b in right_observations
                       if _separator_hint_pair_compatible(a,b)]
                if not pairs:continue
                # Prefer the spelling backed by more source observations. This is
                # an evidence tie-breaker, not a canonical name or skill catalog.
                target,alternate=(left,right) if score(left,left_observations)>=score(right,right_observations) else (right,left)
                target_key=key(target);alternate_key=key(alternate)
                target.setdefault('observed_name_candidates',[target['name']])
                for candidate in alternate.get('observed_name_candidates',[]):
                    if candidate not in target['observed_name_candidates']:
                        target['observed_name_candidates'].append(candidate)
                if alternate['name'] not in target['observed_name_candidates']:
                    target['observed_name_candidates'].append(alternate['name'])
                prior_evidence=alternate.get('alternate_name_evidence',[])
                if isinstance(prior_evidence,list):
                    target.setdefault('alternate_name_evidence',[])
                    for item in prior_evidence:
                        if item not in target['alternate_name_evidence']:
                            target['alternate_name_evidence'].append(deepcopy(item))
                target.setdefault('alternate_name_evidence',[]).append(dict(
                    name=alternate['name'],evidence=[item['evidence'] for item in
                    _separator_hint_observations(alternate,event,rows_by_evidence)],
                    evidence_pairs=[[a['evidence'],b['evidence']] for a,b in pairs],
                    source_timestamps_ms=sorted({item['timestamp'] for pair in pairs for item in pair}),
                    basis='same_amount_sentence_and_adjacent_stationary_receipt_slot'))
                target['name_resolution']='same_receipt_separator_variant'
                merged=list(field_evidence.get(target_key,[]))
                for proof in field_evidence.get(alternate_key,[]):
                    if proof not in merged:merged.append(proof)
                field_evidence[target_key]=merged
                removed.append(alternate)
                merged_one=True
                break
            if merged_one:break
        if not merged_one:break
    event['effects']=[effect for effect in event['effects'] if effect not in removed]


def collapse_song_variants(event,timestamps):
    songs=[e for e in event['effects'] if e['kind']=='song_learned']
    removed=[]
    def times(effect):
        return sorted({timestamps[p] for p in event['field_evidence'].get('song_learned||'+effect['name'],[]) if p in timestamps})
    def missing_chars(a,b):
        """Whether ``b`` is ``a`` with one or two characters dropped."""
        if not 1<=len(a)-len(b)<=2:return False
        i=j=0
        while i<len(a) and j<len(b):
            if a[i]==b[j]:j+=1
            i+=1
        return j==len(b)
    def note_misread(a,b):
        """Whether ``b`` is ``a`` with its note glyph read as one other glyph."""
        return a.endswith('♪') and len(b)>=len(a) and b[:len(a)-1]==a[:-1] and len(b[len(a)-1:].strip())==1 and '♪' not in b
    # The note-glyph folds first, since they need no frame count and the
    # frames they fold in count for the song when the weaker spellings are
    # compared next.
    for glyph_pass in (True,False):
        for weak in sorted(songs,key=lambda e:len(times(e))):
            observed=times(weak)
            if not observed or weak in removed:continue
            candidates=[]
            for strong in songs:
                if strong is weak or strong in removed:continue
                complete=times(strong);a=strong['name'];b=weak['name']
                # The note glyph the game prints after a song title, read as a
                # letter on however many frames: the glyph is the proof.
                if glyph_pass:
                    if len(complete)>=1 and note_misread(a,b):candidates.append(strong)
                    continue
                if len(complete)<3:continue
                # One frame missing a character or two of the repeated name
                # (the fading first or last frame of the receipt); a spelling
                # repeated on two frames is an alternative, not a fade.
                if (len(observed)==1 and complete[0]-250<=observed[0]<=complete[-1]+250
                        and missing_chars(a,b)):candidates.append(strong)
                # The closing quote read as a stray glyph after the name on fewer
                # frames than the name was read whole ('Present March D').
                elif (len(observed)<len(complete) and b.startswith(a) and 1<=len(b)-len(a)<=2
                        and len(b[len(a):].strip())==1):candidates.append(strong)
            if len(candidates)!=1:continue
            target=candidates[0]
            target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
            target['name_resolution']='repeated_complete_name_with_one_overlapping_or_adjacent_missing_character_observation'
            # The folded spelling's frames are this song's frames.
            key='song_learned||'+target['name'];proofs=event['field_evidence'].setdefault(key,[])
            proofs.extend(p for p in event['field_evidence'].get('song_learned||'+weak['name'],[]) if p not in proofs)
            removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]
