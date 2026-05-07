from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict
from pathlib import Path

from scope.contracts.state import (
    ScopeBestVerification,
    ScopeCaseState,
    parse_decomposition_payload,
    parse_retrieval_payload,
    parse_reasoning_payload,
    parse_repair_payload,
    parse_synthesis_payload,
    parse_verification_payload,
)
from scope.runtime.image_backend import generate_image_bytes, guess_image_extension
from scope.runtime.judge_backend import judge_image
from scope.runtime.reference_grid import collapse_reference_images_for_generation
from scope.runtime.settings import get_runtime_settings
from scope.workflow.config import MODEL_NAME


def run_decompose_stage(
    *,
    prompt: str,
    state_path: str | Path,
    output_dir: str | Path,
    decomposition_payload: dict | None = None,
    benchmark_input: dict | None = None,
    benchmark_input_path: str | Path | None = None,
) -> ScopeCaseState:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    state = ScopeCaseState(prompt=prompt, model_name=MODEL_NAME)
    state.run_config = get_runtime_settings().describe()
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="run_config.json",
        kind="run_config",
        payload=state.run_config,
    )
    if decomposition_payload is None:
        raise ValueError("decompose requires --decomposition-json")
    decomposition = parse_decomposition_payload(decomposition_payload)
    state.entities = list(decomposition.entities)
    state.constraints = list(decomposition.constraints)
    state.unknowns = list(decomposition.unknowns)
    if not state.entities and not state.constraints and not state.unknowns:
        raise ValueError(
            "decomposition must include at least one item across entities/constraints/unknowns"
        )
    if benchmark_input is not None:
        _bind_benchmark_input(
            state=state,
            benchmark_input=benchmark_input,
            benchmark_input_path=benchmark_input_path,
            state_path=state_path,
        )
    state.stage_trace.append("decompose")
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="decomposition.json",
        kind="decomposition",
        payload={
            "prompt": state.prompt,
            "source": "agent_json",
            "entities": [asdict(item) for item in state.entities],
            "constraints": [asdict(item) for item in state.constraints],
            "unknowns": [asdict(item) for item in state.unknowns],
        },
    )
    save_state(state, state_path)
    return state


def run_retrieve_stage(
    *,
    state_path: str | Path,
    retrieval_payload: dict | None = None,
) -> ScopeCaseState:
    state = load_state(state_path)
    external_unknowns = _active_unknowns(state.unknowns, kind="external_reference")
    if external_unknowns:
        if retrieval_payload is None:
            raise ValueError("retrieve requires --retrieval-json")
        retrieval_resolutions = parse_retrieval_payload(
            retrieval_payload,
            available_unknowns=external_unknowns,
        )
        source = "agent_json"
        state.retrieval_resolutions = _upsert_resolutions(state.retrieval_resolutions, retrieval_resolutions)
        _add_retrieval_reference_images(state, retrieval_resolutions)
        state.unknowns = _mark_unknowns_resolved(state.unknowns, retrieval_resolutions, stage="retrieve")
        state.stage_trace.append("retrieve")
        triggered = True
    else:
        if retrieval_payload is not None:
            raise ValueError("retrieval payload was provided but state has no external_reference unknowns")
        state.stage_trace.append("retrieve:skipped:no_external_reference_unknown")
        triggered = False
        source = "stage_skip"
        retrieval_resolutions = []
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="retrieval.json",
        kind="retrieval",
        payload={
            "source": source,
            "triggered": triggered,
            "unknowns": [asdict(item) for item in external_unknowns],
            "retrieval_resolutions": [asdict(item) for item in retrieval_resolutions],
            "resolved_unknown_ids": [item.unknown_id for item in retrieval_resolutions],
            "skip_reason": "" if triggered else "no_external_reference_unknown",
        },
    )
    save_state(state, state_path)
    return state


def _add_retrieval_reference_images(state: ScopeCaseState, retrieval_resolutions: list) -> None:
    existing = set(state.reference_images)
    for resolution in retrieval_resolutions:
        for evidence in resolution.evidence:
            local_path = str(evidence.local_path).strip()
            if not local_path or local_path in existing:
                continue
            if Path(local_path).is_file():
                state.reference_images.append(local_path)
                existing.add(local_path)


def run_reason_stage(
    *,
    state_path: str | Path,
    reasoning_payload: dict | None = None,
) -> ScopeCaseState:
    state = load_state(state_path)
    reasoning_unknowns = _active_unknowns(state.unknowns, kind="semantic_reasoning")
    if reasoning_unknowns:
        if reasoning_payload is None:
            raise ValueError("reason requires --reasoning-json")
        reasoning_resolutions = parse_reasoning_payload(
            reasoning_payload,
            available_unknowns=reasoning_unknowns,
        )
        source = "agent_json"
        state.reasoning_resolutions = _upsert_resolutions(state.reasoning_resolutions, reasoning_resolutions)
        state.unknowns = _mark_unknowns_resolved(state.unknowns, reasoning_resolutions, stage="reason")
        state.stage_trace.append("reason")
        triggered = True
    else:
        if reasoning_payload is not None:
            raise ValueError("reasoning payload was provided but state has no semantic_reasoning unknowns")
        state.stage_trace.append("reason:skipped:no_semantic_reasoning_unknown")
        triggered = False
        source = "stage_skip"
        reasoning_resolutions = []
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="reasoning.json",
        kind="reasoning",
        payload={
            "source": source,
            "triggered": triggered,
            "unknowns": [asdict(item) for item in reasoning_unknowns],
            "reasoning_resolutions": [asdict(item) for item in reasoning_resolutions],
            "resolved_unknown_ids": [item.unknown_id for item in reasoning_resolutions],
            "skip_reason": "" if triggered else "no_semantic_reasoning_unknown",
        },
    )
    save_state(state, state_path)
    return state


def run_synthesize_stage(
    *,
    state_path: str | Path,
    synthesis_payload: dict | None = None,
) -> ScopeCaseState:
    state = load_state(state_path)
    notes = _collect_resolution_notes(state)
    if synthesis_payload is None:
        raise ValueError("synthesize requires --synthesis-json")
    synthesis_plan = parse_synthesis_payload(synthesis_payload)
    state.final_prompt = synthesis_plan.final_prompt
    synthesis_notes = synthesis_plan.synthesis_notes
    source = "agent_json"
    state.stage_trace.append("synthesize")
    state.verification_unknowns = []
    state.unknowns = _consume_resolved_unknowns(state.unknowns)
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="synthesis.json",
        kind="synthesis",
        payload={
            "source": source,
            "prompt": state.prompt,
            "notes": notes,
            "retrieval_resolutions": [asdict(item) for item in state.retrieval_resolutions],
            "reasoning_resolutions": [asdict(item) for item in state.reasoning_resolutions],
            "synthesis_notes": synthesis_notes,
            "final_prompt": state.final_prompt,
        },
    )
    save_state(state, state_path)
    return state


def run_generate_stage(
    *,
    state_path: str | Path,
    output_dir: str | Path,
) -> ScopeCaseState:
    state = load_state(state_path)
    settings = get_runtime_settings()
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    state.iteration += 1
    use_edit_mode = state.repair_action == "image_edit" or bool(state.input_images)
    input_images = _resolve_generation_input_images(state)
    raw_reference_images = _resolve_generation_reference_images(state)
    reference_images, reference_grid_path = _prepare_generation_reference_images(
        raw_reference_images,
        output_dir=output_root,
        iteration=state.iteration,
    )
    image_bytes = generate_image_bytes(
        prompt=state.final_prompt or state.prompt,
        input_images=input_images,
        reference_images=reference_images,
        settings=settings,
        use_edit_mode=use_edit_mode,
    )
    image_extension = guess_image_extension(image_bytes)
    image_path = output_root / f"iteration_{state.iteration:02d}.image{image_extension}"
    image_path.write_bytes(image_bytes)
    state.last_image_path = str(image_path)
    if reference_grid_path:
        state.add_artifact("reference_grid", Path(reference_grid_path))
    state.add_artifact("image", image_path)
    state.stage_trace.append("generate")
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="generation.json",
        kind="generation",
        payload={
            "iteration": state.iteration,
            "image_path": state.last_image_path,
            "prompt": state.final_prompt or state.prompt,
            "backend": settings.image_gen.provider,
            "input_images": input_images,
            "reference_images": reference_images,
            "raw_reference_images": raw_reference_images,
            "reference_grid_path": reference_grid_path,
            "use_edit_mode": use_edit_mode,
        },
    )
    save_state(state, state_path)
    return state


def run_verify_stage(
    *,
    state_path: str | Path,
    verification_payload: dict | None = None,
) -> ScopeCaseState:
    state = load_state(state_path)
    settings = get_runtime_settings()
    image_path = Path(state.last_image_path) if str(state.last_image_path).strip() else None
    image_bytes = image_path.read_bytes() if image_path is not None and image_path.is_file() else b""
    unknowns_before_verify = list(state.unknowns)
    if verification_payload is not None:
        verification_outcome = parse_verification_payload(
            verification_payload,
            entities=state.entities,
            constraints=state.constraints,
            checklist=state.checklist,
        )
        verification_source = "agent_json"
        verification_backend = settings.controller.agent_family or "codex"
    elif settings.judge.configured:
        verification_outcome = judge_image(
            prompt=state.final_prompt or state.prompt,
            image_bytes=image_bytes,
            checklist=state.checklist,
            benchmark=state.benchmark,
            entities=state.entities,
            constraints=state.constraints,
            settings=settings,
        )
        verification_source = "judge_backend"
        verification_backend = settings.judge.provider
    else:
        raise RuntimeError("Verification requires either --verification-json or a configured SCOPE judge backend.")
    state.review_results = verification_outcome.review_results
    state.verification_unknowns = list(verification_outcome.new_unknowns)
    if state.verification_unknowns:
        state.unknowns, activated_unknown_ids = _merge_unknowns(state.unknowns, state.verification_unknowns)
        state.retrieval_resolutions = _drop_resolutions_for_unknowns(state.retrieval_resolutions, activated_unknown_ids)
        state.reasoning_resolutions = _drop_resolutions_for_unknowns(state.reasoning_resolutions, activated_unknown_ids)
    current_verification_summary = _build_verification_summary(
        state=state,
        verification_source=verification_source,
        verification_backend=verification_backend,
    )
    selected_as_best = _update_best_verification(state=state, candidate=current_verification_summary)
    state.stage_trace.append("verify")
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="verification.json",
        kind="verification",
        payload={
            "source": verification_source,
            "backend": verification_backend,
            "iteration": state.iteration,
            "image_path": state.last_image_path,
            "entities": [asdict(item) for item in state.entities],
            "constraints": [asdict(item) for item in state.constraints],
            "unknowns_before_verify": [asdict(item) for item in unknowns_before_verify],
            "unknowns_after_verify": [asdict(item) for item in state.unknowns],
            "review_results": [asdict(item) for item in state.review_results],
            "new_unknowns": [asdict(item) for item in state.verification_unknowns],
            "checklist": state.checklist,
            "benchmark": state.benchmark,
            "current_verification_summary": asdict(current_verification_summary),
            "selected_as_best": selected_as_best,
            "best_verification_summary": asdict(state.best_verification) if state.best_verification is not None else None,
            "best_image_path": state.best_image_path,
        },
    )
    save_state(state, state_path)
    return state


def run_repair_stage(
    *,
    state_path: str | Path,
    repair_payload: dict | None = None,
) -> ScopeCaseState:
    state = load_state(state_path)
    if not state.review_results:
        raise ValueError("repair requires existing review_results from a verify stage")
    if repair_payload is None:
        raise ValueError("repair requires --repair-json")
    repair_update = parse_repair_payload(
        repair_payload,
        available_review_ids={item.id for item in state.review_results},
    )
    decision = repair_update.decision
    source = "agent_json"
    state.repair_decision = decision
    state.repair_action = decision.repair_action
    if state.repair_action == "none":
        state.stage_trace.append("repair:skipped:review_passed")
    else:
        if repair_update is not None and repair_update.updated_final_prompt:
            state.final_prompt = repair_update.updated_final_prompt
        state.stage_trace.append(f"repair:{state.repair_action}")
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="repair_decision.json",
        kind="repair_decision",
        payload={
            "source": source,
            **asdict(decision),
            "updated_final_prompt": state.final_prompt if state.repair_action != "none" else "",
        },
    )
    _append_repair_history(state=state, state_path=state_path)
    save_state(state, state_path)
    return state


def write_final_prompt(*, state_path: str | Path, output_dir: str | Path) -> ScopeCaseState:
    state = load_state(state_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    final_prompt_path = output_root / "final_prompt.txt"
    final_prompt_path.write_text(state.final_prompt or state.prompt, encoding="utf-8")
    state.add_artifact("final_prompt", final_prompt_path)
    state.stage_trace.append("finalize")
    _write_stage_artifact(
        state=state,
        state_path=state_path,
        filename="finalization.json",
        kind="finalization",
        payload={
            "final_prompt_path": str(final_prompt_path),
            "final_prompt": state.final_prompt or state.prompt,
            "latest_image_path": state.last_image_path,
            "best_image_path": state.best_image_path or state.last_image_path,
            "best_iteration": state.best_iteration or state.iteration,
            "best_verification": asdict(state.best_verification) if state.best_verification is not None else None,
            "iteration": state.iteration,
        },
    )
    save_state(state, state_path)
    return state


def _build_verification_summary(
    *,
    state: ScopeCaseState,
    verification_source: str,
    verification_backend: str,
) -> ScopeBestVerification:
    pass_count = sum(1 for item in state.review_results if item.verdict == "pass")
    fail_count = sum(1 for item in state.review_results if item.verdict == "fail")
    uncertain_count = sum(1 for item in state.review_results if item.verdict == "uncertain")
    total = len(state.review_results)
    return ScopeBestVerification(
        iteration=state.iteration,
        image_path=str(state.last_image_path or ""),
        pass_count=pass_count,
        fail_count=fail_count,
        uncertain_count=uncertain_count,
        total=total,
        new_unknown_count=len(state.verification_unknowns),
        source=verification_source,
        backend=verification_backend,
    )


def _verification_rank(summary: ScopeBestVerification) -> tuple[int, int, int, int]:
    return (
        summary.fail_count,
        summary.uncertain_count,
        -summary.pass_count,
        summary.new_unknown_count,
    )


def _update_best_verification(*, state: ScopeCaseState, candidate: ScopeBestVerification) -> bool:
    current_best = state.best_verification
    if current_best is not None and _verification_rank(candidate) >= _verification_rank(current_best):
        if not state.best_image_path:
            state.best_image_path = current_best.image_path
        if not state.best_iteration:
            state.best_iteration = current_best.iteration
        return False
    state.best_verification = candidate
    state.best_image_path = candidate.image_path
    state.best_iteration = candidate.iteration
    if candidate.image_path:
        state.add_artifact("best_image", Path(candidate.image_path))
    return True


def load_state(state_path: str | Path) -> ScopeCaseState:
    path = Path(state_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return ScopeCaseState.from_dict(data)


def save_state(state: ScopeCaseState, state_path: str | Path) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_stage_artifact(
    *,
    state: ScopeCaseState,
    state_path: str | Path,
    filename: str,
    kind: str,
    payload: dict,
) -> Path:
    path = Path(state_path).parent / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    state.add_artifact(kind, path)
    return path


def _append_repair_history(*, state: ScopeCaseState, state_path: str | Path) -> Path:
    path = Path(state_path).parent / "repair_history.json"
    history = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except json.JSONDecodeError:
            history = []
    history.append(
        {
            "iteration": state.iteration,
            "repair_action": state.repair_action,
            "repair_decision": asdict(state.repair_decision) if state.repair_decision is not None else None,
            "review_results": [asdict(item) for item in state.review_results],
            "final_prompt_after_repair": state.final_prompt,
        }
    )
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    state.add_artifact("repair_history", path)
    return path


def _resolve_generation_input_images(state: ScopeCaseState) -> list[str]:
    if state.repair_action == "image_edit" and state.last_image_path:
        return [state.last_image_path, *state.input_images]
    return list(state.input_images)


def _resolve_generation_reference_images(state: ScopeCaseState) -> list[str]:
    reference_images = list(state.reference_images)
    benchmark_reference_images = state.benchmark.get("reference_images", [])
    if isinstance(benchmark_reference_images, list):
        for item in benchmark_reference_images:
            text = str(item).strip()
            if text and text not in reference_images:
                reference_images.append(text)
    return reference_images


def _prepare_generation_reference_images(
    reference_images: list[str],
    *,
    output_dir: str | Path,
    iteration: int,
) -> tuple[list[str], str]:
    return collapse_reference_images_for_generation(
        reference_images,
        output_dir=output_dir,
        grid_stem=f"iteration_{iteration:02d}.reference_grid",
    )


def _active_unknowns(unknowns: list, *, kind: str) -> list:
    return [item for item in unknowns if item.kind == kind and item.status == "open"]


def _mark_unknowns_resolved(unknowns: list, resolutions: list, *, stage: str) -> list:
    resolved_ids = {item.unknown_id for item in resolutions}
    updated = []
    for unknown in unknowns:
        if unknown.id in resolved_ids:
            updated.append(
                type(unknown)(
                    id=unknown.id,
                    kind=unknown.kind,
                    owner_id=unknown.owner_id,
                    owner_kind=unknown.owner_kind,
                    question=unknown.question,
                    owner_name=unknown.owner_name,
                    status="resolved",
                    source=unknown.source,
                    resolved_by=stage,
                )
            )
        else:
            updated.append(unknown)
    return updated


def _consume_resolved_unknowns(unknowns: list) -> list:
    consumed = []
    for unknown in unknowns:
        if unknown.status == "resolved":
            consumed.append(
                type(unknown)(
                    id=unknown.id,
                    kind=unknown.kind,
                    owner_id=unknown.owner_id,
                    owner_kind=unknown.owner_kind,
                    question=unknown.question,
                    owner_name=unknown.owner_name,
                    status="consumed",
                    source=unknown.source,
                    resolved_by=unknown.resolved_by,
                )
            )
        else:
            consumed.append(unknown)
    return consumed


def _collect_resolution_notes(state: ScopeCaseState) -> list[str]:
    return [item.note for item in state.retrieval_resolutions] + [item.note for item in state.reasoning_resolutions]


def _upsert_resolutions(existing: list, new_items: list) -> list:
    merged = {item.unknown_id: item for item in existing}
    for item in new_items:
        merged[item.unknown_id] = item
    return list(merged.values())


def _drop_resolutions_for_unknowns(resolutions: list, unknown_ids: set[str]) -> list:
    if not unknown_ids:
        return list(resolutions)
    return [item for item in resolutions if item.unknown_id not in unknown_ids]


def _merge_unknowns(existing: list, new_items: list) -> tuple[list, set[str]]:
    merged = {item.id: item for item in existing}
    existing_id_by_signature = {_unknown_signature(item): item.id for item in existing}
    activated_ids: set[str] = set()
    for item in new_items:
        target_id = item.id
        signature = _unknown_signature(item)
        if target_id not in merged and signature in existing_id_by_signature:
            target_id = existing_id_by_signature[signature]
        merged[target_id] = type(item)(
            id=target_id,
            kind=item.kind,
            owner_id=item.owner_id,
            owner_kind=item.owner_kind,
            question=item.question,
            owner_name=item.owner_name,
            status="open",
            source=item.source,
            resolved_by="",
        )
        activated_ids.add(target_id)
        existing_id_by_signature[signature] = target_id
    return list(merged.values()), activated_ids


def _unknown_signature(item: object) -> tuple[str, str, str]:
    return (str(getattr(item, "kind", "")), str(getattr(item, "owner_kind", "")), str(getattr(item, "owner_id", "")))


def _bind_benchmark_input(
    *,
    state: ScopeCaseState,
    benchmark_input: dict,
    benchmark_input_path: str | Path | None,
    state_path: str | Path,
) -> None:
    if not isinstance(benchmark_input, dict):
        raise ValueError("benchmark_input must be a JSON object")

    case_prompt = str(benchmark_input.get("prompt", "")).strip()
    if case_prompt and _normalize_prompt_for_compare(str(state.prompt)) != _normalize_prompt_for_compare(case_prompt):
        raise ValueError("benchmark input prompt does not match the decompose prompt")

    state.case_id = str(benchmark_input.get("case_id", "")).strip()
    state.input_images = [str(item).strip() for item in benchmark_input.get("input_images", []) if str(item).strip()]
    state.reference_images = []
    state.checklist = []
    state.benchmark = {}
    eval_packet_path = ""
    if benchmark_input_path:
        candidate = Path(benchmark_input_path).with_name("benchmark_eval.json")
        if candidate.is_file():
            eval_packet_path = str(candidate)
    binding_path = Path(state_path).parent / "benchmark_binding.json"
    binding_path.write_text(
        json.dumps(
            {
                "source": "benchmark_input_json",
                "case_id": state.case_id,
                "prompt": case_prompt,
                "state_inputs": {
                    "input_images": list(state.input_images),
                    "reference_images": list(state.reference_images),
                    "checklist": list(state.checklist),
                    "benchmark": dict(state.benchmark),
                },
                "eval_packet_json": eval_packet_path,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state.add_artifact("benchmark_binding", binding_path)


def _normalize_prompt_for_compare(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\ufeff", "").replace("\u200b", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()
