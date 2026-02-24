from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import KOFIU_LAW_NOTICE_LIST, KOFIU_PRESS_LIST
from briefing.types import Category, FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class KofiuConnector(SourceConnector):
    """
    금융정보분석원(KoFIU)
    - 보도자료: notification/report.do
    - 입법/규정변경 예고: law/legislation_list.do
    """

    code = "kofiu"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        items.extend(
            self._fetch_list(
                KOFIU_PRESS_LIST,
                category="press",
                key_prefix="press:",
            )
        )
        items.extend(
            self._fetch_list(
                KOFIU_LAW_NOTICE_LIST,
                category="legislation",
                key_prefix="lawnote:",
            )
        )
        return items

    def _fetch_list(self, url: str, *, category: Category, key_prefix: str) -> list[FetchedItem]:
        html = self._http.get_text(url)
        soup = soupify(html)

        out: list[FetchedItem] = []
        # KoFIU 게시판은 일반적으로 table 행마다 view 링크와 날짜를 포함하는 구조
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="view.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue

            # 절대 URL 구성
            item_url = href if href.startswith("http") else f"https://www.kofiu.go.kr{href}"
            qs = parse_qs(urlparse(item_url).query)
            # KoFIU는 일반적으로 'seq', 'nttNo' 등의 키를 사용하지만, 없으면 전체 URL을 key로 사용
            key = (qs.get("seq") or qs.get("nttNo") or [None])[0] or item_url

            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))

            out.append(
                FetchedItem(
                    source="kofiu",
                    category=category,
                    source_item_key=f"{key_prefix}{key}",
                    title=title,
                    url=item_url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
            if len(out) >= self._max_items:
                break
        return out

