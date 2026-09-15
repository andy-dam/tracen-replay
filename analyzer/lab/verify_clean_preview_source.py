"""Verify clean training-menu controls and result-crossfade negatives.

This is a source fixture verifier, not a production parser.  It exercises the
same ``NeuralReader`` and ``vision.parse`` path used by a recording replay,
then records the accepted typed preview channels and source hashes.  The
crossfade cases deliberately use ``read_training`` so a result inspection
cannot pass by clearing ``completed_action`` or by treating a retained menu
layer as a browse menu.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

from tracen_replay.preview_observations import build_preview_observations
from tracen_replay.vision import NeuralReader, parse


ROOT = Path(__file__).resolve().parents[2]

CASES = (
    {
        "case_id": "T028-clean-menu-before-transition",
        "path": ".local/full-recording/v1/gameplay/part-004-frame-000200.png",
        "timestamp_ms": 529750,
        "reader": "read",
        "expected": {
            "option": "wit",
            "main": {
                ("stat_change", "wit", 18),
                ("stat_change", "skill_points", 10),
            },
            "modifiers": set(),
        },
    },
    {
        "case_id": "T068-clean-menu-before-transition",
        "path": ".local/full-recording/v1/gameplay/part-011-frame-000331.png",
        "timestamp_ms": 1402500,
        "reader": "read",
        "expected": {
            "option": "stamina",
            "main": {
                ("stat_change", "stamina", 29),
                ("stat_change", "guts", 15),
                ("stat_change", "skill_points", 10),
            },
            "modifiers": {
                ("training_modifier_change", "stamina", 15),
                ("training_modifier_change", "guts", 7),
                ("training_modifier_change", "skill_points", 12),
            },
        },
    },
)

NEGATIVES = (
    {
        "case_id": "T028-result-crossfade-target",
        "path": ".local/full-recording/v1/training-inspection/531000/frame-000012.png",
        "timestamp_ms": 531367,
    },
    {
        "case_id": "T068-result-crossfade-target",
        "path": ".local/full-recording/v1/training-inspection/1405000/frame-000013.png",
        "timestamp_ms": 1405400,
    },
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _effect_summary(effects: Any) -> list[dict[str, Any]]:
    if not isinstance(effects, list):
        return []
    return [
        {
            key: effect[key]
            for key in ("kind", "field", "amount", "modifier")
            if key in effect
        }
        for effect in effects
        if isinstance(effect, dict)
    ]


def _run_case(reader: NeuralReader, case: dict[str, Any], *, result: bool) -> dict[str, Any]:
    relative = Path(case["path"])
    path = ROOT / relative
    with Image.open(path) as image:
        raw = (reader.read_training if result else reader.read)(image.convert("RGB"))
    parsed = parse(raw)
    facts = parsed.get("facts") if isinstance(parsed.get("facts"), dict) else {}
    recovery = raw.get("preview_recovery")
    row = dict(
        parsed,
        source_timestamp_ms=case["timestamp_ms"],
        evidence=case["path"],
    )
    observations = build_preview_observations([row])
    main = {
        (item.get("kind"), item.get("field"), item.get("amount"))
        for item in facts.get("preview_overlay_effects", [])
        if isinstance(item, dict)
    }
    modifiers = {
        ("training_modifier_change", item.get("field"), item.get("amount"))
        for item in facts.get("preview_modifier_effects", [])
        if isinstance(item, dict)
    }
    output = {
        "case_id": case["case_id"],
        "path": case["path"],
        "source_sha256": _sha256(path),
        "source_timestamp_ms": case["timestamp_ms"],
        "reader": "read_training" if result else "read",
        "raw_flags": {
            key: raw.get(key)
            for key in ("header", "current_grid", "result_grid", "inspection")
        },
        "parsed": {
            "screen": parsed.get("screen"),
            "completed_action": parsed.get("completed_action"),
            "preview_option": parsed.get("stats", {}).get("preview_option"),
            "preview_overlay_effects": _effect_summary(
                facts.get("preview_overlay_effects")
            ),
            "preview_modifier_effects": _effect_summary(
                facts.get("preview_modifier_effects")
            ),
        },
        "recovery": {
            "status": recovery.get("status") if isinstance(recovery, dict) else None,
            "option": recovery.get("option") if isinstance(recovery, dict) else None,
            "resolved_regions": (
                {
                    name: region.get("parsed_value")
                    for name, region in recovery.get("regions", {}).items()
                    if isinstance(region, dict) and region.get("parsed_value") is not None
                }
                if isinstance(recovery, dict)
                else {}
            ),
        },
        "observation_payloads": [
            item.get("payload")
            for item in observations.get("observations", [])
            if isinstance(item, dict)
        ],
    }
    if result:
        output["verified_negative"] = (
            parsed.get("screen") == "training_result"
            and parsed.get("completed_action") == "training"
            and not facts.get("preview_overlay_effects")
            and not facts.get("preview_modifier_effects")
            and not observations.get("observations")
        )
    else:
        expected = case["expected"]
        output["verified_positive"] = (
            raw.get("current_grid") is True
            and raw.get("result_grid") is False
            and parsed.get("screen") == "training_preview"
            and parsed.get("completed_action") is None
            and expected["option"] == parsed.get("stats", {}).get("preview_option")
            and expected["main"] <= main
            and expected["modifiers"] <= modifiers
            and ("stat_change", "skill_points", 1) not in main
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/final-reliability-v1/preview-clean-menu-source-verification-v1.json"),
    )
    args = parser.parse_args()
    reader = NeuralReader()
    checks = [
        _run_case(reader, case, result=False)
        for case in CASES
    ]
    negatives = [
        _run_case(reader, case, result=True)
        for case in NEGATIVES
    ]
    implementation = {}
    for relative in (
        "tracen_replay/preview_observations.py",
        "tracen_replay/preview_recovery.py",
        "tracen_replay/vision.py",
        "analyzer/lab/verify_clean_preview_source.py",
    ):
        implementation[relative] = _sha256(ROOT / relative)
    artifact = {
        "schema_version": "tracen-replay/preview-clean-menu-source-verification-v1",
        "purpose": "Verify real clean menu controls and reject result-crossfade preview promotion.",
        "source_video_scope": "Existing v1 source only; no expected values or balances are read by the parser.",
        "method": {
            "reader": "NeuralReader.read for clean controls; NeuralReader.read_training for result negatives",
            "consumer": "vision.parse followed by build_preview_observations",
            "amount_proof": "same-source validated crop consensus; no totals, balances, neighboring frames, or timestamp selectors",
        },
        "implementation_sha256": implementation,
        "clean_menu_cases": checks,
        "result_crossfade_negatives": negatives,
        "all_checks_passed": all(item.get("verified_positive") for item in checks)
        and all(item.get("verified_negative") for item in negatives),
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "all_checks_passed": artifact["all_checks_passed"]}))
    return 0 if artifact["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
