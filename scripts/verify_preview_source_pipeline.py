"""Reproduce the source-bound training preview parser audit.

This verifier runs the real neural reader on a small, immutable set of source
frames and writes a provenance record under ``.local/final-reliability-v1``.
The source image and OCR lines are retained in the result so a reviewer can
recheck every accepted amount.  The expected values in this file are audit
assertions only; they are never passed to the parser or used to repair OCR.

The T042 frame deliberately records the two visible speed rows separately:
the orange ``+10`` training row is the main preview, while the pink music-note
``+4`` row is a separate bonus candidate.  The preview parser must not merge
that bonus into the training amount or create a duplicate committed effect.
The dedicated song-modifier preview channel assigns that bonus from the same
marker and row geometry.  It remains a preview observation and is never
treated as an applied stat change.
"""

from __future__ import annotations

from collections import Counter
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from PIL import Image

from tracen_replay.preview_observations import (
    _SIGNED_AMOUNT_RE,
    _column_for_x,
    _confidence,
    _is_source_reference,
    _line_box,
    build_preview_observations,
    parse_preview_overlay,
    produce_preview_panel_from_lines,
)
from tracen_replay.vision import NeuralReader, parse as parse_vision
from tracen_replay.full_recording import assemble


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".local" / "final-reliability-v1" / "preview-parser-source-verification-v2.json"


CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "v1-T028",
        "recording": "v1",
        "mode": "read_training",
        "image": ".local/full-recording/v1/training-inspection/531000/frame-000012.png",
        "expected_preview": {
            "composure": 8,
            "passion": 8,
            "skill_points": 10,
            "speed": 4,
            "wit": 18,
        },
        "expected_option": "wit",
        "expected_accepted": True,
    },
    {
        "id": "v1-T068",
        "recording": "v1",
        "mode": "read_training",
        "image": ".local/full-recording/v1/training-inspection/1405000/frame-000013.png",
        "expected_preview": {
            "guts": 22,
            "passion": 24,
            "skill_points": 22,
            "stamina": 44,
            "visual": 24,
        },
        "expected_option": "stamina",
        "expected_accepted": True,
    },
    {
        "id": "independent-T042",
        "recording": "independent-01",
        "mode": "read_then_parse",
        "image": ".local/full-recording/independent-01/gameplay/part-006-frame-000250.png",
        "expected_preview": {
            "composure": 15,
            "skill_points": 11,
            "speed": 10,
            "vocal": 15,
            "wit": 28,
        },
        "expected_option": "wit",
        "expected_accepted": True,
        "separate_bonus": {"field": "speed", "amount": 4},
        "frozen_gold_note": (
            "The prior frozen speed=4 row is the visible pink song-bonus row; "
            "the orange main training row is speed=10."
        ),
    },
    {
        "id": "v1-negative-T004",
        "recording": "v1",
        "mode": "read_training",
        "image": ".local/full-recording/v1/training-inspection/47250/frame-000018.png",
        "expected_preview": {},
        "expected_option": "speed",
        "expected_accepted": False,
    },
    {
        "id": "v1-negative-T017",
        "recording": "v1",
        "mode": "read_training",
        "image": ".local/full-recording/v1/training-inspection/245000/frame-000012.png",
        "expected_preview": {},
        "expected_option": "speed",
        "expected_accepted": False,
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def binding(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": relative(path), "exists": path.is_file()}
    if path.is_file():
        result["sha256"] = sha256(path)
        result["size_bytes"] = path.stat().st_size
    return result


def compact_effect(effect: Any) -> dict[str, Any]:
    if not isinstance(effect, dict):
        return {"value": repr(effect)}
    keys = (
        "kind", "field", "amount", "phase", "preview", "awarded", "applied",
        "source_evidence", "source_semantics", "source_region", "source_index",
        "box", "preview_geometry", "raw_text", "confidence",
    )
    return {key: effect[key] for key in keys if key in effect}


def compact_panel(panel: Any) -> dict[str, Any] | None:
    if not isinstance(panel, dict):
        return None
    return {
        "menu_proven": panel.get("menu_proven"),
        "result_proven": panel.get("result_proven"),
        "basis": panel.get("basis"),
        "option": panel.get("option"),
        "evidence": panel.get("evidence", []),
        "geometry": panel.get("geometry", {}),
        "effects": [compact_effect(item) for item in panel.get("effects", [])],
        "modifier_effects": [
            compact_effect(item) for item in panel.get("modifier_effects", [])
        ],
    }


def compact_line(line: Any) -> dict[str, Any]:
    if not isinstance(line, dict):
        return {"value": repr(line)}
    return {
        key: line[key]
        for key in ("text", "confidence", "box", "source_index", "source_region")
        if key in line
    }


def signed_stat_rows(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return raw signed stat rows with their source line identity.

    This is an audit view of the same OCR lines.  It does not choose an
    accepted amount, and it is deliberately kept separate from parser output.
    """

    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if _confidence(line) < 90:
            continue
        text = line.get("text") if isinstance(line, dict) else None
        box = _line_box(line)
        if not isinstance(text, str) or box is None:
            continue
        match = _SIGNED_AMOUNT_RE.fullmatch(text.strip())
        if not match:
            continue
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        if not 600 <= center_y <= 780:
            continue
        field = _column_for_x(center_x)
        if field is None:
            continue
        rows.append({
            "source_index": index,
            "field": field,
            "amount": int(match[1]),
            "box": list(box),
        })
    return rows


def duplicate_signatures(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        (item.get("kind"), item.get("field"), item.get("amount"))
        for item in effects
        if isinstance(item, dict)
    )
    return [
        {"kind": key[0], "field": key[1], "amount": key[2], "count": count}
        for key, count in sorted(counts.items(), key=lambda item: str(item[0]))
        if count > 1
    ]


def source_evidence_violations(value: Any, path: str = "$",) -> list[str]:
    """Return invalid worker-facing source-evidence entries in a value."""

    violations: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "source_evidence":
                entries = [child] if isinstance(child, str) else child
                if not isinstance(entries, (list, tuple)):
                    violations.append(f"{child_path}:not-a-list")
                else:
                    for index, entry in enumerate(entries):
                        if not isinstance(entry, str) or not entry:
                            violations.append(f"{child_path}[{index}]:not-a-string")
                            continue
                        if _is_source_reference(entry):
                            violations.append(f"{child_path}[{index}]:typed-reference:{entry}")
                            continue
                        candidate = Path(entry)
                        if not candidate.is_absolute():
                            candidate = ROOT / candidate
                        if not candidate.is_file():
                            violations.append(f"{child_path}[{index}]:missing:{entry}")
            violations.extend(source_evidence_violations(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            violations.extend(source_evidence_violations(child, f"{path}[{index}]"))
    return violations


def preview_signatures(effects: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(item.get("field")): item.get("amount")
        for item in effects
        if isinstance(item, dict) and item.get("field") is not None
    }


def source_sidecars(image: Path) -> list[dict[str, Any]]:
    """Bind adjacent OCR sidecars when a source capture preserved them."""

    candidates = (
        image.with_suffix(".v2.json"),
        image.with_suffix(".v2.performance.json"),
        image.with_suffix(".json"),
    )
    return [binding(candidate) for candidate in candidates if candidate.is_file()]


def assemble_single_reading(vision: dict[str, Any], image: Path) -> dict[str, Any]:
    """Run the real full-recording assembly adapter on one source reading.

    The verifier uses a synthetic envelope only to avoid reprocessing a full
    recording.  The reading itself is the output of the normal neural
    ``read``/``parse`` path, and ``assemble`` is the production function used
    by full_recording reports.
    """

    row = copy.deepcopy(vision)
    row.update(
        source_timestamp_ms=1,
        evidence=relative(image),
        completed_action=None,
    )
    report = {
        "source": {
            "sha256": "source-verification-envelope",
            "name": image.name,
            "size_bytes": image.stat().st_size,
            "duration_ms": 1_000_000,
            "timeline_origin_seconds": 0,
            "width": 1920,
            "height": 1080,
            "codec": "source-verification",
        },
        "frames": [],
        "sampling": {
            "requested_fps": 4,
            "frame_count": 1,
            "method": "source-verification-single-reading",
            "guarantees_all_events": False,
        },
    }
    assembled = assemble(report, [row])
    return assembled["gameplay_tracking"]


def case_record(reader: NeuralReader, spec: dict[str, Any]) -> dict[str, Any]:
    image = ROOT / spec["image"]
    if not image.is_file():
        raise FileNotFoundError(image)
    with Image.open(image) as opened:
        pane = opened.convert("RGB")
    if spec["mode"] == "read_training":
        raw = reader.read_training(pane)
    elif spec["mode"] == "read_then_parse":
        raw = reader.read(pane)
    else:
        raise ValueError(f"Unsupported source mode: {spec['mode']}")
    # The production full-recording worker attaches the frame path before the
    # parsed facts are persisted.  Reproduce that boundary here so the source
    # audit exercises the same evidence contract; OCR line/region IDs belong
    # in typed geometry metadata, never in ``source_evidence``.
    if isinstance(raw, dict):
        raw = dict(raw, evidence=relative(image))

    lines = raw.get("lines", []) if isinstance(raw, dict) else []
    lines = [line for line in lines if isinstance(line, dict)]
    panel = raw.get("preview_panel") if isinstance(raw, dict) else None
    if panel is None:
        panel = produce_preview_panel_from_lines(raw)
    overlay = parse_preview_overlay(raw)
    vision = parse_vision(raw)
    preview_effects = [
        item for item in overlay.get("preview_overlay_effects", [])
        if isinstance(item, dict)
    ]
    preview_modifier_effects = [
        item for item in overlay.get("preview_modifier_effects", [])
        if isinstance(item, dict)
    ]
    committed_effects = [
        item for item in vision.get("effects", [])
        if isinstance(item, dict)
    ]
    # Exercise the same typed adapter that full_recording.assemble uses.  A
    # single source reading is sufficient here because this check is about
    # channel preservation, not repeated-frame coalescing.
    report_preview = build_preview_observations([{
        "source_timestamp_ms": 0,
        "evidence": relative(image),
        "screen": vision.get("screen"),
        "completed_action": None,
        "facts": vision.get("facts", {}),
        "stats": {"preview_option": overlay.get("preview_option")},
    }])
    assembled_tracking = assemble_single_reading(vision, image)
    assembled_preview = assembled_tracking.get("preview_observations", {})
    raw_rows = signed_stat_rows(lines)
    accepted_indices = {
        item.get("source_index")
        for item in preview_effects
        if item.get("field") in {"speed", "stamina", "power", "guts", "wit", "skill_points"}
        and isinstance(item.get("source_index"), int)
    }
    accepted_indices.update(
        item.get("preview_geometry", {}).get("source_index")
        for item in preview_effects
        if isinstance(item.get("preview_geometry"), dict)
        and isinstance(item.get("preview_geometry", {}).get("source_index"), int)
    )
    t042_bonus_check = None
    if spec["id"] == "independent-T042":
        speed_rows = [item for item in raw_rows if item["field"] == "speed"]
        t042_bonus_check = {
            "raw_speed_rows": speed_rows,
            "accepted_main_preview_speed": [
                item.get("amount") for item in preview_effects
                if item.get("field") == "speed"
            ],
            "unselected_speed_rows": [
                item for item in speed_rows
                if item["source_index"] not in accepted_indices
            ],
            "separate_bonus_candidate": {
                "field": "speed",
                "amount": 4,
                "observed": any(item["amount"] == 4 for item in speed_rows),
                "excluded_from_main_preview": not any(
                    item.get("field") == "speed" and item.get("amount") == 4
                    for item in preview_effects
                ),
                "accepted_as_song_modifier": any(
                    item.get("field") == "speed" and item.get("amount") == 4
                    for item in preview_modifier_effects
                ),
            },
        }

    preview = preview_signatures(preview_effects)
    expected = dict(spec["expected_preview"])
    preview_match = preview == expected
    option_match = overlay.get("preview_option") == spec["expected_option"]
    accepted_match = bool(overlay.get("preview_overlay_proven")) == spec["expected_accepted"]
    committed_overlap = [
        item for item in committed_effects
        if any(
            item.get("kind") == preview_item.get("kind")
            and item.get("field") == preview_item.get("field")
            and item.get("amount") == preview_item.get("amount")
            for preview_item in preview_effects
        )
    ]
    committed_modifier_effects = [
        item for item in committed_effects
        if item.get("kind") == "training_modifier_change"
        or item.get("modifier") == "song"
    ]
    report_preview_observations = report_preview.get("observations", [])
    report_preview_modifiers = [
        item for item in report_preview_observations
        if isinstance(item, dict)
        and item.get("payload", {}).get("modifier") == "song"
    ]
    report_preview_main = [
        item for item in report_preview_observations
        if isinstance(item, dict)
        and item.get("payload", {}).get("kind") in {
            "stat_change", "performance_change"
        }
    ]
    assembled_preview_observations = assembled_preview.get("observations", [])
    assembled_preview_modifiers = [
        item for item in assembled_preview_observations
        if isinstance(item, dict)
        and item.get("payload", {}).get("modifier") == "song"
    ]
    assembled_preview_main = [
        item for item in assembled_preview_observations
        if isinstance(item, dict)
        and item.get("payload", {}).get("kind") in {
            "stat_change", "performance_change"
        }
    ]
    evidence_violations = source_evidence_violations({
        "typed_panel": panel,
        "parsed_preview_overlay": overlay,
        "vision_parse": vision,
        "report_adapter": report_preview,
        "assemble": assembled_tracking,
    })
    checks: dict[str, Any] = {
        "preview_values_match_audit_assertion": preview_match,
        "preview_option_matches_audit_assertion": option_match,
        "accepted_state_matches_audit_assertion": accepted_match,
        "committed_effect_duplicates": duplicate_signatures(committed_effects),
        "preview_committed_overlap": [compact_effect(item) for item in committed_overlap],
        "no_duplicate_committed_effects": not duplicate_signatures(committed_effects),
        "no_preview_effect_promoted_as_committed": not committed_overlap,
        "no_song_modifier_promoted_as_committed": not committed_modifier_effects,
        "source_evidence_paths_are_files": not evidence_violations,
    }
    if t042_bonus_check is not None:
        separate = t042_bonus_check["separate_bonus_candidate"]
        checks["t042_main_preview_separates_bonus_row"] = bool(
            separate["observed"] and separate["excluded_from_main_preview"]
            and preview.get("speed") == 10
        )
        checks["t042_song_modifier_channel_preserves_bonus"] = bool(
            separate["accepted_as_song_modifier"]
            and any(
                item.get("field") == "speed"
                and item.get("amount") == 4
                for item in preview_modifier_effects
            )
            and not committed_modifier_effects
        )
        checks["t042_report_adapter_preserves_channels"] = bool(
            any(
                item.get("payload", {}).get("field") == "speed"
                and item.get("payload", {}).get("amount") == 10
                for item in report_preview_main
            )
            and any(
                item.get("payload", {}).get("field") == "speed"
                and item.get("payload", {}).get("amount") == 4
                for item in report_preview_modifiers
            )
        )
        checks["t042_assemble_preserves_channels"] = bool(
            any(
                item.get("payload", {}).get("field") == "speed"
                and item.get("payload", {}).get("amount") == 10
                for item in assembled_preview_main
            )
            and any(
                item.get("payload", {}).get("field") == "speed"
                and item.get("payload", {}).get("amount") == 4
                for item in assembled_preview_modifiers
            )
            and not assembled_tracking.get("events")
        )
    required_checks = (
        "preview_values_match_audit_assertion",
        "preview_option_matches_audit_assertion",
        "accepted_state_matches_audit_assertion",
        "no_duplicate_committed_effects",
        "no_preview_effect_promoted_as_committed",
        "no_song_modifier_promoted_as_committed",
        "source_evidence_paths_are_files",
    )
    if t042_bonus_check is not None:
        required_checks += (
            "t042_main_preview_separates_bonus_row",
            "t042_song_modifier_channel_preserves_bonus",
            "t042_report_adapter_preserves_channels",
            "t042_assemble_preserves_channels",
        )
    checks["passed"] = all(checks[key] is True for key in required_checks)
    result: dict[str, Any] = {
        "id": spec["id"],
        "recording": spec["recording"],
        "mode": spec["mode"],
        "source": binding(image),
        "source_sidecars": source_sidecars(image),
        "audit_assertions": {
            "expected_preview": expected,
            "expected_option": spec["expected_option"],
            "expected_accepted": spec["expected_accepted"],
        },
        "reader_metadata": {
            key: raw.get(key)
            for key in (
                "header", "current_grid", "result_grid", "inspection",
                "engine_fingerprint", "model_sha256", "gameplay_sha256",
            )
            if isinstance(raw, dict) and key in raw
        },
        "raw": {
            "lines": [compact_line(line) for line in lines],
            "regions": raw.get("regions", {}) if isinstance(raw, dict) else {},
            "signed_stat_rows": raw_rows,
        },
        "typed_panel": compact_panel(panel),
        "parsed_preview_overlay": {
            "preview_option": overlay.get("preview_option"),
            "preview_overlay_proven": overlay.get("preview_overlay_proven"),
            "preview_modifier_proven": overlay.get("preview_modifier_proven"),
            "preview_phase_proof": overlay.get("preview_phase_proof"),
            "preview_overlay_effects": [compact_effect(item) for item in preview_effects],
            "preview_modifier_effects": [
                compact_effect(item) for item in preview_modifier_effects
            ],
            "rejected_counts": overlay.get("rejected_counts", {}),
        },
        "vision_parse": {
            "screen": vision.get("screen"),
            "training_option": vision.get("training_option"),
            "effects": [compact_effect(item) for item in committed_effects],
            "facts_preview_overlay_effects": [
                compact_effect(item)
                for item in vision.get("facts", {}).get("preview_overlay_effects", [])
                if isinstance(item, dict)
            ],
            "facts_preview_modifier_effects": [
                compact_effect(item)
                for item in vision.get("facts", {}).get("preview_modifier_effects", [])
                if isinstance(item, dict)
            ],
        },
        "evidence_validation": {
            "source_evidence_violations": evidence_violations,
        },
        "report_adapter": {
            "observations": [
                {
                    "phase": item.get("phase"),
                    "category": item.get("category"),
                    "payload": item.get("payload"),
                    "observation_basis": item.get("observation_basis"),
                    "source_fact_keys": item.get("source_fact_keys"),
                }
                for item in report_preview_observations
                if isinstance(item, dict)
            ],
            "rejected_counts": report_preview.get("rejected_counts", {}),
            "preview_is_not_applied": report_preview.get("preview_is_not_applied"),
            "committed_actions_inferred": report_preview.get("committed_actions_inferred"),
        },
        "assemble": {
            "screen": vision.get("screen"),
            "preview_observations": [
                {
                    "phase": item.get("phase"),
                    "category": item.get("category"),
                    "payload": item.get("payload"),
                    "observation_basis": item.get("observation_basis"),
                    "source_fact_keys": item.get("source_fact_keys"),
                }
                for item in assembled_preview_observations
                if isinstance(item, dict)
            ],
            "committed_events": assembled_tracking.get("events", []),
            "preview_is_not_applied": assembled_preview.get("preview_is_not_applied"),
        },
        "checks": checks,
    }
    if spec.get("separate_bonus"):
        result["separate_bonus_assertion"] = spec["separate_bonus"]
        result["frozen_gold_note"] = spec["frozen_gold_note"]
        result["bonus_row_check"] = t042_bonus_check
    return result


def bonus_regression_record() -> dict[str, Any]:
    """Exercise the established adjacent bonus-row contract directly."""

    checks = []
    for preview, bonus in ((17, 3), (2, 90), (0, 4)):
        raw = {
            "header": "Training",
            "current_grid": True,
            "result_grid": False,
            "regions": {},
            "lines": [
                {"text": "Wit Lvl 5", "confidence": 99, "box": [230, 169, 319, 193]},
                {"text": f"+{bonus}", "confidence": 99, "box": [291, 633, 345, 672]},
                {"text": f"+{preview}", "confidence": 99, "box": [291, 670, 343, 704]},
                {"text": "Speed", "confidence": 99, "box": [299, 697, 361, 721]},
            ],
        }
        parsed = parse_preview_overlay(raw)
        effects = parsed.get("preview_overlay_effects", [])
        amounts = [
            item.get("amount") for item in effects if item.get("field") == "speed"
        ]
        checks.append({
            "preview": preview,
            "bonus": bonus,
            "accepted_speed_amounts": amounts,
            "passed": amounts == [preview],
        })
    return {
        "name": "tests.test_preview_bonus_rows adjacent-row contract",
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    output = parser.parse_args().output.resolve()
    if output.exists():
        parser.error('Verification output exists; use --output with a new artifact path.')
    reader = NeuralReader(model_dir=str(ROOT / ".local" / "models" / "rapidocr"))
    records = [case_record(reader, spec) for spec in CASES]
    bonus = bonus_regression_record()
    document = {
        "schema_version": "tracen-replay/preview-source-verification-v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Source verification of typed training previews, result rejection, and bonus-row separation.",
        "policy": (
            "The parser consumes same-frame accepted geometry only. Expected values are audit assertions; "
            "final totals, neighboring frames, timestamps, and recording-specific rules are not inputs."
        ),
        "implementation_sha256": {
            path: sha256(ROOT / path)
            for path in (
                "tracen_replay/preview_observations.py",
                "tracen_replay/vision.py",
                "scripts/verify_preview_source_pipeline.py",
            )
        },
        "model_sha256": reader.models,
        "cases": records,
        "bonus_row_regression": bonus,
        "summary": {
            "source_case_count": len(records),
            "source_cases_passed": sum(item["checks"]["passed"] for item in records),
            "bonus_row_regression_passed": bonus["passed"],
            "negative_result_cases": sum(
                not item["audit_assertions"]["expected_accepted"] for item in records
            ),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "output": relative(output),
        "source_cases": len(records),
        "source_cases_passed": document["summary"]["source_cases_passed"],
        "bonus_row_regression_passed": bonus["passed"],
    }, sort_keys=True))
    return 0 if document["summary"]["source_cases_passed"] == len(records) and bonus["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
