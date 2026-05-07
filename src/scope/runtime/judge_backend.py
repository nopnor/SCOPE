from __future__ import annotations

import base64
import json
import uuid

import requests

from scope.contracts.state import (
    ScopeConstraint,
    ScopeEntity,
    ScopeReviewResult,
    ScopeVerificationOutcome,
    parse_verification_payload,
)
from scope.runtime.http_retry import request_with_retry
from scope.runtime.settings import RuntimeSettings


JD_CLOUD_RESPONSES_ENDPOINT = "/responses"
SUPPORTED_JUDGE_PROVIDERS = {"gemini_jdcloud", "jdcloud_gemini", "jdcloud"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_WEBP_SIGNATURE = b"RIFF"
_GIF_SIGNATURE = b"GIF8"


def judge_image(
    *,
    prompt: str,
    image_bytes: bytes,
    checklist: list[str] | None,
    benchmark: dict | None,
    entities: list[ScopeEntity],
    constraints: list[ScopeConstraint],
    settings: RuntimeSettings,
) -> ScopeVerificationOutcome:
    provider = (settings.judge.provider or "").lower()
    if provider not in SUPPORTED_JUDGE_PROVIDERS:
        raise RuntimeError(
            f"Unsupported SCOPE judge provider '{settings.judge.provider}'. "
            f"Supported providers: {sorted(SUPPORTED_JUDGE_PROVIDERS)}"
        )
    if not settings.judge.configured:
        raise RuntimeError("SCOPE judge backend is configured but required API settings are incomplete.")
    if not image_bytes:
        return ScopeVerificationOutcome(
            review_results=[
                ScopeReviewResult(
                    id="missing_image",
                    verdict="uncertain",
                    reason="Judge backend received no image bytes to evaluate.",
                    item_kind="constraint",
                    evidence="The image artifact was empty or missing.",
                )
            ]
        )
    if not looks_like_supported_image(image_bytes):
        return ScopeVerificationOutcome(
            review_results=[
                ScopeReviewResult(
                    id="invalid_image_payload",
                    verdict="uncertain",
                    reason="Judge backend skipped because the current artifact is not a supported raster image.",
                    item_kind="constraint",
                    evidence="The upstream generate stage did not produce PNG/JPEG/WEBP/GIF bytes.",
                )
            ]
        )

    payload = _build_jdcloud_payload(
        prompt=prompt,
        image_bytes=image_bytes,
        checklist=checklist or [],
        benchmark=benchmark or {},
        entities=entities,
        constraints=constraints,
        model_name=settings.judge.model_name,
    )
    response = request_with_retry(
        lambda: requests.post(
            f"{settings.judge.base_url.rstrip('/')}{JD_CLOUD_RESPONSES_ENDPOINT}",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.judge.api_key}",
                "Trace-Id": str(uuid.uuid4()),
            },
            json=payload,
            timeout=settings.judge.request_timeout,
        )
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"SCOPE judge backend request failed with status {response.status_code}: {response.text[:500]}"
        ) from exc

    response_json = response.json()
    content = _extract_jdcloud_text(response_json)
    return _parse_verification_outcome(content, checklist or [], entities=entities, constraints=constraints)


def _build_jdcloud_payload(
    *,
    prompt: str,
    image_bytes: bytes,
    checklist: list[str],
    benchmark: dict,
    entities: list[ScopeEntity],
    constraints: list[ScopeConstraint],
    model_name: str,
) -> dict:
    review_target = checklist or ["Overall alignment with the prompt."]
    judge_instruction = {
        "task": "Evaluate whether the image satisfies each checklist item.",
        "prompt": prompt,
        "checklist": review_target,
        "benchmark_context": {
            "dataset": benchmark.get("dataset"),
            "type": benchmark.get("type"),
            "generation_type": benchmark.get("generation_type"),
            "world_knowledge_text": benchmark.get("world_knowledge_text", ""),
        },
        "owner_space": {
            "prompt": [{"id": "p0", "label": "whole prompt"}],
            "objects": [{"id": item.id, "name": item.name} for item in entities],
            "constraints": [{"id": item.id, "text": item.text, "type": item.type} for item in constraints],
        },
        "output_schema": {
            "review_results": [
                {
                    "id": "string",
                    "verdict": "pass|fail|uncertain",
                    "reason": "short explanation",
                    "item_kind": "object|constraint",
                    "target_id": "entity id or constraint id being reviewed",
                    "owner_id": "related object id when applicable",
                    "failure_family": "subject_repair|text_repair|relation_repair|count_repair|attribute_repair|layout_repair|style_repair, required for fail or uncertain repairable issues",
                    "blocked_by": "upstream failed review id when this item depends on an earlier failure",
                    "confidence": "number between 0 and 1",
                    "evidence": "concise visual evidence",
                }
            ],
            "new_unknowns": [
                {
                    "id": "string",
                    "kind": "external_reference|semantic_reasoning",
                    "owner_id": "one of the ids listed in owner_space",
                    "owner_kind": "object|constraint|prompt",
                    "owner_name": "optional object display name",
                    "question": "short missing-information question",
                }
            ],
        },
        "requirements": [
            "Return valid JSON only.",
            "Produce one review_results item per checklist entry in order.",
            "If a checklist entry cannot be verified confidently from the image, use verdict 'uncertain'.",
            "Keep reasons concise and grounded in visible evidence.",
            "Only return new_unknowns when the next generation attempt requires missing external knowledge or unresolved reasoning.",
            "When returning new_unknowns, choose the narrowest matching owner from owner_space.",
            "Use owner_kind 'prompt' and owner_id 'p0' only when the gap genuinely applies to the whole prompt.",
            "For object-level gaps, use the exact object id from owner_space.objects.",
            "For constraint-level gaps, use the exact constraint id from owner_space.constraints.",
        ],
    }
    return {
        "model": model_name,
        "stream": False,
        "contents": {
            "role": "user",
            "parts": [
                {
                    "inlineData": {
                        "mimeType": _guess_mime_type(image_bytes),
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    }
                },
                {"text": json.dumps(judge_instruction, ensure_ascii=False, indent=2)},
            ],
        },
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
        },
    }


def _guess_mime_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(_PNG_SIGNATURE):
        return "image/png"
    if image_bytes.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if image_bytes.startswith(_WEBP_SIGNATURE) and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes.startswith(_GIF_SIGNATURE):
        return "image/gif"
    return "image/jpeg"


def looks_like_supported_image(image_bytes: bytes) -> bool:
    return _guess_mime_type(image_bytes) != "image/jpeg" or image_bytes.startswith(_JPEG_SIGNATURE)


def _extract_jdcloud_text(response_json: dict) -> str:
    candidates = response_json.get("candidates", [])
    if isinstance(candidates, list):
        texts: list[str] = []
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part["text"]))
        if texts:
            return "\n".join(texts).strip()

    output = response_json.get("output", [])
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            for part in item.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    texts.append(str(part["text"]))
        if texts:
            return "\n".join(texts).strip()

    if isinstance(response_json.get("text"), str):
        return str(response_json["text"]).strip()
    raise RuntimeError(f"Unexpected judge response format: {json.dumps(response_json, ensure_ascii=False)[:1000]}")


def _parse_verification_outcome(
    raw_text: str,
    checklist: list[str],
    *,
    entities: list[ScopeEntity],
    constraints: list[ScopeConstraint],
) -> ScopeVerificationOutcome:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Judge backend returned invalid JSON: {raw_text[:1000]}") from exc
    if isinstance(payload, list):
        payload = {
            "review_results": payload,
            "new_unknowns": [],
        }
    try:
        return parse_verification_payload(
            payload,
            entities=entities,
            constraints=constraints,
            checklist=checklist,
        )
    except ValueError as exc:
        raise RuntimeError(f"Judge backend returned invalid verification payload: {raw_text[:1000]}") from exc
