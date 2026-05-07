from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from scope.runtime.image_backend import guess_image_extension
from scope.contracts.state import ScopeRetrievalEvidence, ScopeUnknown
from scope.runtime.http_retry import request_with_retry
from scope.runtime.settings import RuntimeSettings, get_runtime_settings


SERPER_SEARCH_ENDPOINT = "https://google.serper.dev/search"
SERPER_IMAGES_ENDPOINT = "https://google.serper.dev/images"
BLOCKED_HOST_TOKENS = ("youtube.com", "youtu.be")
BLOCKED_SUFFIXES = (".pdf",)
LOW_QUALITY_HOST_TOKENS = (
    "medium.com",
    "substack.com",
    "blog.",
    "news.",
    "linkedin.com",
    "instagram.com",
    "facebook.com",
    "x.com",
    "twitter.com",
    "reddit.com",
    "zhihu.com",
    "bilibili.com",
    "weibo.com",
)
PREFERRED_HOST_TOKENS = (
    "wikipedia.org",
    "wikimedia.org",
    ".gov",
    ".edu",
    ".org",
)


def search_external_unknowns(
    unknowns: list[ScopeUnknown],
    *,
    settings: RuntimeSettings | None = None,
    download_dir: str | Path | None = None,
    max_image_downloads_per_unknown: int = 1,
) -> list[dict[str, Any]]:
    settings = settings or get_runtime_settings()
    external_unknowns = [item for item in unknowns if item.kind == "external_reference" and item.status == "open"]
    records: list[dict[str, Any]] = []
    for unknown in external_unknowns:
        query = build_query_for_unknown(unknown)
        evidence = search_query(query, settings=settings)
        evidence = _download_image_evidence(
            evidence,
            unknown_id=unknown.id,
            download_dir=download_dir,
            max_downloads=max_image_downloads_per_unknown,
            settings=settings,
        )
        records.append(
            {
                "unknown_id": unknown.id,
                "owner_id": unknown.owner_id,
                "owner_kind": unknown.owner_kind,
                "owner_name": unknown.owner_name,
                "question": unknown.question,
                "query": query,
                "evidence": [asdict(item) for item in evidence],
                "search_provider": settings.search.provider,
                "search_configured": settings.search.configured,
            }
        )
    return records


def search_query_plan(
    query_plans: list[dict[str, Any]],
    *,
    unknowns: list[ScopeUnknown],
    settings: RuntimeSettings | None = None,
    download_dir: str | Path | None = None,
    max_image_downloads_per_unknown: int = 1,
) -> list[dict[str, Any]]:
    settings = settings or get_runtime_settings()
    unknown_by_id = {
        item.id: item
        for item in unknowns
        if item.kind == "external_reference" and item.status == "open"
    }
    records: list[dict[str, Any]] = []
    for plan in query_plans:
        unknown_id = str(plan.get("unknown_id", "")).strip()
        unknown = unknown_by_id.get(unknown_id)
        if unknown is None:
            raise ValueError(f"query plan references non-open external_reference unknown: {unknown_id}")
        text_queries = _normalize_query_list(plan.get("text_queries"))
        image_queries = _normalize_query_list(plan.get("image_queries"))
        evidence: list[ScopeRetrievalEvidence] = []
        for query in text_queries:
            evidence.extend(_fetch_serper_web_results(query=query, settings=settings))
        for query in image_queries:
            evidence.extend(_fetch_serper_image_results(query=query, settings=settings))
        evidence = _download_image_evidence(
            evidence,
            unknown_id=unknown.id,
            download_dir=download_dir,
            max_downloads=max(max_image_downloads_per_unknown, len(image_queries)),
            settings=settings,
        )
        records.append(
            {
                "unknown_id": unknown.id,
                "owner_id": unknown.owner_id,
                "owner_kind": unknown.owner_kind,
                "owner_name": unknown.owner_name,
                "question": unknown.question,
                "text_queries": text_queries,
                "image_queries": image_queries,
                "evidence": [asdict(item) for item in evidence],
                "search_provider": settings.search.provider,
                "search_configured": settings.search.configured,
            }
        )
    return records


def build_query_for_unknown(unknown: ScopeUnknown) -> str:
    owner = unknown.owner_name or unknown.owner_id or "visual reference"
    if unknown.owner_kind == "object":
        return owner
    return f"{owner} {unknown.question}".strip()


def _normalize_query_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        text = " ".join(str(item or "").split()).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def search_query(query: str, *, settings: RuntimeSettings | None = None) -> list[ScopeRetrievalEvidence]:
    settings = settings or get_runtime_settings()
    if not query.strip() or not settings.search.configured:
        return []
    if settings.search.provider != "serper":
        raise RuntimeError(f"Unsupported SCOPE search provider '{settings.search.provider}'.")

    web_results = _fetch_serper_web_results(query=query, settings=settings)
    image_results = _fetch_serper_image_results(query=query, settings=settings)
    return web_results + image_results


def _fetch_serper_web_results(query: str, *, settings: RuntimeSettings) -> list[ScopeRetrievalEvidence]:
    response = request_with_retry(
        lambda: requests.post(
            SERPER_SEARCH_ENDPOINT,
            headers={
                "X-API-KEY": settings.search.serper_api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": max(settings.search.num_results, 1)},
            timeout=settings.search.request_timeout,
        )
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"SCOPE web search failed with status {response.status_code}: {response.text[:500]}") from exc

    payload = response.json()
    items = payload.get("organic", [])
    if not isinstance(items, list):
        return []

    candidates: list[ScopeRetrievalEvidence] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link", "")).strip()
        if not url or _should_skip_url(url):
            continue
        candidates.append(
            ScopeRetrievalEvidence(
                kind="web",
                title=str(item.get("title", "")).strip() or url,
                url=url,
                snippet=str(item.get("snippet", "")).strip(),
                query=query,
            )
        )
    return sorted(candidates, key=lambda item: _score_evidence(item, query), reverse=True)[: max(settings.search.num_results, 1)]


def _fetch_serper_image_results(query: str, *, settings: RuntimeSettings) -> list[ScopeRetrievalEvidence]:
    per_query_limit = 1
    response = request_with_retry(
        lambda: requests.post(
            SERPER_IMAGES_ENDPOINT,
            headers={
                "X-API-KEY": settings.search.serper_api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": per_query_limit},
            timeout=settings.search.request_timeout,
        )
    )
    try:
        response.raise_for_status()
    except Exception:
        return []

    payload = response.json()
    items = payload.get("images", [])
    if not isinstance(items, list):
        return []

    evidence: list[ScopeRetrievalEvidence] = []
    for item in items[:per_query_limit]:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("imageUrl", "")).strip()
        if not image_url:
            continue
        evidence.append(
            ScopeRetrievalEvidence(
                kind="image",
                title=str(item.get("title", "")).strip() or image_url,
                url=image_url,
                snippet=str(item.get("source", "")).strip(),
                query=query,
            )
        )
    return evidence[:per_query_limit]


def _download_image_evidence(
    evidence: list[ScopeRetrievalEvidence],
    *,
    unknown_id: str,
    download_dir: str | Path | None,
    max_downloads: int,
    settings: RuntimeSettings,
) -> list[ScopeRetrievalEvidence]:
    if not download_dir or max_downloads <= 0:
        return evidence
    output_dir = Path(download_dir) / _safe_filename(unknown_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    updated: list[ScopeRetrievalEvidence] = []
    for item in evidence:
        if item.kind != "image" or downloaded >= max_downloads:
            updated.append(item)
            continue
        local_path = _download_reference_image(
            url=item.url,
            output_dir=output_dir,
            stem=f"{downloaded + 1:02d}",
            timeout=settings.search.request_timeout,
        )
        if local_path:
            downloaded += 1
            updated.append(
                ScopeRetrievalEvidence(
                    kind=item.kind,
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    query=item.query,
                    local_path=local_path,
                )
            )
        else:
            updated.append(item)
    return updated


def _download_reference_image(*, url: str, output_dir: Path, stem: str, timeout: int) -> str:
    try:
        response = request_with_retry(lambda: requests.get(url, timeout=timeout, headers={"User-Agent": "SCOPE/0.1"}))
        response.raise_for_status()
    except Exception:
        return ""
    content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    image_bytes = response.content
    extension = guess_image_extension(image_bytes)
    if extension == ".txt":
        extension = _extension_from_content_type(content_type)
    if extension == ".txt":
        return ""
    path = output_dir / f"{stem}{extension}"
    path.write_bytes(image_bytes)
    return str(path)


def _extension_from_content_type(content_type: str) -> str:
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/gif":
        return ".gif"
    return ".txt"


def _safe_filename(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return text or "unknown"


def _should_skip_url(url: str) -> bool:
    lowered = url.casefold()
    if any(token in lowered for token in BLOCKED_HOST_TOKENS):
        return True
    if any(lowered.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        return True
    return False


def _score_evidence(item: ScopeRetrievalEvidence, query: str) -> int:
    score = 0
    domain = urlparse(item.url).netloc.casefold()
    title = item.title.casefold()
    snippet = item.snippet.casefold()
    if any(token in domain for token in PREFERRED_HOST_TOKENS):
        score += 4
    if any(token in domain for token in LOW_QUALITY_HOST_TOKENS):
        score -= 4
    if "official" in title or "official" in snippet:
        score += 2
    for token in _query_tokens(query):
        if token in domain:
            score += 2
        if token in title:
            score += 1
        if token in snippet:
            score += 1
    return score


def _query_tokens(query: str) -> list[str]:
    stopwords = {"what", "does", "look", "like", "the", "and", "from", "with", "official", "visual", "reference"}
    return [token for token in "".join(ch if ch.isalnum() else " " for ch in query.casefold()).split() if len(token) >= 3 and token not in stopwords]
