from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image

import scope.workflow.stages as workflow_stages
from scope.cli.benchmark import (
    _build_codex_command,
    _build_codex_prompt,
    _materialize_manifest_cases,
    build_parser,
)
from scope.cli.gen_arena_eval import score_gen_arena_case, summarize_results
from scope.contracts.state import (
    ScopeCaseState,
    ScopeConstraint,
    ScopeEntity,
    ScopeRetrievalEvidence,
    ScopeReviewResult,
    ScopeUnknown,
    parse_decomposition_payload,
    parse_retrieval_payload,
    parse_reasoning_payload,
    parse_repair_payload,
    parse_synthesis_payload,
    parse_verification_payload,
)
from scope.runtime.image_backend import guess_image_extension
from scope.runtime.judge_backend import _parse_verification_outcome, looks_like_supported_image
from scope.runtime.reference_grid import MAX_DIRECT_REFERENCE_IMAGES
from scope.runtime.search_backend import _fetch_serper_image_results, search_external_unknowns, search_query_plan
from scope.runtime.settings import ImageGenSettings, JudgeSettings, RuntimeSettings, SearchSettings, get_runtime_settings
from scope.workflow.stages import (
    _prepare_generation_reference_images,
    run_decompose_stage,
    run_generate_stage,
    run_reason_stage,
    run_repair_stage,
    run_retrieve_stage,
    run_synthesize_stage,
    run_verify_stage,
    save_state,
    write_final_prompt,
)


@pytest.fixture(autouse=True)
def clear_runtime_settings_cache():
    get_runtime_settings.cache_clear()
    yield
    get_runtime_settings.cache_clear()


def _tiny_png_bytes() -> bytes:
    handle = BytesIO()
    Image.new("RGB", (8, 8), (32, 96, 160)).save(handle, format="PNG")
    return handle.getvalue()


def test_staged_workflow_writes_state_with_mocked_image_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_stages, "generate_image_bytes", lambda **_: _tiny_png_bytes())
    state_path = tmp_path / "state.json"
    run_decompose_stage(
        prompt="A clean math poster showing 17 + 28 in large readable numerals.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "poster", "priority": "primary"}],
            "constraints": [
                {
                    "id": "c1",
                    "text": "The poster asks for the result of 17 + 28.",
                    "type": "text",
                    "priority": "critical",
                    "spec": {"require_readable_text": True},
                }
            ],
            "unknowns": [
                {
                    "id": "u1",
                    "kind": "semantic_reasoning",
                    "owner_id": "c1",
                    "owner_kind": "constraint",
                    "question": "What final answer should be visible?",
                }
            ],
        },
    )
    run_reason_stage(
        state_path=state_path,
        reasoning_payload={
            "reasoning_resolutions": [
                {"unknown_id": "u1", "note": "The resolved final answer is 45."}
            ]
        },
    )
    run_synthesize_stage(
        state_path=state_path,
        synthesis_payload={
            "final_prompt": "A clean physical poster on a wall with large readable text: \"45\".",
            "synthesis_notes": ["Replaced the unresolved arithmetic expression with the resolved final answer."],
        },
    )
    run_generate_stage(state_path=state_path, output_dir=tmp_path)
    state = run_verify_stage(
        state_path=state_path,
        verification_payload={
            "review_results": [
                {
                    "id": "poster_text",
                    "verdict": "pass",
                    "reason": "The generated poster is treated as satisfying the resolved text for this unit test.",
                    "item_kind": "constraint",
                    "target_id": "c1",
                    "owner_id": "o1",
                }
            ],
            "new_unknowns": [],
        },
    )
    write_final_prompt(state_path=state_path, output_dir=tmp_path)

    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["model_name"] == "SCOPE"
    assert len(data["entities"]) >= 1
    assert len(data["constraints"]) >= 1
    assert "45" in data["final_prompt"]
    assert state.repair_action == "none"


def test_runtime_settings_default_to_outer_agent_controller():
    settings = RuntimeSettings()
    description = settings.describe()

    assert description["controller"]["owner"] == "outer_agent"
    assert description["controller"]["agent_family"] == "codex"
    assert "reasoning" not in description


def test_image_settings_follow_care_style_defaults(monkeypatch):
    for key in (
        "SCOPE_IMAGE_PROVIDER",
        "SCOPE_IMAGE_API_KEY",
        "SCOPE_IMAGE_BASE_URL",
        "SCOPE_IMAGE_MODEL",
        "SCOPE_IMAGE_EDIT_MODEL",
        "JDCLOUD_API_KEY",
        "JDCLOUD_MODEL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = ImageGenSettings()

    assert settings.provider == "jdcloud_gemini"
    assert settings.base_url == "https://modelservice.jdcloud.com/v1"
    assert settings.gen_model == "Gemini 3-Pro-Image-Preview"
    assert settings.edit_model == "Gemini 3-Pro-Image-Preview"


def test_judge_settings_follow_analysis_style_defaults(monkeypatch):
    for key in (
        "SCOPE_JUDGE_PROVIDER",
        "SCOPE_JUDGE_API_KEY",
        "SCOPE_JUDGE_BASE_URL",
        "SCOPE_JUDGE_MODEL",
        "JDCLOUD_API_KEY",
        "JDCLOUD_MODEL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = JudgeSettings()

    assert settings.provider == "jdcloud_gemini"
    assert settings.base_url == "https://modelservice.jdcloud.com/v1"
    assert settings.model_name == "Gemini 3-Pro-Preview"


def test_guess_image_extension_supports_common_formats():
    assert guess_image_extension(b"\x89PNG\r\n\x1a\npayload") == ".png"
    assert guess_image_extension(b"\xff\xd8\xffpayload") == ".jpg"
    assert guess_image_extension(b"RIFFxxxxWEBPpayload") == ".webp"
    assert guess_image_extension(b"GIF89apayload") == ".gif"
    assert guess_image_extension(b"text payload") == ".txt"


def test_judge_image_guard_recognizes_supported_raster_bytes():
    assert looks_like_supported_image(b"\x89PNG\r\n\x1a\npayload") is True
    assert looks_like_supported_image(b"\xff\xd8\xffpayload") is True
    assert looks_like_supported_image(b"text payload") is False


def test_state_roundtrip_preserves_reference_images():
    state = ScopeCaseState(
        prompt="test",
        input_images=["input.png"],
        reference_images=["ref_a.png", "ref_b.png"],
    )
    restored = ScopeCaseState.from_dict(state.to_dict())
    assert restored.reference_images == ["ref_a.png", "ref_b.png"]


def test_parse_agent_decomposition_payload_accepts_owned_structure():
    payload = {
        "entities": [
            {"id": "o1", "name": "mug", "priority": "primary"},
            {"id": "o2", "name": "table", "priority": "supporting"},
        ],
        "constraints": [
            {
                "id": "c1",
                "text": "The mug is blue.",
                "type": "attribute",
                "priority": "major",
                "spec": {"target_id": "o1", "attribute": "color", "value": "blue"},
            }
        ],
        "unknowns": [
            {
                "id": "u1",
                "kind": "external_reference",
                "owner_id": "o1",
                "owner_kind": "object",
                "question": "Is the mug tied to a specific brand reference?",
            }
        ],
    }

    decomposition = parse_decomposition_payload(payload)

    assert [item.name for item in decomposition.entities] == ["mug", "table"]
    assert decomposition.constraints[0].type == "attribute"
    assert decomposition.unknowns[0].owner_id == "o1"
    assert decomposition.unknowns[0].owner_name == "mug"


def test_decompose_stage_prefers_agent_payload_when_provided(tmp_path):
    state_path = tmp_path / "state.json"
    payload = {
        "entities": [{"id": "o1", "name": "poster", "priority": "primary"}],
        "constraints": [
            {
                "id": "c1",
                "text": "Show the equation 17 + 28 clearly.",
                "type": "text",
                "priority": "critical",
                "spec": {"require_readable_text": True},
            }
        ],
        "unknowns": [
            {
                "id": "u1",
                "kind": "semantic_reasoning",
                "owner_id": "c1",
                "owner_kind": "constraint",
                "question": "What exact result should be shown?",
            }
        ],
    }

    state = run_decompose_stage(
        prompt="A math poster.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload=payload,
    )

    assert [item.name for item in state.entities] == ["poster"]
    decomposition_data = json.loads((tmp_path / "decomposition.json").read_text(encoding="utf-8"))
    assert decomposition_data["source"] == "agent_json"
    assert decomposition_data["constraints"][0]["id"] == "c1"


def test_verify_stage_requires_payload_or_configured_judge_backend(tmp_path, monkeypatch):
    for key in (
        "SCOPE_JUDGE_API_KEY",
        "JDCLOUD_API_KEY",
        "JDCLOUD_MODEL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A simple prompt.")
    save_state(state, state_path)

    with pytest.raises(RuntimeError, match="Verification requires either --verification-json"):
        run_verify_stage(state_path=state_path)


def test_benchmark_cli_rejects_unknown_command():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["unsupported-command"])


def test_benchmark_cli_accepts_codex_dispatch_command(tmp_path):
    parser = build_parser()

    args = parser.parse_args(
        [
            "run-codex",
            "--index",
            str(tmp_path / "cases.index.json"),
            "--run-root",
            str(tmp_path / "runs"),
            "--concurrency",
            "3",
        ]
    )

    assert args.command == "run-codex"
    assert args.concurrency == 3


def test_codex_dispatch_command_uses_noninteractive_exec(tmp_path):
    command = _build_codex_command(
        codex_bin="codex",
        repo_root=tmp_path,
        model="gpt-test",
        sandbox="danger-full-access",
        last_message_path=tmp_path / "last.md",
        extra_args=["--search"],
    )

    assert command[:2] == ["codex", "exec"]
    assert "--cd" in command
    assert "--skip-git-repo-check" in command
    assert "--output-last-message" in command
    assert "--model" in command
    assert "gpt-test" in command
    assert command[-1] == "-"


def test_codex_dispatch_prompt_keeps_eval_packet_out_of_runtime(tmp_path):
    prompt = _build_codex_prompt(
        case_id="case_a",
        benchmark_input={
            "case_id": "case_a",
            "prompt": "Generate a red mug.",
            "input_images": [],
        },
        benchmark_input_path=tmp_path / "case_a" / "benchmark_input.json",
        run_dir=tmp_path / "runs" / "case_a",
        repo_root=tmp_path,
    )

    assert "scope-agentic-generation" in prompt
    assert "--benchmark-input-json" in prompt
    assert "Do not open or use the sibling benchmark_eval.json" in prompt
    assert "benchmark_eval.json" in prompt
    assert "checklist" not in prompt.lower()


def test_materialize_manifest_cases_writes_one_case_packet_per_row(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    rows = [
        {
            "case_id": "case_a",
            "dataset": "Gen-Arena/example",
            "type": "example",
            "prompt": "Prompt A",
        },
        {
            "case_id": "case_b",
            "dataset": "Gen-Arena/example",
            "type": "example",
            "prompt": "Prompt B",
        },
    ]
    manifest_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in rows),
        encoding="utf-8",
    )

    index_path = _materialize_manifest_cases(
        manifest_path=manifest_path,
        output_root=tmp_path / "materialized",
    )

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(index) == 2
    assert (tmp_path / "materialized" / "case_a" / "benchmark_input.json").exists()
    assert (tmp_path / "materialized" / "case_a" / "benchmark_eval.json").exists()
    assert (tmp_path / "materialized" / "case_b" / "benchmark_input.json").exists()
    assert (tmp_path / "materialized" / "case_b" / "benchmark_eval.json").exists()
    assert index[0]["input_json"].endswith("benchmark_input.json")
    assert index[0]["eval_json"].endswith("benchmark_eval.json")


def test_decompose_stage_can_bind_benchmark_input_context(tmp_path):
    state_path = tmp_path / "run" / "state.json"
    input_path = tmp_path / "run" / "benchmark_input.json"
    eval_path = tmp_path / "run" / "benchmark_eval.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(
        json.dumps(
            {
                "case_id": "gen_arena_case_001",
                "prompt": "A benchmark prompt.",
                "input_images": ["D:/input.png"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    eval_path.write_text(
        json.dumps(
            {
                "case_id": "gen_arena_case_001",
                "checklist": ["Subject is visible."],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state = run_decompose_stage(
        prompt="A benchmark prompt.",
        state_path=state_path,
        output_dir=state_path.parent,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "subject", "priority": "primary"}],
            "constraints": [],
            "unknowns": [],
        },
        benchmark_input=json.loads(input_path.read_text(encoding="utf-8")),
        benchmark_input_path=input_path,
    )

    data = json.loads(state_path.read_text(encoding="utf-8"))
    binding = json.loads((state_path.parent / "benchmark_binding.json").read_text(encoding="utf-8"))
    assert data["case_id"] == "gen_arena_case_001"
    assert data["input_images"] == ["D:/input.png"]
    assert data["reference_images"] == []
    assert data["checklist"] == []
    assert data["benchmark"] == {}
    assert state.stage_trace == ["decompose"]
    assert binding["case_id"] == "gen_arena_case_001"
    assert binding["state_inputs"]["input_images"] == ["D:/input.png"]
    assert binding["state_inputs"]["reference_images"] == []
    assert binding["state_inputs"]["checklist"] == []
    assert binding["state_inputs"]["benchmark"] == {}
    assert binding["eval_packet_json"].endswith("benchmark_eval.json")


def test_gen_arena_official_score_applies_entity_gate():
    eval_spec = {
        "case_id": "case_a",
        "type": "toy",
        "entities": [
            {"id": "O1", "name": "cat"},
            {"id": "O2", "name": "hat"},
        ],
        "constraints": [
            {"id": "C1", "type": "relation", "text": "The cat wears the hat.", "depends_on": ["O1", "O2"]},
            {"id": "C2", "type": "attribute", "text": "The hat is red.", "depends_on": ["O2"]},
        ],
    }
    verifier_payload = {
        "entities": [
            {"id": "O1", "verdict": "fail", "reason": "cat is missing"},
            {"id": "O2", "verdict": "pass", "reason": "hat is visible"},
        ],
        "constraints": [
            {"id": "C1", "verdict": "pass", "reason": "direct relation appears plausible"},
            {"id": "C2", "verdict": "pass", "reason": "hat is red"},
        ],
    }

    scored = score_gen_arena_case(eval_spec=eval_spec, verifier_payload=verifier_payload)

    assert scored["egip_pass"] is False
    assert scored["entity_pass_count"] == 1
    assert scored["constraint_direct_pass_count"] == 2
    assert scored["constraint_gated_pass_count"] == 1
    assert scored["constraints"][0]["direct_passed"] is True
    assert scored["constraints"][0]["gated_passed"] is False
    assert scored["constraints"][1]["gated_passed"] is True


def test_gen_arena_official_summary_treats_errors_as_failed_cases():
    rows = [
        {
            "case_id": "case_a",
            "type": "toy",
            "egip_pass": True,
            "entity_pass_count": 2,
            "entity_total": 2,
            "constraint_direct_pass_count": 2,
            "constraint_gated_pass_count": 2,
            "constraint_total": 2,
            "constraint_type_counts": {"attribute": {"total": 2, "direct_pass": 2, "gated_pass": 2}},
            "error": "",
        },
        {
            "case_id": "case_b",
            "type": "toy",
            "egip_pass": False,
            "error": "missing image",
        },
    ]

    summary = summarize_results(rows)

    assert summary["cases"] == 2
    assert summary["evaluated_cases"] == 1
    assert summary["error_cases"] == 1
    assert summary["egip"] == 0.5
    assert summary["by_category"]["toy"]["egip"] == 1.0


def test_parse_reasoning_payload_requires_bound_resolutions():
    notes = parse_reasoning_payload(
        {
            "reasoning_resolutions": [
                {
                    "unknown_id": "u1",
                    "note": "The resolved final answer is 45.",
                },
            ]
        },
        available_unknowns=[
            ScopeUnknown(
                id="u1",
                kind="semantic_reasoning",
                owner_id="c1",
                owner_kind="constraint",
                question="What exact result should be shown?",
            )
        ],
    )
    assert notes[0].unknown_id == "u1"
    assert notes[0].stage == "reason"
    assert notes[0].note == "The resolved final answer is 45."


def test_parse_retrieval_payload_requires_bound_resolutions():
    notes = parse_retrieval_payload(
        {
            "retrieval_resolutions": [
                {
                    "unknown_id": "u1",
                    "note": "Use the official reference image to preserve logo placement.",
                    "evidence": [
                        {
                            "kind": "web",
                            "title": "Official reference page",
                            "url": "https://example.com/reference",
                            "snippet": "Reference snippet.",
                            "query": "official reference",
                        }
                    ],
                }
            ]
        },
        available_unknowns=[
            ScopeUnknown(
                id="u1",
                kind="external_reference",
                owner_id="o1",
                owner_kind="object",
                question="Which reference should define the logo placement?",
                owner_name="mug",
            )
        ],
    )
    assert notes[0].unknown_id == "u1"
    assert notes[0].stage == "retrieve"
    assert notes[0].note == "Use the official reference image to preserve logo placement."
    assert notes[0].evidence[0].url == "https://example.com/reference"


def test_parse_synthesis_payload_accepts_final_prompt():
    payload = parse_synthesis_payload(
        {
            "final_prompt": "A clean poster with large readable text: \"45\".",
            "synthesis_notes": ["Replaced the unresolved arithmetic expression with the resolved final answer."],
        }
    )
    assert '"45"' in payload.final_prompt
    assert payload.synthesis_notes == ["Replaced the unresolved arithmetic expression with the resolved final answer."]


def test_parse_verification_payload_accepts_codex_review_results():
    payload = parse_verification_payload(
        {
            "review_results": [
                {
                    "id": "check_color",
                    "verdict": "pass",
                    "reason": "The mug is visibly blue.",
                    "item_kind": "constraint",
                    "target_id": "c1",
                    "owner_id": "o1",
                    "confidence": 0.88,
                }
            ],
            "new_unknowns": [],
        },
        entities=[ScopeEntity(id="o1", name="mug", priority="primary")],
        constraints=[],
        checklist=["The mug is blue."],
    )

    assert payload.review_results[0].id == "check_color"
    assert payload.review_results[0].verdict == "pass"
    assert payload.review_results[0].target_id == "c1"
    assert payload.review_results[0].owner_id == "o1"
    assert payload.new_unknowns == []


def test_parse_verification_payload_preserves_repair_routing_fields():
    payload = parse_verification_payload(
        {
            "review_results": [
                {
                    "id": "object_o1",
                    "verdict": "fail",
                    "reason": "The generated character does not match the runtime reference image.",
                    "item_kind": "object",
                    "target_id": "o1",
                    "owner_id": "o1",
                    "failure_family": "subject_repair",
                    "evidence": "Face and outfit differ from the reference.",
                },
                {
                    "id": "constraint_c1",
                    "verdict": "uncertain",
                    "reason": "Blocked by object identity failure.",
                    "item_kind": "constraint",
                    "target_id": "c1",
                    "owner_id": "o1",
                    "failure_family": "subject_repair",
                    "blocked_by": "object_o1",
                },
            ],
            "new_unknowns": [],
        },
        entities=[ScopeEntity(id="o1", name="character", priority="primary")],
        constraints=[
            ScopeConstraint(
                id="c1",
                text="The character should wear the canonical outfit.",
                type="attribute",
                priority="critical",
                spec={"target_id": "o1"},
            )
        ],
    )

    assert payload.review_results[0].failure_family == "subject_repair"
    assert payload.review_results[1].blocked_by == "object_o1"


def test_parse_repair_payload_requires_prompt_update_for_rewrite_prompt():
    payload = parse_repair_payload(
        {
            "selected_review_ids": ["math_result"],
            "repair_action": "rewrite_prompt",
            "updated_final_prompt": "A clean poster that explicitly shows 17 + 28 = 45.",
            "repair_patch": {
                "skill": "text_repair",
                "targets": ["math_result"],
                "recommended_action": "rewrite_prompt",
                "diagnosis": "The exact visible text is missing the resolved result.",
                "additions": ["Make the visible result explicit as 45."],
            },
        },
        available_review_ids={"math_result"},
    )
    assert payload.decision.repair_action == "rewrite_prompt"
    assert payload.updated_final_prompt.endswith("45.")


def test_parse_repair_payload_requires_patch_targets_and_diagnosis_for_non_none_action():
    with pytest.raises(ValueError, match="repair payload repair_patch.targets must be non-empty"):
        parse_repair_payload(
            {
                "selected_review_ids": ["math_result"],
                "repair_action": "rewrite_prompt",
                "updated_final_prompt": "A clean poster that explicitly shows 17 + 28 = 45.",
                "repair_patch": {
                    "skill": "text_repair",
                    "recommended_action": "rewrite_prompt",
                    "diagnosis": "The text is unresolved.",
                },
            },
            available_review_ids={"math_result"},
        )


def test_parse_repair_payload_rejects_unknown_repair_family():
    with pytest.raises(ValueError, match="repair_patch.skill must be one of"):
        parse_repair_payload(
            {
                "selected_review_ids": ["math_result"],
                "repair_action": "rewrite_prompt",
                "updated_final_prompt": "A clean poster that explicitly shows 17 + 28 = 45.",
                "repair_patch": {
                    "skill": "prompt_rewrite",
                    "targets": ["math_result"],
                    "recommended_action": "rewrite_prompt",
                    "diagnosis": "The text is unresolved.",
                },
            },
            available_review_ids={"math_result"},
        )


def test_reason_stage_prefers_agent_payload_when_provided(tmp_path):
    state_path = tmp_path / "state.json"
    run_decompose_stage(
        prompt="A math poster.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "poster", "priority": "primary"}],
            "constraints": [
                {
                    "id": "c1",
                    "text": "Show the equation 17 + 28 clearly.",
                    "type": "text",
                    "priority": "critical",
                    "spec": {"require_readable_text": True},
                }
            ],
            "unknowns": [
                {
                    "id": "u1",
                    "kind": "semantic_reasoning",
                    "owner_id": "c1",
                    "owner_kind": "constraint",
                    "question": "What exact result should be shown?",
                }
            ],
        },
    )

    state = run_reason_stage(
        state_path=state_path,
        reasoning_payload={
            "reasoning_resolutions": [
                {
                    "unknown_id": "u1",
                    "note": "Show only the final answer 45 in the final poster text.",
                }
            ]
        },
    )

    assert state.reasoning_resolutions[0].unknown_id == "u1"
    assert state.reasoning_resolutions[0].note == "Show only the final answer 45 in the final poster text."
    assert state.constraints[0].text == "Show the equation 17 + 28 clearly."
    reasoning_data = json.loads((tmp_path / "reasoning.json").read_text(encoding="utf-8"))
    assert reasoning_data["source"] == "agent_json"


def test_retrieve_stage_prefers_agent_payload_when_provided(tmp_path):
    state_path = tmp_path / "state.json"
    run_decompose_stage(
        prompt="A realistic poster for a specific brand mug.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "mug", "priority": "primary"}],
            "constraints": [],
            "unknowns": [
                {
                    "id": "u1",
                    "kind": "external_reference",
                    "owner_id": "o1",
                    "owner_kind": "object",
                    "question": "What visual reference is needed to preserve the brand identity?",
                }
            ],
        },
    )

    state = run_retrieve_stage(
        state_path=state_path,
        retrieval_payload={
            "retrieval_resolutions": [
                {
                    "unknown_id": "u1",
                    "note": "Use the official product reference to preserve the mug logo and handle silhouette.",
                }
            ]
        },
    )

    assert state.retrieval_resolutions[0].unknown_id == "u1"
    assert state.retrieval_resolutions[0].note == "Use the official product reference to preserve the mug logo and handle silhouette."
    retrieval_data = json.loads((tmp_path / "retrieval.json").read_text(encoding="utf-8"))
    assert retrieval_data["source"] == "agent_json"


def test_retrieve_stage_marks_unknown_resolved_and_synthesize_consumes_it(tmp_path):
    state_path = tmp_path / "state.json"
    run_decompose_stage(
        prompt="A realistic poster for a specific brand mug.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "mug", "priority": "primary"}],
            "constraints": [],
            "unknowns": [
                {
                    "id": "u1",
                    "kind": "external_reference",
                    "owner_id": "o1",
                    "owner_kind": "object",
                    "question": "What visual reference is needed to preserve the brand identity?",
                }
            ],
        },
    )

    state = run_retrieve_stage(
        state_path=state_path,
        retrieval_payload={
            "retrieval_resolutions": [
                {
                    "unknown_id": "u1",
                    "note": "Use the official product reference to preserve the mug logo and handle silhouette.",
                }
            ]
        },
    )

    assert state.unknowns[0].status == "resolved"

    state = run_synthesize_stage(
        state_path=state_path,
        synthesis_payload={
            "final_prompt": "A realistic poster of the official branded mug with the correct logo and handle silhouette.",
            "synthesis_notes": ["Folded the bound retrieval resolution into the prompt."],
        },
    )

    assert state.unknowns[0].status == "consumed"
    assert state.retrieval_resolutions[0].unknown_id == "u1"


def test_search_external_unknowns_uses_serper_results(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = "{}"
        ok = True

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, headers, json, timeout):
        if url.endswith("/images"):
            return FakeResponse(
                {
                    "images": [
                        {
                            "title": "Official image result",
                            "imageUrl": "https://example.com/image.jpg",
                            "source": "Example source",
                        }
                    ]
                }
            )
        return FakeResponse(
            {
                "organic": [
                    {
                        "title": "Official visual reference",
                        "link": "https://example.com/reference",
                        "snippet": "Shows the recognizable subject appearance.",
                    }
                ]
            }
        )

    monkeypatch.setattr("scope.runtime.search_backend.requests.post", fake_post)
    settings = RuntimeSettings(
        search=SearchSettings(
            provider="serper",
            serper_api_key="test-key",
            num_results=2,
            request_timeout=1,
        )
    )

    records = search_external_unknowns(
        [
            ScopeUnknown(
                id="u1",
                kind="external_reference",
                owner_id="o1",
                owner_kind="object",
                owner_name="Mr.Beast",
                question="What should Mr.Beast look like visually?",
            )
        ],
        settings=settings,
    )

    assert records[0]["unknown_id"] == "u1"
    assert records[0]["query"] == "Mr.Beast"
    assert records[0]["evidence"][0]["kind"] == "web"
    assert records[0]["evidence"][0]["url"] == "https://example.com/reference"
    assert any(item["kind"] == "image" for item in records[0]["evidence"])


def test_search_query_plan_uses_agent_planned_queries(monkeypatch):
    def fake_web_results(query, settings):
        return [
            ScopeRetrievalEvidence(
                kind="web",
                title="Official result",
                url="https://example.com/official",
                query=query,
            )
        ]

    def fake_image_results(query, settings):
        return [
            ScopeRetrievalEvidence(
                kind="image",
                title="Portrait result",
                url="https://example.com/portrait.jpg",
                query=query,
            )
        ]

    monkeypatch.setattr("scope.runtime.search_backend._fetch_serper_web_results", fake_web_results)
    monkeypatch.setattr("scope.runtime.search_backend._fetch_serper_image_results", fake_image_results)
    settings = RuntimeSettings(
        search=SearchSettings(
            provider="serper",
            serper_api_key="test-key",
            num_results=2,
            request_timeout=1,
        )
    )

    records = search_query_plan(
        [
            {
                "unknown_id": "u1",
                "text_queries": ["MrBeast"],
                "image_queries": ["MrBeast portrait", "MrBeast official photo"],
            }
        ],
        unknowns=[
            ScopeUnknown(
                id="u1",
                kind="external_reference",
                owner_id="o1",
                owner_kind="object",
                owner_name="MrBeast",
                question="What should MrBeast look like?",
            )
        ],
        settings=settings,
    )

    assert records[0]["text_queries"] == ["MrBeast"]
    assert records[0]["image_queries"] == ["MrBeast portrait", "MrBeast official photo"]
    assert {item["query"] for item in records[0]["evidence"]} == {
        "MrBeast",
        "MrBeast portrait",
        "MrBeast official photo",
    }


def test_fetch_serper_image_results_limits_each_query_to_one_result(monkeypatch):
    class FakeResponse:
        status_code = 200
        ok = True

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "images": [
                    {"title": "first", "imageUrl": "https://example.com/1.jpg", "source": "Example"},
                    {"title": "second", "imageUrl": "https://example.com/2.jpg", "source": "Example"},
                ]
            }

    def fake_post(url, headers, json, timeout):
        assert json["num"] == 1
        return FakeResponse()

    monkeypatch.setattr("scope.runtime.search_backend.requests.post", fake_post)
    settings = RuntimeSettings(
        search=SearchSettings(
            provider="serper",
            serper_api_key="test-key",
            num_results=5,
            request_timeout=1,
        )
    )

    results = _fetch_serper_image_results("MrBeast portrait", settings=settings)

    assert len(results) == 1
    assert results[0].url == "https://example.com/1.jpg"


def test_search_query_plan_downloads_one_image_for_each_image_query(monkeypatch, tmp_path):
    def fake_web_results(query, settings):
        return []

    def fake_image_results(query, settings):
        slug = query.replace(" ", "_")
        return [
            ScopeRetrievalEvidence(
                kind="image",
                title=f"{query} result",
                url=f"https://example.com/{slug}.jpg",
                query=query,
            )
        ]

    def fake_download_reference_image(url, output_dir, stem, timeout):
        path = output_dir / f"{stem}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff")
        return str(path)

    monkeypatch.setattr("scope.runtime.search_backend._fetch_serper_web_results", fake_web_results)
    monkeypatch.setattr("scope.runtime.search_backend._fetch_serper_image_results", fake_image_results)
    monkeypatch.setattr("scope.runtime.search_backend._download_reference_image", fake_download_reference_image)
    settings = RuntimeSettings(
        search=SearchSettings(
            provider="serper",
            serper_api_key="test-key",
            num_results=5,
            request_timeout=1,
        )
    )

    records = search_query_plan(
        [
            {
                "unknown_id": "u1",
                "text_queries": [],
                "image_queries": [
                    "query one",
                    "query two",
                    "query three",
                    "query four",
                ],
            }
        ],
        unknowns=[
            ScopeUnknown(
                id="u1",
                kind="external_reference",
                owner_id="o1",
                owner_kind="object",
                owner_name="Example subject",
                question="What should it look like?",
            )
        ],
        settings=settings,
        download_dir=tmp_path,
    )

    image_evidence = [item for item in records[0]["evidence"] if item["kind"] == "image"]
    assert len(image_evidence) == 4
    assert all(item["local_path"] for item in image_evidence)


def test_prepare_generation_reference_images_collapses_more_than_three_images_into_grid(tmp_path):
    image_paths = []
    for index in range(MAX_DIRECT_REFERENCE_IMAGES + 1):
        path = tmp_path / f"reference_{index}.png"
        Image.new("RGB", (48, 48), (index * 20, 80, 160)).save(path)
        image_paths.append(str(path))

    prepared, grid_path = _prepare_generation_reference_images(
        image_paths,
        output_dir=tmp_path,
        iteration=1,
    )

    assert len(prepared) == 1
    assert prepared[0] == grid_path
    assert Path(grid_path).is_file()


def test_generate_stage_records_reference_grid_when_reference_count_exceeds_three(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_stages, "generate_image_bytes", lambda **_: _tiny_png_bytes())
    state_path = tmp_path / "state.json"
    reference_images = []
    for index in range(MAX_DIRECT_REFERENCE_IMAGES + 1):
        path = tmp_path / f"reference_{index}.png"
        Image.new("RGB", (48, 48), (index * 20, 80, 160)).save(path)
        reference_images.append(str(path))

    state = ScopeCaseState(prompt="A prompt.", reference_images=reference_images)
    save_state(state, state_path)

    run_generate_stage(state_path=state_path, output_dir=tmp_path)

    generation = json.loads((tmp_path / "generation.json").read_text(encoding="utf-8"))
    assert generation["raw_reference_images"] == reference_images
    assert len(generation["reference_images"]) == 1
    assert generation["reference_grid_path"] == generation["reference_images"][0]
    assert Path(generation["reference_grid_path"]).is_file()


def test_decompose_stage_requires_agent_payload_in_strict_mode(tmp_path):
    with pytest.raises(ValueError, match="decompose requires --decomposition-json"):
        run_decompose_stage(
            prompt="A mug on a table.",
            state_path=tmp_path / "state.json",
            output_dir=tmp_path,
        )


def test_synthesize_stage_prefers_agent_payload_when_provided(tmp_path):
    state_path = tmp_path / "state.json"
    run_decompose_stage(
        prompt="A blue mug on a table.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "mug", "priority": "primary"}],
            "constraints": [],
            "unknowns": [],
        },
    )

    state = run_synthesize_stage(
        state_path=state_path,
        synthesis_payload={
            "final_prompt": "A realistic studio photo of a single blue mug on a clean wooden table, no text.",
            "synthesis_notes": ["Made style and text exclusion explicit."],
        },
    )

    assert state.final_prompt.startswith("A realistic studio photo")
    synthesis_data = json.loads((tmp_path / "synthesis.json").read_text(encoding="utf-8"))
    assert synthesis_data["source"] == "agent_json"


def test_synthesize_stage_requires_agent_payload_in_strict_mode(tmp_path):
    state_path = tmp_path / "state.json"
    run_decompose_stage(
        prompt="A blue mug on a table.",
        state_path=state_path,
        output_dir=tmp_path,
        decomposition_payload={
            "entities": [{"id": "o1", "name": "mug", "priority": "primary"}],
            "constraints": [],
            "unknowns": [],
        },
    )

    with pytest.raises(ValueError, match="synthesize requires --synthesis-json"):
        run_synthesize_stage(
            state_path=state_path,
        )


def test_verify_stage_discovers_new_unknowns_and_merges_them(tmp_path):
    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A poster showing 17 + 28.")
    state.final_prompt = "A poster showing 17 + 28."
    state_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    updated = run_verify_stage(
        state_path=state_path,
        verification_payload={
            "review_results": [
                {
                    "id": "math_result",
                    "verdict": "fail",
                    "reason": "The exact arithmetic result is not yet rendered.",
                    "item_kind": "constraint",
                    "target_id": "p0",
                    "owner_id": "p0",
                }
            ],
            "new_unknowns": [
                {
                    "id": "u_verify_reason_1",
                    "kind": "semantic_reasoning",
                    "owner_id": "p0",
                    "owner_kind": "prompt",
                    "question": "What exact arithmetic result should be rendered before the next generation attempt?",
                }
            ],
        },
    )

    assert any(item.id == "u_verify_reason_1" for item in updated.verification_unknowns)
    assert any(item.id == "u_verify_reason_1" for item in updated.unknowns)
    verification_data = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    assert verification_data["new_unknowns"][0]["id"] == "u_verify_reason_1"


def test_verify_stage_prefers_codex_payload_over_backend(tmp_path):
    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A blue mug on a table.")
    state.entities = [ScopeEntity(id="o1", name="mug", priority="primary")]
    state.constraints = [
        ScopeConstraint(
            id="c1",
            text="The mug must be blue.",
            type="attribute",
            priority="critical",
        )
    ]
    save_state(state, state_path)

    updated = run_verify_stage(
        state_path=state_path,
        verification_payload={
            "review_results": [
                {
                    "id": "blue_mug",
                    "verdict": "uncertain",
                    "reason": "The generated image has not clearly established the mug color.",
                    "item_kind": "constraint",
                    "target_id": "c1",
                    "owner_id": "o1",
                    "failure_family": "attribute_repair",
                    "confidence": 0.4,
                    "evidence": "Color is ambiguous.",
                }
            ],
            "new_unknowns": [
                {
                    "id": "u_verify_color_1",
                    "kind": "semantic_reasoning",
                    "owner_id": "c1",
                    "owner_kind": "constraint",
                    "question": "What explicit color instruction should be carried into the next prompt?",
                }
            ],
        },
    )

    verification_data = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    assert verification_data["source"] == "agent_json"
    assert verification_data["backend"] == "codex"
    assert verification_data["entities"][0]["id"] == "o1"
    assert verification_data["constraints"][0]["id"] == "c1"
    assert verification_data["unknowns_before_verify"] == []
    assert verification_data["unknowns_after_verify"][0]["id"] == "u_verify_color_1"
    assert verification_data["review_results"][0]["target_id"] == "c1"
    assert verification_data["review_results"][0]["failure_family"] == "attribute_repair"
    assert updated.review_results[0].id == "blue_mug"
    assert updated.review_results[0].failure_family == "attribute_repair"
    assert updated.verification_unknowns[0].id == "u_verify_color_1"
    assert any(item.id == "u_verify_color_1" for item in updated.unknowns)


def test_verify_stage_can_discover_external_reference_unknowns(tmp_path):
    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A clean poster showing Mr.Beast as the main subject.")
    state.entities = [ScopeEntity(id="o1", name="Mr.Beast", priority="primary")]
    state.final_prompt = "A clean poster showing Mr.Beast as the main subject, no extra text."
    state_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    updated = run_verify_stage(
        state_path=state_path,
        verification_payload={
            "review_results": [
                {
                    "id": "identity_grounding",
                    "verdict": "fail",
                    "reason": "The named subject requires external visual grounding.",
                    "item_kind": "object",
                    "target_id": "o1",
                    "owner_id": "o1",
                }
            ],
            "new_unknowns": [
                {
                    "id": "u_verify_reference_1",
                    "kind": "external_reference",
                    "owner_id": "o1",
                    "owner_kind": "object",
                    "question": "What should Mr.Beast look like visually in the next generation attempt?",
                }
            ],
        },
    )

    assert any(item.id == "u_verify_reference_1" for item in updated.verification_unknowns)
    assert any(item.kind == "external_reference" for item in updated.unknowns)


def test_verify_stage_reopens_matching_unknown_and_drops_stale_resolution(tmp_path):
    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A poster showing 17 + 28.")
    state.unknowns = [
        ScopeUnknown(
            id="u_existing",
            kind="semantic_reasoning",
            owner_id="p0",
            owner_kind="prompt",
            question="What exact arithmetic result should be rendered before the next generation attempt?",
            status="consumed",
            source="verify",
            resolved_by="reason",
        )
    ]
    state.reasoning_resolutions = [
        {
            "unknown_id": "u_existing",
            "note": "The resolved final answer is 45.",
            "owner_id": "p0",
            "owner_kind": "prompt",
            "kind": "semantic_reasoning",
            "stage": "reason",
            "owner_name": "",
            "question": "What exact arithmetic result should be rendered before the next generation attempt?",
        }
    ]
    state.final_prompt = "A poster showing 17 + 28."
    state_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    updated = run_verify_stage(
        state_path=state_path,
        verification_payload={
            "review_results": [
                {
                    "id": "math_result",
                    "verdict": "fail",
                    "reason": "The prompt still lacks the resolved arithmetic answer.",
                    "item_kind": "constraint",
                    "target_id": "p0",
                    "owner_id": "p0",
                }
            ],
            "new_unknowns": [
                {
                    "id": "u_verify_reason_1",
                    "kind": "semantic_reasoning",
                    "owner_id": "p0",
                    "owner_kind": "prompt",
                    "question": "What exact arithmetic result should be rendered before the next generation attempt?",
                }
            ],
        },
    )

    assert any(item.id == "u_existing" and item.status == "open" for item in updated.unknowns)
    assert updated.reasoning_resolutions == []


def test_judge_parser_accepts_owner_aligned_new_unknowns():
    raw_text = json.dumps(
        {
            "review_results": [
                {
                    "id": "identity_grounding",
                    "verdict": "fail",
                    "reason": "The identity is not grounded precisely enough.",
                    "item_kind": "object",
                    "confidence": 0.62,
                    "evidence": "The subject is generic rather than clearly matching the named person.",
                }
            ],
            "new_unknowns": [
                {
                    "id": "u_verify_reference_1",
                    "kind": "external_reference",
                    "owner_id": "o1",
                    "owner_kind": "object",
                    "owner_name": "Mr.Beast",
                    "question": "What should Mr.Beast look like visually in the next generation attempt?",
                }
            ],
        }
    )

    outcome = _parse_verification_outcome(
        raw_text,
        [],
        entities=[ScopeEntity(id="o1", name="Mr.Beast", priority="primary")],
        constraints=[],
    )

    assert outcome.new_unknowns[0].owner_id == "o1"
    assert outcome.new_unknowns[0].owner_kind == "object"


def test_repair_stage_prefers_agent_payload_and_updates_prompt(tmp_path):
    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A poster showing 17 + 28.")
    state.final_prompt = "A poster showing 17 + 28."
    state.review_results = [
        ScopeReviewResult(
            id="math_result",
            verdict="fail",
            reason="The exact result is missing.",
            item_kind="constraint",
        )
    ]
    state_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    updated = run_repair_stage(
        state_path=state_path,
        repair_payload={
            "selected_review_ids": ["math_result"],
            "repair_action": "rewrite_prompt",
            "updated_final_prompt": "A poster showing 17 + 28 = 45.",
            "repair_patch": {
                "skill": "text_repair",
                "targets": ["math_result"],
                "recommended_action": "rewrite_prompt",
                "diagnosis": "The current prompt does not explicitly require the resolved result to be shown.",
                "additions": ["Explicitly include the correct result 45."],
            },
        },
    )

    assert updated.repair_action == "rewrite_prompt"
    assert updated.final_prompt.endswith("45.")
    repair_data = json.loads((tmp_path / "repair_decision.json").read_text(encoding="utf-8"))
    assert repair_data["source"] == "agent_json"


def test_repair_stage_requires_agent_payload_in_strict_mode(tmp_path):
    state_path = tmp_path / "state.json"
    state = ScopeCaseState(prompt="A poster showing 17 + 28.")
    state.review_results = [
        ScopeReviewResult(
            id="math_result",
            verdict="fail",
            reason="The exact result is missing.",
            item_kind="constraint",
        )
    ]
    state_path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="repair requires --repair-json"):
        run_repair_stage(
            state_path=state_path,
        )
