from __future__ import annotations

import requests

from apps_script_client import AppsScriptRequestError, get_json_with_retry, post_json_with_retry


class FakeResponse:
    def __init__(self, status_code=200, body=None, url="https://script.google.com/exec"):
        self.status_code = status_code
        self._body = {"status": "ok"} if body is None else body
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} error",
                response=self,
            )

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_returns_successful_json_without_retry():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(body={"status": "ok", "changed": 1})

    result = post_json_with_retry(
        "https://script.google.com/macros/s/test/exec",
        {"action": "batch_update"},
        post_func=post,
        sleep_func=lambda _seconds: None,
    )

    assert result == {"status": "ok", "changed": 1}
    assert len(calls) == 1
    assert calls[0][1]["headers"]["Cache-Control"] == "no-cache, no-store"


def test_retries_original_exec_after_googleusercontent_404():
    responses = [
        FakeResponse(
            status_code=404,
            url="https://script.googleusercontent.com/macros/echo?user_content_key=expired-secret",
        ),
        FakeResponse(body={"status": "ok", "changed": 0}),
    ]
    calls = []
    sleeps = []

    def post(url, **kwargs):
        calls.append(url)
        return responses.pop(0)

    result = post_json_with_retry(
        "https://script.google.com/macros/s/test/exec",
        {"action": "batch_update"},
        post_func=post,
        sleep_func=sleeps.append,
    )

    assert result["status"] == "ok"
    assert calls == [
        "https://script.google.com/macros/s/test/exec",
        "https://script.google.com/macros/s/test/exec",
    ]
    assert sleeps == [1]


def test_retries_timeout_and_server_error_with_backoff():
    outcomes = [
        requests.Timeout("slow"),
        FakeResponse(status_code=503, url="https://script.google.com/macros/s/test/exec"),
        FakeResponse(body={"status": "ok"}),
    ]
    sleeps = []

    def post(_url, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = post_json_with_retry(
        "https://script.google.com/macros/s/test/exec",
        {"action": "batch_update"},
        post_func=post,
        sleep_func=sleeps.append,
    )

    assert result["status"] == "ok"
    assert sleeps == [1, 2]


def test_does_not_retry_non_transient_original_404():
    calls = []

    def post(_url, **_kwargs):
        calls.append(1)
        return FakeResponse(
            status_code=404,
            url="https://script.google.com/macros/s/missing/exec",
        )

    try:
        post_json_with_retry(
            "https://script.google.com/macros/s/missing/exec",
            {"action": "batch_update"},
            post_func=post,
            sleep_func=lambda _seconds: None,
        )
    except AppsScriptRequestError as exc:
        assert "script.google.com" in str(exc)
        assert "user_content_key" not in str(exc)
    else:
        raise AssertionError("AppsScriptRequestError was not raised")
    assert len(calls) == 1


def test_final_error_does_not_expose_temporary_response_url():
    def post(_url, **_kwargs):
        return FakeResponse(
            status_code=404,
            url="https://script.googleusercontent.com/macros/echo?user_content_key=sensitive-token",
        )

    try:
        post_json_with_retry(
            "https://script.google.com/macros/s/test/exec",
            {"action": "batch_update"},
            max_attempts=2,
            post_func=post,
            sleep_func=lambda _seconds: None,
        )
    except AppsScriptRequestError as exc:
        message = str(exc)
        assert "2회 재시도" in message
        assert "sensitive-token" not in message
        assert "user_content_key" not in message
    else:
        raise AssertionError("AppsScriptRequestError was not raised")

def test_get_retries_original_exec_and_preserves_sheet_parameter():
    responses = [
        FakeResponse(
            status_code=404,
            url="https://script.googleusercontent.com/macros/echo?user_content_key=expired",
        ),
        FakeResponse(body={"data": [{"Status": "New"}]}),
    ]
    calls = []
    sleeps = []

    def get(url, **kwargs):
        calls.append((url, kwargs["params"]))
        return responses.pop(0)

    result = get_json_with_retry(
        "https://script.google.com/macros/s/test/exec",
        {"sheet": "Google モニタリング"},
        get_func=get,
        sleep_func=sleeps.append,
    )

    assert result == {"data": [{"Status": "New"}]}
    assert len(calls) == 2
    assert all(call[0] == "https://script.google.com/macros/s/test/exec" for call in calls)
    assert all(call[1]["sheet"] == "Google モニタリング" for call in calls)
    assert calls[0][1]["_client_attempt"] == 1
    assert calls[1][1]["_client_attempt"] == 2
    assert sleeps == [1]


def test_get_final_error_hides_temporary_url():
    def get(_url, **_kwargs):
        return FakeResponse(
            status_code=404,
            url="https://script.googleusercontent.com/macros/echo?user_content_key=hidden-token",
        )

    try:
        get_json_with_retry(
            "https://script.google.com/macros/s/test/exec",
            {"sheet": "Google モニタリング"},
            max_attempts=2,
            get_func=get,
            sleep_func=lambda _seconds: None,
        )
    except AppsScriptRequestError as exc:
        message = str(exc)
        assert "2회 재시도" in message
        assert "hidden-token" not in message
        assert "user_content_key" not in message
    else:
        raise AssertionError("AppsScriptRequestError was not raised")
