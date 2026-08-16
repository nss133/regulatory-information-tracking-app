from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import NHRCK_PRESS_LIST
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class NhrckConnector(SourceConnector):
    code = "nhrck"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        html = self._http.get_text(
            NHRCK_PRESS_LIST,
            params={"boardManagementNo": "24", "menuLevel": "3", "menuNo": "91"},
        )
        soup = soupify(html)

        out: list[FetchedItem] = []
        for a in soup.select('a[href*="/base/board/read"]'):
            href = a.get("href") or ""
            if "boardManagementNo=24" not in href:
                continue
            # 신규 표시용 <em class="new_mark">N</em>은 제목이 아니다.
            new_mark = a.select_one(".new_mark")
            if new_mark:
                new_mark.decompose()
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue
            url = href if href.startswith("http") else f"https://www.humanrights.go.kr{href}"
            qs = parse_qs(urlparse(url).query)
            board_no = (qs.get("boardNo") or [None])[0]
            key = board_no or url
            # 날짜는 제목 td의 형제가 아니라 같은 tr의 별도 td.date에 있다.
            row = a.find_parent("tr")
            date_cell = row.select_one("td.date") if row else None
            published_at = parse_yyyy_mm_dd(
                date_cell.get_text(" ", strip=True) if date_cell else ""
            )
            out.append(
                FetchedItem(
                    source="nhrck",
                    category="press",
                    source_item_key=str(key),
                    title=title,
                    url=url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
            if len(out) >= self._max_items:
                break
        return out
