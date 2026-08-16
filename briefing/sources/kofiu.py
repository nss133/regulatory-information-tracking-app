from __future__ import annotations

import json
from urllib.parse import urlencode

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import KOFIU_BOARD_LIST_API, KOFIU_LAW_LIST_API
from briefing.types import Attachment, Category, FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


_BASE = "https://www.kofiu.go.kr"


class KofiuConnector(SourceConnector):
    """금융정보분석원 동적 게시판 JSON API 커넥터.

    KoFIU 목록 HTML은 빈 ``<ul>``만 제공하고 JavaScript가 JSON API를 호출해
    항목을 채운다. 따라서 정적 HTML의 ``table tr``을 읽으면 항상 0건이 된다.
    """

    code = "kofiu"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        items: list[FetchedItem] = []
        items.extend(self._fetch_press())
        items.extend(
            self._fetch_law(
                se_cd="001", category="admin_notice", key_prefix="announce:",
                viewer="/kor/law/announce_view.do",
            )
        )
        items.extend(
            self._fetch_law(
                se_cd="002", category="legislation", key_prefix="lawnote:",
                viewer="/kor/law/legislation_view.do",
            )
        )
        return items

    def _api_rows(self, api_url: str, *, se_cd: str) -> list[dict]:
        text = self._http.get_text(
            api_url,
            params={
                "selScope": "",
                "subSech": "",
                "size": str(self._max_items),
                "page": "1",
                "seCd": se_cd,
            },
        )
        payload = json.loads(text)
        rows = payload.get("result", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise RuntimeError("KoFIU JSON 응답의 result가 목록이 아닙니다.")
        return [row for row in rows if isinstance(row, dict)][: self._max_items]

    @staticmethod
    def _press_attachments(row: dict) -> list[Attachment]:
        out: list[Attachment] = []
        for f in row.get("fileList") or []:
            file_id = str(f.get("fileId") or "").strip()
            label = normalize_ws(str(f.get("atchmnflOrginlNm") or "첨부"))
            if not file_id:
                continue
            out.append(
                Attachment(
                    label=label,
                    url=f"{_BASE}/cmn/file/downloadBoard.do?"
                    + urlencode({"fileId": file_id}),
                )
            )
        return out

    @staticmethod
    def _law_attachments(row: dict) -> list[Attachment]:
        out: list[Attachment] = []
        for f in row.get("fileList") or []:
            params = {
                "seCd": str(f.get("lawordInfoTySeCd") or ""),
                "ordrNo": str(f.get("lawordInfoOrdrNo") or ""),
                "fileOrdrNo": str(f.get("atchmnflOrdrNo") or ""),
                "fileNm": str(f.get("atchmnflStreNm") or ""),
            }
            if not all(params.values()):
                continue
            label = normalize_ws(str(f.get("atchmnflOrginlNm") or "첨부"))
            out.append(
                Attachment(
                    label=label,
                    url=f"{_BASE}/cmn/file/downloadBoard.do?" + urlencode(params),
                )
            )
        return out

    def _fetch_press(self) -> list[FetchedItem]:
        out: list[FetchedItem] = []
        for row in self._api_rows(KOFIU_BOARD_LIST_API, se_cd="0001"):
            key = str(row.get("ntcnYardOrdrNo") or "").strip()
            title = normalize_ws(str(row.get("ntcnYardSjNm") or ""))
            if not key or not title:
                continue
            url = f"{_BASE}/kor/notification/report_view.do?" + urlencode(
                {"ntcnYardOrdrNo": key, "seCd": "0001"}
            )
            out.append(
                FetchedItem(
                    source="kofiu",
                    category="press",
                    source_item_key=f"press:{key}",
                    title=title,
                    url=url,
                    published_at=parse_yyyy_mm_dd(str(row.get("ntcnYardRgiDt") or "")),
                    attachments=self._press_attachments(row),
                    raw_text=None,
                )
            )
        return out

    def _fetch_law(
        self,
        *,
        se_cd: str,
        category: Category,
        key_prefix: str,
        viewer: str,
    ) -> list[FetchedItem]:
        out: list[FetchedItem] = []
        for row in self._api_rows(KOFIU_LAW_LIST_API, se_cd=se_cd):
            key = str(row.get("lawordInfoOrdrNo") or "").strip()
            title = normalize_ws(str(row.get("lawordInfoSjNm") or ""))
            if not key or not title:
                continue
            url = f"{_BASE}{viewer}?" + urlencode(
                {"lawordInfoOrdrNo": key, "seCd": se_cd}
            )
            raw_html = str(row.get("lawordInfoCn") or "")
            raw_text = normalize_ws(soupify(raw_html).get_text(" ", strip=True)) or None
            out.append(
                FetchedItem(
                    source="kofiu",
                    category=category,
                    source_item_key=f"{key_prefix}{key}",
                    title=title,
                    url=url,
                    published_at=parse_yyyy_mm_dd(str(row.get("lawordInfoRgiDt") or "")),
                    attachments=self._law_attachments(row),
                    raw_text=raw_text,
                )
            )
        return out
