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

        return RankResult(importance="high", reason=f"키워드(상): {', '.join(high_hits[:6])}")

    med_hits = [k for k in cfg.medium_keywords if k.lower() in hay]
    if med_hits:
        return RankResult(importance="medium", reason=f"키워드(중): {', '.join(med_hits[:6])}")

    return RankResult(importance="low", reason="키워드 매칭 없음")

