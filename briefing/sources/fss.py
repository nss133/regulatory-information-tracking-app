from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import (
    FSS_ADMGD_DETAIL_LIST,
    FSS_ADMGD_PREVIEW_LIST,
    FSS_PRESS_LIST,
)
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class FssConnector(SourceConnector):
    code = "fss"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        out: list[FetchedItem] = []
        out.extend(self._fetch_press())
        out.extend(self._fetch_admgd_preview())
        out.extend(self._fetch_admgd_detail())
        return out

    # ── 보도자료 ──────────────────────────────────────────

    def _fetch_press(self) -> list[FetchedItem]:
        html = self._http.get_text(FSS_PRESS_LIST, params={"menuNo": "200218"})
        soup = soupify(html)

        out: list[FetchedItem] = []
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

    # ── 행정지도 예고 ─────────────────────────────────────

    def _fetch_admgd_preview(self) -> list[FetchedItem]:
        html = self._http.get_text(FSS_ADMGD_PREVIEW_LIST, params={"menuNo": "200491"})
        soup = soupify(html)

        out: list[FetchedItem] = []
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="admnPrvntc/view.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue

            url = href if href.startswith("http") else f"https://www.fss.or.kr{href}"
            qs = parse_qs(urlparse(url).query)
            seqno = (qs.get("seqno") or [None])[0]
            key = f"admgd_preview_{seqno}" if seqno else url

            # 의견청취 기간에서 시작일 추출 (예: 2026-04-07 ~ 2026-04-27)
            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))

            out.append(
                FetchedItem(
                    source="fss",
                    category="admin_notice",
                    source_item_key=str(key),
                    title=f"[행정지도 예고] {title}",
                    url=url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
            if len(out) >= self._max_items:
                break
        return out

    # ── 행정지도 내역 ─────────────────────────────────────

    @staticmethod
    def _parse_yyyymmdd(s: str) -> Optional[datetime]:
        """YYYYMMDD 형식(공백 없는 8자리)을 파싱합니다."""
        m = re.search(r"(20\d{2})(\d{2})(\d{2})", s)
        if not m:
            return None
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def _fetch_admgd_detail(self) -> list[FetchedItem]:
        html = self._http.get_text(FSS_ADMGD_DETAIL_LIST, params={"menuNo": "200492"})
        soup = soupify(html)

        out: list[FetchedItem] = []
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="view.do"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue

            # ./view.do?guGuidanceMgrSeq=...  →  절대 URL로 변환
            if href.startswith("./"):
                href = f"https://www.fss.or.kr/fss/job/admnstgudc/{href[2:]}"
            url = href if href.startswith("http") else f"https://www.fss.or.kr{href}"

            qs = parse_qs(urlparse(url).query)
            seq = (qs.get("guGuidanceMgrSeq") or [None])[0]
            key = f"admgd_detail_{seq}" if seq else url

            # 시행일(YYYYMMDD) 파싱
            tds = tr.select("td")
            published_at = None
            for td in tds:
                parsed = self._parse_yyyymmdd(td.get_text(strip=True))
                if parsed:
                    published_at = parsed
                    break

            out.append(
                FetchedItem(
                    source="fss",
                    category="admin_notice",
                    source_item_key=str(key),
                    title=f"[행정지도 내역] {title}",
                    url=url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
            if len(out) >= self._max_items:
                break
        return out

