from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import FSS_PRESS_LIST
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class FssConnector(SourceConnector):
    code = "fss"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        html = self._http.get_text(FSS_PRESS_LIST, params={"menuNo": "200218"})
        soup = soupify(html)

        out: list[FetchedItem] = []
        # 목록 테이블의 각 행에서 view 링크를 찾습니다.
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="B0000188/view.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue

            url = href if href.startswith("http") else f"https://www.fss.or.kr{href}"
            qs = parse_qs(urlparse(url).query)
            ntt_id = (qs.get("nttId") or [None])[0]
            key = ntt_id or url

            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))

            out.append(
                FetchedItem(
                    source="fss",
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

