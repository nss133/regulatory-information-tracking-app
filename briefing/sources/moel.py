from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urljoin

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import MOEL_LAWMAKING_LIST, MOEL_PRESS_LIST
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class MoelConnector(SourceConnector):
    code = "moel"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        items.extend(self._fetch_press())
        items.extend(self._fetch_lawmaking())
        return items

    def _fetch_press(self) -> list[FetchedItem]:
        html = self._http.get_text(MOEL_PRESS_LIST)
        soup = soupify(html)
        out: list[FetchedItem] = []
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="enewsView.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue
            # 상대경로(`enewsView.do?...` 등)를 안전하게 절대 URL로 변환
            url = href if href.startswith("http") else urljoin(MOEL_PRESS_LIST, href)
            qs = parse_qs(urlparse(url).query)
            news_seq = (qs.get("news_seq") or [None])[0]
            key = news_seq or url
            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))
            out.append(
                FetchedItem(
                    source="moel",
                    category="press",
                    source_item_key=f"press:{key}",
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

    def _fetch_lawmaking(self) -> list[FetchedItem]:
        html = self._http.get_text(MOEL_LAWMAKING_LIST)
        soup = soupify(html)
        out: list[FetchedItem] = []
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="/info/lawinfo/lawmaking/view.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue
            # 상대경로(`/info/lawinfo/lawmaking/view.do` 등)를 안전하게 절대 URL로 변환
            url = href if href.startswith("http") else urljoin(MOEL_LAWMAKING_LIST, href)
            qs = parse_qs(urlparse(url).query)
            bbs_seq = (qs.get("bbs_seq") or [None])[0]
            key = bbs_seq or url
            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))
            out.append(
                FetchedItem(
                    source="moel",
                    category="legislation",
                    source_item_key=f"lawmaking:{key}",
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

