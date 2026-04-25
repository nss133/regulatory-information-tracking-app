from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from briefing.config import LlmConfig
from briefing.types import Importance


@dataclass(frozen=True)
class LlmResult:
    importance: Optional[Importance]
    summary: Optional[str]
    reason: Optional[str]


def _sanitize_json(text: str) -> str:
    """JSON 문자열 값 안의 리터럴 제어문자(줄바꿈 등)를 공백으로 치환."""
    return re.sub(
        r'"(?:[^"\\]|\\.)*"',
        lambda m: m.group().replace("\n", " ").replace("\r", " ").replace("\t", " "),
        text,
    )


def _extract_json(text: str) -> dict:
    """LLM 응답에서 JSON 객체를 추출·파싱. 실패 시 예외 발생."""
    if "```" in text:
        text = text.split("```")[-2].removeprefix("json").strip()
    # 중괄호 블록만 추출 (앞뒤 여분 텍스트 제거)
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        text = m.group()
    return json.loads(_sanitize_json(text))


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
  "summary_ko": "해당 자료의 요지를 세 줄로 요약. 구체적인 사건·행위사실과 핵심 주제 서술에 집중. 조치 권고는 넣지 말 것. 반드시 '~음', '~함', '~됨', '~임', '~예정임' 등 간결한 명사형·단축형 어미로 끝낼 것. '~습니다', '~이다', '~했다' 등 서술형 어미 사용 금지.",
  "action_required": null,
  "reason_ko": "보험사 관점에서 중요한 이유 1문장."
}}

중요: summary_ko는 반드시 한국어로만 작성. 구어체·서술형 어미 금지. 조치·권고 문구 금지. 사실관계와 핵심 내용만. 각 줄은 '~음/~함/~됨/~임'으로 종결."""

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
            data = _extract_json(text)
        except Exception as e:
            return LlmResult(importance=None, summary=None, reason=f"LLM 호출 실패: {e}")
    elif llm.provider in ("openai", "groq"):
        try:
            from openai import OpenAI  # type: ignore
        except Exception:
            return LlmResult(importance=None, summary=None, reason="openai 패키지 미설치")
        try:
            kwargs: dict = {"api_key": api_key}
            if llm.base_url:
                kwargs["base_url"] = llm.base_url
            client = OpenAI(**kwargs)
            resp = client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
            )
            text = (resp.choices[0].message.content or "").strip()
            data = _extract_json(text)
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

