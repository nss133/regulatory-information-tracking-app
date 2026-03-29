from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urljoin

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import SCOURT_MAJOR_DECISIONS_LIST, SCOURT_PRESS_LIST
from briefing.types import Attachment, Category, FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


class ScourtConnector(SourceConnector):
    """
    대법원
    - 보도자료/언론보도 해명: portal/news/NewsListAction.work?gubun=4&type=0
    - 주요판결(대법원 주요판결): CourtLibrary judg/result + judgDetail
    """

    code = "scourt"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        items.extend(self._fetch_press())
        items.extend(self._fetch_major_decisions())
        return items

    def _fetch_press(self) -> list[FetchedItem]:
        html = self._http.get_text(SCOURT_PRESS_LIST)
        soup = soupify(html)

        out: list[FetchedItem] = []
        for tr in soup.select("table tr"):
            a = tr.select_one('a[href*="NewsViewAction.work"]')
            if not a:
                continue
            href = a.get("href") or ""
            title = normalize_ws(a.get_text(" ", strip=True))
            if not href or not title:
                continue

            url = href if href.startswith("http") else urljoin(SCOURT_PRESS_LIST, href)
            qs = parse_qs(urlparse(url).query)
            seqnum = (qs.get("seqnum") or [None])[0]
            key = seqnum or url

            published_at = parse_yyyy_mm_dd(tr.get_text(" ", strip=True))
            attachments, body_text = self._parse_press_detail(url)

            out.append(
                FetchedItem(
                    source="scourt",
                    category="press",
                    source_item_key=f"press:{key}",
                    title=title,
                    url=url,
                    published_at=published_at,
                    attachments=attachments,
                    raw_text=body_text,
                )
            )
            if len(out) >= self._max_items:
                break
        return out

    def _parse_press_detail(self, url: str) -> tuple[list[Attachment], str | None]:
        html = self._http.get_text(url)
        soup = soupify(html)

        # 첨부파일: /sjudge/ 경로의 PDF/HWP/HWPX 링크를 모두 수집
        attachments: list[Attachment] = []
        for a in soup.select('a[href*="/sjudge/"]'):
            href = a.get("href") or ""
            if not href:
                continue
            label = normalize_ws(a.get_text(" ", strip=True)) or "첨부"
            att_url = href if href.startswith("http") else urljoin(url, href)
            attachments.append(Attachment(label=label, url=att_url))

        # 본문은 페이지에서 주요 텍스트 블록을 통째로 가져온다.
        main = (
            soup.select_one("#content")
            or soup.select_one("#contents")
            or soup.select_one(".board_view")
            or soup.select_one(".news_view")
            or soup.body
        )
        text = normalize_ws(main.get_text(" ", strip=True)) if main else None
        return attachments, text

    def _fetch_major_decisions(self) -> list[FetchedItem]:
        """
        CourtLibrary의 판례판결 > 대법원주요판결 리스트를 이용해 주요판결을 수집합니다.
        """
        html = self._http.get_text(SCOURT_MAJOR_DECISIONS_LIST)
        soup = soupify(html)

        out: list[FetchedItem] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="judgDetail?seqNo="]'):
            href = a.get("href") or ""
            if not href:
                continue
            url = href if href.startswith("http") else urljoin(SCOURT_MAJOR_DECISIONS_LIST, href)
            if url in seen:
                continue
            seen.add(url)

            # 주변 텍스트에 '법원 대법원'이 포함된 경우만 필터링
            row = a.find_parent("tr") or a.parent
            context_text = normalize_ws(row.get_text(" ", strip=True)) if row else ""
            if "법원 대법원" not in context_text and "법원대법원" not in context_text:
                continue

            title = normalize_ws(a.get_text(" ", strip=True))
            if not title:
                continue

            published_at = parse_yyyy_mm_dd(context_text)
            attachments, body_text = self._parse_major_detail(url)

            qs = parse_qs(urlparse(url).query)
            seq_no = (qs.get("seqNo") or [None])[0]
            key = seq_no or url

            out.append(
                FetchedItem(
                    source="scourt",
                    category="case_law",
                    source_item_key=f"case:{key}",
                    title=title,
                    url=url,
                    published_at=published_at,
                    attachments=attachments,
                    raw_text=body_text,
                )
            )
            if len(out) >= self._max_items:
                break
        return out

    def _parse_major_detail(self, url: str) -> tuple[list[Attachment], str | None]:
        html = self._http.get_text(url)
        soup = soupify(html)

        # 판례 상세 페이지 상단의 긴 사건 개요 텍스트를 본문으로 사용
        main = soup.select_one("div#contents") or soup.select_one("div#content") or soup.body
        text = normalize_ws(main.get_text(" ", strip=True)) if main else None

        # 첨부파일 링크는 javascript:openJudgFilePopup(...) 형태이므로,
        # 메일에서는 판례 상세 페이지 자체를 '원문 보기' 링크로 제공한다.
        attachments = [
            Attachment(label="원문 보기", url=url),
        ]
        return attachments, text

