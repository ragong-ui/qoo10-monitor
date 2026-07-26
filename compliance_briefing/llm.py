"""
LLM abstraction layer — bilingual summary generation.
Supports: anthropic, openai, disabled (rule-based fallback).
"""

from __future__ import annotations
import logging
import re
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from .config import ComplianceConfig

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a bilingual (Korean/Japanese) compliance analyst specializing in Japanese marketplace regulations and product safety.
Your task: given a raw compliance alert item, produce a short bilingual summary.

Rules:
- title_ko, title_ja: concise title (max 80 chars each)
- summary_ko, summary_ja: 2-3 sentence factual summary (max 300 chars each)
- Do not add commentary or analysis beyond the facts
- Do not fabricate information not present in the input
- Output ONLY valid JSON with keys: title_ko, title_ja, summary_ko, summary_ja
"""


def _rule_based_summary(item: dict) -> dict:
    """Fallback: extract title/summary from raw_item without LLM."""
    title_raw = (item.get("title") or "").strip()
    body_raw = (item.get("body") or "").strip()
    summary_raw = body_raw[:280] + ("…" if len(body_raw) > 280 else "")
    if not summary_raw:
        summary_raw = title_raw

    hangul_count = len(re.findall(r"[\uac00-\ud7a3]", title_raw + summary_raw))
    japanese_count = len(re.findall(
        r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]",
        title_raw + summary_raw,
    ))

    if hangul_count > japanese_count:
        title_ko = title_raw[:80] if title_raw else "(제목 없음)"
        summary_ko = summary_raw[:300] if summary_raw else title_ko
        title_ja = ""
        summary_ja = ""
    elif japanese_count:
        title_ko = ""
        summary_ko = ""
        title_ja = title_raw[:80] if title_raw else "（タイトルなし）"
        summary_ja = summary_raw[:300] if summary_raw else title_ja
    else:
        # Keep language-neutral/English source text visible in both columns.
        title_ko = title_raw[:80] if title_raw else "(제목 없음)"
        title_ja = title_raw[:80] if title_raw else "（タイトルなし）"
        summary_ko = summary_raw[:300] if summary_raw else title_ko
        summary_ja = summary_raw[:300] if summary_raw else title_ja

    return {
        "title_ko": title_ko,
        "title_ja": title_ja,
        "summary_ko": summary_ko,
        "summary_ja": summary_ja,
    }


def _summarize_apps_script(cfg: "ComplianceConfig", items: list[dict]) -> list[dict]:
    """Use Apps Script LanguageApp for keyless Korean/Japanese translation."""
    if not cfg.compliance_apps_script_url or not cfg.compliance_apps_script_token:
        log.warning("[llm/apps_script] URL or API token missing — using rule-based")
        return [_rule_based_summary(item) for item in items]

    results: list[dict] = []
    batch_size = 15
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        payload_rows = [
            {
                "title": (item.get("title") or "").strip(),
                "body": (item.get("body") or "").strip()[:1000],
            }
            for item in batch
        ]
        try:
            response = requests.post(
                cfg.compliance_apps_script_url,
                json={
                    "action": "translate_batch",
                    "api_token": cfg.compliance_apps_script_token,
                    "rows": payload_rows,
                },
                timeout=max(cfg.llm_timeout, 120),
            )
            response.raise_for_status()
            payload = response.json()
            translated = payload.get("rows", [])
            if payload.get("status") != "ok" or len(translated) != len(batch):
                raise ValueError("invalid translation response")
            results.extend(translated)
        except Exception as exc:
            log.warning(
                "[llm/apps_script] Batch %d-%d failed: %s — using rule-based",
                start,
                start + len(batch) - 1,
                exc,
            )
            results.extend(_rule_based_summary(item) for item in batch)
    return results


def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM output."""
    import json
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)


def _summarize_anthropic(cfg: "ComplianceConfig", items: list[dict]) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed — falling back to rule-based")
        return [_rule_based_summary(i) for i in items]

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    results = []
    for item in items:
        user_msg = (
            f"Category: {item.get('category', '')}\n"
            f"Country: {item.get('country', '')}\n"
            f"Source: {item.get('source_id', '')}\n"
            f"Title: {item.get('title', '')}\n"
            f"Body: {(item.get('body') or '')[:1000]}\n"
        )
        try:
            msg = client.messages.create(
                model=cfg.llm_model_anthropic,
                max_tokens=512,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
                timeout=cfg.llm_timeout,
            )
            text = msg.content[0].text if msg.content else ""
            results.append(_extract_json(text))
        except Exception as e:
            log.warning("[llm/anthropic] Failed for item %s: %s", item.get("external_id"), e)
            results.append(_rule_based_summary(item))
    return results


def _summarize_openai(cfg: "ComplianceConfig", items: list[dict]) -> list[dict]:
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai package not installed — falling back to rule-based")
        return [_rule_based_summary(i) for i in items]

    client = OpenAI(api_key=cfg.openai_api_key)
    results = []
    for item in items:
        user_msg = (
            f"Category: {item.get('category', '')}\n"
            f"Country: {item.get('country', '')}\n"
            f"Source: {item.get('source_id', '')}\n"
            f"Title: {item.get('title', '')}\n"
            f"Body: {(item.get('body') or '')[:1000]}\n"
        )
        try:
            resp = client.chat.completions.create(
                model=cfg.llm_model_openai,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=512,
                timeout=cfg.llm_timeout,
                response_format={"type": "json_object"},
            )
            text = resp.choices[0].message.content or ""
            results.append(_extract_json(text))
        except Exception as e:
            log.warning("[llm/openai] Failed for item %s: %s", item.get("external_id"), e)
            results.append(_rule_based_summary(item))
    return results


def generate_summaries(cfg: "ComplianceConfig", items: list[dict]) -> list[dict]:
    """
    Generate bilingual summaries for a list of raw_items.
    Returns list of dicts with keys: title_ko, title_ja, summary_ko, summary_ja.
    """
    if not items:
        return []

    provider = cfg.llm_provider.lower()

    if provider == "anthropic" and cfg.anthropic_api_key:
        return _summarize_anthropic(cfg, items)

    if provider == "openai" and cfg.openai_api_key:
        return _summarize_openai(cfg, items)

    if provider == "apps_script":
        return _summarize_apps_script(cfg, items)

    if provider not in ("disabled", ""):
        log.warning("[llm] Provider '%s' configured but no API key found — using rule-based", provider)

    return [_rule_based_summary(i) for i in items]
