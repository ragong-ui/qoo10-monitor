"""
LLM abstraction layer — bilingual summary generation.
Supports: anthropic, openai, disabled (rule-based fallback).
"""

from __future__ import annotations
import logging
import re
from typing import TYPE_CHECKING

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

    # Use raw title as-is for Japanese; truncate body for summary
    title_ja = title_raw[:80] if title_raw else "（タイトルなし）"
    summary_ja = body_raw[:280] + ("…" if len(body_raw) > 280 else "") if body_raw else title_ja

    # For Korean, attempt very basic transliteration hint or copy
    title_ko = title_raw[:80] if title_raw else "(제목 없음)"
    summary_ko = summary_ja  # same text; translator will handle it if installed

    return {
        "title_ko": title_ko,
        "title_ja": title_ja,
        "summary_ko": summary_ko,
        "summary_ja": summary_ja,
    }


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

    if provider not in ("disabled", ""):
        log.warning("[llm] Provider '%s' configured but no API key found — using rule-based", provider)

    return [_rule_based_summary(i) for i in items]
