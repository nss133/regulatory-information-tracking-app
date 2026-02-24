from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from briefing.config import LlmConfig
from briefing.types import Importance


@dataclass(frozen=True)
class LlmResult:
    importance: Optional[Importance]
    summary: Optional[str]
    reason: Optional[str]


def _importance_order(x: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(x, 999)


def should_call_llm(*, llm: LlmConfig, current_importance: Importance) -> bool:
    if not llm.enabled:
        return False
    threshold = llm.only_when_importance_at_least
    return _importance_order(current_importance) >= _importance_order(threshold)


def summarize_with_llm(
    *,
    llm: LlmConfig,
    title: str,
    body: str,
    source_name_ko: str,
) -> LlmResult:
    """
    외부 LLM 전송 정책이 미정이므로:
    - 기본은 llm.enabled=false
    - enabled=true일 때만 동작
    - openai 패키지가 없거나 키가 없으면 안전하게 폴백
    """
    api_key = llm.api_key()
    if not api_key:
        return LlmResult(importance=None, summary=None, reason="LLM API 키 없음")

    if llm.provider != "openai":
        return LlmResult(importance=None, summary=None, reason=f"미지원 provider: {llm.provider}")

    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return LlmResult(importance=None, summary=None, reason="openai 패키지 미설치")

    client = OpenAI(api_key=api_key)

    prompt = f"""
너는 보험회사 규제/리스크 모니터링 담당자다.
아래 자료(제목/본문)를 읽고 JSON으로만 답해라.

기관: {source_name_ko}
제목: {title}
본문:
{body[:6000]}

출력 JSON 스키마:
{{
  "importance": "low|medium|high",
  "summary_ko": "한글 2~4문장 요약",
  "reason_ko": "왜 중요한지 한글 1~2문장"
}}
"""
    try:
        resp = client.responses.create(
            model=llm.model,
            input=prompt,
        )
        text = getattr(resp, "output_text", None) or ""
        text = text.strip()
        data = json.loads(text)
        importance = data.get("importance")
        summary = data.get("summary_ko")
        reason = data.get("reason_ko")
        if importance not in ("low", "medium", "high"):
            importance = None
        return LlmResult(importance=importance, summary=summary, reason=reason)
    except Exception as e:
        return LlmResult(importance=None, summary=None, reason=f"LLM 호출 실패: {e}")

