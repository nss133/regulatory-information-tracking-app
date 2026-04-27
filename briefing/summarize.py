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
아래 자료를 읽고 JSON으로만 응답하세요. 반드시 순수 JSON만 출력하고 다른 텍스트는 일절 넣지 마세요.

기관: {source_name_ko}
제목: {title}
본문:
{body[:2500]}

출력 형식 (이 구조 그대로):
{{"importance":"low|medium|high","summary_ko":"줄1 / 줄2 / 줄3 / 줄4","reason_ko":"이유 1문장"}}

[summary_ko 작성 규칙 — 반드시 준수]
- 순수 한국어만 사용. 영어·중국어·일본어 등 외국어 단어 절대 금지.
- 사건·행위사실과 핵심 주제만 기술. 조치·권고·평가 문구 금지.
- 반드시 3~4줄로 작성할 것 (/ 구분). 2줄 이하 또는 5줄 이상은 허용하지 않음.
- 각 줄(/ 구분)은 반드시 다음 어미 중 하나로 끝낼 것: 음 / 함 / 됨 / 임 / 예정임 / 받음 / 내림
- 서술형 어미(~다, ~습니다, ~한다, ~됩니다, ~였다) 절대 금지.
- 올바른 예: "3개사가 가격 담합 행위로 공정위에 제재를 받음 / 과징금 총 120억 원 부과됨 / 향후 재발 방지 명령 포함됨 / 피심인 이의신청 기간 30일 부여됨"
- 잘못된 예: "공정위가 제재를 내렸다 / 과징금이 부과되었습니다"

[importance 기준]
- high: 보험사 영업·준법·리스크에 즉각적 영향
- medium: 간접적 영향 또는 모니터링 필요
- low: 참고 수준"""

    if llm.provider == "anthropic":
        try:
            import anthropic  # type: ignore
        except Exception:
            return LlmResult(importance=None, summary=None, reason="anthropic 패키지 미설치")
        try:
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=llm.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            data = _extract_json(text)
        except Exception as e:
            return LlmResult(importance=None, summary=None, reason=f"LLM 호출 실패: {e}")
    elif llm.provider in ("openai", "groq", "deepseek"):
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
                max_tokens=1024,
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

