from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import KNIA_PRESS_LIST
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class KniaConnector(SourceConnector):
    """손해보험협회(KNIA) 보도자료 수집."""

    code = "knia"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        try:
            html = self._http.get_text(KNIA_PRESS_LIST)
        except Exception:
            return []
        soup = soupify(html)

        out: list[FetchedItem] = []
        for a in soup.select('a[href*="/data/news/content?index="]')[: self._max_items]:
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue
            url = href if href.startswith("http") else f"https://www.knia.or.kr{href}"

            # URL의 index 파라미터를 key로 사용
            qs = parse_qs(urlparse(href).query)
            key = qs.get("index", [None])[0] or url

            # 부모 컨테이너 텍스트에서 날짜 추출
            published_at = None
            container = a.parent
            for _ in range(4):
                published_at = parse_yyyy_mm_dd(container.get_text(" ", strip=True))
                if published_at:
                    break
                container = container.parent

            out.append(
                FetchedItem(
                    source="knia",
                    category="press",
                    source_item_key=str(key),
                    title=title,
                    url=url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
        return out
