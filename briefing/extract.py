from __future__ import annotations

import re

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.utils import normalize_ws


def extract_main_text(http: HttpClient, url: str) -> str:
    """
    사이트별 정교한 본문 파서까지 만들면 유지보수 비용이 커서,
    MVP에서는 공통(보수적) 추출만 제공합니다.
    """
    html = http.get_text(url)
    soup = soupify(html)

    # script/style 제거
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_ws(text)

