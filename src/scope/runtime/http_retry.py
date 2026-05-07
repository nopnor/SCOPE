from __future__ import annotations

import random
import time
from typing import Callable

import requests


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def request_with_retry(
    request_fn: Callable[[], requests.Response],
    *,
    max_attempts: int = 6,
    initial_delay: float = 2.0,
    max_delay: float = 30.0,
) -> requests.Response:
    last_response: requests.Response | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = request_fn()
        except requests.RequestException as exc:
            if attempt >= max_attempts or not should_retry_exception(exc):
                raise
            time.sleep(_compute_delay(attempt=attempt, initial_delay=initial_delay, max_delay=max_delay))
            continue

        last_response = response
        if response.ok or not should_retry_response(response) or attempt >= max_attempts:
            return response

        time.sleep(_compute_delay(attempt=attempt, initial_delay=initial_delay, max_delay=max_delay))

    if last_response is None:
        raise RuntimeError("Retry loop ended without a response.")
    return last_response


def should_retry_exception(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    return bool(response is not None and should_retry_response(response))


def should_retry_response(response: requests.Response) -> bool:
    if response.status_code in RETRYABLE_STATUS_CODES:
        return True

    body = (response.text or "").lower()
    retry_markers = (
        "resource_exhausted",
        '"code": 429',
        '"status": "resource_exhausted"',
        "rate limit",
        "please try again later",
        "temporarily unavailable",
        "server overloaded",
    )
    return any(marker in body for marker in retry_markers)


def _compute_delay(*, attempt: int, initial_delay: float, max_delay: float) -> float:
    base_delay = min(max_delay, initial_delay * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.0, min(1.0, base_delay / 4))
    return base_delay + jitter
