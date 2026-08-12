"""Google Apps Script Web App의 임시 응답 리디렉션을 견디는 HTTP 클라이언트."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests


TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class AppsScriptRequestError(RuntimeError):
    """사용자 화면에 임시 Google 응답 URL을 노출하지 않는 저장 오류."""


def _response_host(response: requests.Response | None) -> str:
    if response is None:
        return ""
    return (urlsplit(str(response.url or "")).hostname or "").lower()


def _is_retryable_http(response: requests.Response) -> bool:
    if response.status_code in TRANSIENT_STATUS_CODES:
        return True
    # Apps Script ContentService가 생성한 임시 응답 URL은 간헐적으로 404가
    # 발생할 수 있다. 이때 만료된 URL을 재사용하지 않고 /exec부터 재요청한다.
    return (
        response.status_code == 404
        and _response_host(response).endswith("script.googleusercontent.com")
    )


def _short_error(response: requests.Response | None, exc: Exception) -> str:
    if response is not None:
        host = _response_host(response) or "Google Apps Script"
        return f"HTTP {response.status_code} ({host})"
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection error"
    return type(exc).__name__


def post_json_with_retry(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int = 90,
    max_attempts: int = 5,
    sleep_func: Callable[[float], None] = time.sleep,
    post_func: Callable[..., requests.Response] = requests.post,
) -> dict[str, Any]:
    """원본 Apps Script `/exec`에 재요청하며 JSON 응답을 반환한다.

    요청값 업데이트는 서버에서 동일값을 다시 적용하지 않으므로, 응답 전달
    단계에서 실패한 요청을 재시도해도 변경 이력이 중복 생성되지 않는다.
    """
    if not url:
        raise AppsScriptRequestError("Apps Script URL이 설정되지 않았습니다.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    last_reason = "unknown error"
    for attempt in range(1, max_attempts + 1):
        response: requests.Response | None = None
        try:
            response = post_func(
                url,
                json=payload,
                params={"_client_attempt": attempt, "_ts": time.time_ns()},
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache, no-store",
                    "Pragma": "no-cache",
                },
                timeout=timeout,
            )
            if _is_retryable_http(response):
                last_reason = f"HTTP {response.status_code} ({_response_host(response)})"
                if attempt < max_attempts:
                    sleep_func(2 ** (attempt - 1))
                    continue

            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                last_reason = "invalid JSON response"
                if attempt < max_attempts:
                    sleep_func(2 ** (attempt - 1))
                    continue
                raise AppsScriptRequestError(
                    f"Apps Script 응답을 해석하지 못했습니다. ({max_attempts}회 시도)"
                ) from exc

            if not isinstance(body, dict):
                raise AppsScriptRequestError("Apps Script 응답 형식이 올바르지 않습니다.")
            return body
        except AppsScriptRequestError:
            raise
        except requests.RequestException as exc:
            last_reason = _short_error(response, exc)
            retryable = response is None or _is_retryable_http(response)
            if retryable and attempt < max_attempts:
                sleep_func(2 ** (attempt - 1))
                continue
            if not retryable:
                raise AppsScriptRequestError(
                    f"Apps Script 저장 요청이 거부되었습니다: {last_reason}"
                ) from exc
            break

    raise AppsScriptRequestError(
        "Google의 Apps Script 응답 전달이 일시적으로 실패했습니다. "
        f"{max_attempts}회 재시도 후 중단했습니다: {last_reason}"
    )
