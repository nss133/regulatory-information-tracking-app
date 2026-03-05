from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from briefing.config import RankingConfig
from briefing.types import Importance


@dataclass(frozen=True)
class RankResult:
    importance: Importance
    reason: str


def rank_item(*, title: str, raw_text: Optional[str], cfg: RankingConfig) -> RankResult:
    hay = f"{title}\n{raw_text or ''}".lower()

    high_hits = [k for k in cfg.high_keywords if k.lower() in hay]
    if high_hits:
        # 특수 규칙 1: '고시'는 HIGH 이지만, '신고시'처럼 바로 앞에 '신'이 붙은 경우는 MEDIUM
        if any(k.lower() == "고시" for k in high_hits) and "신고시" in hay:
            return RankResult(importance="medium", reason="키워드(중): 신고시")

        # 특수 규칙 2: 워크샵 관련 이슈는 HIGH 후보더라도 MEDIUM으로 완화
        if "워크샵" in hay or "워크숍" in hay:
            return RankResult(importance="medium", reason="키워드(중): 워크샵")

        # 특수 규칙 3: 입법예고/규정변경예고 + 상호금융업/대부업/여신전문금융업 → LOW
        if any(k in hay for k in ["입법예고", "규정변경예고", "변경예고"]) and any(
            kw in hay for kw in ["상호금융업", "대부업", "여신전문금융업"]
        ):
            return RankResult(importance="low", reason="키워드(하): 입법/규정예고(상호금융 등)")

        # 특수 규칙 4: '보험'이 '고용보험'/'산재보험' 문맥이면 LOW
        if any(k.lower() == "보험" for k in high_hits) and ("고용보험" in hay or "산재보험" in hay):
            return RankResult(importance="low", reason="키워드(하): 고용/산재보험")

        # 특수 규칙 5: '제재' + '하도급' → MEDIUM
        if any(k.lower() == "제재" for k in high_hits) and ("하도급" in hay or "하도급법" in hay):
            return RankResult(importance="medium", reason="키워드(중): 제재(하도급)")

        return RankResult(importance="high", reason=f"키워드(상): {', '.join(high_hits[:6])}")

    med_hits = [k for k in cfg.medium_keywords if k.lower() in hay]
    if med_hits:
        return RankResult(importance="medium", reason=f"키워드(중): {', '.join(med_hits[:6])}")

    return RankResult(importance="low", reason="키워드 매칭 없음")

