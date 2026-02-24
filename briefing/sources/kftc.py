from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import KFTC_GOSI_LIST, KFTC_LAW_NOTICE_LIST, KFTC_PRESS_LIST
from briefing.types import Attachment, FetchedItem, Category
from briefing.utils import normalize_ws, parse_yyyy_mm_dd

KFTC_BASE = "https://www.ftc.go.kr/www/"


def _abs(url: str, base: str = KFTC_BASE) -> str:
    if url.startswith("http"):
        return url
    resolved = urljoin(base, url)
    return resolved


class KftcConnector(SourceConnector):
    code = "kftc"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        items.extend(
            self._fetch_list(
                KFTC_PRESS_LIST,
                params={"bordCd": "3", "key": "12", "searchCtgry": "01,02"},
                category="press",
                key_prefix="press:",
            )
        )
        items.extend(
            self._fetch_list(
                KFTC_LAW_NOTICE_LIST,
                params={"bordCd": "105", "key": "193"},
                category="legislation",
                key_prefix="lawnote:",
            )
        )
        items.extend(
            self._fetch_list(
                KFTC_GOSI_LIST,
                params={"bordCd": "6", "key": "21"},
                category="other",
                key_prefix="gosi:",
            )
        )
        return items

    def _fetch_list(
        self, url: str, *, params: dict[str, str], category: Category, key_prefix: str
    ) -> list[FetchedItem]:
        html = self._http.get_text(url, params=params)
        soup = soupify(html)

        out: list[FetchedItem] = []
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="selectBbsNttView.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue
            item_url = _abs(href)
            qs = parse_qs(urlparse(item_url).query)
            ntt_sn = (qs.get("nttSn") or [None])[0]
            key = ntt_sn or item_url

            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))

            atts: list[Attachment] = []
            for att_a in tr.select('a[href*="downloadBbsFile"]'):
                att_href = att_a.get("href") or ""
                if not att_href:
                    continue
                atts.append(
                    Attachment(
                        label=normalize_ws(att_a.get_text(" ", strip=True) or "첨부"),
                        url=_abs(att_href),
                    )
                )
            for att_a in tr.select('a[href*="previewBbsAtchmnfl.do"]'):
                att_href = att_a.get("href") or ""
                if not att_href:
                    continue
                atts.append(
                    Attachment(
                        label=normalize_ws(att_a.get_text(" ", strip=True) or "문서뷰어"),
                        url=_abs(att_href),
                    )
                )

            out.append(
                FetchedItem(
                    source="kftc",
                    category=category,
                    source_item_key=f"{key_prefix}{key}",
                    title=title,
                    url=item_url,
                    published_at=published_at,
                    attachments=atts,
                    raw_text=None,
                )
            )
            if len(out) >= self._max_items:
                break
        return out

