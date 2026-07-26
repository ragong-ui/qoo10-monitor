from types import SimpleNamespace

from compliance_briefing.llm import _rule_based_summary, generate_summaries


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "status": "ok",
            "rows": [{
                "title_ko": "소비자청이 조치 명령",
                "title_ja": "消費者庁が措置命令",
                "summary_ko": "소비자청이 조치 명령",
                "summary_ja": "消費者庁が措置命令",
            }],
        }


def test_rule_based_fallback_does_not_mislabel_japanese_as_korean():
    result = _rule_based_summary({
        "title": "消費者庁が措置命令",
        "body": "",
    })

    assert result["title_ko"] == ""
    assert result["title_ja"] == "消費者庁が措置命令"
    assert result["summary_ko"] == ""
    assert result["summary_ja"] == "消費者庁が措置命令"


def test_apps_script_provider_returns_bilingual_fields(monkeypatch):
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Response()

    monkeypatch.setattr("compliance_briefing.llm.requests.post", post)
    cfg = SimpleNamespace(
        llm_provider="apps_script",
        compliance_apps_script_url="https://example.com/exec",
        compliance_apps_script_token="secret",
        llm_timeout=30,
        anthropic_api_key="",
        openai_api_key="",
    )

    result = generate_summaries(
        cfg,
        [{"title": "消費者庁が措置命令", "body": ""}],
    )

    assert result[0]["title_ko"] == "소비자청이 조치 명령"
    assert result[0]["title_ja"] == "消費者庁が措置命令"
    assert calls[0][1]["action"] == "translate_batch"
    assert calls[0][1]["api_token"] == "secret"
