import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path.cwd()
parser = argparse.ArgumentParser(description="Build source fixtures without overwriting reviewed evidence.")
parser.add_argument('--output', type=Path, required=True)
OUT = parser.parse_args().output.resolve()
if OUT.exists():
    raise FileExistsError(f'Refusing to overwrite source fixture: {OUT}')
NUMERIC_PATH = ROOT / ".local/final-reliability-v1/numeric-causes.json"
DERIVED_PATH = ROOT / ".local/final-reliability-v1/derived-source-causes.json"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path):
    return path.relative_to(ROOT).as_posix()


def sidecar_candidates(image_path, recording, explicit=None):
    candidates = []
    if explicit:
        candidates.append(ROOT / explicit)
    candidates.extend(
        [
            image_path.with_suffix(".v2.json"),
            image_path.with_suffix(".json"),
            image_path.parent.parent / "neural" / f"{image_path.stem}.json",
            ROOT
            / f".local/full-recording/{recording}/neural/{image_path.stem}.json",
        ]
    )
    result = []
    for path in candidates:
        if path.exists() and path not in result:
            result.append(path)
    return result


def source_path_for(recording, value):
    value = value.replace("\\", "/")
    if value.startswith(".local/"):
        return ROOT / value
    return ROOT / f".local/full-recording/{recording}/{value}"


def amount_like(text):
    if not isinstance(text, str):
        return False
    text = text.strip()
    if not text or "/" in text:
        return False
    return bool(re.search(r"\d", text))


def region_role(key, text):
    text = text if isinstance(text, str) else ""
    if "/" in text:
        return "state_or_balance_text_excluded"
    if key.startswith(("gain.", "wide_gain.", "expanded_gain.")) and amount_like(text):
        if re.match(r"\s*\+\s*\d", text):
            return "amount_crop_candidate"
        return "unmarked_amount_candidate_excluded"
    if key.startswith("result.") and amount_like(text):
        return "result_crop_diagnostic_excluded"
    if key.startswith("result."):
        return "result_counter_or_unmarked_text_excluded"
    return "raw_crop_diagnostic"


def line_role(text, box, composure=False):
    text = text if isinstance(text, str) else ""
    y = box[1] if isinstance(box, list) and len(box) >= 2 else None
    if composure and "56+19" in text:
        return "merged_current_projection_observation", False
    if "/" in text:
        return "state_or_balance_text_excluded", False
    if re.match(r"\s*\+\s*\d", text):
        return "amount_or_projection_candidate", True
    if "+" in text and re.search(r"\d", text):
        return "noisy_amount_candidate_excluded", False
    if y is not None and y >= 680 and re.fullmatch(r"\s*\d{1,3}\s*", text):
        return "unmarked_counter_text_excluded", False
    return "source_context_line", False


def selected_lines(sidecar, composure=False):
    lines = sidecar.get("lines") or []
    result = []
    # Native v2 training sidecars intentionally have only header/option lines.
    # Neural gameplay sidecars contain the complete OCR list; retain the result
    # panel and the explicit current/projection geometry, while excluding the
    # upper stat/balance panel.
    for line in lines:
        text = line.get("text", "")
        box = line.get("box")
        y = box[1] if isinstance(box, list) and len(box) >= 2 else None
        keep = len(lines) <= 8
        if not keep and composure:
            keep = (
                "56+19" in text
                or text in {"Performance", "Points", "Vi", "Co"}
                or (isinstance(y, int) and 430 <= y <= 620 and "+" in text)
            )
        if not keep and not composure:
            keep = isinstance(y, int) and y >= 680
        if not keep:
            continue
        role, eligible = line_role(text, box, composure=composure)
        result.append(
            {
                "text": text,
                "confidence": line.get("confidence"),
                "box": box,
                "input_eligible": eligible,
                "role": role,
            }
        )
    return result


def selected_regions(sidecar):
    output = {}
    for key, value in sorted((sidecar.get("regions") or {}).items()):
        if not key.startswith(("gain.", "wide_gain.", "expanded_gain.", "result.")):
            continue
        text = value.get("text", "")
        role = region_role(key, text)
        output[key] = {
            "text": text,
            "confidence": value.get("confidence"),
            "box": value.get("box"),
            "input_eligible": role == "amount_crop_candidate",
            "role": role,
        }
    return output


def raw_observation(
    recording,
    evidence,
    *,
    source_visual=None,
    raw_ocr=None,
    phase_role=None,
    manual_label_hash=None,
    composure=False,
):
    image_rel = evidence.get("path") or evidence.get("absolute_path")
    if image_rel and not image_rel.startswith(".local/"):
        image_rel = Path(image_rel.replace("\\", "/")).as_posix()
    image_path = source_path_for(recording, image_rel)
    sidecars = sidecar_candidates(image_path, recording, evidence.get("raw_sidecar"))
    sidecar = sidecars[0] if sidecars else None
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    if sidecar is None:
        raise FileNotFoundError(f"No raw sidecar for {image_path}")
    sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
    image_sha = sha256(image_path)
    actual_ts = sidecar_data.get("source_timestamp_ms")
    expected_ts = evidence.get("timestamp_ms", evidence.get("source_timestamp_ms"))
    if expected_ts is None:
        expected_ts = actual_ts
    source_capture_sha = sidecar_data.get("source_frame_sha256")
    return {
        "path": rel(image_path),
        "timestamp_ms": expected_ts,
        "phase_role": phase_role,
        "source_visual_observation": source_visual,
        "source_raw_ocr_summary": raw_ocr,
        "capture_hashes": {
            "image_sha256": image_sha,
            "capture_sha256": source_capture_sha,
            "recording_source_sha256": sidecar_data.get("source_sha256"),
            "gameplay_capture_sha256": sidecar_data.get("gameplay_sha256"),
            "raw_sidecar_sha256": sha256(sidecar),
        },
        "source_label_hash_sha256": manual_label_hash,
        "raw_sidecar": {
            "path": rel(sidecar),
            "format": (
                "neural_capture_v2"
                if sidecar.name.endswith(".v2.json")
                else "neural_capture"
            ),
            "source_timestamp_ms": actual_ts,
            "source_frame_sha256": source_capture_sha,
            "source_sha256": sidecar_data.get("source_sha256"),
            "gameplay_sha256": sidecar_data.get("gameplay_sha256"),
            "engine_fingerprint": sidecar_data.get("engine_fingerprint"),
            "model_sha256": sidecar_data.get("model_sha256"),
        },
        "raw_lines": selected_lines(sidecar_data, composure=composure),
        "raw_regions": selected_regions(sidecar_data),
    }


numeric = json.loads(NUMERIC_PATH.read_text(encoding="utf-8"))
derived = json.loads(DERIVED_PATH.read_text(encoding="utf-8"))

numeric_phase_roles = {
    "v1/turn-017/skill_points": [
        "same_frame_full_badge_and_cross_crop_conflict",
        "recovery_full_badge_confirmation",
    ],
    "v1/turn-049/skill_points": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "v1/turn-063/speed": [
        "complete_badge_with_tight_crop_clipping",
        "reduced_component_phase",
    ],
    "v1/turn-064/speed": [
        "complete_badge_with_tight_crop_clipping",
        "reduced_component_phase",
    ],
    "v1/turn-064/wit": [
        "complete_badge_with_tight_crop_clipping",
        "reduced_component_phase",
    ],
    "v1/turn-064/skill_points": [
        "complete_badge_with_tight_crop_clipping",
        "reduced_component_phase",
    ],
    "v1/turn-066/speed": [
        "complete_badge_with_multiple_crop_disagreement",
        "reduced_component_phase",
    ],
    "v1/turn-072/training-0074": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "v1/turn-072/training-0076": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "independent-01/turn-031/wit": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "independent-01/turn-036/skill_points": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "independent-01/turn-042/skill_points": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "independent-01/turn-043/speed": [
        "recovery_full_badge",
        "reduced_component_phase",
    ],
    "independent-01/turn-071/speed": [
        "complete_badge_with_tight_crop_clipping",
        "reduced_component_phase",
    ],
    "independent-01/turn-073/speed": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
    "independent-02/turn-049/skill_points": [
        "complete_full_badge",
        "reduced_component_phase",
    ],
}

numeric_cases = []
for case in numeric["cases"]:
    case_id = case["id"]
    phases = numeric_phase_roles.get(case_id, [])
    observations = []
    for index, evidence in enumerate(case.get("source_evidence") or []):
        observations.append(
            raw_observation(
                case["recording"],
                evidence,
                source_visual=evidence.get("visual"),
                raw_ocr=evidence.get("raw_ocr"),
                phase_role=(
                    phases[index]
                    if index < len(phases)
                    else "source_reviewed_phase"
                ),
                manual_label_hash=evidence.get("sha256"),
            )
        )
    contribution_fixtures = []
    for contribution in case.get("contributions") or []:
        contribution_id = f"{case_id}/{contribution['event_id']}"
        contribution_phases = numeric_phase_roles.get(
            contribution_id,
            ["complete_full_badge", "reduced_component_phase"],
        )
        contribution_observations = []
        for index, evidence in enumerate(
            contribution.get("source_evidence") or []
        ):
            contribution_observations.append(
                raw_observation(
                    case["recording"],
                    evidence,
                    source_visual=evidence.get("visual"),
                    raw_ocr=evidence.get("raw_ocr"),
                    phase_role=(
                        contribution_phases[index]
                        if index < len(contribution_phases)
                        else "source_reviewed_phase"
                    ),
                    manual_label_hash=evidence.get("sha256"),
                )
            )
        contribution_fixtures.append(
            {
                "fixture_id": contribution_id,
                "kind": "reviewed_numeric_contribution",
                "source_backed": bool(contribution_observations),
                "manual_source_reviewed": bool(contribution_observations),
                "recording": case["recording"],
                "turn_id": case.get("turn_id"),
                "event_id": contribution.get("event_id"),
                "field": case.get("field"),
                "primary_cause": case.get("primary_cause"),
                "root_causes": case.get("root_causes"),
                "source_observation": contribution.get("source_evidence", [
                    {}
                ])[0].get("visual")
                if contribution.get("source_evidence")
                else None,
                "negative_evidence": contribution.get("negative_evidence") or [],
                "observation_window_ms": contribution.get("observation_window_ms"),
                "observed_amounts": contribution.get("observed_amounts"),
                "source_expected_amount": contribution.get("source_amount"),
                "observations": contribution_observations,
            }
        )
    item = {
        "fixture_id": case_id,
        "kind": "reviewed_numeric_case",
        "source_backed": bool(observations or contribution_fixtures),
        "manual_source_reviewed": bool(observations or contribution_fixtures),
        "recording": case["recording"],
        "turn_id": case.get("turn_id"),
        "event_id": case.get("event_id"),
        "field": case.get("field"),
        "primary_cause": case.get("primary_cause"),
        "root_causes": case.get("root_causes"),
        "source_observation": case.get("verdict"),
        "negative_evidence": case.get("negative_evidence") or [],
        "observation_window_ms": case.get("observation_window_ms"),
        "observations": observations,
    }
    item["contribution_count"] = (
        len(contribution_fixtures)
        if contribution_fixtures
        else int(bool(observations))
    )
    if contribution_fixtures:
        # Keep the aggregate comparison as one parent while preserving each
        # source-reviewed training event as its own child.  There is no
        # source_expected_amount on the parent: +14 and +42 are not one +56
        # award.
        item["contribution_count"] = len(contribution_fixtures)
        item["contributions"] = contribution_fixtures
    if case.get("source_amount") is not None and observations:
        item["source_expected_amount"] = case["source_amount"]
    if not observations and not contribution_fixtures:
        item["source_gap"] = (
            "No source image was bound for this aggregate residual; amount "
            "remains unobservable here."
        )
    numeric_cases.append(item)

composure = numeric["additional_targeted_cases"][0]
composure_evidence = composure["source_evidence"]
composure_item = {
    "fixture_id": composure["id"],
    "kind": "reviewed_composure_current_and_projection",
    "source_backed": True,
    "manual_source_reviewed": True,
    "recording": composure["recording"],
    "field": "performance_panel",
    "fields": ["current_composure", "projected_composure_gain"],
    "primary_cause": composure["primary_cause"],
    "source_observation": composure["verdict"],
    "negative_evidence": composure["negative_evidence"],
    "observations": [
        raw_observation(
            composure["recording"],
            composure_evidence,
            source_visual=composure_evidence.get("visual"),
            raw_ocr=composure_evidence.get("raw_ocr"),
            phase_role="same_panel_merged_current_projection_geometry",
            manual_label_hash=composure_evidence.get("sha256"),
            composure=True,
        )
    ],
    "source_expected_amounts": dict(composure["source_amounts"]),
    "source_pointers": {
        "current_composure": {
            "timestamp_ms": composure["timestamp_ms"],
            "raw_line_text": "56+19",
            "raw_line_box": [208, 515, 314, 553],
            "geometry_component": "left_component_of_shared_merged_line",
        },
        "projected_composure_gain": {
            "timestamp_ms": composure["timestamp_ms"],
            "raw_line_text": "56+19",
            "raw_line_box": [208, 515, 314, 553],
            "geometry_component": "right_component_of_shared_merged_line",
        },
    },
    "resolution_rule": (
        "Keep current_composure and projected_composure_gain as two separate "
        "fields; do not add them into one award."
    ),
}

derived_expected = {
    "v1-turn-028-training-0031-prefix": {"speed": 4, "skill_points": 13},
    "v1-turn-024-training-0026-wit": {"wit": 14},
    "independent-01-turn-016-training-0017": {
        "speed": 5,
        "power": 3,
        "guts": 11,
        "skill_points": 6,
    },
    "independent-01-turn-063-training-0059-prefix": {
        "wit": 20,
        "skill_points": 14,
    },
    "independent-02-turn-053-training-0083": {"skill_points": 14},
    "independent-02-turn-020-training-0031": {
        "stamina": 13,
        "guts": 13,
        "skill_points": 6,
    },
    "v1-turn-038-training-0042-direct-single": {"wit": 32},
    "independent-01-turn-015-training-0016-direct-single": {
        "speed": 3,
        "wit": 9,
        "skill_points": 6,
    },
    "v1-turn-068-training-0070-prefix": {
        "stamina": 44,
        "guts": 22,
        "skill_points": 22,
    },
    "independent-01-turn-053-training-0049": {
        "speed": 38,
        "skill_points": 18,
    },
}

derived_phase_roles = {
    "reviewed_prefix_conflict": [
        "transient_component_or_prefix_phase",
        "complete_or_stable_badge_phase",
    ],
    "reviewed_positive_visible": ["single_source_reviewed_badge"],
    "reviewed_readable_but_rejected": [
        "candidate_under_overlay_or_short_window",
        "later_other_field_phase",
    ],
}

derived_examples = []
for case in derived["reviewed_cases"]:
    phases = derived_phase_roles.get(case.get("status"), [])
    observations = []
    for index, frame in enumerate(case.get("source_frames") or []):
        evidence = {
            "path": (
                f".local/full-recording/{case['recording']}/"
                f"{frame['relative_path']}"
            ),
            "timestamp_ms": frame.get("source_timestamp_ms"),
            "visual": frame.get("visual_observation"),
            "raw_sidecar": None,
        }
        observations.append(
            raw_observation(
                case["recording"],
                evidence,
                source_visual=frame.get("visual_observation"),
                phase_role=(
                    phases[index]
                    if index < len(phases)
                    else "source_reviewed_phase"
                ),
            )
        )
    item = {
        "fixture_id": case["id"],
        "kind": "reviewed_derived_source_animation_example",
        "source_backed": bool(observations),
        "manual_source_reviewed": bool(observations),
        "recording": case["recording"],
        "turn_id": case.get("turn_id"),
        "event_id": case.get("event_id"),
        "fields": case.get("fields"),
        "source_review_status": case.get("status"),
        "badge_visibility": case.get("badge_visibility"),
        "source_observation": case.get("observation"),
        "observations": observations,
    }
    if case["id"] in derived_expected:
        item["source_expected_amounts"] = derived_expected[case["id"]]
    else:
        item["source_expected_amounts_status"] = (
            "omitted_for_candidate_or_rejected_promotion"
        )
    derived_examples.append(item)


def obs_rows(values, fields=None):
    rows = []
    for timestamp, value in values:
        row = {
            "source_timestamp_ms": timestamp,
            "evidence": f"{timestamp}.png",
            "raw_value": value,
        }
        if fields is not None:
            row["observed_training_gain_fields"] = list(fields)
        rows.append(row)
    return rows


def result_rows(rows, option="wit"):
    return [
        {
            "source_timestamp_ms": timestamp,
            "evidence": f"{timestamp}.png",
            "screen": "training_result",
            "training_option": option,
            "raw_training_gains": dict(gains),
            "raw_training_gain_candidates": {
                field: [amount] for field, amount in gains.items()
            },
        }
        for timestamp, gains in rows
    ]


synthetic_controls = [
    {
        "fixture_id": "synthetic/resolve-prefix/full-before-component",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_resolution.py",
        "test_name": (
            "TrainingGainResolutionTests."
            "test_brief_prefix_does_not_discard_two_complete_badges"
        ),
        "purpose": (
            "A brief leading-digit observation is retained as a prefix while "
            "two complete observations resolve the amount."
        ),
        "control_input": {
            "observations": obs_rows([(0, 2), (33, 21), (66, 21)])
        },
        "control_oracle": {
            "status": "resolved_prefix",
            "accepted_amount": 21,
            "complete_evidence": ["33.png", "66.png"],
            "prefix_value": 2,
        },
    },
    {
        "fixture_id": "synthetic/resolve-prefix/bracketed-glare",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_resolution.py",
        "test_name": (
            "TrainingGainResolutionTests."
            "test_bracketed_glare_can_hide_a_trailing_digit"
        ),
        "purpose": (
            "A one-frame full reading bracketing repeated prefix readings can "
            "resolve to the full badge."
        ),
        "control_input": {
            "observations": obs_rows([(0, 52), (33, 5), (66, 5), (100, 52)])
        },
        "control_oracle": {"status": "resolved_prefix", "accepted_amount": 52},
    },
    {
        "fixture_id": "synthetic/resolve-prefix/mixed-component-shapes",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_resolution.py",
        "test_name": (
            "TrainingGainResolutionTests."
            "test_mixed_component_shapes_cannot_fall_back_to_prefix_resolution"
        ),
        "purpose": (
            "Mixed companion shapes invalidate prefix fallback in either "
            "temporal order."
        ),
        "control_input": {
            "observations": [
                {
                    "source_timestamp_ms": 0,
                    "evidence": "0.png",
                    "raw_value": 52,
                    "observed_training_gain_fields": ["speed", "wit"],
                },
                {
                    "source_timestamp_ms": 33,
                    "evidence": "33.png",
                    "raw_value": 52,
                    "observed_training_gain_fields": ["speed", "wit"],
                },
                {
                    "source_timestamp_ms": 66,
                    "evidence": "66.png",
                    "raw_value": 5,
                    "observed_training_gain_fields": ["speed"],
                },
                {
                    "source_timestamp_ms": 99,
                    "evidence": "99.png",
                    "raw_value": 5,
                    "observed_training_gain_fields": ["speed", "wit"],
                },
            ]
        },
        "control_oracle": {
            "status": "unresolved",
            "accepted_amount": None,
            "reason": "mixed_component_shapes",
        },
    },
    {
        "fixture_id": "synthetic/resolve-prefix/ambiguous-alternatives",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_resolution.py",
        "test_name": (
            "TrainingGainResolutionTests."
            "test_reject_singleton_sustained_nonprefix_and_duplicate_views"
        ),
        "purpose": (
            "Singleton, duplicate, nonprefix, and reversed alternatives remain "
            "unresolved."
        ),
        "control_input": {
            "alternative_sequences": [
                [2, 21],
                [2, 21, 21],
                [2, 21, 21, 2, 2],
                [7, 21, 21],
                [2, 21, 21],
                [2, 21, 21],
                [72, 7, 7, 7],
            ]
        },
        "control_oracle": {
            "status": "unresolved",
            "accepted_amount": None,
            "reason": "insufficient_or_nonprefix_evidence",
        },
    },
    {
        "fixture_id": "synthetic/training-events/full-component-strict-shape",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_phases.py",
        "test_name": (
            "TrainingGainPhaseTests."
            "test_repeated_full_result_records_strict_component_candidate_"
            "without_accounting"
        ),
        "purpose": (
            "The full shape is retained as a strict component-phase candidate; "
            "the smaller component is not accounted separately."
        ),
        "control_input": {
            "rows": result_rows(
                [
                    (1000, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1033, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1066, {"speed": 8, "skill_points": 1}),
                    (1099, {"speed": 8, "skill_points": 1}),
                ]
            )
        },
        "control_oracle": {
            "status": "unresolved",
            "canonical_field_omitted": "skill_points",
            "conflicting_readings": [1, 11],
            "candidate_basis": (
                "repeated_full_gain_shape_strictly_contains_all_component_shapes"
            ),
            "phase_order": "full_before_component",
        },
    },
    {
        "fixture_id": "synthetic/training-events/equal-shape-alternatives",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_phases.py",
        "test_name": (
            "TrainingGainPhaseTests."
            "test_equal_shape_alternatives_remain_ambiguous_even_when_larger"
        ),
        "purpose": "Equal-shape alternatives stay ambiguous even when one value is larger.",
        "control_input": {
            "rows": result_rows(
                [
                    (1000, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1033, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1066, {"speed": 8, "wit": 21, "skill_points": 14}),
                    (1099, {"speed": 8, "wit": 21, "skill_points": 14}),
                ]
            )
        },
        "control_oracle": {
            "status": "unresolved",
            "canonical_field_omitted": "skill_points",
            "conflicting_readings": [11, 14],
        },
    },
    {
        "fixture_id": "synthetic/training-events/two-full-candidates",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_phases.py",
        "test_name": (
            "TrainingGainPhaseTests."
            "test_two_full_gain_candidates_stay_ambiguous_with_a_component_present"
        ),
        "purpose": (
            "A component between two complete candidates cannot choose between "
            "the complete values."
        ),
        "control_input": {
            "rows": result_rows(
                [
                    (1000, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1033, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1066, {"speed": 8, "skill_points": 1}),
                    (1099, {"speed": 8, "wit": 21, "skill_points": 14}),
                    (1132, {"speed": 8, "wit": 21, "skill_points": 14}),
                ]
            )
        },
        "control_oracle": {
            "status": "unresolved",
            "canonical_field_omitted": "skill_points",
            "conflicting_readings": [1, 11, 14],
        },
    },
    {
        "fixture_id": "synthetic/training-events/single-component",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_phases.py",
        "test_name": (
            "TrainingGainPhaseTests."
            "test_single_component_observation_is_not_enough_phase_evidence"
        ),
        "purpose": (
            "One component-only frame does not prove a full award or phase "
            "transition."
        ),
        "control_input": {
            "rows": result_rows(
                [
                    (1000, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1033, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1066, {"speed": 8, "skill_points": 1}),
                ]
            )
        },
        "control_oracle": {
            "status": "unresolved",
            "canonical_field_omitted": "skill_points",
            "conflicting_readings": [1, 11],
        },
    },
    {
        "fixture_id": "synthetic/training-events/reversed-phases",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_phases.py",
        "test_name": (
            "TrainingGainPhaseTests."
            "test_reversed_full_and_component_phases_remain_ambiguous"
        ),
        "purpose": (
            "The mixed-shape guard remains when the component precedes the "
            "complete shape."
        ),
        "control_input": {
            "rows": result_rows(
                [
                    (1000, {"speed": 8, "skill_points": 1}),
                    (1033, {"speed": 8, "skill_points": 1}),
                    (1066, {"speed": 8, "wit": 21, "skill_points": 11}),
                    (1099, {"speed": 8, "wit": 21, "skill_points": 11}),
                ],
                option=None,
            )
        },
        "control_oracle": {
            "status": "unresolved",
            "canonical_field_omitted": "skill_points",
            "conflicting_readings": [1, 11],
        },
    },
    {
        "fixture_id": "synthetic/recovery/conflicting-badges-without-balance",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_recovery.py",
        "test_name": (
            "TrainingGainRecoveryTests."
            "test_plan_uses_conflicting_badges_without_balance_input"
        ),
        "purpose": (
            "Recovery planning uses conflicting source badge readings and a "
            "bounded window; it does not use state balance arithmetic."
        ),
        "control_input": {
            "rows": [
                {
                    "source_timestamp_ms": 100,
                    "evidence": "100.png",
                    "raw_training_gains": {"speed": 1},
                },
                {
                    "source_timestamp_ms": 150,
                    "evidence": "150.png",
                    "raw_training_gains": {"speed": 13},
                },
            ],
            "event": {
                "first_seen_ms": 90,
                "last_seen_ms": 400,
                "conflicting_readings": {"speed": [1, 13]},
            },
        },
        "control_oracle": {
            "status": "recovery_window_planned",
            "window": {"start_ms": 90, "end_ms": 250, "fields": ["speed"]},
            "reason": "conflicting_observed_training_badge_digits",
        },
    },
    {
        "fixture_id": "synthetic/recovery/no-ocr-budget",
        "kind": "synthetic_control",
        "source_backed": False,
        "test_file": "tests/test_training_gain_recovery.py",
        "test_name": (
            "TrainingGainRecoveryTests."
            "test_no_ocr_and_zero_budget_leave_uncached_windows_pending"
        ),
        "purpose": (
            "When recovery is disabled or has no budget, pending windows remain "
            "explicit and no amount is manufactured."
        ),
        "control_input": {
            "rows": [
                {
                    "source_timestamp_ms": 100,
                    "evidence": "100.png",
                    "raw_training_gains": {"speed": 1},
                },
                {
                    "source_timestamp_ms": 150,
                    "evidence": "150.png",
                    "raw_training_gains": {"speed": 13},
                },
            ],
            "event": {
                "first_seen_ms": 90,
                "last_seen_ms": 400,
                "conflicting_readings": {"speed": [1, 13]},
            },
            "recovery_options": [
                {"allow_ocr": False},
                {"max_windows": 0},
                {"max_duration_ms": 0},
            ],
        },
        "control_oracle": {
            "status": "pending_unresolved",
            "new_frames": 0,
            "promotion": "none",
        },
    },
]

all_source_items = numeric_cases + [composure_item] + derived_examples


def item_observations(item):
    """Yield direct and nested source observations for a fixture item."""
    yield from item.get("observations", [])
    for contribution in item.get("contributions", []):
        yield from contribution.get("observations", [])


def numeric_contribution_count(items):
    """Count source contribution records while retaining aggregate parents."""
    return sum(
        len(item.get("contributions", []))
        if item.get("contributions")
        else int(bool(item.get("observations")))
        for item in items
    )


source_roots = sorted(
    {
        "/".join(obs["path"].split("/")[:3])
        for item in all_source_items
        for obs in item_observations(item)
        if obs["path"].startswith(".local/full-recording/")
    }
)

validation = {
    "images_checked": 0,
    "raw_sidecars_checked": 0,
    "missing_images": [],
    "missing_raw_sidecars": [],
    "image_sha256_mismatches": [],
    "source_label_sha256_mismatches": [],
    "timestamp_mismatches": [],
}
seen_images = set()
seen_sidecars = set()
for item in all_source_items:
    for obs in item_observations(item):
        image_path = ROOT / obs["path"]
        if obs["path"] in seen_images:
            continue
        seen_images.add(obs["path"])
        validation["images_checked"] += 1
        if not image_path.exists():
            validation["missing_images"].append(obs["path"])
            continue
        actual = sha256(image_path)
        if actual != obs["capture_hashes"]["image_sha256"]:
            validation["image_sha256_mismatches"].append(
                {
                    "path": obs["path"],
                    "expected": obs["capture_hashes"]["image_sha256"],
                    "actual": actual,
                }
            )
        if obs.get("source_label_hash_sha256") and actual != obs["source_label_hash_sha256"]:
            validation["source_label_sha256_mismatches"].append(
                {
                    "path": obs["path"],
                    "expected": obs["source_label_hash_sha256"],
                    "actual": actual,
                }
            )
        side_path = ROOT / obs["raw_sidecar"]["path"]
        if obs["raw_sidecar"]["path"] not in seen_sidecars:
            seen_sidecars.add(obs["raw_sidecar"]["path"])
            validation["raw_sidecars_checked"] += 1
            if not side_path.exists():
                validation["missing_raw_sidecars"].append(obs["raw_sidecar"]["path"])
            else:
                sidecar_data = json.loads(side_path.read_text(encoding="utf-8"))
                if (
                    sidecar_data.get("source_timestamp_ms")
                    != obs["raw_sidecar"]["source_timestamp_ms"]
                ):
                    validation["timestamp_mismatches"].append(
                        {
                            "path": obs["path"],
                            "source_timestamp_ms": obs["raw_sidecar"][
                                "source_timestamp_ms"
                            ],
                            "sidecar_timestamp_ms": sidecar_data.get(
                                "source_timestamp_ms"
                            ),
                        }
                    )
        elif not side_path.exists():
            validation["missing_raw_sidecars"].append(obs["raw_sidecar"]["path"])

validation["all_references_validated"] = not any(
    validation[key]
    for key in (
        "missing_images",
        "missing_raw_sidecars",
        "image_sha256_mismatches",
        "source_label_sha256_mismatches",
        "timestamp_mismatches",
    )
)

artifact = {
    "schema_version": "tracen-replay/training-gain-source-fixtures-v1",
    "generated_on": date.today().isoformat(),
    "purpose": (
        "Portable source-backed raw training-gain and current/projection "
        "fixtures for post-G2 crop and phase helper work."
    ),
    "source_policy": {
        "source_amount_rule": (
            "A source_expected_amount is present only when a human source "
            "review bound that amount to the visible gameplay badge or "
            "explicitly localized panel geometry."
        ),
        "input_scope": "Gameplay training-result and performance-panel crops only.",
        "excluded_inputs": [
            "before/after stats.values and facts.result_values",
            "balance constraints, residual arithmetic, and endpoint deltas",
            "auxiliary right-hand Career Profile panels and side logs",
            "state-derived amounts used to select a source amount",
        ],
        "raw_diagnostics_rule": (
            "State-like OCR remains only as raw diagnostic text with "
            "input_eligible=false; helper implementations must consume eligible "
            "crop observations and phase/geometry metadata."
        ),
        "mixed_shape_guard": (
            "A full/component phase requires the strict observed "
            "companion-shape and temporal evidence; mixed-shape ambiguity stays "
            "unresolved."
        ),
        "event_identity_rule": (
            "Use source path, capture hash, timestamp, crop family, and "
            "geometry; event IDs are context only and may shift between reports."
        ),
    },
    "frozen_inputs": {
        "numeric_causes": {
            "path": rel(NUMERIC_PATH),
            "sha256": sha256(NUMERIC_PATH),
        },
        "derived_source_causes": {
            "path": rel(DERIVED_PATH),
            "sha256": sha256(DERIVED_PATH),
        },
        "checklist": {
            "path": ".local/final-reliability-v1/final-analyzer-reliability-checklist.md",
            "sha256": sha256(ROOT / ".local/final-reliability-v1/final-analyzer-reliability-checklist.md"),
        },
        "synthetic_test_files": [
            {
                "path": "tests/test_training_gain_resolution.py",
                "sha256": sha256(ROOT / "analyzer/tests/test_training_gain_resolution.py"),
            },
            {
                "path": "tests/test_training_gain_phases.py",
                "sha256": sha256(ROOT / "analyzer/tests/test_training_gain_phases.py"),
            },
            {
                "path": "tests/test_training_gain_recovery.py",
                "sha256": sha256(ROOT / "analyzer/tests/test_training_gain_recovery.py"),
            },
        ],
    },
    "numeric_cases": numeric_cases,
    "composure_case": composure_item,
    "derived_source_examples": derived_examples,
    "synthetic_controls": synthetic_controls,
    "implementation_guidance": [
        (
            "Resolve crop disagreement by source geometry and crop family: an "
            "eligible full or expanded badge can coexist with clipped "
            "tight/result text; preserve alternatives as diagnostics."
        ),
        (
            "Resolve animation phases by companion-field shape and ordered frame "
            "timestamps. A reduced component reading is not a second award, and "
            "the existing mixed-shape guard must remain strict."
        ),
        (
            "Promotion requires a canonical source-linked amount observation. A "
            "raw OCR candidate with no canonical event delta remains an "
            "unresolved control case even if a report balance happens to match."
        ),
        (
            "Keep current Composure 56 and projected +19 as separate geometry "
            "observations. Do not synthesize a combined amount or use a balance "
            "to choose either value."
        ),
    ],
        "source_roots": source_roots,
    "validation": validation,
    "summary": {
        "numeric_case_count": len(numeric_cases),
        "numeric_cases_with_source_images": sum(
            any(item_observations(item)) for item in numeric_cases
        ),
        "numeric_cases_with_source_gap": sum(
            not any(item_observations(item)) for item in numeric_cases
        ),
        "numeric_contribution_count": numeric_contribution_count(numeric_cases),
        "composure_case_count": 1,
        "derived_example_count": len(derived_examples),
        "synthetic_control_count": len(synthetic_controls),
        "source_observation_count": sum(
            sum(1 for _ in item_observations(item)) for item in all_source_items
        ),
        "unique_source_images": validation["images_checked"],
        "unique_raw_sidecars": validation["raw_sidecars_checked"],
        "cause_examples": {
            "cross_crop_override": ["v1/turn-017/skill_points"],
            "clipped_digits": [
                "v1/turn-063/speed",
                "v1/turn-064/speed",
                "v1/turn-064/wit",
                "v1/turn-064/skill_points",
                "v1/turn-066/speed",
                "independent-01/turn-071/speed",
            ],
            "component_phase": [
                "v1/turn-049/skill_points",
                "independent-01/turn-073/speed",
                "independent-02/turn-049/skill_points",
            ],
            "current_projection_merge": [composure["id"]],
        },
        "required_examples_present": {
            "cross_crop_plus7_vs_plus72": any(
                item["fixture_id"] == "v1/turn-017/skill_points"
                for item in numeric_cases
            ),
            "clipped_plus11_plus54": (
                any(
                    item["fixture_id"] == "v1/turn-064/speed"
                    for item in numeric_cases
                )
                and any(
                    item["fixture_id"] == "v1/turn-064/wit"
                    for item in numeric_cases
                )
            ),
            "full_vs_component_13_vs_1": any(
                item["fixture_id"] == "independent-01/turn-073/speed"
                for item in numeric_cases
            ),
            "turn072_two_source_contributions": (
                next(
                    (
                        [
                            child.get("source_expected_amount")
                            for child in item.get("contributions", [])
                        ]
                        for item in numeric_cases
                        if item["fixture_id"] == "v1/turn-072"
                    ),
                    [],
                )
                == [14, 42]
            ),
            "composure_56_and_plus19_separate": composure_item[
                "source_expected_amounts"
            ]
            == {"current_composure": 56, "projected_composure_gain": 19},
        },
    },
}

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open('x', encoding='utf-8') as stream:
    stream.write(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
print(
    json.dumps(
        {
            "output": rel(OUT),
            "bytes": OUT.stat().st_size,
            "summary": artifact["summary"],
            "validation": validation,
        },
        indent=2,
    )
)
