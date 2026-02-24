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
        return RankResult(importance="high", reason=f"키워드(상): {', '.join(high_hits[:6])}")

    med_hits = [k for k in cfg.medium_keywords if k.lower() in hay]
    if med_hits:
        return RankResult(importance="medium", reason=f"키워드(중): {', '.join(med_hits[:6])}")

    return RankResult(importance="low", reason="키워드 매칭 없음")

