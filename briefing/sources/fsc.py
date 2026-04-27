from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlparse

import feedparser

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import FSC_LEGISLATION_LIST, FSC_PRESS_RSS
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


def _key_from_fsc_url(url: str) -> Optional[str]:
    # ex) https://www.fsc.go.kr/no010101/86298?...  -> 86298
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    for p in reversed(parts):
        if p.isdigit():
            return p
    # legislation detail has noticeId=...
    qs = parse_qs(urlparse(url).query)
    if "noticeId" in qs and qs["noticeId"]:
        return qs["noticeId"][0]
    return None


class FscConnector(SourceConnector):
    code = "fsc"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        items.extend(self._fetch_press_rss())
        items.extend(self._fetch_legislation_list())
        return items

    def _fetch_press_rss(self) -> list[FetchedItem]:
        text = self._http.get_text(FSC_PRESS_RSS)
        feed = feedparser.parse(text)
        out: list[FetchedItem] = []
        for e in feed.entries[: self._max_items]:
            url = str(getattr(e, "link", "")).strip()
            title = normalize_ws(str(getattr(e, "title", "")).strip())
            if not url or not title:
                continue
            key = _key_from_fsc_url(url) or url
            published_at: Optional[datetime] = None
            if getattr(e, "published_parsed", None):
                published_at = datetime(*e.published_parsed[:6])
            out.append(
                FetchedItem(
                    source="fsc",
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

    def _fetch_legislation_list(self) -> list[FetchedItem]:
        html = self._http.get_text(FSC_LEGISLATION_LIST)
        soup = soupify(html)

        out: list[FetchedItem] = []
        for a in soup.select('a[href*="/po040301/view?"]')[: self._max_items]:
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue
            if href.startswith("./"):
                href = href[1:]
            url = href if href.startswith("http") else f"https://www.fsc.go.kr{href}"
            key = _key_from_fsc_url(url) or url

            # 목록 페이지에 날짜가 항상 같이 보이지 않아서, 근처 텍스트에서 YYYY-MM-DD 패턴을 찾습니다.
            published_at = parse_yyyy_mm_dd(a.parent.get_text(" ", strip=True))

            out.append(
                FetchedItem(
                    source="fsc",
                    category="legislation",
                    source_item_key=str(key),
                    title=title,
                    url=url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
        return out


