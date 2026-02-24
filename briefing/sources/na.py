from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.sources.urls import NA_MAIN
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd


# 보험회사 관점에서 핵심적으로 보는 법률(정식 명칭 위주, 약칭도 일부 포함)
TARGET_LAW_KEYWORDS: list[str] = [
    # 보험/금융 규제
    "보험업법",
    "자본시장과 금융투자업에 관한 법률",  # 자본시장법
    "금융소비자 보호에 관한 법률",
    "독점규제 및 공정거래에 관한 법률",  # 공정거래법
    "신용정보의 이용 및 보호에 관한 법률",
    "금융복합기업집단의 감독에 관한 법률",
    "금융회사의 지배구조에 관한 법률",
    # 기본 민상법
    "상법",
    "민법",
    "민사소송법",
    # 약관/노동/퇴직급여
    "약관의 규제에 관한 법률",
    "근로자퇴직급여 보장법",
    "근로기준법",
    "노동조합 및 노동관계조정법",
    # 개인정보
    "개인정보 보호법",
]


class NaAssemblyConnector(SourceConnector):
    """
    국회 의안정보시스템 메인 페이지에서 최근 접수/처리 의안 중
    '보험' 관련 키워드가 들어간 법률안을 간단하게 수집합니다.

    - 대상: 메인 화면의 '최근 접수의안', '본회의 부의안건', '최근 본회의 처리의안'
    - 필터: 제목에 '보험'이 포함된 의안만 대상으로 함(보험회사 관점)
    """

    code = "na"

    def __init__(self, http: HttpClient, *, max_items: int = 50):
        self._http = http
        self._max_items = max_items

    def fetch_latest(self) -> list[FetchedItem]:
        html = self._http.get_text(NA_MAIN)
        soup = soupify(html)

        items: list[FetchedItem] = []

        # 메인 페이지 전체에서 billDetailPage.do 링크를 찾되,
        # 제목 텍스트(링크 텍스트)에 '보험'이 들어간 것만 필터링합니다.
        for a in soup.select('a[href*="billDetailPage.do?billId="]'):
            title = normalize_ws(a.get_text(" ", strip=True))
            if not title:
                continue

            # 보험회사의 관점에서 특정 법률(및 전부/일부개정법률안)만 모니터링한다.
            if not any(k in title for k in TARGET_LAW_KEYWORDS):
                continue

            href = a.get("href") or ""
            url = href if href.startswith("http") else f"https://likms.assembly.go.kr{href}"
            qs = parse_qs(urlparse(url).query)
            bill_id = (qs.get("billId") or [None])[0]
            key = bill_id or url

            # 인접한 텍스트(번호/발의자/날짜/위원장/의결결과 등)에서 날짜/단계를 추출
            context_text = normalize_ws(a.parent.get_text(" ", strip=True))

            # '위원장' 표기가 있거나 '원안가결/대안반영폐기/수정안반영폐기' 등
            # 본회의 부의 이후 단계(상임위+법사위 보고 이후)로 추정되는 것만 포함.
            if ("위원장" not in context_text) and (
                "원안가결" not in context_text
                and "대안반영폐기" not in context_text
                and "수정안반영폐기" not in context_text
            ):
                # '최근 접수의안' 단계(의원 등 10인 ...)은 스킵
                continue

            published_at = parse_yyyy_mm_dd(context_text)

            items.append(
                FetchedItem(
                    source="na",
                    category="legislation",
                    source_item_key=str(key),
                    title=title,
                    url=url,
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )
            if len(items) >= self._max_items:
                break

        return items

