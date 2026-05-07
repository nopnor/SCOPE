from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

import requests

from scope.runtime.http_retry import request_with_retry
from scope.runtime.settings import RuntimeSettings


GEMINI_IMAGE_ENDPOINT = "/images/gemini_flash/generations"
DEFAULT_ASPECT_RATIO = "1:1"
SUPPORTED_IMAGE_PROVIDERS = {"gemini_jdcloud", "jdcloud_gemini", "jdcloud"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_WEBP_SIGNATURE = b"RIFF"
_GIF_SIGNATURE = b"GIF8"


def generate_image_bytes(
    *,
    prompt: str,
    input_images: list[str] | None,
    reference_images: list[str] | None,
    settings: RuntimeSettings,
    use_edit_mode: bool = False,
) -> bytes:
    provider = (settings.image_gen.provider or "").lower()
    if provider not in SUPPORTED_IMAGE_PROVIDERS:
        raise RuntimeError(
            f"Unsupported SCOPE image provider '{settings.image_gen.provider}'. "
            f"Supported providers: {sorted(SUPPORTED_IMAGE_PROVIDERS)}"
        )
    return _generate_via_jdcloud_gemini(
        prompt=prompt,
        input_images=input_images or [],
        reference_images=reference_images or [],
        settings=settings,
        use_edit_mode=use_edit_mode,
    )


def guess_image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(_PNG_SIGNATURE):
        return ".png"
    if image_bytes.startswith(_JPEG_SIGNATURE):
        return ".jpg"
    if image_bytes.startswith(_WEBP_SIGNATURE) and image_bytes[8:12] == b"WEBP":
        return ".webp"
    if image_bytes.startswith(_GIF_SIGNATURE):
        return ".gif"
    return ".txt"


def _generate_via_jdcloud_gemini(
    *,
    prompt: str,
    input_images: list[str],
    reference_images: list[str],
    settings: RuntimeSettings,
    use_edit_mode: bool,
) -> bytes:
    image_settings = settings.image_gen
    model_name = image_settings.edit_model if use_edit_mode else image_settings.gen_model
    if not model_name:
        raise RuntimeError("SCOPE image backend is configured but no image model name was provided.")
    if not image_settings.api_key:
        raise RuntimeError("SCOPE image backend is configured but no API key was provided.")

    parts: list[dict[str, object]] = []
    for image_path in _existing_local_images([*input_images, *reference_images]):
        mime_type, encoded = _encode_image_file(image_path)
        parts.append({"inline_data": {"mimeType": mime_type, "data": encoded}})
    parts.append({"text": prompt})

    payload = {
        "model": model_name,
        "contents": [{"role": "USER", "parts": parts}],
        "generation_config": {
            "response_modalities": ["TEXT", "IMAGE"],
            "image_config": {"aspect_ratio": DEFAULT_ASPECT_RATIO},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {image_settings.api_key}",
    }
    response = request_with_retry(
        lambda: requests.post(
            f"{image_settings.base_url.rstrip('/')}{GEMINI_IMAGE_ENDPOINT}",
            headers=headers,
            json=payload,
            timeout=120,
        )
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"SCOPE image backend request failed with status {response.status_code}: {response.text[:500]}"
        ) from exc

    try:
        response_json = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("SCOPE image backend returned non-JSON response.") from exc

    decoded = _decode_gemini_response(response_json)
    if decoded is None:
        raise RuntimeError(
            "SCOPE image backend returned no inline image data. "
            f"Response excerpt: {json.dumps(response_json, ensure_ascii=False)[:1000]}"
        )
    return decoded


def _existing_local_images(paths: list[str]) -> list[str]:
    unique: list[str] = []
    for raw_path in paths:
        path = str(raw_path).strip()
        if not path or path in unique:
            continue
        if Path(path).exists():
            unique.append(path)
    return unique


def _encode_image_file(image_path: str) -> tuple[str, str]:
    mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    image_bytes = Path(image_path).read_bytes()
    return mime_type, base64.b64encode(image_bytes).decode("utf-8")


def _decode_gemini_response(response_json: dict) -> bytes | None:
    candidates = response_json.get("candidates", [])
    if not isinstance(candidates, list):
        return None

    for candidate in candidates:
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data") or {}
            if not isinstance(inline_data, dict):
                continue
            b64_data = str(inline_data.get("data", "")).strip()
            if b64_data:
                try:
                    return base64.b64decode(b64_data)
                except Exception as exc:
                    raise RuntimeError("SCOPE image backend returned invalid base64 image payload.") from exc
    return None
