"""Promote accepted typed preview facts into explicit preview observations.

Preview panels are useful evidence, but they do not prove that an action was
committed or that an effect was applied.  This module is deliberately a small
boundary between the parsers and the report adapters:

* only already typed facts in an explicit preview collection are considered;
* raw OCR text, candidate collections, and state differences are ignored;
* repeated frames are coalesced only when their option/context and typed
  payload agree over a short, contiguous span;
* a conflicting typed amount is retained as an ambiguity and is not promoted.

Lesson card costs are joined from the parser's source-bound
``facts.lesson_offer_preview`` envelope onto the already accepted lesson
occurrence.  The envelope is not a second occurrence stream: each offer is
matched by its typed ``offer_id`` and only accepted price slots are copied.
Missing or conflicting slots stay absent (unknown), including a visible zero
that has no accepted price proof.

The returned observations use the shared observation-evaluation shape.  Their
``phase`` is always ``preview`` and their ``category`` is ``effect`` or
``purchase``.  Nothing returned by this module is an applied effect or a
committed action.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

from .reconcile import FIELDS
from .stats import BOXES


SCHEMA = "tracen-replay/preview-observations-v1"

STAT_FIELDS = frozenset(("speed", "stamina", "power", "guts", "wit", "skill_points"))
PERFORMANCE_FIELDS = frozenset(("dance", "passion", "vocal", "visual", "composure"))

# These are parser-owned, typed fact fields.  In particular, ``ocr`` and any
# field containing ``candidate`` are intentionally absent from this list.
PREVIEW_FACT_KEYS = (
    "preview_effects",
    "training_preview_effects",
    "preview_overlay_effects",
    "training_preview_overlay_effects",
    # Song/concert modifiers are a separate typed channel.  They are not
    # ordinary stat changes because a visible modifier row is still a menu
    # projection and has not been awarded or applied.
    "preview_modifier_effects",
    "training_preview_modifier_effects",
    "projected_effects",
    "available_effects",
)

# ``preview_overlay_effects`` is populated only by :func:`parse_preview_overlay`.
# The marker is separate from the list so a result reader cannot accidentally
# make a committed frame eligible merely by leaving an empty collection.
PREVIEW_OVERLAY_FACT_KEYS = frozenset(
    ("preview_overlay_effects", "training_preview_overlay_effects")
)
PREVIEW_MODIFIER_FACT_KEYS = frozenset(
    ("preview_modifier_effects", "training_preview_modifier_effects")
)

_LESSON_SCREENS = frozenset(("lesson_selection", "lesson_confirmation"))
_SKILL_SCREENS = frozenset(("skill_selection", "skill_confirmation"))
_PREVIEW_SCREENS = _LESSON_SCREENS | _SKILL_SCREENS | frozenset(("training_preview",))
# A frame classifier can remain undecided while a typed parser has already
# proven the browse-menu geometry.  ``unknown`` is the only such classifier
# value that may use that proof; other unrecognized screens stay rejected.
_UNKNOWN_PREVIEW_SCREENS = frozenset(("unknown",))
_PREVIEW_CONTROL_NAMES = frozenset((
    "turf", "dirt", "incline", "bunny-hop", "bunny hop", "breaststroke",
))
_APPLIED_SCREENS = frozenset(
    (
        "training_result",
        "event_outcome",
        "race_result",
        "skill_receipt",
        "career_summary",
        "career_finish_confirmation",
    )
)

_SEMANTIC_ALIASES = frozenset(
    (
        "lesson_offer",
        "lesson_preview",
        "skill_offer",
        "skill_preview",
        "skill_hint",
        "typed_preview",
    )
)


# The gameplay crop is fixed at 810x1080 with its left edge at x=148 in the
# source frame.  Reusing the stat reader's boxes makes the numeric boundary a
# layout rule, rather than a value or timestamp learned from one recording.
# Typed geometry arriving from another parser is still untrusted input.  Keep
# the pane bounds here so an ordered box cannot manufacture a phase proof from
# coordinates outside the gameplay crop.
_GAMEPLAY_PANE_BOUNDS = (148.0, 0.0, 958.0, 1080.0)
_STAT_LABELS = {
    "speed": frozenset(("speed",)),
    "stamina": frozenset(("stamina",)),
    "power": frozenset(("power",)),
    "guts": frozenset(("guts",)),
    "wit": frozenset(("wit",)),
    "skill_points": frozenset(("skill pts", "skill points", "skillpts")),
}
_STAT_COLUMN_BOXES = dict(zip(FIELDS, BOXES))
_PERFORMANCE_LABELS = {
    "dance": frozenset(("da", "dance")),
    "passion": frozenset(("pa", "passion")),
    "vocal": frozenset(("vo", "vocal")),
    "visual": frozenset(("vi", "visual")),
    "composure": frozenset(("co", "composure")),
}
_SIGNED_AMOUNT_RE = re.compile(r"^\+\s*(\d{1,4})\s*[.,]?$")
_CURRENT_PROJECTED_RE = re.compile(r"^(\d{1,4})\s*\+\s*(\d{1,4})\s*[.,]?$")
_TRAINING_OPTION_RE = re.compile(
    r"^(Speed|Stamina|Power|Guts|Wit)\s+Lv[lI1]\s*\d{1,2}$", re.I
)
_PERFORMANCE_HEADER_RE = re.compile(r"^performance$", re.I)
_POINTS_HEADER_RE = re.compile(r"^points$", re.I)

# The performance sidebar reader keeps a typed provenance record even when a
# merged current+projection OCR line falls below its balance-value confidence
# floor.  A preview can use that record only after the same source has already
# proven a browse menu.  This floor is the parser's normal preview floor; it
# is a quality boundary, not a value learned from any recording.
_PERFORMANCE_PANEL_PREVIEW_MIN_CONFIDENCE = 90.0
_PERFORMANCE_PANEL_PREVIEW_STATUSES = frozenset((
    "resolved_merged_panel_value",
    "resolved_separate_panel_values",
    "resolved_separate_component_panel_values",
    "unresolved_low_confidence_merged_panel_value",
))

# A dedicated numeric reader may need to re-read a translucent menu after the
# fast reader has classified the same pixels as a result grid.  The reader
# sends that positive phase evidence through this small typed boundary.  The
# preview producer never treats ``result_grid`` or ``inspection`` as a menu
# proof on its own, since both are capture metadata and can be stale during a
# transition animation.
_PREVIEW_PANEL_KEYS = ("preview_panel", "training_preview_panel")
_PREVIEW_PHASE_PROOF_KEYS = (
    "preview_phase_proof",
    "training_preview_phase_proof",
)

# These are the phase-proof schemas emitted by the preview readers.  A phase
# proof is an authority boundary: a caller cannot make an arbitrary string
# authoritative simply by putting it in ``basis``.  The two short names at
# the end are retained for the v1 typed-panel/unknown-screen compatibility
# fixtures; they are still accepted only as explicit parser fields, never as
# free-form effect metadata.
_KNOWN_PREVIEW_PHASE_BASES = frozenset((
    "typed_projected_performance_menu_region",
    "current_training_grid_and_failure_badge",
    "current_training_grid",
    "training_menu_failure_badge_and_controls",
    "career_training_menu_performance_panel",
    "current_grid_and_fixed_failure_and_training_control",
    "current_grid_and_source_failure_and_training_control",
    "current_grid_and_training_header_and_option",
    "source_training_failure_badge_and_preview_row_geometry",
    "performance_panel_current_plus_projected_geometry",
    "accepted_current_menu_geometry",
    "source_menu_geometry",
))

_PHASE_GEOMETRY_STRUCTURE_KEYS = frozenset((
    "layout", "stat_row_band", "label_band", "stat_rows", "failure",
    "song_modifier", "rows", "cards", "controls", "menu_controls",
    "control_proof", "failure_regions", "option", "header", "evidence",
    "source_evidence", "source_references",
))
_KNOWN_PREVIEW_GEOMETRY_LAYOUTS = frozenset((
    "gameplay_crop_stat_cards",
))
_RESULT_MARKER_KEYS = frozenset((
    "result_proven",
    "training_result_proven",
    "result_marker",
    "result_marker_visible",
    "success_visible",
))


def _phase_finite_number(value: Any) -> bool:
    """Return whether a geometry scalar is finite and not a boolean."""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _phase_box(value: Any) -> bool:
    """Validate a four-coordinate box used by a typed menu proof."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if not all(_phase_finite_number(item) for item in value):
        return False
    try:
        left, top, right, bottom = (float(item) for item in value)
    except (OverflowError, TypeError, ValueError):
        return False
    pane_left, pane_top, pane_right, pane_bottom = _GAMEPLAY_PANE_BOUNDS
    return (
        pane_left <= left < right <= pane_right
        and pane_top <= top < bottom <= pane_bottom
    )


def _phase_band(value: Any) -> bool:
    """Validate a two-coordinate vertical/horizontal band."""

    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    if not all(_phase_finite_number(item) for item in value):
        return False
    try:
        first, second = (float(item) for item in value)
    except (OverflowError, TypeError, ValueError):
        return False
    # Two-coordinate bands are emitted for vertical stat/label spans.  Their
    # axis is not encoded in the compact proof, but both endpoints still must
    # lie inside the source frame rather than in fabricated coordinates.
    return _GAMEPLAY_PANE_BOUNDS[1] <= first < second <= _GAMEPLAY_PANE_BOUNDS[3]


def _preview_geometry_is_valid(value: Any) -> bool:
    """Validate the small geometry schema attached to an explicit proof.

    Geometry is deliberately structural.  It must contain at least one
    finite, ordered box/band and one key emitted by a preview parser.  This
    rejects ``{}`` and arbitrary metadata while remaining compatible with the
    parser's nested stat-row, failure-badge, modifier, and control shapes.
    """

    if not isinstance(value, dict) or not value:
        return False
    layout = value.get("layout")
    if (
        not isinstance(layout, str)
        or _text(layout) not in _KNOWN_PREVIEW_GEOMETRY_LAYOUTS
    ):
        return False
    structural = False
    coordinate = False
    malformed = False

    def visit(node: Any, key: str | None = None) -> None:
        nonlocal structural, coordinate, malformed
        if isinstance(node, dict):
            for raw_key, child in node.items():
                if not isinstance(raw_key, str):
                    malformed = True
                    continue
                child_key = raw_key.casefold()
                if child_key in _PHASE_GEOMETRY_STRUCTURE_KEYS:
                    structural = True
                if child_key == "layout":
                    if (
                        not isinstance(child, str)
                        or _text(child) not in _KNOWN_PREVIEW_GEOMETRY_LAYOUTS
                    ):
                        malformed = True
                    continue
                if child_key in {
                    "box", "rect", "bounds"
                } or child_key.endswith("_box") or child_key.endswith("_bounds"):
                    if not _phase_box(child):
                        malformed = True
                    else:
                        coordinate = True
                    continue
                if child_key == "boxes":
                    if not isinstance(child, (list, tuple)) or not child:
                        malformed = True
                    else:
                        for box in child:
                            if not _phase_box(box):
                                malformed = True
                            else:
                                coordinate = True
                    continue
                if (
                    child_key in {"band", "stat_row_band", "label_band"}
                    or child_key.endswith("_band")
                ):
                    # The stat/label bands are [top, bottom]; a panel band
                    # is [left, top, right, bottom].  Both are parser-owned
                    # shapes and must be ordered and finite.
                    valid = _phase_band(child) or _phase_box(child)
                    if not valid:
                        malformed = True
                    else:
                        coordinate = True
                    continue
                visit(child, child_key)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child, key)

    visit(value)
    return structural and coordinate and not malformed


def _phase_proof_is_candidate(value: dict[str, Any]) -> tuple[dict[str, Any] | None, bool, bool]:
    """Sanitize one explicit phase proof.

    The tuple is ``(candidate, invalid, result_conflict)``.  Invalid positive
    proofs are surfaced to the caller instead of being silently ignored when
    another proof happens to be present.
    """

    # Every explicitly supplied marker is a strict boolean.  Python's normal
    # equality would otherwise let ``1``, ``"true"`` or ``{}` slip through as
    # a harmless-looking absent marker.
    for key in _RESULT_MARKER_KEYS | frozenset(("menu_proven",)):
        if key in value and type(value[key]) is not bool:
            return None, True, False
    result_conflict = any(
        value.get(key) is True
        for key in _RESULT_MARKER_KEYS
    )
    if result_conflict:
        return None, True, True
    if value.get("menu_proven") is not True:
        return None, False, False
    phase = value.get("phase")
    if phase is not None and (
        not isinstance(phase, str) or _text(phase) != "preview"
    ):
        return None, True, False

    basis_present = "basis" in value or "geometry_basis" in value
    basis_value = value.get("basis") if "basis" in value else value.get("geometry_basis")
    if basis_present and not isinstance(basis_value, str):
        return None, True, False
    basis = _text(basis_value)
    geometry_present = "geometry" in value or "menu_geometry" in value
    geometry = value.get("geometry") if "geometry" in value else value.get("menu_geometry")
    if geometry_present and not _preview_geometry_is_valid(geometry):
        return None, True, False
    if basis is None and not geometry_present:
        return None, True, False
    # A proof cannot register a new schema by supplying an arbitrary geometry
    # dictionary.  Both basis-only and geometry-bearing proofs must identify
    # a parser-owned schema; geometry is an additional shape/bounds check.
    if basis is not None and basis not in _KNOWN_PREVIEW_PHASE_BASES:
        return None, True, False
    if basis is None and geometry_present:
        return None, True, False

    proof = {
        "phase": "preview",
        "menu_proven": True,
        "result_proven": False,
        "basis": basis or "typed_preview_menu_geometry",
    }
    if geometry_present:
        proof["geometry"] = deepcopy(geometry)
    option = _canonical_option(value.get("option", value.get("option_label")))
    if option:
        proof["option"] = option
    proof_paths: list[str] = []
    proof_references: list[str] = []
    for key in ("evidence", "source_evidence"):
        paths, references = _split_source_provenance(value.get(key))
        proof_paths.extend(paths)
        proof_references.extend(references)
    if proof_paths:
        proof["evidence"] = list(dict.fromkeys(proof_paths))
    if proof_references:
        proof["source_references"] = list(dict.fromkeys(proof_references))
    return proof, False, False

_OPTION_NAMES = frozenset(STAT_FIELDS - frozenset(("skill_points",)))


def _canonical_option(value: Any) -> str | None:
    """Normalize a typed training option without accepting arbitrary text."""

    if isinstance(value, dict):
        value = value.get("option", value.get("label", value.get("text")))
    value = _text(value)
    if not value:
        return None
    normalized = value.casefold()
    if normalized in _OPTION_NAMES:
        return normalized
    match = _TRAINING_OPTION_RE.fullmatch(value)
    return match[1].casefold() if match else None


def _preview_panel(source: dict[str, Any]) -> dict[str, Any] | None:
    for key in _PREVIEW_PANEL_KEYS:
        panel = source.get(key)
        if isinstance(panel, dict):
            return panel
    return None


def _explicit_phase_proof(source: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicitly accepted menu proof, if one is present.

    This deliberately requires both a positive menu marker and a declared
    geometry basis.  A boolean copied from an inspection label is not enough
    to establish a browse phase for the preview parser.  A result marker on
    the same proof is a conflict and causes abstention rather than choosing
    the more convenient interpretation.
    """

    for key in _RESULT_MARKER_KEYS:
        if key in source and type(source[key]) is not bool:
            return None
        if source.get(key) is True:
            return None
    panel = _preview_panel(source)
    candidates: list[dict[str, Any]] = []
    invalid = False
    result_conflict = False
    for value in (
        *(source.get(key) for key in _PREVIEW_PHASE_PROOF_KEYS),
        panel,
    ):
        if not isinstance(value, dict):
            continue
        candidate, value_invalid, value_result_conflict = _phase_proof_is_candidate(value)
        invalid = invalid or value_invalid
        result_conflict = result_conflict or value_result_conflict
        if candidate is not None:
            candidates.append(candidate)

    # A malformed positive proof or a competing result declaration cannot be
    # outweighed by a second, more convenient menu proof.  Abstain before
    # comparing candidates so stale panel metadata cannot win by ordering.
    if invalid or result_conflict:
        return None

    if len(candidates) > 1:
        signatures = {
            json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            for candidate in candidates
        }
        if len(signatures) > 1:
            return None
    if not candidates:
        return None

    # A typed panel captured by ``read_training`` can retain a fading menu
    # heading and Failure badge after the fixed result probes have classified
    # the same pixels as a result grid.  One image cannot prove that the user
    # is still browsing, so a stale panel must not bypass the applied-action
    # boundary.  A future source-bound temporal recovery record can establish
    # that distinction explicitly; this parser does not invent one.
    if source.get("result_grid") is True:
        return None
    return candidates[0]


def _explicit_phase_record_present(source: dict[str, Any]) -> bool:
    """Return whether raw input contains an explicit phase record.

    A malformed positive panel must not disappear and let the heuristic
    ``current_grid`` fallback promote the same frame.  This predicate only
    looks for fields that belong to the typed phase contract; an ordinary
    raw panel containing just OCR/effect data remains eligible for the normal
    source-geometry reader.
    """

    phase_keys = frozenset((
        "phase", "menu_proven", "basis", "geometry_basis", "geometry",
        "menu_geometry",
    )) | _RESULT_MARKER_KEYS
    for value in (
        *(source.get(key) for key in _PREVIEW_PHASE_PROOF_KEYS),
        _preview_panel(source),
    ):
        if isinstance(value, dict) and any(key in value for key in phase_keys):
            return True
    return False


def _explicit_result_marker(source: dict[str, Any]) -> bool:
    """Recognize only positive committed-result evidence from a raw source."""

    panel = _preview_panel(source)
    values = [source.get(key) for key in _RESULT_MARKER_KEYS]
    values.extend(source.get(key) for key in _PREVIEW_PHASE_PROOF_KEYS)
    if panel is not None:
        values.append(panel)
    for key in _RESULT_MARKER_KEYS:
        if key in source and type(source[key]) is not bool:
            return True
    for value in values:
        if isinstance(value, dict):
            for key in _RESULT_MARKER_KEYS:
                if key not in value:
                    continue
                if type(value[key]) is not bool:
                    return True
                if value[key] is True:
                    return True
        elif type(value) is bool and value is True:
            return True
    return False


def _raw_menu_controls(lines: list[dict[str, Any]], minimum_confidence: float) -> int:
    """Count bottom training controls using their fixed menu band."""

    names = _OPTION_NAMES
    count = set()
    for line in lines:
        if _confidence(line) < minimum_confidence:
            continue
        text = _line_text(line)
        box = _line_box(line)
        if not text or box is None:
            continue
        x, y = _center(box)
        if not (850 <= y <= 1010 and 120 <= x <= 850):
            continue
        normalized = text.casefold().replace(" ", "")
        for name in names:
            if normalized == name or normalized.startswith(name + "lvl"):
                count.add(name)
    return len(count)


def _raw_navigation_controls(lines: list[dict[str, Any]], minimum_confidence: float) -> int:
    """Count the lower navigation controls retained on Career transition frames."""

    names = frozenset(("infirmary", "recreation", "lessons", "races", "rest", "skills"))
    count = set()
    for line in lines:
        if _confidence(line) < minimum_confidence:
            continue
        text = _line_text(line)
        box = _line_box(line)
        if not text or box is None:
            continue
        _x, y = _center(box)
        if not 850 <= y <= 1010:
            continue
        normalized = re.sub(r"[^a-z]", "", text.casefold())
        if normalized in names:
            count.add(normalized)
    return len(count)


def _raw_failure_popup(lines: list[dict[str, Any]], minimum_confidence: float) -> bool:
    for line in lines:
        if _confidence(line) < minimum_confidence or _line_text(line) != "Failure":
            continue
        box = _line_box(line)
        if box is None:
            continue
        left, top, right, bottom = box
        if 250 <= left < right <= 850 and 750 <= top < bottom <= 850:
            return True
    return False


def _raw_performance_structure(lines: list[dict[str, Any]], minimum_confidence: float) -> bool:
    """Detect the persistent Performance Points panel without reading values."""

    headers = set()
    labels = set()
    for line in lines:
        if _confidence(line) < minimum_confidence:
            continue
        text = _line_text(line)
        box = _line_box(line)
        if not text or box is None:
            continue
        x, y = _center(box)
        if _PERFORMANCE_HEADER_RE.fullmatch(text) and 135 <= x <= 280 and 235 <= y <= 305:
            headers.add("performance")
        if _POINTS_HEADER_RE.fullmatch(text) and 150 <= x <= 280 and 255 <= y <= 325:
            headers.add("points")
        field = _canonical_label(text, _PERFORMANCE_LABELS)
        if field and 135 <= x <= 205 and 280 <= y <= 590:
            labels.add(field)
    return headers == {"performance", "points"} and bool(labels)


def _raw_menu_phase_proof(
    source: dict[str, Any], lines: list[dict[str, Any]],
    regions: dict[str, Any], option: str | None,
    minimum_confidence: float,
) -> dict[str, Any] | None:
    """Infer browse phase only from positive menu layout evidence.

    ``current_grid`` remains a useful fast-path when it is not accompanied by
    result evidence.  The fallback looks for the failure badge and multiple
    lower training controls; this covers transition frames where the color
    probe misses the current stat grid.  Signed rows alone never establish the
    phase, which is what prevents a fading SUCCESS overlay from becoming a
    browse preview.
    """

    explicit = _explicit_phase_proof(source)
    explicit_result = _explicit_result_marker(source)
    # A large SUCCESS/SU banner is positive result evidence even when the
    # fast metadata flags were not populated.  It wins over a stale current
    # grid and prevents both ordinary and song preview rows from leaking out.
    if _preview_success_banner_line(lines, minimum_confidence) is not None:
        return None
    if explicit_result and explicit is not None:
        return None
    if explicit is not None:
        if option and "option" not in explicit:
            explicit["option"] = option
        return explicit
    # Do not fall back to the heuristic current-grid path after a typed phase
    # record was supplied but failed validation.  Otherwise a malformed
    # marker or fabricated panel could be promoted merely because a stale
    # ``current_grid`` flag happened to be present.
    if _explicit_phase_record_present(source):
        return None

    # A dedicated projected-performance crop is produced only by the lesson
    # / training-menu reader.  Its typed region name is stronger than a stale
    # result_grid flag, provided no positive result marker is present.
    projected_regions = [
        name for name in regions
        if isinstance(name, str) and name.startswith("projected_performance.")
    ]
    if projected_regions and not explicit_result and source.get("result_grid") is not True:
        return {
            "phase": "preview", "menu_proven": True, "result_proven": False,
            "basis": "typed_projected_performance_menu_region",
            "option": option,
            "stat_row_proven": False,
        }

    header = _text(source.get("header")) or ""
    training_header = header.casefold().startswith(("training", "career"))
    current_grid = source.get("current_grid") is True
    result_grid = source.get("result_grid") is True
    if (header.casefold().startswith("training") and current_grid
            and not result_grid
            and _raw_failure_popup(lines, minimum_confidence)
            and not explicit_result):
        return {
            "phase": "preview", "menu_proven": True, "result_proven": False,
            "basis": "current_training_grid_and_failure_badge", "option": option,
            "stat_row_proven": True,
        }
    if (header.casefold().startswith("training") and current_grid
            and not result_grid and not explicit_result):
        return {
            "phase": "preview", "menu_proven": True, "result_proven": False,
            "basis": "current_training_grid", "option": option,
            "stat_row_proven": header.casefold().startswith("training"),
        }

    # ``read_training`` reports fixed result crops as
    # ``result_grid=True``/``current_grid=False``.  A fading option heading
    # and Failure badge can remain in the same image, but those remnants do
    # not establish a browsable menu.  Without an explicit source-bound phase
    # proof, signed rows from this transition stay with result processing.

    # Some dense transition frames are labeled Career and have an intact
    # performance panel while the training buttons are already fading in.  The
    # panel is usable for projected performance, but the overlaid stat rows are
    # deliberately not treated as a stat preview without a current-grid proof.
    if training_header and _raw_performance_structure(lines, minimum_confidence):
        controls = _raw_menu_controls(lines, minimum_confidence)
        if (_raw_failure_popup(lines, minimum_confidence) and controls >= 2
                and not result_grid and not explicit_result):
            return {
                "phase": "preview", "menu_proven": True, "result_proven": False,
                "basis": "training_menu_failure_badge_and_controls",
                "option": option, "stat_row_proven": False,
            }
        if (header.casefold().startswith("career") and current_grid and not result_grid
                and _raw_navigation_controls(lines, minimum_confidence) >= 2):
            return {
                "phase": "preview", "menu_proven": True, "result_proven": False,
                "basis": "career_training_menu_performance_panel", "option": option,
                "stat_row_proven": False,
            }
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value or None


def _line_box(line: Any) -> tuple[float, float, float, float] | None:
    """Return a finite OCR box without repairing malformed coordinates."""

    if not isinstance(line, dict):
        return None
    box = line.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        values = tuple(float(value) for value in box)
    except (OverflowError, TypeError, ValueError):
        return None
    left, top, right, bottom = values
    if not (
        all(math.isfinite(value) for value in values)
        and left < right
        and top < bottom
    ):
        return None
    pane_left, pane_top, pane_right, pane_bottom = _GAMEPLAY_PANE_BOUNDS
    if not (
        pane_left <= left < right <= pane_right
        and pane_top <= top < bottom <= pane_bottom
    ):
        return None
    return values


def _line_text(line: Any) -> str | None:
    if not isinstance(line, dict):
        return None
    return _text(line.get("text"))


def _confidence(line: Any) -> float:
    if not isinstance(line, dict):
        return 0.0
    try:
        value = float(line.get("confidence", 0))
    except (OverflowError, TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and 0 <= value <= 100 else 0.0


def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _canonical_label(value: Any, aliases: dict[str, frozenset[str]]) -> str | None:
    value = _text(value)
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value.casefold())
    for field, choices in aliases.items():
        if normalized in choices:
            return field
    return None


# Values such as ``line:13`` and ``region:performance_gain.vocal`` identify an
# OCR/detector record inside one source frame.  They are useful for explaining
# how a typed effect was produced, but they are not source files.  The worker's
# evidence contract treats every item in ``source_evidence`` as a path, so
# mixing these namespaces makes an otherwise valid report fail validation.
# Keep the reference vocabulary deliberately small and explicit: unknown
# strings remain source identifiers for backwards compatibility with callers
# that use abstract, path-like evidence IDs.
_SOURCE_REFERENCE_PREFIXES = frozenset({
    "box", "candidate", "frame", "index", "line", "ocr", "region",
    "source_index", "source_region", "timestamp",
})


def _is_source_reference(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    # A Windows drive letter is a path, even though it contains a colon.
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return False
    prefix, separator, _rest = text.partition(":")
    return bool(separator and prefix.casefold() in _SOURCE_REFERENCE_PREFIXES)


def _split_source_provenance(values: Any) -> tuple[list[str], list[str]]:
    """Split physical evidence paths from typed geometry references."""

    paths: list[str] = []
    references: list[str] = []
    for value in _strings(values):
        (references if _is_source_reference(value) else paths).append(value)
    return list(dict.fromkeys(paths)), list(dict.fromkeys(references))


def _evidence_paths(values: Any) -> list[str]:
    return _split_source_provenance(values)[0]


def _evidence_references(values: Any) -> list[str]:
    return _split_source_provenance(values)[1]


def _source_evidence(source: Any, explicit: Any = None) -> list[str]:
    """Read physical source paths; generated geometry stays in metadata."""

    values = _strings(explicit)
    if isinstance(source, dict):
        values.extend(_strings(source.get("evidence")))
    return _evidence_paths(values)


def _training_marker(source: dict[str, Any], lines: list[dict[str, Any]],
                     regions: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
    """Require a gameplay header and an explicit/typed training option."""

    header = _text(source.get("header"))
    header_line = None
    if not header:
        for index, line in enumerate(lines):
            text = _line_text(line)
            box = _line_box(line)
            if text and box and _center(box)[1] <= 55 and text.casefold().startswith("training"):
                header = text
                header_line = index
                break
    if not header or not header.casefold().startswith(("training", "career")):
        return False, None, {}

    option_line = None
    option = None
    option_region = regions.get("option") if isinstance(regions, dict) else None
    option_region_text = _line_text(option_region)
    if option_region_text:
        match = _TRAINING_OPTION_RE.fullmatch(option_region_text)
        if match and _confidence(option_region) >= 85:
            option = match[1].casefold()
            option_line = "regions.option"
    if option is None:
        for index, line in enumerate(lines):
            text = _line_text(line)
            if not text or _confidence(line) < 85:
                continue
            match = _TRAINING_OPTION_RE.fullmatch(text)
            if match:
                option = match[1].casefold()
                option_line = index
                break
    # A typed menu reader may have recovered the option while the OCR heading
    # is clipped or below its confidence floor.  This is accepted only from
    # the explicit preview-phase contract; arbitrary context/name candidates
    # are never consulted.
    if option is None:
        panel = _preview_panel(source)
        for candidate in (
            panel.get("option") if panel else None,
            panel.get("option_label") if panel else None,
        ):
            option = _canonical_option(candidate)
            if option:
                option_line = "typed_preview_panel"
                break
    if option is None:
        # Career transition frames can retain the complete performance menu
        # while the selected training heading has faded.  They are phase-safe
        # only when the raw layout independently proves a training menu.
        if header.casefold().startswith("career") and _raw_menu_phase_proof(
            source, lines, regions, None, 85
        ) is not None:
            return True, None, {"header": header, "header_line": header_line,
                                "option": None, "option_line": None}
        return False, None, {}
    return True, option, {"header": header, "header_line": header_line,
                          "option": option, "option_line": option_line}


def _parser_owned_preview_options(source: dict[str, Any], *,
                                  minimum_confidence: float) -> set[str]:
    """Collect selected-option declarations from the source parser.

    A preview recovery is allowed to fill a clipped numeric row, but it must
    not choose which training card was selected.  The OCR option line and the
    typed panel are therefore two independent declarations.  Menu cards in
    the lower gameplay area also carry option-like labels; only the fixed
    selected-option band is considered here so those cards do not manufacture
    a conflict.
    """

    options: set[str] = set()

    def add(value: Any) -> None:
        option = _canonical_option(value)
        if option is not None:
            options.add(option)

    panels = [_preview_panel(source)]
    facts = source.get("facts")
    if isinstance(facts, dict):
        panels.append(_preview_panel(facts))
    for panel in panels:
        if isinstance(panel, dict):
            add(panel.get("option"))
            add(panel.get("option_label"))

    regions = source.get("regions")
    if isinstance(regions, dict):
        option_region = regions.get("option")
        if isinstance(option_region, dict) and _confidence(option_region) >= minimum_confidence:
            add(option_region.get("text"))

    stats = source.get("stats")
    # These fields are emitted by the typed preview readers.  ``training_option``
    # is included because a fresh source row may not yet have the more specific
    # preview alias; the conflict gate still only compares canonical names.
    for value in (
        source.get("preview_option"),
        source.get("training_option"),
        facts.get("preview_option") if isinstance(facts, dict) else None,
        stats.get("preview_option") if isinstance(stats, dict) else None,
    ):
        add(value)

    # ``lines``/``preview_lines`` are the normal reader views.  Persisted
    # report rows retain the same source detector output under ``ocr.neural``.
    # Keep OCR option declarations tied to the source selection band; otherwise
    # every visible menu card would appear to be a competing selected option.
    collections: list[Any] = [source.get("preview_lines"), source.get("lines")]
    ocr = source.get("ocr")
    if isinstance(ocr, dict):
        collections.append(ocr.get("neural"))
    seen: set[int] = set()
    for collection in collections:
        if not isinstance(collection, (list, tuple)) or id(collection) in seen:
            continue
        seen.add(id(collection))
        for line in collection:
            if not isinstance(line, dict) or _confidence(line) < minimum_confidence:
                continue
            text = _line_text(line)
            box = _line_box(line)
            if text is None or box is None or _TRAINING_OPTION_RE.fullmatch(text) is None:
                continue
            center_x, center_y = _center(box)
            if 190 <= center_x <= 430 and 120 <= center_y <= 240:
                add(text)
    return options


def _parser_preview_option_conflict(source: dict[str, Any], *,
                                    minimum_confidence: float) -> bool:
    """Return whether parser-owned selected-option facts disagree."""

    return len(_parser_owned_preview_options(
        source, minimum_confidence=minimum_confidence
    )) > 1


def _column_for_x(x: float) -> str | None:
    """Map a signed amount to one stat column using layout overlap."""

    candidates = []
    for field, box in _STAT_COLUMN_BOXES.items():
        left, _top, right, _bottom = box
        # A glyph may be wider than the underlying value box while still
        # being unambiguously centered in that column.
        margin = max(18.0, (right - left) * 0.35)
        if left - margin <= x <= right + margin:
            center = (left + right) / 2
            candidates.append((abs(x - center), field))
    if not candidates:
        return None
    candidates.sort()
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1]


def _stat_label_lines(lines: list[dict[str, Any]], minimum_confidence: float):
    labels = {}
    for index, line in enumerate(lines):
        if _confidence(line) < minimum_confidence:
            continue
        field = _canonical_label(_line_text(line), _STAT_LABELS)
        box = _line_box(line)
        if field is None or box is None:
            continue
        _x, y = _center(box)
        # This is the persistent stat-card region.  The lower result cards
        # have different geometry and must not act as preview label anchors.
        if not 650 <= y <= 820:
            continue
        labels.setdefault(field, []).append((index, line, box))
    return labels


def _deduplicate_numeric_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate same-region OCR views while retaining disagreement."""

    unique = {}
    for candidate in candidates:
        box = tuple(round(float(value), 2) for value in candidate["box"])
        key = (candidate["field"], candidate["amount"], box,
               candidate.get("source_region"))
        unique.setdefault(key, candidate)
    return list(unique.values())


def _select_stat_preview_candidates(candidates: list[dict[str, Any]], labels,
                                    current_grid: bool, result_grid: bool):
    """Select the preview row without choosing a second applied row.

    Training result animation can contain two signed rows in one column.  A
    preview row is above the stat-card label; a lower row that overlaps the
    label belongs to the result animation and is left to the applied reader.
    If the panel is a normal current-grid preview, its sole row is accepted
    with the current-grid proof even when the glyph touches the label.  A
    multi-column stack is withheld because OCR geometry cannot distinguish
    scenario and training layers there; the typed numeric panel owns that
    decision.
    """

    selected = []
    rejected = Counter()
    all_label_tops = [entry[2][1] for entries in labels.values() for entry in entries]
    panel_label_top = min(all_label_tops) if all_label_tops else None
    panel_label_count = sum(len(entries) for entries in labels.values())
    by_field: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in _deduplicate_numeric_candidates(candidates):
        by_field[candidate["field"]].append(candidate)
    # One column may legitimately carry a scenario/song bonus above the
    # ordinary preview row.  The established row geometry handles that case
    # below.  When several columns carry different stacked amounts together,
    # however, the frame is a transition composite and there is no reliable
    # way to associate each row with its semantic layer from OCR boxes alone.
    # Require the numeric reader's typed panel for that multi-column case.
    stacked_fields = {
        field for field, values in by_field.items()
        if len({item["amount"] for item in values}) > 1
    }
    for field, values in by_field.items():
        values.sort(key=lambda item: (item["box"][1], item["box"][0]))
        label_top = None
        field_labels = labels.get(field, [])
        if field_labels:
            label_top = min(item[2][1] for item in field_labels)
        above = []
        anchor_top = label_top if label_top is not None else panel_label_top
        if current_grid and not result_grid and anchor_top is not None:
            # A current-grid probe proves that a menu is visible, but it does
            # not tell us which of two vertically stacked signed rows belongs
            # to the preview.  Grand Live transition frames can contain a
            # fading scenario row and the actual training row in one column;
            # choosing the row nearest the label would silently select the
            # wrong amount.  Keep the field unresolved until the numeric
            # reader supplies a typed panel with row semantics.  Repeated OCR
            # views of the same amount remain safe to collapse below.
            if field in stacked_fields and len(stacked_fields) >= 2:
                rejected["conflicting_current_preview_rows"] += 1
                continue
            # The ordinary training menu can show a song-bonus badge above
            # the actual training preview. Its signed number must not replace
            # the lower preview number beside the stat label. Use the label's
            # scale to identify that adjacent row; never choose by amount.
            anchor_labels = field_labels or [entry for entries in labels.values() for entry in entries]
            heights = sorted(entry[2][3] - entry[2][1] for entry in anchor_labels)
            label_height = heights[len(heights) // 2]
            adjacent = [candidate for candidate in values
                        if anchor_top - 1.25 * label_height <= _center(candidate['box'])[1]
                        <= anchor_top + 0.5 * label_height]
            if adjacent and len({candidate['amount'] for candidate in adjacent}) == 1:
                selected.append(adjacent[0])
            else:
                rejected['unresolved_current_preview_row'] += 1
            continue
        for candidate in values:
            bottom = candidate["box"][3]
            # A preview glyph is normally fully above the label.  A fading
            # single glyph can touch/overlap its only surviving label, which
            # is handled by the one-candidate fallback below.
            if anchor_top is not None and bottom <= anchor_top - 1:
                above.append(candidate)
        if above:
            # Two distinct amounts in the same above-label row are not
            # resolved by position alone.
            amounts = {item["amount"] for item in above}
            if len(amounts) != 1:
                rejected["conflicting_preview_row_amounts"] += 1
                continue
            selected.append(above[0])
            continue
        if len(values) == 1 and current_grid:
            selected.append(values[0])
            continue
        # A fading result overlay can have no surviving label, but a result
        # frame with one signed amount is still accepted only when a label or
        # result-grid marker proved the panel geometry.
        if len(values) == 1 and (field_labels or result_grid or panel_label_count == 1):
            # With several visible labels and no grid marker, a glyph that
            # overlaps a label is the lower result row.  Leave it to the
            # applied reader.  A single surviving label is the known fading
            # overlay case (for example during the first result animation).
            if (not current_grid and not result_grid and panel_label_count >= 2
                    and label_top is not None):
                rejected["unresolved_preview_row_geometry"] += 1
                continue
            selected.append(values[0])
            continue
        rejected["unresolved_preview_row_geometry"] += 1
    return selected, rejected


def _performance_panel_proof(lines: list[dict[str, Any]], minimum_confidence: float):
    header = False
    points = False
    labels = {}
    for index, line in enumerate(lines):
        text = _line_text(line)
        box = _line_box(line)
        if text is None or box is None or _confidence(line) < minimum_confidence:
            continue
        x, y = _center(box)
        if _PERFORMANCE_HEADER_RE.fullmatch(text) and 135 <= x <= 280 and 235 <= y <= 305:
            header = True
        if _POINTS_HEADER_RE.fullmatch(text) and 150 <= x <= 280 and 255 <= y <= 325:
            points = True
        field = _canonical_label(text, _PERFORMANCE_LABELS)
        if field and 135 <= x <= 205 and 280 <= y <= 590:
            labels.setdefault(field, []).append((index, line, box))
    return header and points and len(labels) >= 1, labels


def _make_preview_effect(field: str, amount: int, *, source_evidence: list[str],
                         basis: str, source_index: Any = None,
                         source_region: str | None = None,
                         box: tuple[float, float, float, float] | None = None,
                         source_references: Any = None):
    evidence_paths, inferred_references = _split_source_provenance(source_evidence)
    references = list(dict.fromkeys(
        inferred_references + _evidence_references(source_references)
    ))
    effect = {
        "kind": "stat_change" if field in STAT_FIELDS else "performance_change",
        "field": field,
        "amount": amount,
        "phase": "preview",
        "preview": True,
        "awarded": False,
        "source_evidence": evidence_paths,
        "source_semantics": "typed_training_preview_overlay",
        "preview_geometry": {"basis": basis},
    }
    if references:
        effect["preview_geometry"]["source_references"] = references
    if source_index is not None:
        effect["preview_geometry"]["source_index"] = source_index
    if source_region is not None:
        effect["preview_geometry"]["source_region"] = source_region
    if box is not None:
        effect["preview_geometry"]["box"] = [
            int(value) if float(value).is_integer() else float(value) for value in box
        ]
    return effect


# The fast training-result reader intentionally concentrates on the result
# cards at the bottom of the gameplay crop.  A selected training menu is still
# visible above those cards while the result classifier is running.  This
# bounded producer consumes only detector lines from that source crop and
# turns the rows into a typed panel for :func:`parse_preview_overlay`.
#
# The coordinates describe the stable gameplay layout, not a recording or a
# particular value.  In particular, the producer never reads a total and never
# computes a difference from a later result card.
_SOURCE_PREVIEW_STAT_Y = (600.0, 780.0)
_SOURCE_PREVIEW_LABEL_Y = (680.0, 790.0)


def _preview_source_lines(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Return source detector lines without trusting derived OCR collections."""

    values = source.get("preview_lines")
    if not isinstance(values, (list, tuple)):
        values = source.get("lines")
    # Serialized gameplay readings keep the immutable detector pass under
    # ``ocr.neural`` after transient top-level fields have been projected out.
    # Reuse that exact source collection for cached parsing; do not consult
    # report effects, candidates, or state-derived fields as a fallback.
    if not isinstance(values, (list, tuple)):
        ocr = source.get("ocr")
        if isinstance(ocr, dict):
            values = ocr.get("neural")
    return [line for line in values if isinstance(line, dict)] if isinstance(values, (list, tuple)) else []


def _preview_menu_failure_line(lines: list[dict[str, Any]], minimum_confidence: float):
    """Find the selected-training failure badge, including a clipped suffix."""

    for line in lines:
        if _confidence(line) < minimum_confidence:
            continue
        text = (_line_text(line) or "").casefold()
        # The badge is occasionally recognized as ``Failurer``/``Failure.``
        # when its rounded right edge is covered by a support card.  Accept a
        # suffix only after the complete fixed badge word is present.
        if not re.fullmatch(r"failure[.!r]*", text):
            continue
        box = _line_box(line)
        if box is None:
            continue
        left, top, right, bottom = box
        if 240 <= left < right <= 850 and 735 <= top < bottom <= 850:
            return line
    return None


def _preview_song_modifier_marker(
    lines: list[dict[str, Any]], minimum_confidence: float,
) -> dict[str, Any] | None:
    """Find the source-visible Concert Bonuses marker.

    The game renders this marker as either one OCR line or two stacked lines
    on the left edge of the training cards.  It is the semantic evidence that
    an upper signed row is a song modifier.  A signed amount by itself is not
    enough: result animations and ordinary training cards can contain the
    same glyph shape.

    The marker confidence floor is slightly below the normal numeric floor so
    a partially translucent heading can still prove its meaning.  Geometry,
    exact marker vocabulary, and a same-source row pair remain required.
    """

    marker_floor = max(80.0, float(minimum_confidence) - 10.0)
    candidates: list[tuple[int, str, tuple[float, float, float, float], str]] = []
    for index, line in enumerate(lines):
        if _confidence(line) < marker_floor:
            continue
        box = _line_box(line)
        text = _line_text(line)
        if box is None or text is None:
            continue
        left, top, right, bottom = box
        center_x, center_y = _center(box)
        if not (135 <= center_x <= 360 and 520 <= center_y <= 680):
            continue
        normalized = re.sub(r"[^a-z]+", " ", text.casefold()).strip()
        # The left edge of the Bonuses heading is occasionally duplicated by
        # OCR (``BBonuses``).  Keep this bounded to one repeated leading
        # letter and the same marker geometry; arbitrary text suffixes must
        # remain non-evidence.
        if normalized == "bbonuses":
            normalized = "bonuses"
        if normalized not in {"concert", "bonuses", "concert bonuses"}:
            continue
        candidates.append((index, normalized, box, text))

    if not candidates:
        return None

    combined = [item for item in candidates if item[1] == "concert bonuses"]
    if combined:
        # Multiple OCR views of the exact same line are corroboration.  If
        # they disagree in geometry, abstain rather than merging unrelated
        # marker instances from the same timestamp.
        boxes = {tuple(round(value, 2) for value in item[2]) for item in combined}
        if len(boxes) != 1:
            return None
        index, _text_value, box, text = combined[0]
        return {
            "form": "combined",
            "indices": [index],
            "source_references": [f"line:{index}"],
            "boxes": [list(box)],
            "text": text,
        }

    concerts = [item for item in candidates if item[1] == "concert"]
    bonuses = [item for item in candidates if item[1] == "bonuses"]
    pairs: list[tuple[float, tuple[Any, ...]]] = []
    for concert in concerts:
        for bonus in bonuses:
            concert_box = concert[2]
            bonus_box = bonus[2]
            concert_center = _center(concert_box)
            bonus_center = _center(bonus_box)
            vertical_gap = abs(concert_center[1] - bonus_center[1])
            horizontal_gap = abs(concert_center[0] - bonus_center[0])
            if vertical_gap > 85 or horizontal_gap > 130:
                continue
            # The UI stacks Concert above Bonuses.  Allow a small OCR-order
            # inversion but reject a pair whose boxes are effectively on the
            # same row, since that is more likely unrelated text.
            if bonus_center[1] - concert_center[1] < -12:
                continue
            score = vertical_gap + 0.25 * horizontal_gap
            pairs.append((score, (concert, bonus)))
    if not pairs:
        return None
    pairs.sort(key=lambda item: (item[0], item[1][0][0], item[1][1][0]))
    if len(pairs) > 1 and abs(pairs[0][0] - pairs[1][0]) < 1e-6:
        return None
    concert, bonus = pairs[0][1]
    return {
        "form": "split",
        "indices": [concert[0], bonus[0]],
        "source_references": [f"line:{concert[0]}", f"line:{bonus[0]}"],
        "boxes": [list(concert[2]), list(bonus[2])],
        "text": f"{concert[3]} {bonus[3]}",
    }


def _preview_success_banner_line(lines: list[dict[str, Any]], minimum_confidence: float):
    """Return a large SUCCESS/SU banner that makes a frame result evidence."""

    for line in lines:
        if _confidence(line) < minimum_confidence:
            continue
        text = re.sub(r"\s+", "", (_line_text(line) or "").casefold())
        if not text.startswith(("su", "success", "friendshiptraining")):
            continue
        box = _line_box(line)
        if box is None:
            continue
        left, top, right, bottom = box
        # A normal stat glyph is narrow and cannot satisfy this area test.  A
        # result banner remains large even when OCR returns only ``SU``.
        if top >= 560 and right - left >= 160 and bottom - top >= 70:
            return line
    return None


def _preview_stat_candidates_from_lines(
    lines: list[dict[str, Any]], minimum_confidence: float,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Collect signed stat rows and label anchors from source geometry."""

    labels = _stat_label_lines(lines, minimum_confidence)
    by_field: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, line in enumerate(lines):
        if _confidence(line) < minimum_confidence:
            continue
        text = _line_text(line)
        box = _line_box(line)
        if text is None or box is None:
            continue
        match = _SIGNED_AMOUNT_RE.fullmatch(text)
        if not match:
            continue
        _x, y = _center(box)
        if not (_SOURCE_PREVIEW_STAT_Y[0] <= y <= _SOURCE_PREVIEW_STAT_Y[1]):
            continue
        field = _column_for_x(_x)
        if field is None:
            continue
        by_field[field].append({
            "field": field,
            "amount": int(match[1]),
            "box": box,
            "source_index": index,
            "source_references": [f"line:{index}"],
        })
    return by_field, labels


def _preview_source_views_conflict(
    source: dict[str, Any], minimum_confidence: float,
) -> bool:
    """Detect a disagreement between two same-frame OCR views.

    ``preview_lines`` is a denser detector pass, while legacy ``lines`` may be
    a small result-reader view.  Neither list is inherently newer or more
    authoritative.  If both contain different signed values for the same
    field and overlapping source geometry, the parser must wait for the
    source-bound crop consensus in ``preview_recovery`` instead of preferring
    whichever list happened to be selected first.
    """

    full = source.get("preview_lines")
    legacy = source.get("lines")
    if not isinstance(full, (list, tuple)) or not isinstance(legacy, (list, tuple)):
        return False
    if list(full) == list(legacy):
        return False
    full_by_field, _ = _preview_stat_candidates_from_lines(
        [line for line in full if isinstance(line, dict)], minimum_confidence
    )
    legacy_by_field, _ = _preview_stat_candidates_from_lines(
        [line for line in legacy if isinstance(line, dict)], minimum_confidence
    )

    def overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
        first = _line_box(left)
        second = _line_box(right)
        if first is None or second is None:
            return False
        horizontal = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        vertical = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
        first_area = max(1.0, (first[2] - first[0]) * (first[3] - first[1]))
        second_area = max(1.0, (second[2] - second[0]) * (second[3] - second[1]))
        intersection = horizontal * vertical
        # OCR boxes from two passes may shift by a few pixels.  Requiring a
        # substantial overlap binds the disagreement to one physical row and
        # avoids comparing neighboring song/main rows.
        return intersection / min(first_area, second_area) >= 0.45

    for field in sorted(set(full_by_field) & set(legacy_by_field)):
        for dense in full_by_field[field]:
            for sparse in legacy_by_field[field]:
                if dense.get("amount") != sparse.get("amount") and overlaps(dense, sparse):
                    return True
    return False


def _preview_stat_panel_effects(
    by_field: dict[str, list[dict[str, Any]]],
    labels: dict[str, list[tuple[int, dict[str, Any], tuple[float, float, float, float]]]],
    *, result_grid: bool,
    minimum_confidence: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve one typed value per visible stat field.

    The lower row is the ordinary training component.  A second upper row is
    retained as an explicitly observed component, but it is never summed into
    the ordinary value here.  Its semantic owner may promote it to a dedicated
    song/modifier channel only when a separate source marker proves that role.
    This keeps a result-transition frame's visible orange ``+29`` and pink
    ``+15`` distinct.  A frame with more than two rows, equal row centers, or
    an unresolved label is withheld rather than guessing.
    """

    effects: list[dict[str, Any]] = []
    geometry: dict[str, Any] = {"stat_rows": {}}
    all_label_tops = [entry[2][1] for entries in labels.values() for entry in entries]
    # A translucent number can hide its own label.  Neighboring stat labels
    # establish the same horizontal row without allowing a missing label to
    # become a value or an identity claim.
    panel_anchor_top = (
        sorted(all_label_tops)[len(all_label_tops) // 2]
        if all_label_tops else 721.0
    )

    for field, candidates in sorted(by_field.items()):
        unique = _deduplicate_numeric_candidates(candidates)
        unique.sort(key=lambda item: (item["box"][1], item["box"][0]))
        if not unique:
            continue
        anchor_top = None
        if labels.get(field):
            anchor_top = min(item[2][1] for item in labels[field])
        else:
            # The fixed stat-card row is a layout anchor even when a translucent
            # overlay hides one label.  It is not an amount or an inferred
            # state, so it is safe to use as geometry only.
            anchor_top = panel_anchor_top
        label_height = 25.0
        if labels.get(field):
            heights = [entry[2][3] - entry[2][1] for entry in labels[field]]
            if heights:
                label_height = sorted(heights)[len(heights) // 2]
        lower = [item for item in unique
                 if anchor_top - 0.8 * label_height
                 <= _center(item["box"])[1]
                 <= anchor_top + 0.5 * label_height]
        upper = [item for item in unique if item not in lower
                 and item["box"][3] <= anchor_top + 0.15 * label_height]
        chosen: list[dict[str, Any]]
        component_mode = "training_row"
        if len(unique) > 2:
            geometry["stat_rows"][field] = {"status": "too_many_rows"}
            continue
        if len(unique) == 2:
            centers = {_center(item["box"])[1] for item in unique}
            if len(centers) != 2:
                geometry["stat_rows"][field] = {"status": "same_row_conflict"}
                continue
            if len(lower) == 1:
                chosen = [lower[0]]
                if len(upper) == 1:
                    # Retain the source-visible upper component for audit
                    # consumers without letting it alter the ordinary amount.
                    # The dedicated modifier reader owns any semantic
                    # promotion after an explicit Concert marker.
                    geometry.setdefault("unassigned_components", {})[field] = {
                        "status": "observed_upper_component",
                        "row": {
                            "source_index": upper[0]["source_index"],
                            "amount": upper[0]["amount"],
                            "box": list(upper[0]["box"]),
                            "source_references": _evidence_references(
                                upper[0].get(
                                    "source_references", upper[0].get("evidence")
                                )
                            ),
                        },
                        "reason": "separate_component_requires_semantic_marker",
                    }
            else:
                geometry["stat_rows"][field] = {"status": "unresolved_row_geometry"}
                continue
        else:
            if len(lower) == 1:
                chosen = lower
            else:
                # A lone upper number may be a song modifier whose main-row
                # amount was missed by OCR. Its position above the label is
                # insufficient to establish an ordinary training gain.
                geometry["stat_rows"][field] = {"status": "unresolved_row_geometry"}
                continue

        # ``chosen`` is intentionally one lower/main row.  Summing visible
        # rows would fabricate an aggregate (for example 29 + 15) and can
        # make a result animation look like a preview.
        amount = chosen[0]["amount"]
        if amount <= 0:
            geometry["stat_rows"][field] = {"status": "non_positive_amount"}
            continue
        geometry["stat_rows"][field] = {
            "status": "accepted",
            "mode": component_mode,
            "rows": [
                {"source_index": item["source_index"],
                 "amount": item["amount"],
                 "box": list(item["box"]),
                 "source_references": _evidence_references(
                     item.get("source_references", item.get("evidence"))
                 )} for item in chosen
            ],
        }
        first = chosen[0]
        source_references = []
        for item in chosen:
            source_references.extend(_evidence_references(
                item.get("source_references", item.get("evidence"))
            ))
        effects.append({
            "kind": "stat_change",
            "field": field,
            "amount": amount,
            "awarded": False,
            "phase": "preview",
            "preview": True,
            "box": list(first["box"]),
            "source_index": first["source_index"],
            "source_evidence": _evidence_paths(first.get("source_evidence")),
            "preview_geometry": {
                "basis": "source_stat_column_row_geometry",
                "mode": component_mode,
                "rows": geometry["stat_rows"][field]["rows"],
                "source_references": list(dict.fromkeys(source_references)),
            },
        })
    return effects, geometry


def _preview_song_modifier_effects(
    source: dict[str, Any],
    by_field: dict[str, list[dict[str, Any]]],
    labels: dict[str, list[tuple[int, dict[str, Any], tuple[float, float, float, float]]]],
    minimum_confidence: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read the distinct upper song row from a current training menu.

    A Concert Bonuses marker plus a paired upper/lower row is the bounded
    proof used here.  The lower row remains the ordinary training preview;
    only the upper row is emitted on the dedicated modifier channel.  A
    result grid, a missing marker, a missing pair, and unresolved row geometry
    all produce no modifier observation.  The function never uses a known
    amount, recording identity, timestamp, or final stat.
    """

    geometry: dict[str, Any] = {
        "status": "unproven",
        "marker": None,
        "stat_rows": {},
    }
    # The dedicated producer is intentionally narrower than the ordinary
    # panel reader.  It must have a current menu and must not be a result
    # composite before the upper row can be assigned song semantics.
    if source.get("current_grid") is not True or source.get("result_grid") is True:
        geometry["status"] = "requires_current_menu_only"
        return [], geometry
    marker = _preview_song_modifier_marker(
        _preview_source_lines(source), minimum_confidence
    )
    if marker is None:
        geometry["status"] = "missing_concert_bonuses_marker"
        return [], geometry
    geometry["status"] = "marker_proven"
    geometry["marker"] = marker

    all_label_tops = [entry[2][1] for entries in labels.values() for entry in entries]
    panel_anchor_top = (
        sorted(all_label_tops)[len(all_label_tops) // 2]
        if all_label_tops else 721.0
    )
    effects: list[dict[str, Any]] = []
    for field, candidates in sorted(by_field.items()):
        unique = _deduplicate_numeric_candidates(candidates)
        unique.sort(key=lambda item: (item["box"][1], item["box"][0]))
        row_geometry: dict[str, Any] = {
            "status": "unresolved",
            "candidate_count": len(unique),
        }
        geometry["stat_rows"][field] = row_geometry
        # Exactly two distinct rows are required.  Three rows or same-row
        # disagreement cannot be assigned to song versus training semantics
        # from position alone.
        if len(unique) != 2:
            row_geometry["status"] = (
                "missing_main_or_modifier_row" if len(unique) < 2
                else "ambiguous_row_count"
            )
            continue
        anchor_top = min(
            (entry[2][1] for entry in labels.get(field, [])),
            default=panel_anchor_top,
        )
        label_height = 25.0
        if labels.get(field):
            heights = [entry[2][3] - entry[2][1] for entry in labels[field]]
            if heights:
                label_height = sorted(heights)[len(heights) // 2]
        lower = [
            item for item in unique
            if anchor_top - 0.8 * label_height
            <= _center(item["box"])[1]
            <= anchor_top + 0.5 * label_height
        ]
        upper = [
            item for item in unique
            if item not in lower
            and _center(item["box"])[1] < anchor_top
            and item["box"][3] <= anchor_top + 0.15 * label_height
        ]
        if len(lower) != 1 or len(upper) != 1:
            row_geometry["status"] = "unresolved_row_geometry"
            row_geometry["lower_count"] = len(lower)
            row_geometry["upper_count"] = len(upper)
            continue
        main_row = lower[0]
        modifier_row = upper[0]
        if _center(modifier_row["box"])[1] >= _center(main_row["box"])[1]:
            row_geometry["status"] = "modifier_not_above_main_row"
            continue
        amount = modifier_row.get("amount")
        if type(amount) is not int or amount < 0:
            row_geometry["status"] = "invalid_modifier_amount"
            continue
        marker_references = _evidence_references(
            marker.get("source_references", marker.get("evidence"))
        )
        row_references = list(dict.fromkeys(
            marker_references
            + _evidence_references(
                modifier_row.get("source_references", modifier_row.get("evidence"))
            )
            + _evidence_references(
                main_row.get("source_references", main_row.get("evidence"))
            )
        ))
        modifier_geometry = {
            "basis": "source_song_modifier_row_geometry",
            "role": "song_modifier_row",
            "marker": "concert_bonuses",
            "marker_references": marker_references,
            "modifier_row": {
                "source_index": modifier_row["source_index"],
                "amount": amount,
                "box": list(modifier_row["box"]),
                "source_references": _evidence_references(
                    modifier_row.get("source_references", modifier_row.get("evidence"))
                ),
            },
            "main_training_row": {
                "source_index": main_row["source_index"],
                "amount": main_row["amount"],
                "box": list(main_row["box"]),
                "source_references": _evidence_references(
                    main_row.get("source_references", main_row.get("evidence"))
                ),
            },
            "source_references": row_references,
        }
        row_geometry.update({
            "status": "accepted",
            "modifier_row": modifier_geometry["modifier_row"],
            "main_training_row": modifier_geometry["main_training_row"],
        })
        effects.append({
            "kind": "song_modifier_change",
            "field": field,
            "amount": amount,
            "phase": "preview",
            "preview": True,
            "awarded": False,
            "source_evidence": _source_evidence(source),
            "source_semantics": "typed_training_song_modifier_preview",
            "modifier": "song",
            "preview_geometry": modifier_geometry,
        })
    if not effects:
        geometry["status"] = "marker_without_unambiguous_row_pair"
    return effects, geometry


def _preview_performance_effects_from_source(
    source: dict[str, Any], lines: list[dict[str, Any]], minimum_confidence: float,
) -> list[dict[str, Any]]:
    """Read signed projected performance values from typed regions or rows."""

    candidates: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    regions = source.get("regions") if isinstance(source.get("regions"), dict) else {}
    for name, region in regions.items():
        if not isinstance(name, str) or not name.startswith("performance_gain."):
            continue
        field = name.split(".", 1)[1].casefold()
        if field not in PERFORMANCE_FIELDS or _confidence(region) < minimum_confidence:
            continue
        match = _SIGNED_AMOUNT_RE.fullmatch(_line_text(region) or "")
        box = _line_box(region)
        if not match or box is None:
            continue
        candidates[field].append({
            "field": field, "amount": int(match[1]), "box": box,
            "source_region": name,
            "source_references": [f"region:{name}"],
        })

    performance_proven, labels = _performance_panel_proof(lines, minimum_confidence)
    if performance_proven:
        for index, line in enumerate(lines):
            if _confidence(line) < minimum_confidence:
                continue
            match = _CURRENT_PROJECTED_RE.fullmatch(_line_text(line) or "")
            box = _line_box(line)
            if not match or box is None:
                continue
            _x, y = _center(box)
            for field, entries in labels.items():
                if any(abs(y - _center(label_box)[1]) <= 38 for _idx, _label, label_box in entries):
                    candidates[field].append({
                        "field": field, "amount": int(match[2]), "box": box,
                        "source_index": index,
                        "source_references": [f"line:{index}"],
                    })
                    break

    effects = []
    for field, values in sorted(candidates.items()):
        unique = _deduplicate_numeric_candidates(values)
        amounts = {item["amount"] for item in unique}
        if len(amounts) != 1:
            continue
        item = unique[0]
        effects.append({
            "kind": "performance_change", "field": field,
            "amount": item["amount"], "awarded": False,
            "phase": "preview", "preview": True,
            "box": list(item["box"]),
            "source_region": item.get("source_region"),
            "source_index": item.get("source_index"),
            "source_evidence": _evidence_paths(item.get("source_evidence")),
            "preview_geometry": {
                "basis": "source_performance_panel_row_geometry",
                "source_references": _evidence_references(
                    item.get("source_references")
                ),
            },
        })
    return effects


def produce_preview_panel_from_lines(
    source: Any, *, minimum_confidence: float = 90,
) -> dict[str, Any] | None:
    """Produce a bounded typed preview panel from one source OCR reading.

    This adapter is intended for the normal neural reader.  It requires the
    selected training heading, the selected-training Failure badge, and a
    visible lower stat-card layout.  A large SUCCESS/result banner wins over
    signed rows.  It accepts only OCR lines/typed numeric regions from this
    same source image; it does not inspect final totals, neighboring frames,
    expected values, or raw candidate collections.
    """

    if not isinstance(source, dict):
        return None
    if not isinstance(minimum_confidence, (int, float)) or isinstance(minimum_confidence, bool):
        raise ValueError("minimum_confidence must be numeric")
    if not 0 <= minimum_confidence <= 100:
        raise ValueError("minimum_confidence must be between 0 and 100")
    if _preview_source_views_conflict(source, float(minimum_confidence)):
        # The typed crop consensus is the only path allowed to arbitrate this
        # disagreement; the producer itself never picks the dense list merely
        # because it contains a longer digit.
        return None
    lines = _preview_source_lines(source)
    # The fast colour marker is normally enough, but can be false for a
    # translucent or pink/red training card.  In that case use the same
    # source-only menu proof as the bounded recovery reader: Training header,
    # selected option, stat-label band, Failure badge, and no result marker.
    # A result grid or SUCCESS marker still vetoes this path.
    current_grid = source.get("current_grid") is True
    if not current_grid:
        fallback_phase = _raw_menu_phase_proof(
            source, lines,
            source.get("regions") if isinstance(source.get("regions"), dict) else {},
            _canonical_option(source.get("option")),
            float(minimum_confidence),
        )
        if fallback_phase is None or source.get("result_grid") is True:
            return None
    header = _text(source.get("header")) or ""
    if not header.casefold().startswith("training"):
        return None
    option = _canonical_option(source.get("option"))
    if option is None:
        option_region = source.get("regions", {}).get("option") if isinstance(source.get("regions"), dict) else None
        if _confidence(option_region) >= minimum_confidence:
            option = _canonical_option(option_region)
    if option is None:
        for line in lines:
            if _confidence(line) < minimum_confidence:
                continue
            option = _canonical_option(_line_text(line))
            if option:
                break
    if option is None:
        return None
    if source.get("success_visible") is True or source.get("result_marker_visible") is True:
        return None
    success = _preview_success_banner_line(lines, minimum_confidence)
    if success is not None:
        return None
    failure = _preview_menu_failure_line(lines, minimum_confidence)
    if failure is None:
        return None

    by_field, labels = _preview_stat_candidates_from_lines(lines, minimum_confidence)
    stat_effects, geometry = _preview_stat_panel_effects(
        by_field, labels, result_grid=source.get("result_grid") is True,
        minimum_confidence=minimum_confidence,
    )
    song_modifier_effects, modifier_geometry = _preview_song_modifier_effects(
        source, by_field, labels, minimum_confidence
    )
    performance_effects = _preview_performance_effects_from_source(
        source, lines, minimum_confidence)
    effects = stat_effects + performance_effects
    if not effects:
        return None
    evidence = _source_evidence(source)
    for effect in effects:
        paths, references = _split_source_provenance(effect.get("source_evidence"))
        effect["source_evidence"] = list(dict.fromkeys(evidence + paths))
        if references:
            geometry = effect.setdefault("preview_geometry", {})
            geometry["source_references"] = list(dict.fromkeys(
                _strings(geometry.get("source_references")) + references
            ))
    for effect in song_modifier_effects:
        paths, references = _split_source_provenance(effect.get("source_evidence"))
        effect["source_evidence"] = list(dict.fromkeys(evidence + paths))
        if references:
            geometry = effect.setdefault("preview_geometry", {})
            geometry["source_references"] = list(dict.fromkeys(
                _strings(geometry.get("source_references")) + references
            ))
    return {
        "menu_proven": True,
        "result_proven": False,
        "basis": "source_training_failure_badge_and_preview_row_geometry",
        "geometry": {
            "layout": "gameplay_crop_stat_cards",
            "stat_row_band": list(_SOURCE_PREVIEW_STAT_Y),
            "label_band": list(_SOURCE_PREVIEW_LABEL_Y),
            "stat_rows": geometry.get("stat_rows", {}),
            "unassigned_components": geometry.get("unassigned_components", {}),
            "failure": {
                "box": list(_line_box(failure)),
                "text": _line_text(failure),
            },
            "song_modifier": modifier_geometry,
        },
        "option": option,
        "evidence": evidence,
        "effects": effects,
        # Keep modifiers out of ``effects`` so downstream code cannot treat
        # them as ordinary training stat changes.  The typed panel contract
        # validates this channel separately below.
        "modifier_effects": song_modifier_effects,
    }


def _typed_preview_child_flag_error(item: dict[str, Any]) -> str | None:
    """Validate the phase/award markers on one typed preview child.

    These markers are an input contract, so Python's ``0 == False`` and
    ``1 == True`` coercions must not decide whether an effect is applied.
    Missing markers are allowed for parser panels whose parent phase proof is
    authoritative; present markers must use their exact wire types.
    """

    for key in ("awarded", "applied", "preview"):
        if key in item and type(item[key]) is not bool:
            return key
    if item.get("awarded") is True:
        return "awarded"
    if item.get("applied") is True:
        return "applied"
    if "preview" in item and item["preview"] is False:
        return "preview"
    for key in ("phase", "source_phase"):
        if key not in item:
            continue
        value = item[key]
        if not isinstance(value, str) or _text(value) != "preview":
            return key
    return None


def _typed_panel_effects(
    source: dict[str, Any], panel: dict[str, Any],
    source_evidence: list[str], phase_proof: dict[str, Any],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Map an already accepted numeric menu panel into preview effects.

    The panel is an integration boundary for the numeric reader.  It must
    contain typed ``effects``; OCR strings, candidates, residuals, and final
    totals are intentionally not interpreted here.  A panel with a positive
    result marker is rejected by :func:`preview_phase_proof` before this
    function is called.
    """

    rejected: Counter[str] = Counter()
    values = panel.get("effects")
    if not isinstance(values, list):
        return [], Counter({"typed_preview_panel_missing_effects": 1})
    panel_evidence: list[str] = []
    panel_references: list[str] = []
    for key in ("evidence", "source_evidence"):
        paths, references = _split_source_provenance(panel.get(key))
        panel_evidence.extend(paths)
        panel_references.extend(references)
    evidence = list(dict.fromkeys(
        _evidence_paths(source_evidence) + panel_evidence
    ))
    basis = _text(
        panel.get("geometry_basis")
        or panel.get("basis")
        or phase_proof.get("basis")
    ) or "typed_preview_menu_geometry"
    effects: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            rejected["typed_preview_panel_non_object_effect"] += 1
            continue
        # Candidate/raw fields are a hard boundary.  This catches accidental
        # promotion when a caller passes the entire OCR result object.
        if "raw_text" in item or any("candidate" in str(key).casefold() for key in item):
            rejected["typed_preview_panel_untyped_effect"] += 1
            continue
        kind = _text(item.get("kind"))
        field = _text(item.get("field"))
        if field:
            field = {"vocals": "vocal", "visuals": "visual"}.get(
                field.casefold(), field.casefold()
            )
        if kind not in ("stat_change", "performance_change"):
            rejected["typed_preview_panel_unsupported_kind"] += 1
            continue
        allowed = STAT_FIELDS if kind == "stat_change" else PERFORMANCE_FIELDS
        if field not in allowed:
            rejected["typed_preview_panel_field_kind_mismatch"] += 1
            continue
        amount = _amount(item.get("amount"))
        if amount is None:
            rejected["typed_preview_panel_missing_amount"] += 1
            continue
        flag_error = _typed_preview_child_flag_error(item)
        if flag_error in ("phase", "source_phase"):
            rejected["typed_preview_panel_non_preview_phase"] += 1
            continue
        if flag_error == "awarded":
            rejected["typed_preview_panel_awarded"] += 1
            continue
        if flag_error == "applied":
            rejected["typed_preview_panel_applied"] += 1
            continue
        if flag_error == "preview":
            rejected["typed_preview_panel_not_preview"] += 1
            continue
        if flag_error is not None:
            rejected["typed_preview_panel_malformed_flags"] += 1
            continue
        direction = _text(item.get("direction"))
        if direction:
            direction = direction.casefold()
            if direction in ("down", "decrease"):
                amount = -abs(amount)
            elif direction in ("up", "increase"):
                if amount < 0:
                    rejected["typed_preview_panel_direction_conflict"] += 1
                    continue
                amount = abs(amount)
            else:
                rejected["typed_preview_panel_direction_unknown"] += 1
                continue
        effect_paths: list[str] = []
        effect_references: list[str] = []
        for key in ("evidence", "source_evidence", "field_evidence"):
            paths, references = _split_source_provenance(item.get(key))
            effect_paths.extend(paths)
            effect_references.extend(references)
        effect_evidence = list(dict.fromkeys(evidence + effect_paths))
        if not effect_evidence:
            rejected["typed_preview_panel_missing_evidence"] += 1
            continue
        geometry = item.get("preview_geometry")
        box = None
        if "box" in item:
            box = _finite_box(item.get("box"))
            if box is None:
                rejected["typed_preview_panel_invalid_geometry"] += 1
                continue
        elif isinstance(geometry, dict) and "box" in geometry:
            box = _finite_box(geometry.get("box"))
            if box is None:
                rejected["typed_preview_panel_invalid_geometry"] += 1
                continue
        source_region = _text(item.get("source_region"))
        source_index = item.get("source_index")
        effect = _make_preview_effect(
            field, amount, source_evidence=effect_evidence,
            basis=basis, source_index=source_index,
            source_region=source_region, box=box,
            source_references=(
                panel_references + effect_references
                + _evidence_references(
                    (item.get("preview_geometry") or {}).get("source_references")
                    if isinstance(item.get("preview_geometry"), dict) else None
                )
            ),
        )
        effect["source_semantics"] = "typed_training_preview_panel"
        effect["preview_geometry"]["panel_index"] = index
        effects.append(effect)
    # A same-field disagreement in one typed panel is unsafe to resolve.  The
    # canonical builder will also handle conflicts across adjacent frames.
    by_field: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for effect in effects:
        by_field[effect["field"]].append(effect)
    accepted: list[dict[str, Any]] = []
    for field, field_effects in by_field.items():
        amounts = {item["amount"] for item in field_effects}
        if len(amounts) != 1:
            rejected["typed_preview_panel_conflicting_amounts"] += 1
            continue
        accepted.append(field_effects[0])
    accepted.sort(key=lambda item: (item["kind"], item["field"]))
    return accepted, rejected


def _typed_song_modifier_panel_effects(
    source: dict[str, Any], panel: dict[str, Any],
    source_evidence: list[str], phase_proof: dict[str, Any],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Validate the dedicated, non-applied song modifier preview channel.

    ``modifier_effects`` is intentionally separate from the panel's ordinary
    ``effects`` list.  Requiring the explicit row geometry here prevents a
    caller from relabeling an arbitrary stat change as a concert bonus.
    """

    rejected: Counter[str] = Counter()
    values = panel.get("modifier_effects")
    if values is None:
        return [], rejected
    if not isinstance(values, list):
        return [], Counter({"typed_song_modifier_panel_missing_effects": 1})
    panel_evidence: list[str] = []
    panel_references: list[str] = []
    for key in ("evidence", "source_evidence"):
        paths, references = _split_source_provenance(panel.get(key))
        panel_evidence.extend(paths)
        panel_references.extend(references)
    evidence = list(dict.fromkeys(
        _evidence_paths(source_evidence) + panel_evidence
    ))
    accepted: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            rejected["typed_song_modifier_panel_non_object_effect"] += 1
            continue
        if "raw_text" in item or any("candidate" in str(key).casefold() for key in item):
            rejected["typed_song_modifier_panel_untyped_effect"] += 1
            continue
        if _text(item.get("kind")) != "song_modifier_change":
            rejected["typed_song_modifier_panel_unsupported_kind"] += 1
            continue
        field = _text(item.get("field"))
        if field:
            field = {"vocals": "vocal", "visuals": "visual"}.get(
                field.casefold(), field.casefold())
        if field not in (STAT_FIELDS | PERFORMANCE_FIELDS):
            rejected["typed_song_modifier_panel_field_kind_mismatch"] += 1
            continue
        amount = _amount(item.get("amount"))
        if amount is None or amount < 0:
            rejected["typed_song_modifier_panel_missing_amount"] += 1
            continue
        if item.get("modifier") != "song":
            rejected["typed_song_modifier_panel_missing_song_marker"] += 1
            continue
        flag_error = _typed_preview_child_flag_error(item)
        if flag_error in ("phase", "source_phase"):
            rejected["typed_song_modifier_panel_non_preview_phase"] += 1
            continue
        if flag_error == "awarded":
            rejected["typed_song_modifier_panel_awarded"] += 1
            continue
        if flag_error == "applied":
            rejected["typed_song_modifier_panel_applied"] += 1
            continue
        if flag_error == "preview":
            rejected["typed_song_modifier_panel_not_preview"] += 1
            continue
        if flag_error is not None:
            rejected["typed_song_modifier_panel_malformed_flags"] += 1
            continue
        if flag_error is None:
            geometry = item.get("preview_geometry")
            if not isinstance(geometry, dict) or geometry.get("role") != "song_modifier_row":
                rejected["typed_song_modifier_panel_missing_row_geometry"] += 1
                continue
            if geometry.get("marker") != "concert_bonuses":
                rejected["typed_song_modifier_panel_missing_concert_marker"] += 1
                continue
            # ``marker_evidence`` was used by the first typed-panel version for
            # OCR line IDs.  Accept it as a legacy input, but normalize those
            # IDs into metadata before returning a worker-facing effect.
            marker_references = list(dict.fromkeys(
                _strings(geometry.get("marker_references"))
                + _evidence_references(geometry.get("marker_evidence"))
            ))
            modifier_row = geometry.get("modifier_row")
            main_row = geometry.get("main_training_row")
            if not marker_references or not isinstance(modifier_row, dict) or not isinstance(main_row, dict):
                rejected["typed_song_modifier_panel_incomplete_row_geometry"] += 1
                continue
            modifier_box = _finite_box(modifier_row.get("box"))
            main_box = _finite_box(main_row.get("box"))
            if modifier_box is None or main_box is None:
                rejected["typed_song_modifier_panel_incomplete_row_geometry"] += 1
                continue
            if "box" in item and _finite_box(item.get("box")) is None:
                rejected["typed_song_modifier_panel_invalid_geometry"] += 1
                continue
            if _center(modifier_box)[1] >= _center(main_box)[1]:
                rejected["typed_song_modifier_panel_row_order_conflict"] += 1
                continue
            if modifier_row.get("amount") != amount:
                rejected["typed_song_modifier_panel_amount_geometry_conflict"] += 1
                continue
            effect_paths: list[str] = []
            effect_references: list[str] = []
            for key in ("evidence", "source_evidence"):
                paths, references = _split_source_provenance(item.get(key))
                effect_paths.extend(paths)
                effect_references.extend(references)
            effect_evidence = list(dict.fromkeys(evidence + effect_paths))
            if not effect_evidence:
                rejected["typed_song_modifier_panel_missing_evidence"] += 1
                continue
            normalized_geometry = deepcopy(geometry)
            normalized_geometry.pop("marker_evidence", None)
            normalized_geometry["marker_references"] = marker_references
            normalized_geometry["source_references"] = list(dict.fromkeys(
                panel_references
                + marker_references
                + effect_references
                + _evidence_references(geometry.get("source_references"))
            ))
            for row_key in ("modifier_row", "main_training_row"):
                row = normalized_geometry.get(row_key)
                if not isinstance(row, dict):
                    continue
                row_paths, row_references = _split_source_provenance(
                    row.get("source_evidence")
                )
                row["source_evidence"] = row_paths
                row["source_references"] = list(dict.fromkeys(
                    _strings(row.get("source_references")) + row_references
                ))
            effect = {
                "kind": "song_modifier_change",
                "field": field,
                "amount": amount,
                "phase": "preview",
                "preview": True,
                "awarded": False,
                "modifier": "song",
                "source_evidence": effect_evidence,
                "source_semantics": "typed_training_song_modifier_preview",
                "preview_geometry": normalized_geometry,
            }
            for key in ("source_index", "source_region", "box"):
                if key in item:
                    effect[key] = deepcopy(item[key])
            accepted.append(effect)
    by_field: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for effect in accepted:
        by_field[effect["field"]].append(effect)
    resolved: list[dict[str, Any]] = []
    for field, field_effects in by_field.items():
        amounts = {item["amount"] for item in field_effects}
        if len(amounts) != 1:
            rejected["typed_song_modifier_panel_conflicting_amounts"] += 1
            continue
        resolved.append(field_effects[0])
    resolved.sort(key=lambda item: (item["kind"], item["field"]))
    return resolved, rejected


def preview_phase_proof(source: Any, *, minimum_confidence: float = 90) -> dict[str, Any] | None:
    """Return positive browse-menu phase evidence for a raw source reading.

    This is the integration hook for the numeric reader.  The proof is valid
    only for a source that can be treated as a browse menu; it never overrides
    a ``training_result`` classification or a committed action.  ``None``
    means the module cannot distinguish a browse menu from a result frame and
    the caller should abstain.
    """

    if not isinstance(source, dict):
        return None
    # ``preview_lines`` is the full detector view captured by the normal
    # high-rate reader.  It is bound to this raw source image and may be more
    # complete than the cached header/option ``lines`` list.  Use the same
    # source view for phase, option, and amount decisions so a fresh reread
    # can recover a clipped digit (for example ``+1`` versus visible ``+10``)
    # without consulting totals or neighboring frames.
    lines = _preview_source_lines(source)
    regions = source.get("regions")
    regions = regions if isinstance(regions, dict) else {}
    if not isinstance(minimum_confidence, (int, float)) or isinstance(minimum_confidence, bool):
        raise ValueError("minimum_confidence must be numeric")
    if minimum_confidence < 0 or minimum_confidence > 100:
        raise ValueError("minimum_confidence must be between 0 and 100")
    _typed, option, _context = _training_marker(source, lines, regions)
    # A typed preview panel may carry the option, but it still needs a training
    # or career header.  Do not let a panel invent the header itself.
    header = _text(source.get("header")) or ""
    if not header.casefold().startswith(("training", "career")):
        return None
    proof = _raw_menu_phase_proof(
        source, lines, regions, option, float(minimum_confidence)
    )
    if proof is None:
        return None
    if option and "option" not in proof:
        proof["option"] = option
    if "stat_row_proven" not in proof:
        proof["stat_row_proven"] = False
    return proof


def _parse_preview_overlay(source: Any, *, minimum_confidence: float = 90) -> dict[str, Any]:
    """Parse explicit training preview numbers from one OCR source reading.

    The parser accepts only signed amounts in the training stat-card columns,
    or a current-plus-projected value in a labeled Performance Points row.
    A source-proven Concert Bonuses marker may additionally produce
    ``preview_modifier_effects`` for the distinct upper song row; that list
    stays separate from ordinary stat/performance effects.
    It requires positive menu geometry and a typed option heading (or a typed
    numeric panel contract).  A result frame may contain the same signed
    values, but it is never promoted without browse-menu proof.  No final
    totals, residuals, timestamps, or OCR candidates are used to manufacture
    an amount.
    """

    if not isinstance(source, dict):
        source = {"lines": source}
    # Keep cached and fresh paths on one source-bound detector view.  The
    # high-rate reader stores its complete reread in ``preview_lines`` while
    # retaining a small legacy ``lines`` collection for result inspection.
    # Selecting the complete view here lets the typed panel and phase proof
    # use the same pixels that produced the candidate, rather than promoting
    # a clipped legacy amount.
    lines = _preview_source_lines(source)
    regions = source.get("regions")
    regions = regions if isinstance(regions, dict) else {}
    rejected: Counter[str] = Counter()
    if not isinstance(minimum_confidence, (int, float)) or isinstance(minimum_confidence, bool):
        raise ValueError("minimum_confidence must be numeric")
    if minimum_confidence < 0 or minimum_confidence > 100:
        raise ValueError("minimum_confidence must be between 0 and 100")

    typed, option, context = _training_marker(source, lines, regions)
    if not typed:
        rejected["missing_training_header_or_option"] += 1
        return {"preview_option": None, "preview_overlay_proven": False,
                "preview_overlay_effects": [],
                "preview_modifier_proven": False,
                "preview_modifier_effects": [],
                "preview_overlay_evidence": {}, "rejected_counts": dict(rejected)}

    source_evidence = _source_evidence(source)
    if _preview_source_views_conflict(source, float(minimum_confidence)):
        # Do not privilege the full detector list over the legacy crop.  A
        # validated preview_recovery sidecar may arbitrate the same-frame
        # disagreement later in ``parse_preview_overlay``; the ordinary OCR
        # projection must remain unknown until then.
        rejected["conflicting_same_frame_preview_views"] += 1
        return {
            "preview_option": option,
            "preview_overlay_proven": False,
            "preview_overlay_effects": [],
            "preview_modifier_proven": False,
            "preview_modifier_effects": [],
            "preview_phase_proof": {},
            "preview_overlay_evidence": {},
            "rejected_counts": dict(sorted(rejected.items())),
        }
    # A normal detector reading can contain enough source geometry to build a
    # typed panel even when the fast reader did not attach one.  Keep the
    # generated panel local to this parse; callers still receive it through the
    # returned proof/effects and no raw OCR candidate is promoted directly.
    panel = _preview_panel(source)
    if panel is None:
        generated = produce_preview_panel_from_lines(
            source, minimum_confidence=minimum_confidence)
        if generated is not None:
            panel = generated
            source = dict(source, preview_panel=generated)
    phase_proof = preview_phase_proof(source, minimum_confidence=minimum_confidence)
    if phase_proof is None:
        # The heading alone is not a preview.  This is the important negative
        # path for fading result/SUCCESS animations whose OCR still contains a
        # training option and signed amounts.
        rejected["missing_positive_menu_geometry"] += 1
        return {
            "preview_option": option,
            "preview_overlay_proven": False,
            "preview_overlay_effects": [],
            "preview_modifier_proven": False,
            "preview_modifier_effects": [],
            "preview_phase_proof": {},
            "preview_overlay_evidence": {},
            "rejected_counts": dict(sorted(rejected.items())),
        }

    if panel is not None and isinstance(panel.get("effects"), list):
        panel_effects, panel_rejected = _typed_panel_effects(
            source, panel, source_evidence, phase_proof
        )
        panel_modifier_effects, modifier_rejected = _typed_song_modifier_panel_effects(
            source, panel, source_evidence, phase_proof
        )
        rejected.update(panel_rejected)
        rejected.update(modifier_rejected)
        panel_option = _canonical_option(
            panel.get("option", panel.get("option_label"))
        )
        selected_option = panel_option or option or phase_proof.get("option")
        proof = dict(context, **phase_proof)
        proof["panel_effect_count"] = len(panel_effects)
        proof["panel_modifier_effect_count"] = len(panel_modifier_effects)
        return {
            "preview_option": selected_option,
            "preview_overlay_proven": bool(panel_effects),
            "preview_overlay_effects": panel_effects,
            "preview_modifier_proven": bool(panel_modifier_effects),
            "preview_modifier_effects": panel_modifier_effects,
            "preview_phase_proof": phase_proof,
            "preview_overlay_evidence": proof if (panel_effects or panel_modifier_effects) else {},
            "rejected_counts": dict(sorted(rejected.items())),
        }

    labels = _stat_label_lines(lines, float(minimum_confidence))
    stat_candidates = []
    for index, line in enumerate(lines):
        if _confidence(line) < minimum_confidence:
            continue
        box = _line_box(line)
        text = _line_text(line)
        if box is None or text is None:
            continue
        match = _SIGNED_AMOUNT_RE.fullmatch(text)
        if not match:
            continue
        x, y = _center(box)
        # The preview number row sits around the persistent stat cards.  The
        # separate gain.* region path below covers lower result-card crops.
        if not 610 <= y <= 820:
            rejected["signed_amount_outside_preview_row"] += 1
            continue
        field = _column_for_x(x)
        if field is None:
            rejected["signed_amount_outside_stat_column"] += 1
            continue
        stat_candidates.append({"field": field, "amount": int(match[1]),
                                "box": box, "source_index": index})

    current_grid = source.get("current_grid") is True
    result_grid = source.get("result_grid") is True
    # A positive menu phase may come from a fallback geometry proof while the
    # fast color probe says current_grid=False.  Stat rows still require their
    # own current-grid proof; otherwise a stale two-row result overlay can be
    # mistaken for a single preview row (as in a transition frame).
    if not phase_proof.get("stat_row_proven"):
        if stat_candidates:
            rejected["stat_rows_without_current_menu_proof"] += len(stat_candidates)
        stat_candidates = []
    # A surviving label is enough for a faded panel whose color marker was not
    # detected.  Otherwise require an explicit grid marker before accepting a
    # number.  This blocks a stray plus sign elsewhere in a Training dialog.
    if stat_candidates and not (current_grid or result_grid or labels):
        rejected["missing_stat_panel_geometry"] += len(stat_candidates)
        stat_candidates = []
    selected, row_rejected = _select_stat_preview_candidates(
        stat_candidates, labels, current_grid, result_grid)
    rejected.update(row_rejected)

    # The high-rate training reader exposes the same overlay through typed
    # ``gain.<field>`` regions.  A signed value in this lower result-card crop
    # is an explicit preview observation, while an unsigned current/result
    # total is deliberately ignored.  Keep this path separate from ordinary
    # detector lines so a result screen cannot be accepted on geometry alone.
    region_candidates = []
    for region_name, region in regions.items():
        if not isinstance(region_name, str) or not region_name.startswith("gain."):
            continue
        field = region_name.split(".", 1)[1].casefold()
        if field not in STAT_FIELDS:
            continue
        if _confidence(region) < minimum_confidence:
            rejected["unreadable_training_gain_region"] += 1
            continue
        text = _line_text(region)
        match = _SIGNED_AMOUNT_RE.fullmatch(text or "")
        box = _line_box(region)
        if not match or box is None:
            rejected["unreadable_training_gain_region"] += 1
            continue
        _x, y = _center(box)
        # ``gain.*`` is the result-reader crop.  It is never browse-menu proof;
        # accepting it here was the source of the old false preview rows.
        rejected["training_gain_region_without_current_menu_proof"] += 1
        if y < 780:
            rejected["training_gain_region_outside_result_crop"] += 1
            continue
        continue

    # Merge line and region paths conservatively.  Equal typed amounts are
    # corroboration; disagreement removes the field from the preview output.
    accepted_stat = list(selected)
    for candidate in region_candidates:
        same = [item for item in accepted_stat if item["field"] == candidate["field"]]
        if not same:
            accepted_stat.append(candidate)
            continue
        amounts = {item["amount"] for item in same}
        if candidate["amount"] not in amounts:
            rejected["conflicting_stat_preview_amounts"] += 1
            accepted_stat = [item for item in accepted_stat
                             if item["field"] != candidate["field"]]
            continue
        # Prefer the typed crop as the canonical proof when both paths agree;
        # the detector line remains available through the geometry metadata
        # only when it is the sole accepted source.
        accepted_stat = [item for item in accepted_stat
                         if item["field"] != candidate["field"]]
        accepted_stat.append(candidate)

    effects = []
    for candidate in accepted_stat:
        candidate_reference = (
            f"line:{candidate['source_index']}"
            if candidate.get("source_index") is not None else
            f"region:{candidate['source_region']}"
            if candidate.get("source_region") is not None else None
        )
        effects.append(_make_preview_effect(
            candidate["field"], candidate["amount"],
            source_evidence=source_evidence,
            basis=("typed_training_gain_region"
                   if candidate.get("source_region") is not None
                   else "training_stat_column_row_geometry"),
             source_index=candidate.get("source_index"), box=candidate["box"],
             source_references=[candidate_reference] if candidate_reference else None))

    performance_proven, performance_labels = _performance_panel_proof(
        lines, float(minimum_confidence))
    if not phase_proof.get("menu_proven"):
        performance_proven = False
    performance_candidates = []
    if performance_proven:
        for index, line in enumerate(lines):
            if _confidence(line) < minimum_confidence:
                continue
            box = _line_box(line)
            text = _line_text(line)
            if box is None or text is None:
                continue
            match = _CURRENT_PROJECTED_RE.fullmatch(text)
            if not match:
                continue
            x, y = _center(box)
            if not 185 <= x <= 335:
                continue
            rows = []
            for field, entries in performance_labels.items():
                for label_index, _label, label_box in entries:
                    label_y = _center(label_box)[1]
                    if abs(y - label_y) <= 38:
                        rows.append((abs(y - label_y), field))
            if not rows:
                rejected["unlabeled_performance_preview_row"] += 1
                continue
            rows.sort()
            if len(rows) > 1 and rows[0][0] == rows[1][0]:
                rejected["ambiguous_performance_preview_row"] += 1
                continue
            performance_candidates.append({"field": rows[0][1],
                                           "amount": int(match[2]),
                                           "box": box,
                                           "source_index": index})

    # Some dedicated readers expose a typed projected_performance.<field>
    # crop instead of detector lines.  The key and crop are already semantic
    # evidence; the strict signed grammar still applies.
    for region_name, region in regions.items():
        if not isinstance(region_name, str) or not region_name.startswith("projected_performance."):
            continue
        field = region_name.split(".", 1)[1].casefold()
        if field not in PERFORMANCE_FIELDS or _confidence(region) < minimum_confidence:
            if field in PERFORMANCE_FIELDS:
                rejected["unreadable_projected_performance_region"] += 1
            continue
        text = _line_text(region)
        match = _SIGNED_AMOUNT_RE.fullmatch(text or "")
        box = _line_box(region)
        if not match or box is None:
            rejected["unreadable_projected_performance_region"] += 1
            continue
        performance_candidates.append({"field": field, "amount": int(match[1]),
                                       "box": box,
                                       "source_region": region_name})

    by_performance: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in _deduplicate_numeric_candidates(performance_candidates):
        by_performance[candidate["field"]].append(candidate)
    for field, candidates in by_performance.items():
        amounts = {item["amount"] for item in candidates}
        if len(amounts) != 1:
            rejected["conflicting_performance_preview_amounts"] += 1
            continue
        candidate = candidates[0]
        candidate_reference = (
            f"line:{candidate['source_index']}"
            if candidate.get("source_index") is not None else
            f"region:{candidate['source_region']}"
            if candidate.get("source_region") is not None else None
        )
        effects.append(_make_preview_effect(
            field, candidate["amount"], source_evidence=source_evidence,
            basis=("performance_panel_current_plus_projected_geometry"
                   if candidate.get("source_index") is not None
                   else "typed_projected_performance_region"),
            source_index=candidate.get("source_index"),
            source_region=candidate.get("source_region"), box=candidate["box"],
            source_references=[candidate_reference] if candidate_reference else None))

    proof = dict(context)
    proof.update(phase_proof)
    proof.update(
        basis=phase_proof.get("basis", "training_header_option_and_labeled_panel_geometry"),
        stat_labels=sorted(labels), performance_labels=sorted(performance_labels),
        current_grid=current_grid, result_grid=result_grid,
    )
    return {
        "preview_option": option or phase_proof.get("option"),
        "preview_overlay_proven": bool(effects),
        "preview_overlay_effects": effects,
        "preview_modifier_proven": False,
        "preview_modifier_effects": [],
        "preview_phase_proof": phase_proof,
        "preview_overlay_evidence": proof if effects else {},
        "rejected_counts": dict(sorted(rejected.items())),
    }


_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Kept in this module as a validation copy of the source-recovery request
# geometry.  These are layout coordinates, not value expectations; matching
# them prevents a persisted envelope from moving a typed amount onto another
# crop before it reaches the preview adapter.
_PREVIEW_MAIN_REGION_BOXES = {
    "speed": (270, 665, 390, 705),
    "stamina": (370, 665, 480, 705),
    "power": (465, 665, 580, 705),
    "guts": (560, 665, 680, 705),
    "wit": (655, 665, 755, 705),
    "skill_points": (745, 665, 850, 705),
}
_PREVIEW_MODIFIER_REGION_BOXES = {
    field: (box[0], 622, box[2], 668)
    for field, box in _PREVIEW_MAIN_REGION_BOXES.items()
}
_PREVIEW_FIELD_CENTERS = {
    field: (box[0] + box[2]) / 2
    for field, box in _PREVIEW_MAIN_REGION_BOXES.items()
}
# An OCR line may contain two adjacent signed amounts.  Only assign a token
# to a field when its estimated horizontal center has a clear nearest-column
# winner; a token near a column boundary is an ambiguity, never evidence for
# either field.  The estimate is based on the token's character span inside
# the detector box and is used only to bind an already persisted source line
# to its canonical crop.
_PERSISTED_TOKEN_FIELD_MARGIN = 12.0


def _persisted_preview_path(value: Any) -> bool:
    """Accept only a relative source path in a persisted proof envelope."""

    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return False
    return all(part not in ("", ".", "..") for part in normalized.split("/"))


def _persisted_frame_identity(value: Any) -> str | None:
    """Extract a generic ``part-NNN-frame-NNNNNN`` identity from a path."""

    if not isinstance(value, str):
        return None
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    for index, part in enumerate(parts):
        if not re.fullmatch(r"part-\d{3}", part, re.IGNORECASE):
            continue
        if index + 2 >= len(parts) or parts[index + 1].casefold() != "frames":
            continue
        stem = parts[index + 2].rsplit(".", 1)[0]
        if re.fullmatch(r"\d{6}", stem):
            return f"{part.casefold()}-frame-{stem}"
    for part in parts:
        match = re.fullmatch(r"(part-\d{3}-frame-\d{6})\.[^.]+", part,
                             re.IGNORECASE)
        if match:
            return match[1].casefold()
    return None


def _persisted_frame_namespace(value: Any) -> tuple[str, ...] | None:
    """Return the source namespace preceding a generic frame path."""

    if not isinstance(value, str):
        return None
    parts = tuple(part for part in value.replace("\\", "/").split("/") if part)
    if "gameplay" in (part.casefold() for part in parts):
        index = next(index for index, part in enumerate(parts)
                     if part.casefold() == "gameplay")
        return tuple(part.casefold() for part in parts[:index])
    for index, part in enumerate(parts):
        if re.fullmatch(r"part-\d{3}", part, re.IGNORECASE):
            return tuple(item.casefold() for item in parts[:index])
    return None


def _persisted_source_lines(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the independent detector lines retained by a serialized row."""

    ocr = source.get("ocr")
    if isinstance(ocr, dict) and isinstance(ocr.get("neural"), (list, tuple)):
        return [line for line in ocr["neural"] if isinstance(line, dict)]
    for key in ("preview_lines", "lines"):
        values = source.get(key)
        if isinstance(values, (list, tuple)):
            return [line for line in values if isinstance(line, dict)]
    return []


_PERSISTED_SIGNED_TOKEN_RE = re.compile(r"\+\s*(\d{1,4})")


def _persisted_signed_token_centers(
    line: dict[str, Any],
) -> list[tuple[int, float]]:
    """Estimate horizontal centers for signed tokens in one OCR line.

    Neural OCR can return ``+3 +10`` as one line and one enclosing box.  The
    line itself has no per-token geometry, so character-span interpolation is
    the only source information available to the cached consumer.  Returning
    token centers, rather than treating the entire line as every overlapping
    field, prevents a neighboring amount from becoming evidence for the
    selected crop.  Ambiguity is handled by the caller's nearest-column test.
    """

    text = _line_text(line) or ""
    matches = list(_PERSISTED_SIGNED_TOKEN_RE.finditer(text))
    box = _line_box(line)
    if not matches or box is None or not text:
        return []
    left, _top, right, _bottom = box
    width = right - left
    text_length = float(len(text))
    return [
        (
            int(match[1]),
            left + width * ((match.start() + match.end()) / (2.0 * text_length)),
        )
        for match in matches
    ]


def _persisted_source_amounts(
    source: dict[str, Any],
    region_box: tuple[float, float, float, float],
    *,
    field: str,
) -> tuple[set[int], bool]:
    """Return field-local signed amounts and whether their binding is sound.

    The return value is ``(values, ambiguous)``.  A merged line contributes a
    value only when token geometry maps it to the requested canonical field.
    If two localized source lines disagree, the field is ambiguous and the
    persisted recovery is rejected.  A line belonging to a neighboring field
    is ignored rather than copied into this field's evidence set.
    """

    if field not in _PREVIEW_FIELD_CENTERS:
        return set(), True
    values: set[int] = set()
    target_center = _PREVIEW_FIELD_CENTERS[field]
    ambiguous = False
    left, top, right, bottom = region_box
    for line in _persisted_source_lines(source):
        box = _line_box(line)
        if box is None or _confidence(line) < 80:
            continue
        line_left, line_top, line_right, line_bottom = box
        horizontal = max(0.0, min(right, line_right) - max(left, line_left))
        vertical = max(0.0, min(bottom, line_bottom) - max(top, line_top))
        if horizontal <= 0 or vertical <= 0:
            continue
        # A result total immediately below the preview row may touch the
        # region by a few pixels.  Require substantial vertical contact with
        # the signed row while allowing a merged line to span two columns.
        line_height = max(1.0, line_bottom - line_top)
        region_height = max(1.0, bottom - top)
        if vertical / min(line_height, region_height) < 0.45:
            continue
        for amount, token_center in _persisted_signed_token_centers(line):
            ranked = sorted(
                (
                    (abs(token_center - center), candidate)
                    for candidate, center in _PREVIEW_FIELD_CENTERS.items()
                ),
                key=lambda item: item[0],
            )
            if not ranked:
                continue
            nearest_distance, nearest_field = ranked[0]
            second_distance = ranked[1][0] if len(ranked) > 1 else float("inf")
            if second_distance - nearest_distance < _PERSISTED_TOKEN_FIELD_MARGIN:
                # A token close to two columns cannot support either one.
                if nearest_field == field or abs(token_center - target_center) <= (right - left) / 2:
                    ambiguous = True
                continue
            if nearest_field == field:
                values.add(amount)
    if len(values) > 1:
        ambiguous = True
    return values, ambiguous


def _persisted_recovery_observations_match(
    recovery: dict[str, Any], regions: dict[str, Any],
) -> bool:
    """Check that selected crop values still agree with their observations."""

    requests = recovery.get("requests")
    observations = recovery.get("observations")
    if not isinstance(requests, list) or not isinstance(observations, list):
        return False
    if len(requests) != len(observations):
        return False
    request_ids: list[str] = []
    for request, observation in zip(requests, observations):
        if not isinstance(request, dict) or not isinstance(observation, dict):
            return False
        request_id = request.get("id")
        if not isinstance(request_id, str) or not request_id or request_id in request_ids:
            return False
        request_ids.append(request_id)
        if observation.get("request_id") != request_id:
            return False
        region_name = request.get("region")
        if not isinstance(region_name, str) or observation.get("region") != region_name:
            return False
        region = regions.get(region_name)
        selected = observation.get("selected")
        if region is None:
            if selected is not None:
                return False
            continue
        if not isinstance(region, dict) or not isinstance(selected, dict):
            return False
        if observation.get("box") != region.get("box"):
            return False
        if selected.get("text") != region.get("text"):
            return False
        if selected.get("parsed_value") != region.get("parsed_value"):
            return False
        if selected.get("eligible") is not True:
            return False
    return True


def _persisted_source_record(
    source: dict[str, Any], recovery: dict[str, Any], source_root: str | Path | None,
) -> dict[str, Any] | None:
    """Load the immutable neural record that owns a persisted recovery.

    Report JSON normally has no filesystem context.  When a worker supplies
    its validated root, resolve exactly one ``neural/<frame>.json`` sibling
    from the evidence namespace, verify its complete fingerprint and source
    fields, and return that record as the independent OCR witness.  This is a
    deterministic lookup; it never scans for a convenient matching file.
    """

    if source_root is None:
        return None
    try:
        root = Path(source_root).resolve()
        evidence = (root / str(source.get("evidence"))).resolve()
        frame = (root / str(recovery.get("source_frame_evidence"))).resolve()
        evidence.relative_to(root)
        frame.relative_to(root)
        if not evidence.is_file() or not frame.is_file():
            return None
        from .preview_recovery import file_fingerprint, fingerprint, gameplay_fingerprint
        if recovery.get("evidence_sha256") != file_fingerprint(evidence):
            return None
        if recovery.get("gameplay_sha256") != gameplay_fingerprint(evidence):
            return None
        if recovery.get("source_frame_sha256") != file_fingerprint(frame):
            return None

        # The serialized report keeps the OCR lines but not a path to the
        # immutable neural record.  Resolve that record from the evidence
        # namespace and bind its complete bytes to the recovery fingerprint.
        # This is one deterministic sibling lookup, never a directory scan.
        # Older serialized reports kept the frame id only in the recovery
        # envelope.  The envelope is still bound below to the exact source
        # frame path/hash and neural witness, so using that declared id here
        # preserves compatibility without falling back to a directory scan.
        source_frame_id = source.get("source_frame_id")
        if source_frame_id is None:
            source_frame_id = recovery.get("source_frame_id")
        if not isinstance(source_frame_id, str) or not source_frame_id:
            return None
        evidence_parts = tuple(Path(str(source.get("evidence"))).parts)
        candidates: list[Path] = []
        for index, part in enumerate(evidence_parts):
            if part.casefold() == "gameplay":
                candidates.append(
                    root.joinpath(*evidence_parts[:index], "neural",
                                  f"{source_frame_id}.json")
                )
        if not candidates:
            candidates.append(root / "neural" / f"{source_frame_id}.json")
        neural_path = None
        for candidate in candidates:
            try:
                candidate = candidate.resolve()
                candidate.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            if candidate.is_file():
                neural_path = candidate
                break
        if neural_path is None:
            return None
        raw = json.loads(neural_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or fingerprint(raw) != recovery.get("raw_sha256"):
            return None
        raw_evidence = raw.get("evidence")
        if not isinstance(raw_evidence, str) or not raw_evidence:
            return None
        raw_evidence_value = Path(raw_evidence)
        if raw_evidence_value.is_absolute() or ".." in raw_evidence_value.parts:
            return None
        raw_evidence_path = (neural_path.parent.parent / raw_evidence_value).resolve()
        if raw_evidence_path != evidence:
            return None
        source_metadata = {
            "source_timestamp_ms": source.get("source_timestamp_ms"),
            "source_frame_sha256": source.get("source_frame_sha256"),
            "gameplay_sha256": source.get("gameplay_sha256"),
            "model_sha256": source.get("model_sha256"),
            "engine_fingerprint": source.get("engine_fingerprint"),
        }
        # Older report serializers omitted the source hashes from the row,
        # while retaining them in the source-bound recovery envelope.  The
        # files above are still rehashed and the neural record is fingerprint
        # checked, so using those declared values here is compatibility for
        # the envelope schema rather than a trust fallback.
        source_metadata.update({
            "source_frame_sha256": recovery.get("source_frame_sha256")
            if source_metadata["source_frame_sha256"] is None
            else source_metadata["source_frame_sha256"],
            "gameplay_sha256": recovery.get("gameplay_sha256")
            if source_metadata["gameplay_sha256"] is None
            else source_metadata["gameplay_sha256"],
            "model_sha256": recovery.get("source_model_sha256")
            if source_metadata["model_sha256"] is None
            else source_metadata["model_sha256"],
            "engine_fingerprint": recovery.get("source_engine_fingerprint")
            if source_metadata["engine_fingerprint"] is None
            else source_metadata["engine_fingerprint"],
        })
        for key in (
            "source_timestamp_ms", "source_frame_sha256", "gameplay_sha256",
            "model_sha256", "engine_fingerprint",
        ):
            if raw.get(key) != source_metadata.get(key):
                return None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return raw


def _persisted_preview_identity(
    source: dict[str, Any], recovery: dict[str, Any],
    *, source_root: str | Path | None = None,
) -> bool:
    """Bind a serialized recovery envelope to its owning report row.

    ``validated_recovery`` remains the authority for normal raw observations,
    where the attached regions are still present.  Report serializers retain
    the complete recovery envelope but may omit that transient top-level
    region map, so this boundary checks the immutable row identity and the
    sidecar's own source verification before allowing report reassembly to
    reuse its typed effects.
    """

    # A report row with only a serialized recovery envelope has no trusted
    # source witness of its own.  Its OCR and envelope are mutable report
    # output, so formatting/path checks cannot make that recovery safe to
    # promote.  Callers that need persisted recovery must provide the
    # immutable cache root; ordinary typed facts remain available to legacy
    # callers without a root.
    if source_root is None:
        return False

    facts = source.get("facts")
    if not isinstance(facts, dict) or facts.get("preview_recovery") != recovery:
        return False
    if recovery.get("version") != 1 or recovery.get("schema_version") != "tracen-replay/preview-recovery-v1" \
            or recovery.get("stage") != "preview_recovery":
        return False
    if recovery.get("independent_observations") is not False:
        return False

    timestamp = recovery.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0 or timestamp != source.get("source_timestamp_ms"):
        return False
    if recovery.get("evidence") != source.get("evidence"):
        return False
    for key in ("raw_sha256", "source_frame_sha256", "gameplay_sha256", "evidence_sha256"):
        if key in recovery and not _HASH_RE.fullmatch(str(recovery.get(key))):
            return False
    for recovery_key, source_key in (
        ("source_frame_sha256", "source_frame_sha256"),
        ("gameplay_sha256", "gameplay_sha256"),
        ("source_sha256", "source_sha256"),
        ("source_engine_fingerprint", "engine_fingerprint"),
    ):
        source_value = source.get(source_key)
        if source_value is None:
            source_value = recovery.get(recovery_key)
        if recovery.get(recovery_key) != source_value:
            return False
    source_model_sha = source.get("model_sha256")
    if source_model_sha is None:
        source_model_sha = recovery.get("source_model_sha256")
    if recovery.get("source_model_sha256") != source_model_sha:
        return False
    source_frame_id_value = source.get("source_frame_id")
    if source_frame_id_value is None:
        source_frame_id_value = recovery.get("source_frame_id")
    if recovery.get("source_frame_id") != source_frame_id_value:
        return False
    # The report row is the independently parsed source record.  Bind the
    # persisted crop to its frame identity and namespace instead of trusting
    # a safe-looking path supplied by the recovery envelope.  This catches a
    # recovery copied from a different frame even when its hashes are merely
    # well-formed and both mirrored copies agree.
    source_frame_id = source_frame_id_value
    if not isinstance(source_frame_id, str) or not source_frame_id:
        return False
    source_frame_id = source_frame_id.casefold()
    source_evidence_id = _persisted_frame_identity(source.get("evidence"))
    if source_evidence_id is None or source_evidence_id != source_frame_id:
        return False
    recovery_models = recovery.get("recovery_model_sha256")
    if (
        not isinstance(recovery_models, dict)
        or not recovery_models
        or any(
            not isinstance(name, str)
            or not _HASH_RE.fullmatch(str(value))
            for name, value in recovery_models.items()
        )
    ):
        return False
    if not _HASH_RE.fullmatch(str(recovery.get("recovery_engine_fingerprint"))):
        return False

    verification = recovery.get("source_frame_verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified":
        return False
    if verification.get("sha256") != recovery.get("source_frame_sha256"):
        return False
    if not _persisted_preview_path(verification.get("path")):
        return False
    source_frame_evidence = recovery.get("source_frame_evidence")
    if not _persisted_preview_path(source_frame_evidence):
        return False
    if verification.get("path") != source_frame_evidence:
        return False
    if _persisted_frame_identity(source_frame_evidence) != source_frame_id:
        return False
    if (_persisted_frame_namespace(source.get("evidence"))
            != _persisted_frame_namespace(source_frame_evidence)):
        return False
    # With a worker source root, use the independently loaded neural record as
    # the OCR witness.  The serialized report row is mutable output and may
    # retain copied or merged lines; accepting it after only checking digest
    # formatting would let a coordinated envelope mutation change a field.
    source_witness = source
    if source_root is not None:
        source_witness = _persisted_source_record(source, recovery, source_root)
        if source_witness is None:
            return False

    phase = recovery.get("phase")
    if not isinstance(phase, dict):
        return False
    if (
        phase.get("status") != "resolved"
        or phase.get("menu_proven") is not True
        or phase.get("result_proven") is not False
        or _explicit_phase_proof({"preview_phase_proof": phase}) is None
    ):
        return False
    controls = phase.get("control_proof")
    if not isinstance(controls, list) or not controls:
        return False
    valid_control_text = False
    for control in controls:
        if not isinstance(control, dict):
            return False
        text = _text(control.get("text"))
        box = _finite_box(control.get("box"))
        confidence = control.get("confidence")
        if (
            text is None
            or box is None
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 100
        ):
            return False
        normalized = text.casefold().replace(" ", "")
        if normalized in {name.replace(" ", "") for name in _PREVIEW_CONTROL_NAMES} \
                or _TRAINING_OPTION_RE.fullmatch(text):
            valid_control_text = True
    if not valid_control_text:
        return False
    marker_proof = phase.get("modifier_marker_proof")
    if not isinstance(marker_proof, list):
        return False
    for marker in marker_proof:
        if not isinstance(marker, dict):
            return False
        if _text(marker.get("text")) is None or _finite_box(marker.get("box")) is None:
            return False
        confidence = marker.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 100
        ):
            return False

    option = _canonical_option(recovery.get("option"))
    if recovery.get("option") is not None and option is None:
        return False
    phase_option = _canonical_option(phase.get("option"))
    if phase.get("option") is not None and phase_option is None:
        return False
    if phase_option != option and phase_option is not None:
        return False
    for value in (
        facts.get("preview_option"),
        source.get("preview_option"),
        (source.get("stats") or {}).get("preview_option")
        if isinstance(source.get("stats"), dict) else None,
    ):
        explicit = _canonical_option(value)
        if value is not None and explicit is None:
            return False
        if explicit is not None and explicit != option:
            return False

    regions = recovery.get("regions")
    if not isinstance(regions, dict) or not regions:
        return False
    for name, region in regions.items():
        if not isinstance(name, str) or not isinstance(region, dict):
            return False
        if not name.startswith("preview."):
            return False
        if region.get("input_eligible") is not True:
            return False
        if not isinstance(region.get("source_request_id"), str) or not region.get("source_request_id"):
            return False
        expected_box = None
        expected_request_id = None
        expected_geometry_basis = None
        if name.startswith("preview.main."):
            field_name = name.removeprefix("preview.main.").casefold()
            expected_box = _PREVIEW_MAIN_REGION_BOXES.get(field_name)
            expected_request_id = f"preview-main:{field_name}"
            expected_geometry_basis = "fixed_preview_main_row_geometry"
        elif name.startswith("preview.modifier."):
            field_name = name.removeprefix("preview.modifier.").casefold()
            expected_box = _PREVIEW_MODIFIER_REGION_BOXES.get(field_name)
            expected_request_id = f"preview-modifier:{field_name}"
            expected_geometry_basis = "fixed_preview_modifier_row_geometry"
        if expected_box is None or expected_request_id is None:
            return False
        if region.get("source_request_id") != expected_request_id:
            return False
        geometry_basis = region.get("geometry_basis")
        if geometry_basis != expected_geometry_basis:
            return False
        box = _finite_box(region.get("box"))
        if box is None or list(box) != list(expected_box):
            return False
        confidence = region.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return False
        if not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 100:
            return False
        text = _text(region.get("text"))
        match = _SIGNED_AMOUNT_RE.fullmatch(text or "")
        parsed_value = region.get("parsed_value")
        if match is None or type(parsed_value) is not int or parsed_value < 0:
            return False
        if int(match[1]) != parsed_value:
            return False
        field = _text(region.get("field"))
        if field is None or field.casefold() != field_name:
            return False

    if not _persisted_recovery_observations_match(recovery, regions):
        return False

    def check_effects(key: str, expected_role: str) -> bool:
        effects = recovery.get(key)
        if not isinstance(effects, list):
            return False
        for effect in effects:
            if not isinstance(effect, dict):
                return False
            kind = effect.get("kind")
            if key == "effects":
                if kind not in ("stat_change", "performance_change"):
                    return False
            elif kind != "song_modifier_change":
                return False
            field = _text(effect.get("field"))
            if field is None:
                return False
            field = {"vocals": "vocal", "visuals": "visual"}.get(field.casefold(), field.casefold())
            fields = STAT_FIELDS if kind == "stat_change" else PERFORMANCE_FIELDS
            if kind == "song_modifier_change":
                fields = STAT_FIELDS | PERFORMANCE_FIELDS
                if effect.get("modifier") != "song":
                    return False
            if field not in fields:
                return False
            amount = effect.get("amount")
            if type(amount) is not int or amount < 0 or amount > 10000:
                return False
            if effect.get("phase") not in (None, "preview") or effect.get("preview") not in (None, True):
                return False
            if effect.get("awarded") not in (None, False) or effect.get("applied") is True:
                return False
            region_name = effect.get("source_region")
            region = regions.get(region_name)
            if not isinstance(region_name, str) or not isinstance(region, dict):
                return False
            if not region_name.startswith(expected_role):
                return False
            region_field = _text(region.get("field"))
            region_field = {"vocals": "vocal", "visuals": "visual"}.get(
                region_field.casefold(), region_field.casefold()
            ) if region_field else None
            if region_field != field or region.get("parsed_value") != amount:
                return False
        return True

    if not check_effects("effects", "preview.main."):
        return False
    if not check_effects("modifier_effects", "preview.modifier."):
        return False

    # Serialized rows retain the parser's detector lines under ``ocr`` even
    # after the transient raw regions have been removed.  Use those lines as
    # an independent source witness for every readable selected crop.  A
    # merged line such as ``+3 +10`` is split by token position and can support
    # only the canonical column to which each token is localized.  Recovery
    # fields for which the base OCR has no token remain supported by the
    # already verified source-frame sidecar; a contradiction is always fatal.
    source_line_count = 0
    for key in ("effects", "modifier_effects"):
        for effect in recovery.get(key, []):
            region_name = effect.get("source_region")
            region = regions.get(region_name)
            if not isinstance(region, dict):
                return False
            region_box = _finite_box(region.get("box"))
            if region_box is None:
                return False
            field = _text(region.get("field"))
            if not field:
                field = _text(effect.get("field"))
            if not field:
                return False
            source_amounts, source_ambiguous = _persisted_source_amounts(
                source_witness, region_box, field=field.casefold()
            )
            if source_ambiguous:
                return False
            if source_amounts:
                source_line_count += 1
                if effect.get("amount") not in source_amounts:
                    return False
    if source_line_count == 0:
        return False
    return True


def _validated_persisted_preview_recovery(
    source: dict[str, Any], recovery: dict[str, Any],
    *, source_root: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return a report recovery only when its complete proof envelope binds."""

    if not _persisted_preview_identity(source, recovery, source_root=source_root):
        return None
    return deepcopy(recovery)


def _preview_effect_identity(effect: dict[str, Any]) -> tuple[Any, ...]:
    """Return the field identity used to replace a native preview reading."""

    return (
        effect.get("kind"),
        effect.get("field"),
        effect.get("modifier"),
    )


def _source_bound_persisted_overlay(
    source: dict[str, Any], recovery: dict[str, Any],
    source_root: str | Path,
    *, minimum_confidence: float = 90,
) -> dict[str, Any] | None:
    """Rebuild persisted preview channels from an immutable source witness.

    A serialized report can contain correct-looking typed facts alongside a
    recovery envelope.  Those facts are mutable report output, so changing a
    value and reserializing the report must not change a source-root build.
    Reparse the verified neural record, then use the independently validated
    recovery only for fields it explicitly owns.
    """

    validated = _validated_persisted_preview_recovery(
        source, recovery, source_root=source_root
    )
    if validated is None:
        return None
    witness = _persisted_source_record(source, validated, source_root)
    if witness is None:
        return None
    native = _parse_preview_overlay(
        witness, minimum_confidence=minimum_confidence
    )
    # The witness is the same physical frame as ``source`` but its record
    # names the frame relative to its own capture namespace (for example
    # ``gameplay/...`` inside ``initial-baseline/``), whereas the reading has
    # already been rebased to the worker root.  Retained native channels must
    # cite the frame by the reading's root-relative path, never by the
    # namespace-relative string, so consumers can resolve every evidence file.
    reading_evidence = _source_evidence(source)
    witness_evidence = _text(witness.get("evidence"))

    def _cite_reading_frame(value: Any) -> Any:
        if isinstance(value, str):
            if witness_evidence and value == witness_evidence and reading_evidence:
                return reading_evidence[0]
            return value
        if isinstance(value, list):
            return [_cite_reading_frame(item) for item in value]
        if isinstance(value, dict):
            return {key: _cite_reading_frame(item) for key, item in value.items()}
        return value

    native = _cite_reading_frame(native)

    result = {
        "preview_option": validated.get("option") or native.get("preview_option"),
        "preview_overlay_proven": False,
        "preview_overlay_effects": [],
        "preview_modifier_proven": False,
        "preview_modifier_effects": [],
        "preview_phase_proof": deepcopy(
            validated.get("phase") or native.get("preview_phase_proof") or {}
        ),
        "preview_overlay_evidence": deepcopy(
            native.get("preview_overlay_evidence") or {}
        ),
        "rejected_counts": deepcopy(native.get("rejected_counts") or {}),
    }

    for recovery_key, output_key in (
        ("effects", "preview_overlay_effects"),
        ("modifier_effects", "preview_modifier_effects"),
    ):
        recovered = validated.get(recovery_key)
        if not isinstance(recovered, list):
            return None
        recovered_effects: list[dict[str, Any]] = []
        recovered_identities: set[tuple[Any, ...]] = set()
        for item in recovered:
            if not isinstance(item, dict):
                return None
            region_name = item.get("source_region")
            regions = validated.get("regions")
            region = regions.get(region_name) if isinstance(regions, dict) else None
            if not isinstance(region_name, str) or not isinstance(region, dict):
                return None
            effect = deepcopy(item)
            effect["source_evidence"] = _source_evidence(source)
            effect["source_references"] = [f"region:{region_name}"]
            effect["preview_option"] = validated.get("option")
            effect["preview_geometry"] = {
                "role": "song_modifier_row"
                if recovery_key == "modifier_effects" else "main_training_row",
                "box": deepcopy(region.get("box")),
                "basis": "validated_source_crop_consensus",
            }
            if recovery_key == "modifier_effects":
                marker = _preview_song_modifier_marker(
                    _preview_source_lines(witness), minimum_confidence
                )
                if marker is None:
                    return None
                effect["preview_geometry"].update(
                    marker="concert_bonuses",
                    marker_references=marker.get("source_references", []),
                    marker_evidence=deepcopy(marker),
                )
            recovered_effects.append(effect)
            recovered_identities.add(_preview_effect_identity(effect))

        native_effects = native.get(
            "preview_modifier_effects"
            if recovery_key == "modifier_effects" else "preview_overlay_effects",
            [],
        )
        if not isinstance(native_effects, list):
            native_effects = []
        # A validated crop is the explicit source-bound resolution for its
        # field.  Remove the native reading for that same field, while keeping
        # unrelated values independently reparsed from the immutable witness.
        retained_native = [
            deepcopy(item) for item in native_effects
            if isinstance(item, dict)
            and _preview_effect_identity(item) not in recovered_identities
        ]
        result[output_key] = retained_native + recovered_effects
        result[
            "preview_modifier_proven"
            if recovery_key == "modifier_effects" else "preview_overlay_proven"
        ] = bool(result[output_key])

    if result["preview_overlay_effects"] or result["preview_modifier_effects"]:
        evidence = result["preview_overlay_evidence"]
        if not isinstance(evidence, dict):
            evidence = {}
        evidence.update({
            "basis": "validated_source_crop_consensus",
            "phase": deepcopy(validated.get("phase", {})),
            "source_evidence": _source_evidence(source),
        })
        result["preview_overlay_evidence"] = evidence
    return result


def _native_witness_overlay(
    source: dict[str, Any], source_root: str | Path,
    *, minimum_confidence: float = 90,
) -> dict[str, Any] | None:
    """Reparse a row's own immutable neural witness for its native previews.

    A worker row whose in-memory recovery envelope cannot be bound to a
    witness (the envelope was built before the record carried its frame
    identity) still has an immutable ``neural/<frame>.json`` sibling under
    the caller's root.  Locate it deterministically, verify that it names the
    same frame, timestamp and gameplay pixels as the row, and reparse it with
    the ordinary overlay parser.  Mutable report lists are never used; the
    recovery envelope is not promoted.
    """

    try:
        root = Path(source_root).resolve()
        evidence_value = source.get("evidence")
        if not isinstance(evidence_value, str) or not evidence_value:
            return None
        evidence = (root / evidence_value).resolve()
        evidence.relative_to(root)
        if not evidence.is_file():
            return None
        source_frame_id = source.get("source_frame_id")
        if not isinstance(source_frame_id, str) or not source_frame_id:
            return None
        evidence_parts = tuple(Path(evidence_value).parts)
        candidates: list[Path] = []
        for index, part in enumerate(evidence_parts):
            if part.casefold() == "gameplay":
                candidates.append(root.joinpath(*evidence_parts[:index], "neural", f"{source_frame_id}.json"))
        if not candidates:
            candidates.append(root / "neural" / f"{source_frame_id}.json")
        neural_path = None
        for candidate in candidates:
            try:
                candidate = candidate.resolve()
                candidate.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            if candidate.is_file():
                neural_path = candidate
                break
        if neural_path is None:
            return None
        raw = json.loads(neural_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        raw_evidence = raw.get("evidence")
        if not isinstance(raw_evidence, str) or not raw_evidence:
            return None
        raw_evidence_value = Path(raw_evidence)
        if raw_evidence_value.is_absolute() or ".." in raw_evidence_value.parts:
            return None
        if (neural_path.parent.parent / raw_evidence_value).resolve() != evidence:
            return None
        timestamp = source.get("source_timestamp_ms")
        if type(timestamp) is not int or raw.get("source_timestamp_ms") != timestamp:
            return None
        from .preview_recovery import gameplay_fingerprint
        pixels = gameplay_fingerprint(evidence)
        if raw.get("gameplay_sha256") != pixels:
            return None
        if source.get("gameplay_sha256") is not None and source.get("gameplay_sha256") != pixels:
            return None
        for key in ("source_frame_sha256", "model_sha256", "engine_fingerprint"):
            if source.get(key) is not None and raw.get(key) is not None and source.get(key) != raw.get(key):
                return None
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    # The witness still carries the attached envelope; strip it so the parse
    # is the plain OCR view and nothing from the unbound recovery is promoted.
    witness = {key: value for key, value in raw.items() if key not in ("preview_recovery", "regions")}
    native = _parse_preview_overlay(witness, minimum_confidence=minimum_confidence)
    reading_evidence = _source_evidence(source)
    witness_evidence = _text(witness.get("evidence"))

    def _cite(value: Any) -> Any:
        if isinstance(value, str):
            if witness_evidence and value == witness_evidence and reading_evidence:
                return reading_evidence[0]
            return value
        if isinstance(value, list):
            return [_cite(item) for item in value]
        if isinstance(value, dict):
            return {key: _cite(item) for key, item in value.items()}
        return value

    return _cite(native)


def _abstain_unverified_persisted_preview(
    parsed: dict[str, Any], reason: str,
) -> dict[str, Any]:
    """Clear persisted preview channels when their immutable witness fails."""

    result = deepcopy(parsed)
    for key in (
        "preview_overlay_effects", "preview_modifier_effects",
        "preview_phase_proof", "preview_overlay_evidence",
    ):
        result[key] = {} if key.endswith(("proof", "evidence")) else []
    result["preview_overlay_proven"] = False
    result["preview_modifier_proven"] = False
    result["preview_option"] = None
    rejected = result.get("rejected_counts")
    if not isinstance(rejected, dict):
        rejected = {}
    rejected[reason] = int(rejected.get(reason, 0)) + 1
    result["rejected_counts"] = dict(sorted(rejected.items()))
    return result


def _abstain_parser_option_conflict(parsed: dict[str, Any]) -> dict[str, Any]:
    """Clear preview effects when source-owned option declarations disagree."""

    result = deepcopy(parsed)
    for key in (
        "preview_overlay_effects", "preview_modifier_effects",
        "preview_phase_proof", "preview_overlay_evidence",
    ):
        result[key] = {} if key.endswith("proof") or key.endswith("evidence") else []
    result["preview_overlay_proven"] = False
    result["preview_modifier_proven"] = False
    result["preview_option"] = None
    rejected = result.get("rejected_counts")
    if not isinstance(rejected, dict):
        rejected = {}
    rejected["conflicting_parser_preview_options"] = (
        int(rejected.get("conflicting_parser_preview_options", 0)) + 1
    )
    result["rejected_counts"] = dict(sorted(rejected.items()))
    return result


def parse_preview_overlay(
    source: Any, *, minimum_confidence: float = 90,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Combine ordinary parsing with validated same-frame preview recovery."""
    parsed = _parse_preview_overlay(source, minimum_confidence=minimum_confidence)
    if not isinstance(source, dict):
        return parsed
    if _parser_preview_option_conflict(
        source, minimum_confidence=float(minimum_confidence)
    ):
        # A typed panel cannot arbitrate an OCR/source option disagreement.
        # Clear ordinary effects as well as recovery effects: retaining the
        # panel's amount would still claim the wrong card was selected.
        return _abstain_parser_option_conflict(parsed)
    from .preview_recovery import validated_recovery
    recovery = None
    raw_recovery = source.get('preview_recovery')
    if not isinstance(raw_recovery, dict):
        facts = source.get('facts')
        if isinstance(facts, dict):
            raw_recovery = facts.get('preview_recovery')
    if not isinstance(raw_recovery, dict):
        return parsed

    # Serialized report rows keep recovery under ``facts``.  Their typed
    # overlay/modifier lists are mutable report output, so a source-root
    # caller must rebuild those channels from the independently loaded neural
    # witness rather than using the row as the ordinary-parser base.
    facts = source.get("facts")
    persisted_report_shape = (
        isinstance(facts, dict)
        and isinstance(facts.get("preview_recovery"), dict)
        and isinstance(source.get("preview_recovery"), dict)
    )
    if (
        source_root is not None
        and persisted_report_shape
    ):
        source_bound = _source_bound_persisted_overlay(
            source, raw_recovery, source_root,
            minimum_confidence=minimum_confidence,
        )
        if source_bound is None:
            return _abstain_unverified_persisted_preview(
                parsed, "unverified_persisted_preview_recovery"
            )
        return source_bound

    # A report row may carry a mirrored top-level recovery for compatibility,
    # but the presence of ``facts.preview_recovery`` still identifies it as a
    # serialized report shape.  Without an immutable root, do not treat that
    # mirror as a fresh in-memory attachment.
    if persisted_report_shape and source_root is None:
        return parsed

    # Normal raw rows still take the complete source reconstruction path.  A
    # serialized report row may retain the attached, source-verified recovery
    # in ``facts`` while its transient ``regions`` map has been omitted from
    # the row envelope.  In that report shape the full validator cannot
    # reconstruct the original raw fingerprint; use the stricter persisted
    # report validator below rather than trusting the amounts directly.
    if isinstance(source.get('preview_recovery'), dict):
        recovery = validated_recovery(source)
    if recovery is None:
        recovery = _validated_persisted_preview_recovery(
            source, raw_recovery, source_root=source_root
        )
    if not recovery or recovery.get('status') != 'resolved':
        return parsed
    option = recovery.get('option')
    if parsed.get('preview_option') not in (None, option):
        return parsed
    result = deepcopy(parsed)
    # Recovery and direct parsing must inspect the same full detector view.
    # A cached result reader may keep only header/option lines in ``lines``
    # while the same-frame reread is stored in ``preview_lines``; using the
    # legacy collection here would silently drop a valid Concert Bonuses
    # marker from the dedicated modifier channel.
    marker = _preview_song_modifier_marker(
        _preview_source_lines(source), minimum_confidence
    )
    for recovery_key, output_key, proven_key in (
        ('effects', 'preview_overlay_effects', 'preview_overlay_proven'),
        ('modifier_effects', 'preview_modifier_effects', 'preview_modifier_proven'),
    ):
        effects = list(result.get(output_key, []))
        for recovered in recovery.get(recovery_key, []):
            region_name = recovered['source_region']
            region = recovery['regions'][region_name]
            effect = deepcopy(recovered)
            effect['source_evidence'] = _source_evidence(source)
            effect['source_references'] = [f'region:{region_name}']
            effect['preview_option'] = option
            effect['preview_geometry'] = dict(
                role='song_modifier_row' if recovery_key == 'modifier_effects' else 'main_training_row',
                box=deepcopy(region['box']), basis='validated_source_crop_consensus')
            if recovery_key == 'modifier_effects':
                if marker is None:
                    continue
                effect['preview_geometry'].update(marker='concert_bonuses',
                    marker_references=marker['source_references'], marker_evidence=deepcopy(marker))
            effects.append(effect)
        grouped = defaultdict(list)
        for effect in effects:
            grouped[(effect.get('kind'), effect.get('field'))].append(effect)
        accepted = []
        for identity, observations in grouped.items():
            if len({item.get('amount') for item in observations}) > 1:
                result.setdefault('rejected_counts', {}).setdefault('recovery_field_conflict', 0)
                result['rejected_counts']['recovery_field_conflict'] += 1
                result.setdefault('preview_recovery_conflicts', []).append(dict(
                    channel=output_key, kind=identity[0], field=identity[1], observations=observations))
            else:
                accepted.extend(observations)
        result[output_key] = accepted
        result[proven_key] = bool(accepted)
    result['preview_option'] = option
    result['preview_phase_proof'] = deepcopy(recovery['phase'])
    result['preview_overlay_evidence'] = dict(
        basis='validated_source_crop_consensus', phase=deepcopy(recovery['phase']),
        source_evidence=_source_evidence(source))
    return result


def _timestamp(value: Any) -> int | None:
    # bool is an int subclass, but cannot be a source timestamp.
    if type(value) is not int or value < 0:
        return None
    return value


def _amount(value: Any) -> int | None:
    # Preview amounts are small game values.  The bound catches malformed
    # inputs without imposing a recording-specific expected amount.
    if type(value) is not int or not -10000 <= value <= 10000:
        return None
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        item = _text(item)
        if item:
            result.append(item)
    return list(dict.fromkeys(result))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence(reading: dict[str, Any], effect: dict[str, Any]) -> list[str]:
    """Combine source-frame proof and optional field-level proof."""

    result = _evidence_paths(reading.get("evidence"))
    for key in ("evidence", "source_evidence", "field_evidence"):
        result.extend(_evidence_paths(effect.get(key)))
    return list(dict.fromkeys(result))


def _preview_option(reading: dict[str, Any], facts: dict[str, Any],
                    effect: dict[str, Any]) -> str | None:
    """Read only preview-labelled option fields.

    A top-level ``training_option`` belongs to a committed result in the
    report schema and is purposely not a fallback here.  The distinction is
    what prevents browsing one option from being reported as the selected
    action.
    """

    stats = reading.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    for value in (
        effect.get("preview_option"),
        effect.get("option"),
        facts.get("preview_option"),
        reading.get("preview_option"),
        stats.get("preview_option"),
    ):
        value = _text(value)
        if value:
            return value.casefold()
    return None


def _preview_context(reading: dict[str, Any], facts: dict[str, Any],
                     effect: dict[str, Any]) -> str | None:
    """Return an explicitly labelled offer/context title, if unambiguous."""

    for value in (effect.get("preview_context"), effect.get("context_title"),
                  facts.get("preview_context"), reading.get("preview_context"),
                  reading.get("context_title")):
        value = _text(value)
        if value:
            return value
    return None


def _semantic_marker(reading: dict[str, Any], facts: dict[str, Any],
                     effect: dict[str, Any]) -> str | None:
    for value in (effect.get("preview_semantics"), effect.get("source_semantics"),
                  facts.get("preview_semantics"), reading.get("preview_semantics")):
        value = _text(value)
        if value:
            return value.casefold()
    return None


def _typed_hint_metadata(effect: dict[str, Any]) -> tuple[dict[str, Any], str] | tuple[None, str]:
    """Copy the small, typed hint descriptors that explain an offer.

    These fields come from the lesson-card parser rather than OCR candidates.
    Keep them optional for older preview facts, but reject malformed values so
    a free-form label or truthy integer cannot silently become canonical hint
    metadata.
    """

    metadata: dict[str, Any] = {}
    if "category" in effect and effect.get("category") is not None:
        category = _text(effect.get("category"))
        if category is None or category.casefold() != "skill_hint":
            return {}, "invalid_typed_hint_category"
        metadata["category"] = "skill_hint"

    if "level" in effect and effect.get("level") is not None:
        level = _text(effect.get("level"))
        if level is None:
            return {}, "invalid_typed_hint_level"
        metadata["level"] = level.casefold()

    if "name_visible" in effect and effect.get("name_visible") is not None:
        if type(effect.get("name_visible")) is not bool:
            return {}, "invalid_typed_hint_name_visibility"
        metadata["name_visible"] = effect["name_visible"]

    if "raw_label" in effect and effect.get("raw_label") is not None:
        raw_label = _text(effect.get("raw_label"))
        if raw_label is None:
            return {}, "invalid_typed_hint_label"
        metadata["raw_label"] = raw_label

    return metadata, "accepted_typed_hint_metadata"


def _typed_source_offer(effect: dict[str, Any]) -> tuple[str | None, str | None]:
    """Validate an explicit source offer identity carried by one effect.

    A grouped lesson adapter may attach the card title to every projected
    effect.  Preserve that identity as typed metadata so multi-field cards
    can be joined without treating each effect as a separate purchase.  The
    field is accepted only with a parser-owned offer semantic or offer id;
    arbitrary context text must stay outside the scored payload.
    """

    if "source_offer" not in effect or effect.get("source_offer") is None:
        return None, None
    source_offer = _text(effect.get("source_offer"))
    if (
        source_offer is None
        or len(source_offer) > 256
        or any(ord(character) < 32 for character in source_offer)
    ):
        return None, "invalid_typed_source_offer"

    semantic = _text(effect.get("source_semantics"))
    if semantic is not None and semantic.casefold() not in _SEMANTIC_ALIASES:
        return None, "invalid_typed_source_offer"
    offer_id = effect.get("offer_id")
    if offer_id is not None:
        offer_id = _text(offer_id)
        if (
            offer_id is None
            or len(offer_id) > 256
            or any(ord(character) < 32 for character in offer_id)
        ):
            return None, "invalid_typed_source_offer"
    if semantic is None and offer_id is None:
        return None, "unproven_typed_source_offer"
    return source_offer, None


_LESSON_OFFER_COST_MAX = 10000
_LESSON_OFFER_PROOF_HASH_KEYS = (
    "evidence_sha256",
    "gameplay_sha256",
    "source_frame_sha256",
    "raw_sha256",
    "raw_fingerprint_sha256",
)


def _typed_offer_id(value: Any) -> str | None:
    """Return a bounded parser-owned offer identity.

    An offer id is a join key, not a display name.  Keep the validation small
    and structural so the bridge can work with future parser versions without
    accepting candidate objects or arbitrary numeric coercions.
    """

    value = _text(value)
    if value is None or len(value) > 256 or any(ord(char) < 32 for char in value):
        return None
    return value


def _lesson_offer_source_paths(
    proof: Any, reading_evidence: Iterable[str],
) -> list[str] | None:
    """Validate one source proof before using its typed costs.

    The lesson adapter has already checked image and raw-line hashes.  This
    boundary still requires a physical evidence path shared with the owning
    reading, a source digest, and a non-negative source-frame verification
    flag when present.  Geometry references such as ``line:14`` remain
    metadata and never enter an evidence list.
    """

    if not isinstance(proof, dict):
        return None
    if proof.get("source_frame_verified") is False:
        return None
    paths = _evidence_paths(proof.get("evidence"))
    if not paths:
        paths = _evidence_paths(proof.get("source_evidence"))
    if not paths:
        return None
    owning_paths = set(_evidence_paths(list(reading_evidence)))
    if owning_paths and not owning_paths.intersection(paths):
        return None
    if not any(
        isinstance(proof.get(key), str) and proof[key].strip()
        for key in _LESSON_OFFER_PROOF_HASH_KEYS
    ):
        return None
    return list(dict.fromkeys(paths))


def _lesson_offer_price_values(offer: dict[str, Any]) -> dict[str, int]:
    """Keep only cost values corroborated by accepted typed price slots.

    The rich lesson envelope contains a convenience ``cost`` mapping, while
    ``prices`` retains the per-slot source status.  Reading only the mapping
    would let an incomplete or hand-constructed envelope turn an unknown slot
    into zero.  A value is therefore eligible only when exactly the same field
    has an accepted integer price and no accepted conflicting value.
    """

    cost = offer.get("cost")
    prices = offer.get("prices")
    if not isinstance(cost, dict) or not isinstance(prices, list):
        return {}

    accepted: defaultdict[str, list[int]] = defaultdict(list)
    for price in prices:
        if not isinstance(price, dict):
            continue
        field = _text(price.get("field"))
        if field is None:
            continue
        field = field.casefold()
        if field not in PERFORMANCE_FIELDS:
            continue
        if price.get("status") != "accepted" or type(price.get("value")) is not int:
            continue
        value = price["value"]
        if 0 <= value <= _LESSON_OFFER_COST_MAX:
            accepted[field].append(value)

    result: dict[str, int] = {}
    for raw_field, raw_value in cost.items():
        field = _text(raw_field)
        if field is None:
            continue
        field = field.casefold()
        if field not in PERFORMANCE_FIELDS:
            continue
        if type(raw_value) is not int or not 0 <= raw_value <= _LESSON_OFFER_COST_MAX:
            continue
        values = accepted.get(field, [])
        # Multiple accepted values are a conflict even when the envelope's
        # convenience map happens to agree with one of them.
        if not values or len(set(values)) != 1 or values[0] != raw_value:
            continue
        result[field] = raw_value
    return result


def _lesson_offer_cost_entries(
    facts: dict[str, Any], reading_evidence: Iterable[str],
    rejected: Counter[str],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Read source-bound lesson costs without creating preview occurrences.

    The returned map is deliberately separate from ``PREVIEW_FACT_KEYS``:
    ``preview_effects`` owns the occurrence, while this envelope contributes
    optional cost metadata to that occurrence.  Duplicate offer ids are
    merged only when their known fields agree; overlapping disagreement makes
    that offer's cost unresolved.
    """

    envelope = facts.get("lesson_offer_preview")
    if envelope is None:
        return {}, set()
    if not isinstance(envelope, dict):
        rejected["invalid_lesson_offer_preview"] += 1
        return {}, set()
    if envelope.get("phase") not in (None, "preview"):
        rejected["non_preview_lesson_offer_envelope"] += 1
        return {}, set()
    if envelope.get("preview_only") is not True or envelope.get("committed") is not False:
        rejected["invalid_lesson_offer_preview_boundary"] += 1
        return {}, set()
    offers = envelope.get("offers")
    if not isinstance(offers, list):
        rejected["invalid_lesson_offer_preview_offers"] += 1
        return {}, set()

    envelope_paths = _lesson_offer_source_paths(
        envelope.get("source_proof"), reading_evidence
    )
    entries: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    for offer_index, offer in enumerate(offers):
        if not isinstance(offer, dict):
            rejected["invalid_lesson_offer_preview_offer"] += 1
            continue
        offer_id = _typed_offer_id(offer.get("offer_id"))
        if offer_id is None:
            rejected["invalid_lesson_offer_id"] += 1
            continue
        if offer.get("phase") not in (None, "preview"):
            rejected["non_preview_lesson_offer"] += 1
            continue
        if offer.get("preview_only") is not True or offer.get("committed") is not False:
            rejected["invalid_lesson_offer_boundary"] += 1
            continue

        proof = offer.get("source_proof")
        if proof is None:
            paths = envelope_paths
        else:
            paths = _lesson_offer_source_paths(proof, reading_evidence)
        if paths is None:
            rejected["unproven_lesson_offer_cost"] += 1
            continue

        name = _text(offer.get("name"))
        payload = offer.get("payload")
        if isinstance(payload, dict):
            payload_kind = _text(payload.get("kind"))
            if payload_kind is not None and payload_kind != "lesson":
                rejected["invalid_lesson_offer_payload"] += 1
                continue
            payload_name = _text(payload.get("name"))
            if name is None:
                name = payload_name
            elif payload_name is not None and payload_name != name:
                rejected["conflicting_lesson_offer_identity"] += 1
                conflicts.add(offer_id)
                continue
        if name is None:
            rejected["missing_lesson_offer_identity"] += 1
            continue

        cost = _lesson_offer_price_values(offer)
        current = entries.get(offer_id)
        candidate = {
            "offer_id": offer_id,
            "name": name,
            "cost": cost,
            "evidence": paths,
            "source_effect_index": f"lesson_offer_preview/offers/{offer_index}",
        }
        if current is None:
            entries[offer_id] = candidate
            continue
        if current["name"] != name:
            conflicts.add(offer_id)
            continue
        merged_cost = dict(current["cost"])
        conflict = False
        for field, value in cost.items():
            if field in merged_cost and merged_cost[field] != value:
                conflict = True
                break
            merged_cost[field] = value
        if conflict:
            conflicts.add(offer_id)
            continue
        current["cost"] = merged_cost
        current["evidence"] = list(dict.fromkeys(current["evidence"] + paths))
        current["source_effect_index"] = list(
            current["source_effect_index"]
            if isinstance(current["source_effect_index"], list)
            else [current["source_effect_index"]]
        ) + [candidate["source_effect_index"]]

    return entries, conflicts


def _enrich_lesson_offer_payload(
    reading: dict[str, Any], facts: dict[str, Any], effect: dict[str, Any],
    payload: dict[str, Any], offer_entries: dict[str, dict[str, Any]],
    offer_conflicts: set[str], rejected: Counter[str],
) -> tuple[dict[str, Any], str | None, list[str], list[str], bool]:
    """Attach validated cost metadata to one existing lesson occurrence."""

    offer_id = _typed_offer_id(effect.get("offer_id"))
    if payload.get("kind") != "lesson" or offer_id is None:
        return payload, offer_id, [], [], False
    if offer_id in offer_conflicts:
        rejected["conflicting_lesson_offer_cost"] += 1
        return payload, offer_id, [], [], False
    entry = offer_entries.get(offer_id)
    if entry is None or not entry.get("cost"):
        return payload, offer_id, [], [], False
    effect_name = _text(effect.get("name"))
    if effect_name is not None and effect_name != entry.get("name"):
        rejected["conflicting_lesson_offer_identity"] += 1
        return payload, offer_id, [], [], False
    enriched = deepcopy(payload)
    enriched["cost"] = deepcopy(entry["cost"])
    indices = entry.get("source_effect_index", [])
    if isinstance(indices, str):
        indices = [indices]
    return (
        enriched,
        offer_id,
        list(entry.get("evidence", [])),
        list(indices),
        True,
    )


def _has_explicit_preview_boundary(facts: dict[str, Any]) -> bool:
    """Return whether typed facts prove a browse menu for an unknown screen.

    The screen classifier is intentionally not treated as an authority when it
    says ``unknown``: transitions and narrow OCR panes can make it undecidable
    even though the source-bound preview parser has a positive menu proof.  A
    typed effect list alone is insufficient, however.  Reuse the same phase
    proof validation used by the parser so an unknown screen cannot be promoted
    from a name/amount candidate or a bare boolean marker.
    """

    if not isinstance(facts, dict):
        return False
    if _explicit_result_marker(facts):
        return False
    return _explicit_phase_proof(facts) is not None


def _is_preview_reading(reading: dict[str, Any], facts: dict[str, Any]) -> bool:
    """Reject known applied/committed frames before inspecting their facts."""

    # An overlay can fade over the same frame as a committed action.  Keeping
    # it in ``facts`` is useful for diagnostics, but it must never bypass the
    # committed-action boundary into a preview observation.
    if reading.get("completed_action") not in (None, False, ""):
        return False
    for key in ("phase", "source_phase"):
        value = reading.get(key)
        if value is not None and _text(value) != "preview":
            return False
    screen = _text(reading.get("screen"))
    screen_key = screen.casefold() if screen else None
    if screen_key in _APPLIED_SCREENS:
        # Result and receipt screens are an applied-effect boundary.  A
        # retained menu layer or typed panel from the same source frame cannot
        # prove that the user is still browsing, so it must not override this
        # classification.
        return False
    # A known screen must be one whose UI semantics expose an offer/preview.
    # An absent screen is allowed for legacy typed callers.  An unknown screen
    # requires a positive parser-owned menu proof; a typed amount by itself is
    # never enough to turn an undecidable frame into a preview.
    if screen_key and screen_key not in _PREVIEW_SCREENS:
        if (screen_key not in _UNKNOWN_PREVIEW_SCREENS
                or not _has_explicit_preview_boundary(facts)):
            return False
    if facts.get("awarded") is True or facts.get("applied") is True:
        return False
    return True


def _typed_payload(reading: dict[str, Any], facts: dict[str, Any],
                   effect: dict[str, Any], *, fact_key: str | None = None
                   ) -> tuple[dict[str, Any], str] | tuple[None, str]:
    """Validate one parser-typed effect without interpreting OCR text."""

    kind = _text(effect.get("kind"))
    if not kind:
        return None, "missing_typed_kind"
    flag_error = _typed_preview_child_flag_error(effect)
    if flag_error in ("phase", "source_phase"):
        return None, "non_preview_effect_phase"
    if flag_error == "awarded":
        return None, "already_awarded_or_unknown"
    if flag_error == "applied":
        return None, "already_applied"
    if flag_error == "preview":
        return None, "explicitly_not_preview"
    if flag_error is not None:
        return None, "malformed_typed_preview_flags"

    source_offer, source_offer_error = _typed_source_offer(effect)
    if source_offer_error is not None:
        return None, source_offer_error

    def finish(payload: dict[str, Any], reason: str) -> tuple[dict[str, Any], str]:
        if source_offer is not None:
            payload["source_offer"] = source_offer
        return payload, reason

    # ``immediate_on_purchase`` is an accepted parser type used for a
    # projected lesson effect.  Mapping it to the canonical stat kind is safe
    # only when the parser has already supplied a typed field and amount.
    original_kind = kind
    field = _text(effect.get("field"))
    if field:
        field = {"vocals": "vocal", "visuals": "visual"}.get(field.casefold(), field.casefold())
    if kind == "immediate_on_purchase":
        if field in STAT_FIELDS:
            kind = "stat_change"
        elif field in PERFORMANCE_FIELDS:
            kind = "performance_change"
        elif field == "energy":
            kind = "energy_change"
        else:
            return None, "untyped_purchase_projection"

    amount = _amount(effect.get("amount"))
    screen = _text(reading.get("screen"))
    if kind == "song_modifier_change":
        # Song modifiers have their own typed preview channel.  They are
        # visible components of a selected training menu, never ordinary
        # stat changes and never applied effects.
        if fact_key is not None and fact_key not in PREVIEW_MODIFIER_FACT_KEYS:
            return None, "song_modifier_wrong_fact_channel"
        if screen != "training_preview":
            return None, "unsupported_song_modifier_preview_screen"
        if effect.get("modifier") != "song":
            return None, "untyped_song_modifier_projection"
        if field not in (STAT_FIELDS | PERFORMANCE_FIELDS) or amount is None or amount < 0:
            return None, "untyped_song_modifier_projection"
        geometry = effect.get("preview_geometry")
        if not isinstance(geometry, dict) or geometry.get("role") != "song_modifier_row":
            return None, "unproven_song_modifier_geometry"
        if geometry.get("marker") != "concert_bonuses":
            return None, "unproven_song_modifier_marker"
        marker_references = list(dict.fromkeys(
            _strings(geometry.get("marker_references"))
            + _evidence_references(geometry.get("marker_evidence"))
        ))
        if not marker_references:
            return None, "unproven_song_modifier_marker"
        payload = {
            "kind": "training_modifier_change",
            "field": field,
            "amount": amount,
            "modifier": "song",
            "timing": "training preview",
        }
        return finish(payload, "accepted_typed_song_modifier_preview")
    if kind in ("future_training_modifier", "queued_concert_bonus", "training_modifier_change"):
        if screen not in _LESSON_SCREENS:
            return None, "unsupported_modifier_preview_screen"
        modifier_fields = STAT_FIELDS | frozenset(("specialty_priority",
            "friendship_training_effectiveness", "support_chain_event_frequency"))
        if field not in modifier_fields or amount is None:
            return None, "untyped_modifier_projection"
        payload = {"kind": "training_modifier_change", "field": field, "amount": amount}
        if kind == "queued_concert_bonus":
            payload["timing"] = "after concert"
        for key in ("unit", "timing"):
            if _text(effect.get(key)):
                payload[key] = _text(effect[key])
        return finish(payload, "accepted_typed_modifier_preview")
    if kind in ("stat_change", "performance_change"):
        if amount is None:
            return None, "missing_typed_amount"
        expected = STAT_FIELDS if kind == "stat_change" else PERFORMANCE_FIELDS
        if field not in expected:
            return None, "field_kind_mismatch"
        direction = _text(effect.get("direction"))
        if direction:
            direction = direction.casefold()
            if direction in ("down", "decrease"):
                amount = -abs(amount)
            elif direction in ("up", "increase"):
                if amount < 0:
                    return None, "direction_amount_conflict"
                amount = abs(amount)
            else:
                return None, "unsupported_direction"
        payload = {"kind": kind, "field": field, "amount": amount}
        return finish(
            payload,
            "mapped_immediate_on_purchase" if original_kind != kind else "accepted_typed_effect",
        )

    if kind == "energy_change":
        # A menu's positive Energy row is a typed preview projection.  It is
        # kept separate from applied energy receipts and requires the parser
        # to identify the field explicitly; this adapter never interprets a
        # raw label such as ``Energy +20`` on its own.
        if screen not in _LESSON_SCREENS:
            return None, "unsupported_energy_preview_screen"
        if field != "energy" or amount is None or amount < 0:
            return None, "untyped_energy_preview"
        return finish(
            {"kind": "energy_change", "amount": amount},
            "mapped_immediate_on_purchase" if original_kind != kind else "accepted_typed_energy_preview",
        )

    marker = _semantic_marker(reading, facts, effect)
    if kind == "skill_hint_change":
        # A hint amount is only meaningful when the parser identified a
        # lesson/skill offer (or supplied an explicit source semantic marker).
        if amount is None or not (screen in _LESSON_SCREENS or marker in _SEMANTIC_ALIASES):
            return None, "unsupported_hint_semantics"
        payload = {"kind": kind, "amount": amount}
        metadata, metadata_reason = _typed_hint_metadata(effect)
        if not metadata and metadata_reason != "accepted_typed_hint_metadata":
            return None, metadata_reason
        payload.update(metadata)
        name = _text(effect.get("name"))
        if name:
            payload["name"] = name
        return finish(payload, "accepted_typed_hint")

    if kind in ("lesson", "skill"):
        allowed = (
            kind == "lesson" and (screen in _LESSON_SCREENS or marker in ("lesson_offer", "lesson_preview"))
        ) or (
            kind == "skill" and (screen in _SKILL_SCREENS or marker in ("skill_offer", "skill_preview"))
        )
        if not allowed:
            return None, "unsupported_purchase_semantics"
        payload = {"kind": kind}
        name = _text(effect.get("name"))
        if not name:
            name = _preview_context(reading, facts, effect)
        if name:
            payload["name"] = name
        # An unlabelled lesson/skill offer is still a typed offer, but a
        # lesson without any identity is not useful as a canonical source.
        if kind == "lesson" and "name" not in payload:
            return None, "missing_offer_identity"
        return finish(payload, "accepted_typed_purchase_offer")

    # Other parser effect kinds (for example future modifiers and concert
    # bonuses) remain in their owning facts until they have a dedicated
    # canonical preview schema.  In particular, raw_text is never parsed here.
    return None, "unsupported_typed_preview_kind"


def _finite_box(value: Any) -> tuple[float, float, float, float] | None:
    """Validate a typed geometry box without accepting NaN or infinity."""

    box = _line_box({"box": value})
    if box is None or not all(math.isfinite(number) for number in box):
        return None
    pane_left, pane_top, pane_right, pane_bottom = _GAMEPLAY_PANE_BOUNDS
    if not (
        pane_left <= box[0] < box[2] <= pane_right
        and pane_top <= box[1] < box[3] <= pane_bottom
    ):
        return None
    return box


def _panel_proof_observation(
    value: Any, band: tuple[float, float, float, float],
) -> dict[str, Any] | None:
    """Sanitize one accepted performance-panel provenance observation.

    ``performance_panel_provenance`` is already a typed output of the
    performance sidebar reader.  It is still an input boundary: a malformed
    or explicitly excluded child must not become a preview amount merely
    because its parent declares a projected value.  The returned record keeps
    only the geometry and OCR text needed to explain the accepted source.
    """

    if not isinstance(value, dict):
        return None
    if "raw_text" in value or any(
        "candidate" in str(key).casefold() for key in value
    ):
        return None
    if value.get("input_eligible") is False:
        return None
    role = _text(value.get("role"))
    if role and any(
        marker in role.casefold()
        for marker in ("candidate", "excluded", "untrusted")
    ):
        return None
    text = _line_text(value)
    box = _finite_box(value.get("box"))
    if text is None or box is None:
        return None
    confidence = _confidence(value)
    if (not math.isfinite(confidence)
            or not 0 <= confidence <= 100
            or confidence < _PERFORMANCE_PANEL_PREVIEW_MIN_CONFIDENCE):
        return None
    # The row box is the semantic field anchor.  A number outside it may be
    # a neighboring stat/result glyph even when its text looks like a gain.
    if not (
        band[0] - 2 <= box[0]
        and box[1] >= band[1] - 2
        and box[2] <= band[2] + 2
        and box[3] <= band[3] + 2
    ):
        return None
    result = {
        "text": text,
        "box": [
            int(number) if number.is_integer() else number for number in box
        ],
        "confidence": confidence,
    }
    for key in ("component", "geometry_basis"):
        normalized = _text(value.get(key))
        if normalized:
            result[key] = normalized
    return result


def _panel_value(value: Any) -> int | None:
    """Read a nonnegative typed panel value, never a balance difference."""

    amount = _amount(value)
    return amount if amount is not None and amount >= 0 else None


def _performance_panel_projection(
    field: str, provenance: dict[str, Any],
) -> tuple[int, dict[str, Any], str] | tuple[None, str, str]:
    """Resolve one source-bound projected performance amount.

    A merged row must expose ``current+projected`` in the same labeled band.
    A separate projection must expose a signed projection (or an explicitly
    typed projected component).  The helper never consults performance
    balances, final totals, neighboring readings, or expected labels.
    """

    status = _text(provenance.get("status"))
    # A current-only balance is useful accounting evidence, but it is not a
    # preview candidate.  Treat it as an intentional no-op so normal frames
    # do not accumulate rejection noise for every untouched sidebar field.
    if status == "resolved_current_panel_value":
        return None, "no_performance_panel_preview_projection", status
    if status not in _PERFORMANCE_PANEL_PREVIEW_STATUSES:
        return None, "unsupported_performance_panel_preview_status", status or ""
    band = _finite_box(provenance.get("band"))
    if band is None or not (
        120 <= band[0] < band[2] <= 400
        and 250 <= band[1] < band[3] <= 620
        and 40 <= band[2] - band[0] <= 220
        and 15 <= band[3] - band[1] <= 100
    ):
        return None, "invalid_performance_panel_preview_band", status
    raw = provenance.get("raw_observations")
    if not isinstance(raw, list):
        return None, "missing_performance_panel_preview_observations", status
    observations = []
    for item in raw:
        record = _panel_proof_observation(item, band)
        if record is not None:
            observations.append(record)
    merged = []
    signed = []
    for record in observations:
        merged_match = _CURRENT_PROJECTED_RE.fullmatch(record["text"])
        if merged_match:
            merged.append((record, int(merged_match[1]), int(merged_match[2])))
            continue
        signed_match = _SIGNED_AMOUNT_RE.fullmatch(record["text"])
        if signed_match:
            signed.append((record, int(signed_match[1])))

    # Identical OCR records are corroboration.  Distinct boxes or values are
    # not collapsed into whichever one happens to appear first.
    def unique_records(values):
        unique = {}
        for record, *numbers in values:
            key = (record["text"], tuple(record["box"]), tuple(numbers))
            unique.setdefault(key, (record, *numbers))
        return list(unique.values())

    merged = unique_records(merged)
    signed = unique_records(signed)
    projected = provenance.get("projected")
    projected_value = None
    projected_observation = None
    if projected is not None:
        if not isinstance(projected, dict):
            return None, "invalid_performance_panel_preview_projection", status
        projected_value = _panel_value(projected.get("value"))
        projected_observation = _panel_proof_observation(
            projected.get("observation"), band
        )
        if projected_value is None or projected_observation is None:
            return None, "invalid_performance_panel_preview_projection", status

    current = provenance.get("current")
    if current is not None:
        if not isinstance(current, dict) or _panel_value(current.get("value")) is None:
            return None, "invalid_performance_panel_preview_current", status

    if status in (
        "resolved_merged_panel_value",
        "unresolved_low_confidence_merged_panel_value",
    ):
        if len(merged) != 1:
            return None, "conflicting_performance_panel_preview_rows", status
        record, current_value, amount = merged[0]
        if amount < 0 or current_value < 0:
            return None, "invalid_performance_panel_preview_amount", status
        # A merged row is one source observation, but a second signed row in
        # the same typed band is still independent evidence.  Do not let the
        # merged spelling hide a conflicting amount merely because it arrived
        # first or because a typed projection is also present.
        if any(signed_amount != amount for _record, signed_amount in signed):
            return None, "conflicting_performance_panel_preview_amounts", status
        if projected_value is not None and projected_value != amount:
            return None, "conflicting_performance_panel_preview_amounts", status
        if isinstance(current, dict) and _panel_value(current.get("value")) != current_value:
            return None, "conflicting_performance_panel_preview_current", status
        if projected_observation is not None:
            projected_match = _CURRENT_PROJECTED_RE.fullmatch(
                record["text"]
            )
            if projected_match is None or projected_observation["text"] not in (
                record["text"], f"+{amount}"
            ):
                # A separate typed projection must agree with the merged row;
                # do not let an unrelated child supply the amount.
                return None, "conflicting_performance_panel_preview_projection", status
        return amount, record, "merged"

    if len(merged) > 0:
        return None, "conflicting_performance_panel_preview_rows", status
    if projected_value is None or projected_observation is None:
        return None, "missing_performance_panel_preview_projection", status
    projection_text = _SIGNED_AMOUNT_RE.fullmatch(projected_observation["text"])
    component_text = re.fullmatch(r"\d{1,4}", projected_observation["text"])
    if projection_text is None and not (
        component_text
        and projected_observation.get("component", "").casefold() == "projected"
    ):
        return None, "untyped_performance_panel_preview_projection", status
    if projection_text and int(projection_text[1]) != projected_value:
        return None, "conflicting_performance_panel_preview_projection", status
    if component_text and int(component_text[0]) != projected_value:
        return None, "conflicting_performance_panel_preview_projection", status
    # The separate typed projection is accepted only when every eligible raw
    # signed row agrees with it.  Otherwise the source has two unresolved
    # readings for one field and the canonical value must remain unknown.
    if any(signed_amount != projected_value for _record, signed_amount in signed):
        return None, "conflicting_performance_panel_preview_amounts", status
    return projected_value, projected_observation, "separate"


def _performance_panel_preview_candidates(
    reading: dict[str, Any], facts: dict[str, Any], timestamp: int,
    sequence_index: int, rejected: Counter[str],
) -> list[dict[str, Any]]:
    """Produce canonical candidates from trusted sidebar projections.

    This is intentionally a bridge, rather than another OCR reader.  The
    sidebar parser has already attached field identity, row geometry, and a
    typed current/projected interpretation.  The bridge only makes that
    accepted source fact visible to the preview observation builder after a
    same-reading browse proof is present.
    """

    provenance = facts.get("performance_panel_provenance")
    if not isinstance(provenance, dict) or not provenance:
        return []
    phase = _explicit_phase_proof(facts)
    if phase is None or _explicit_result_marker(facts):
        rejected["performance_panel_preview_missing_menu_proof"] += len(provenance)
        return []
    evidence = _evidence(reading, {})
    if not evidence:
        rejected["performance_panel_preview_missing_source_evidence"] += len(provenance)
        return []
    candidates = []
    for raw_field, entry in provenance.items():
        field = _text(raw_field)
        if field:
            field = {"vocals": "vocal", "visuals": "visual"}.get(
                field.casefold(), field.casefold()
            )
        if field not in PERFORMANCE_FIELDS:
            rejected["invalid_performance_panel_preview_field"] += 1
            continue
        if not isinstance(entry, dict):
            rejected["invalid_performance_panel_preview_provenance"] += 1
            continue
        declared_field = _text(entry.get("field"))
        if declared_field and {"vocals": "vocal", "visuals": "visual"}.get(
            declared_field.casefold(), declared_field.casefold()
        ) != field:
            rejected["conflicting_performance_panel_preview_field"] += 1
            continue
        amount, observation_or_reason, mode = _performance_panel_projection(
            field, entry
        )
        if amount is None:
            if observation_or_reason == "no_performance_panel_preview_projection":
                continue
            rejected[observation_or_reason] += 1
            continue
        projected_map = facts.get("projected_performance_gains")
        if isinstance(projected_map, dict) and field in projected_map:
            mapped_amount = _panel_value(projected_map.get(field))
            if mapped_amount is None or mapped_amount != amount:
                rejected["conflicting_performance_panel_preview_amounts"] += 1
                continue
        observation = observation_or_reason
        geometry = {
            "basis": "typed_performance_panel_provenance",
            "role": "performance_preview_row",
            "field": field,
            "mode": mode,
            "status": _text(entry.get("status")),
            "band": list(_finite_box(entry["band"])),
            "observation": deepcopy(observation),
            "source_references": [f"region:performance_panel.{field}"],
        }
        effect = {
            "kind": "performance_change",
            "field": field,
            "amount": amount,
            "phase": "preview",
            "preview": True,
            "awarded": False,
            "source_evidence": evidence,
            "source_semantics": "typed_performance_panel_preview",
            "preview_geometry": geometry,
        }
        payload, reason = _typed_payload(
            reading, facts, effect, fact_key="performance_panel_provenance"
        )
        if payload is None:
            rejected[reason] += 1
            continue
        option = _preview_option(reading, facts, effect)
        context = _preview_context(reading, facts, effect)
        candidates.append({
            "payload": payload,
            "option": option,
            "context": context,
            "timestamp": timestamp,
            "sequence_index": sequence_index,
            "evidence": evidence,
            "fact_keys": ["performance_panel_provenance"],
            "source_effect_indices": [
                f"performance_panel_provenance/{field}"
            ],
            "basis": "accepted_typed_performance_panel_preview",
        })
    return candidates


def _facts_with_persisted_preview_recovery(
    reading: dict[str, Any], facts: dict[str, Any],
    *, source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Expose a validated attached recovery through the typed fact channel.

    ``build_preview_observations`` normally consumes facts emitted by
    ``parse_receipt_pixels``.  Older cached reports can contain the complete
    source-bound recovery envelope in ``facts.preview_recovery`` even though
    the parser ran before that envelope was attached, leaving the typed
    overlay list empty.  Reusing :func:`parse_preview_overlay` here keeps the
    repair on the same source/phase boundary and leaves applied channels
    untouched.
    """

    if not (
        isinstance(reading.get("preview_recovery"), dict)
        or isinstance(facts.get("preview_recovery"), dict)
    ):
        return facts
    # Fresh ``vision.parse`` rows keep the in-memory recovery in ``facts``;
    # serialized report rows mirror it at the top level as well.  Only the
    # latter may be treated as persisted output requiring a source root.
    persisted_report_shape = (
        isinstance(facts.get("preview_recovery"), dict)
        and isinstance(reading.get("preview_recovery"), dict)
    )
    # A rootless caller cannot verify a serialized recovery envelope against
    # its neural witness, so the envelope is never promoted on that path.  The
    # parser-owned typed lists on the same row are kept: every worker row
    # carries the envelope at the top level once a recovery ran, and clearing
    # those rows silently dropped the parser's own preview readings for every
    # recovered frame (the same lists are trusted on rows without an
    # envelope).  Rows without a root therefore fall through to the ordinary
    # in-memory promotion and native-fact path below.
    promoted = parse_preview_overlay(reading, source_root=source_root)
    persisted_source_shape = source_root is not None and persisted_report_shape
    if not (
        promoted.get("preview_overlay_effects")
        or promoted.get("preview_modifier_effects")
        or promoted.get("preview_phase_proof")
    ):
        if persisted_source_shape:
            # The envelope did not bind.  Reparse the row's own immutable
            # neural witness under the root before failing closed: that is
            # the same source the envelope would have been checked against,
            # and it never involves the mutable report lists.
            native = _native_witness_overlay(reading, source_root)
            if native and (native.get("preview_overlay_effects") or native.get("preview_modifier_effects")):
                result = deepcopy(facts)
                for key in ("preview_overlay_effects", "preview_modifier_effects"):
                    result[key] = deepcopy(native.get(key) or [])
                for key in (
                    "preview_overlay_proven", "preview_modifier_proven",
                    "preview_phase_proof", "preview_overlay_evidence", "preview_option",
                ):
                    if key in native:
                        result[key] = deepcopy(native[key])
                counts = result.get("rejected_counts")
                counts = dict(counts) if isinstance(counts, dict) else {}
                counts["persisted_preview_recovery_unbound_native_witness_used"] = (
                    int(counts.get("persisted_preview_recovery_unbound_native_witness_used", 0)) + 1
                )
                result["rejected_counts"] = dict(sorted(counts.items()))
                return result
            # The source-root path is fail-closed for a persisted envelope.
            # Do not fall back to mutable report lists when validation failed.
            result = deepcopy(facts)
            result["preview_overlay_effects"] = []
            result["preview_modifier_effects"] = []
            result["preview_overlay_proven"] = False
            result["preview_modifier_proven"] = False
            result["preview_phase_proof"] = {}
            result["preview_overlay_evidence"] = {}
            result["preview_option"] = None
            return result
        return facts
    if persisted_source_shape:
        # ``parse_preview_overlay`` has already rebuilt these lists from the
        # immutable source witness.  Replace the report's lists wholesale;
        # appending would let a changed typed fact (for example SP +99) reach
        # the canonical preview builder beside the verified amount.
        result = deepcopy(facts)
        for key in ("preview_overlay_effects", "preview_modifier_effects"):
            result[key] = deepcopy(promoted.get(key) or [])
        for key in (
            "preview_overlay_proven", "preview_modifier_proven",
            "preview_phase_proof", "preview_overlay_evidence", "preview_option",
        ):
            if key in promoted:
                result[key] = deepcopy(promoted[key])
        return result
    result = deepcopy(facts)
    for key in ("preview_overlay_effects", "preview_modifier_effects"):
        additions = promoted.get(key)
        if not isinstance(additions, list) or not additions:
            continue
        existing = result.get(key)
        if not isinstance(existing, list):
            existing = []
        result[key] = existing + deepcopy(additions)
    if result.get("preview_phase_proof") is None and promoted.get("preview_phase_proof") is not None:
        result["preview_phase_proof"] = deepcopy(promoted["preview_phase_proof"])
    if result.get("preview_option") is None and promoted.get("preview_option") is not None:
        result["preview_option"] = promoted["preview_option"]
    if promoted.get("preview_overlay_proven"):
        result["preview_overlay_proven"] = True
    if promoted.get("preview_modifier_proven"):
        result["preview_modifier_proven"] = True
    if promoted.get("preview_overlay_evidence"):
        evidence = result.get("preview_overlay_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        for key, value in promoted["preview_overlay_evidence"].items():
            evidence.setdefault(key, deepcopy(value))
        result["preview_overlay_evidence"] = evidence
    return result


def _candidate_rows(
    readings: list[dict[str, Any]], rejected: Counter[str],
    *, source_root: str | Path | None = None,
):
    """Yield validated candidate records in source time order."""

    sortable = []
    for source_index, reading in enumerate(readings):
        if not isinstance(reading, dict):
            rejected["invalid_reading"] += 1
            continue
        timestamp = _timestamp(reading.get("source_timestamp_ms"))
        if timestamp is None:
            rejected["invalid_timestamp"] += 1
            continue
        sortable.append((timestamp, source_index, reading))
    sortable.sort(key=lambda item: (item[0], item[1]))

    for sequence_index, (timestamp, source_index, reading) in enumerate(sortable):
        facts = reading.get("facts")
        if not isinstance(facts, dict):
            continue
        facts = _facts_with_persisted_preview_recovery(
            reading, facts, source_root=source_root
        )
        if not _is_preview_reading(reading, facts):
            continue
        row_candidates = {}
        lesson_offer_entries, lesson_offer_conflicts = _lesson_offer_cost_entries(
            facts, _evidence(reading, {}), rejected
        )
        for fact_key in PREVIEW_FACT_KEYS:
            values = facts.get(fact_key)
            if not isinstance(values, list):
                continue
            for effect_index, effect in enumerate(values):
                if not isinstance(effect, dict):
                    rejected["non_object_typed_effect"] += 1
                    continue
                # A field-level timestamp must be the owning reading's exact
                # timestamp.  Nearby timestamps are not silently reassigned.
                for time_key in ("source_timestamp_ms", "timestamp_ms"):
                    if time_key in effect and _timestamp(effect.get(time_key)) != timestamp:
                        rejected["effect_timestamp_mismatch"] += 1
                        break
                else:
                    payload, reason = _typed_payload(
                        reading, facts, effect, fact_key=fact_key
                    )
                    if payload is None:
                        rejected[reason] += 1
                        continue
                    proof = _evidence(reading, effect)
                    if not proof:
                        rejected["missing_source_evidence"] += 1
                        continue
                    offer_id = _typed_offer_id(effect.get("offer_id"))
                    if effect.get("offer_id") is not None and offer_id is None:
                        rejected["invalid_typed_offer_id"] += 1
                        continue
                    offer_proof: list[str] = []
                    offer_source_indices: list[str] = []
                    lesson_cost_enriched = False
                    if payload.get("kind") == "lesson":
                        (
                            payload,
                            offer_id,
                            offer_proof,
                            offer_source_indices,
                            lesson_cost_enriched,
                        ) = _enrich_lesson_offer_payload(
                            reading,
                            facts,
                            effect,
                            payload,
                            lesson_offer_entries,
                            lesson_offer_conflicts,
                            rejected,
                        )
                        proof = list(dict.fromkeys(proof + offer_proof))
                    option = _preview_option(reading, facts, effect)
                    context = _preview_context(reading, facts, effect)
                    # The effect's own identity is the strongest context for
                    # offers; this prevents two equally priced cards from
                    # collapsing when their titles are visible.
                    if payload.get("kind") in ("lesson", "skill") and payload.get("name"):
                        context = payload["name"]
                    signature = (
                        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")),
                        offer_id,
                        option,
                        context,
                    )
                    existing = row_candidates.get(signature)
                    if existing is None:
                        row_candidates[signature] = {
                            "payload": payload,
                            "option": option,
                            "context": context,
                            "offer_id": offer_id,
                            "timestamp": timestamp,
                            # ``sequence_index`` is the position after
                            # timestamp ordering.  Using the caller's
                            # original index here would make a valid
                            # out-of-order source list look non-contiguous.
                            "sequence_index": sequence_index,
                            "evidence": proof,
                            "fact_keys": list(dict.fromkeys(
                                [fact_key]
                                + (["lesson_offer_preview"]
                                   if lesson_cost_enriched else [])
                            )),
                            "source_effect_indices": [f"{fact_key}/{effect_index}"] + offer_source_indices,
                            "basis": (
                                "accepted_typed_lesson_offer_preview"
                                if lesson_cost_enriched
                                else
                                "accepted_typed_preview_overlay"
                                if fact_key in PREVIEW_OVERLAY_FACT_KEYS
                                else "accepted_typed_song_modifier_preview"
                                if fact_key in PREVIEW_MODIFIER_FACT_KEYS
                                else "accepted_typed_preview_fact"
                            ),
                        }
                    else:
                        existing["evidence"] = list(dict.fromkeys(existing["evidence"] + proof))
                        if fact_key not in existing["fact_keys"]:
                            existing["fact_keys"].append(fact_key)
                        existing["source_effect_indices"].append(f"{fact_key}/{effect_index}")
                        if lesson_cost_enriched:
                            existing["fact_keys"] = list(dict.fromkeys(
                                existing["fact_keys"] + ["lesson_offer_preview"]
                            ))
                            existing["source_effect_indices"].extend(offer_source_indices)
        # The performance sidebar keeps a typed current/projected record in a
        # separate fact collection.  Feed only its source-proven projection
        # through the same row deduplication boundary as ordinary effects.
        for candidate in _performance_panel_preview_candidates(
            reading, facts, timestamp, sequence_index, rejected
        ):
            signature = (
                json.dumps(candidate["payload"], ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")),
                candidate.get("offer_id"),
                candidate.get("option"),
                candidate.get("context"),
            )
            existing = row_candidates.get(signature)
            if existing is None:
                row_candidates[signature] = candidate
            else:
                existing["evidence"] = list(dict.fromkeys(
                    existing["evidence"] + candidate["evidence"]
                ))
                existing["fact_keys"] = list(dict.fromkeys(
                    existing["fact_keys"] + candidate["fact_keys"]
                ))
                existing["source_effect_indices"].extend(
                    candidate["source_effect_indices"]
                )
        for candidate in row_candidates.values():
            yield candidate


def _conflict_touch(left: dict[str, Any], right: dict[str, Any], maximum_gap_ms: int) -> bool:
    return max(left["start_ms"], right["start_ms"]) <= min(left["end_ms"], right["end_ms"]) or (
        0 <= right["start_ms"] - left["end_ms"] <= maximum_gap_ms
    )


def build_preview_observations(
    readings: Iterable[dict[str, Any]], *, maximum_gap_ms: int = 500,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build explicit, source-linked preview observations.

    ``readings`` are ordinary gameplay readings containing parser-owned
    ``facts``.  A typed list under one of :data:`PREVIEW_FACT_KEYS` is the
    acceptance boundary.  ``preview_modifier_effects`` is a dedicated source
    channel; its canonical payload uses ``training_modifier_change`` with
    ``modifier == 'song'`` and remains in ``phase == 'preview'``.  The result
    has three intentionally separate parts:

    ``observations``
        Unambiguous observations safe to expose to report adapters.  Every
        row has ``phase == 'preview'``.
    ``ambiguities``
        Conflicting typed values that were seen close together.  These retain
        alternatives and proof but are never counted as observations.
    ``rejected_counts``
        Reason counters for typed facts that did not meet the boundary.

    The input is never modified.  ``maximum_gap_ms`` controls only repeated
    frame coalescing; it is not a gameplay timing assumption.
    """

    if not isinstance(maximum_gap_ms, int) or isinstance(maximum_gap_ms, bool) or maximum_gap_ms < 0:
        raise ValueError("maximum_gap_ms must be a nonnegative integer")
    if not isinstance(readings, (list, tuple)):
        raise ValueError("readings must be a list or tuple of source readings")

    rejected: Counter[str] = Counter()
    candidates = list(_candidate_rows(
        list(readings), rejected, source_root=source_root
    ))
    groups: list[dict[str, Any]] = []
    last_by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}

    for candidate in candidates:
        payload_signature = json.dumps(candidate["payload"], ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":"))
        # Distinct typed lesson cards can have the same title and cost.  The
        # parser's offer id is the stable occurrence key when it is present;
        # leaving it out would collapse two visible cards into one preview.
        signature = (
            payload_signature,
            candidate.get("offer_id"),
            candidate.get("option"),
            candidate.get("context"),
        )
        previous = last_by_signature.get(signature)
        if (
            previous is not None
            and previous["sequence_index"] + 1 == candidate["sequence_index"]
            and candidate["timestamp"] - previous["end_ms"] <= maximum_gap_ms
            and (candidate.get("option") is not None or candidate.get("context") is not None)
        ):
            previous["end_ms"] = candidate["timestamp"]
            previous["evidence"] = list(dict.fromkeys(previous["evidence"] + candidate["evidence"]))
            previous["fact_keys"] = list(dict.fromkeys(previous["fact_keys"] + candidate["fact_keys"]))
            previous["source_effect_indices"].extend(candidate["source_effect_indices"])
        else:
            group = {
                "payload": deepcopy(candidate["payload"]),
                "option": candidate.get("option"),
                "context": candidate.get("context"),
                "offer_id": candidate.get("offer_id"),
                "start_ms": candidate["timestamp"],
                "end_ms": candidate["timestamp"],
                "evidence": list(candidate["evidence"]),
                "fact_keys": list(candidate["fact_keys"]),
                "source_effect_indices": list(candidate["source_effect_indices"]),
                "basis": candidate.get("basis", "accepted_typed_preview_fact"),
                "sequence_index": candidate["sequence_index"],
            }
            groups.append(group)
            last_by_signature[signature] = group

    # Exact payload groups are ready.  Values that differ only in amount and
    # touch the same source span are alternatives, not two awards.  Remove
    # both from canonical output and retain a compact evidence-linked record.
    conflict_groups: set[int] = set()
    ambiguities = []
    by_identity: defaultdict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, group in enumerate(groups):
        payload = group["payload"]
        offer_id = group.get("offer_id")
        # When an offer id exists, compare all observations for that card even
        # if an OCR reread temporarily changes its displayed title/context.
        # Untyped legacy effects retain the historical name/context identity.
        identity_name = (
            ("offer_id", offer_id)
            if offer_id is not None
            else ("name", payload.get("name"))
        )
        identity_context = None if offer_id is not None else group.get("context")
        identity = (
            payload.get("kind"), payload.get("field"), identity_name,
            group.get("option"), identity_context,
        )
        by_identity[identity].append((index, group))
    for identity, entries in by_identity.items():
        for left_index, left in entries:
            for right_index, right in entries:
                if right_index <= left_index:
                    continue
                if left["payload"] == right["payload"] or not _conflict_touch(left, right, maximum_gap_ms):
                    continue
                conflict_groups.update((left_index, right_index))
                alternatives = [deepcopy(left["payload"]), deepcopy(right["payload"])]
                alternatives = sorted(alternatives, key=lambda value: json.dumps(value, sort_keys=True))
                ambiguities.append({
                    "reason": "conflicting_typed_preview_values",
                    "identity": {
                        "kind": identity[0],
                        "field": identity[1],
                        "name": left["payload"].get("name"),
                        "option": identity[3],
                        "context": left.get("context"),
                    },
                    "source_interval_ms": [
                        min(left["start_ms"], right["start_ms"]),
                        max(left["end_ms"], right["end_ms"]),
                    ],
                    "evidence": list(dict.fromkeys(left["evidence"] + right["evidence"])),
                    "alternatives": alternatives,
                })
                if left.get("offer_id") is not None:
                    ambiguities[-1]["identity"]["offer_id"] = left["offer_id"]

    observations = []
    for index, group in enumerate(groups):
        if index in conflict_groups:
            continue
        identity_material = {
            "payload": group["payload"],
            "option": group.get("option"),
            "context": group.get("context"),
            "start_ms": group["start_ms"],
            "first_evidence": group["evidence"][0],
        }
        if group.get("offer_id") is not None:
            identity_material["offer_id"] = group["offer_id"]
        occurrence = "preview:" + _digest(identity_material)
        category = "purchase" if group["payload"].get("kind") in ("lesson", "skill") else "effect"
        observation = {
            "id": "preview-" + occurrence.rsplit(":", 1)[1][:20],
            "category": category,
            "phase": "preview",
            "payload": deepcopy(group["payload"]),
            "start_ms": group["start_ms"],
            "end_ms": group["end_ms"],
            "evidence": list(group["evidence"]),
            "uncertain": False,
            "status": "observed",
            "occurrence_key": occurrence,
            # Option/context are metadata outside the scored payload.  They
            # preserve browsing provenance without becoming a committed action.
            "option": group.get("option"),
            "context": group.get("context"),
            "source_fact_keys": list(group["fact_keys"]),
            "source_effect_indices": list(group["source_effect_indices"]),
            "observation_basis": group.get("basis", "accepted_typed_preview_fact"),
        }
        if group.get("offer_id") is not None:
            observation["offer_id"] = group["offer_id"]
        observations.append(observation)

    observations.sort(key=lambda row: (row["start_ms"], row["end_ms"], row["id"]))
    ambiguities.sort(key=lambda row: (row["source_interval_ms"], row["reason"]))
    return {
        "schema_version": SCHEMA,
        "observations": observations,
        "ambiguities": ambiguities,
        "rejected_counts": dict(sorted(rejected.items())),
        "preview_is_not_applied": True,
        "committed_actions_inferred": False,
    }


def accepted_preview_observations(
    readings: Iterable[dict[str, Any]], *, maximum_gap_ms: int = 500
) -> list[dict[str, Any]]:
    """Convenience view containing only unambiguous canonical observations."""

    return build_preview_observations(readings, maximum_gap_ms=maximum_gap_ms)["observations"]


__all__ = [
    "SCHEMA",
    "STAT_FIELDS",
    "PERFORMANCE_FIELDS",
    "PREVIEW_FACT_KEYS",
    "PREVIEW_OVERLAY_FACT_KEYS",
    "PREVIEW_MODIFIER_FACT_KEYS",
    "preview_phase_proof",
    "produce_preview_panel_from_lines",
    "parse_preview_overlay",
    "build_preview_observations",
    "accepted_preview_observations",
    "build",
]
