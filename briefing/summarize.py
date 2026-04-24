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

    prompt = f"""당신은 미래에셋생명보험 법무·컴플라이언스팀 내부 브리핑 작성자입니다.
아래 자료를 읽고 JSON으로만 응답하세요.

기관: {source_name_ko}
제목: {title}
본문:
{body[:4000]}

출력 JSON:
{{
  "importance": "low|medium|high",
  "summary_ko": "1~2문장. '~함', '~됨', '~예정임' 어미. 핵심 규제 내용 또는 제재 사실만 기술. 배경 설명 생략.",
  "action_required": "법무·컴플라이언스팀이 취해야 할 구체적 조치. 없으면 null.",
  "reason_ko": "보험사 관점에서 중요한 이유 1문장."
}}

중요: summary_ko는 구어체 금지. action_required는 구체적 행동 동사로 시작할 것."""

    if llm.provider == "anthropic":
        try:
            import anthropic  # type: ignore
        except Exception:
            return LlmResult(importance=None, summary=None, reason="anthropic 패키지 미설치")
        try:
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=llm.model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            # JSON 블록 추출 (```json ... ``` 감싸인 경우 대응)
            if "```" in text:
                text = text.split("```")[-2].removeprefix("json").strip()
            data = json.loads(text)
        except Exception as e:
            return LlmResult(importance=None, summary=None, reason=f"LLM 호출 실패: {e}")
    elif llm.provider == "openai":
        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return LlmResult(importance=None, summary=None, reason="openai 패키지 미설치")
        try:
            client = OpenAI(api_key=api_key)
            resp = client.responses.create(model=llm.model, input=prompt)
            text = (getattr(resp, "output_text", None) or "").strip()
            if "```" in text:
                text = text.split("```")[-2].removeprefix("json").strip()
            data = json.loads(text)
        except Exception as e:
            return LlmResult(importance=None, summary=None, reason=f"LLM 호출 실패: {e}")
    else:
        return LlmResult(importance=None, summary=None, reason=f"미지원 provider: {llm.provider}")

    importance = data.get("importance")
    action = data.get("action_required")
    summary_ko = data.get("summary_ko") or ""
    if action:
        summary = f"{summary_ko} [조치] {action}"
    else:
        summary = summary_ko
    reason = data.get("reason_ko")
    if importance not in ("low", "medium", "high"):
        importance = None
    return LlmResult(importance=importance, summary=summary or None, reason=reason)

