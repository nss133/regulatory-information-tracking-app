from __future__ import annotations

import re
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.sources.registry import SourceConnector
from briefing.types import FetchedItem
from briefing.utils import normalize_ws, parse_yyyy_mm_dd

_SUMMARY_URL = "https://likms.assembly.go.kr/bill/bi/popup/billSummary.do"
_RE_WHITESPACE = re.compile(r"\s+")
_RE_BILL_ERROR = re.compile(r"의안\s*정보를\s*찾을\s*수\s*없습니다")


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

def _is_target_law(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    return any(re.match(re.escape(re.sub(r"\s+", "", law)) + r"(?:일부|전부|개정|폐지|제정|법률안|\(|$)", compact)
               for law in TARGET_LAW_KEYWORDS)


BASE = "https://likms.assembly.go.kr"

# 공통 Ajax 헤더
_AJAX_HEADERS_SCH = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE}/bill/bi/bill/sch/detailedSchPage.do",
}
_AJAX_HEADERS_STATE = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE}/bill/bi/bill/state/finishBillPage.do",
}

# 의안명 정제 패턴
_RE_PREFIX = re.compile(r"^(계류의안|처리의안)")
_RE_TEMP_NO = re.compile(r"DD\d+")
_RE_TRAILING_DIGITS = re.compile(r"\d{7,}.*$")


_RE_NEW_WINDOW = re.compile(r"\s*\(새창 열림\)\s*$")
_RE_COMMITTEE_SUFFIX = re.compile(r"\s*\([^)]*위원장\)\s*$")


def _clean_bill_title(raw: str) -> str:
    """의안명에서 시스템 접두어·임시번호·후행 숫자열을 제거하고 정제된 제목을 반환."""
    s = normalize_ws(raw)
    s = _RE_PREFIX.sub("", s)
    s = _RE_TEMP_NO.sub("", s)
    s = _RE_TRAILING_DIGITS.sub("", s)
    return normalize_ws(s)


def _extract_from_anchor(a_tag) -> tuple[str | None, str]:
    """
    <a data-bill-id="PRC_..." title="상법 일부개정법률안 (새창 열림)"> 구조에서
    (bill_id, clean_title) 추출.
    title 속성이 있으면 우선 사용, 없으면 텍스트 정제.
    """
    bill_id: str | None = a_tag.get("data-bill-id") or a_tag.get("data-bill_id")

    # title 속성 우선 (가장 깔끔한 의안명)
    raw_title = a_tag.get("title", "")
    if raw_title:
        title = _RE_NEW_WINDOW.sub("", raw_title)
        title = _RE_COMMITTEE_SUFFIX.sub("", title).strip()
    else:
        title = _clean_bill_title(a_tag.get_text(" ", strip=True))

    return bill_id, title


def _bill_url(bill_id: str) -> str:
    return f"{BASE}/bill/bi/billDetailPage.do?billId={bill_id}"


class NaAssemblyConnector(SourceConnector):
    """
    국회 의안정보시스템 공식 검색의 발의·제출 및 3개 후속 마일스톤을 추적합니다.

    1. 위원회안·대안   (_fetch_plenary_propose)  — 공식 위원회안(대안) 목록; 법사위 회부로 단정하지 않음
    2. 본회의 부의   (_fetch_before_propose)   — 법사위 통과 → 본회의 상정 대기(통과 임박)
    3. 본회의 가결   (_fetch_plenary_pass)      — 본회의 의결 완료
    """

    code = "na"

    def __init__(self, http: HttpClient, max_items: int = 50):
        self._http = http
        self.diagnostics: list[dict] = []
        self._max_pages = 20

    def fetch_latest(self) -> list[FetchedItem]:
        self.diagnostics = []
        items: list[FetchedItem] = []
        items.extend(self._fetch_recent_proposals())
        items.extend(self._fetch_plenary_propose())
        items.extend(self._fetch_before_propose())
        items.extend(self._fetch_plenary_pass())
        # 동일 의안이 여러 목록에 있으면 후속 단계 1건으로 제시한다.
        items = list({item.url: item for item in items}.values())
        # Enrich each item with 제안이유/주요내용 text
        missing_summaries = 0
        for i, item in enumerate(items):
            bill_id = item.url.split("billId=")[-1]
            summary = self._fetch_summary_text(bill_id)
            missing_summaries += int(not summary)
            items[i] = replace(item, raw_text="\n".join(x for x in (item.raw_text, summary) if x) or None)
        if missing_summaries:
            logging.getLogger(__name__).warning("국회 제안이유 본문 미확보 %s/%s건", missing_summaries, len(items))
        return items

    def _fetch_summary_text(self, bill_id: str) -> str | None:
        """billSummary 팝업에서 제안이유 및 주요내용 텍스트를 추출."""
        try:
            html = self._http.get_text(
                f"{_SUMMARY_URL}?billId={bill_id}",
                extra_headers={"Referer": f"{BASE}/bill/bi/billDetailPage.do?billId={bill_id}"},
            )
        except Exception:
            return None
        soup = soupify(html)
        pre = soup.find("pre")
        if pre:
            text = pre.get_text(" ", strip=True)
            text = _RE_WHITESPACE.sub(" ", text).strip() or None
            if text and _RE_BILL_ERROR.search(text):
                return None
            return text
        # fallback: strip tags from full body
        body = soup.find("body")
        if body:
            text = body.get_text(" ", strip=True)
            # keep only the 제안이유 section
            m = re.search(r"제안이유\s*및\s*주요내용(.+?)(?:의안\s*상세정보|$)", text, re.DOTALL)
            if m:
                return _RE_WHITESPACE.sub(" ", m.group(1)).strip() or None
        return None

    # ── API 3: 본회의 가결 ──────────────────────────────────────────────────

    def _fetch_plenary_pass(self) -> list[FetchedItem]:
        return self._fetch_search_pages(
            {"procGbnCd": "P", "mainResultCd": "PRC_0013",
             "procFrom": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
             "procTo": datetime.now().strftime("%Y-%m-%d")},
            milestone="본회의 가결", date_col=4,
        )

    def _fetch_recent_proposals(self) -> list[FetchedItem]:
        # 처리 여부에 관계없이 최근 발의·제출된 안을 확인한다. 이후 가결 등은 별도 키로 추적.
        return self._fetch_search_pages(
            {"fromPropDt": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
             "toPropDt": datetime.now().strftime("%Y-%m-%d")},
            milestone="발의·제출", date_col=3,
        )

    def _fetch_search_pages(self, filters: dict, *, milestone: str, date_col: int) -> list[FetchedItem]:
        params = {"reqPageId": "billSrch", "detailedTab": "billDtl",
                  "billKind": "BKC_0004", "ageFrom": "22", "ageTo": "22",
                  "rows": "100", "schSorting": "dateDesc", **filters}
        return self._fetch_pages(
            f"{BASE}/bill/bi/bill/sch/findSchPaging.do", params, _AJAX_HEADERS_SCH,
            lambda html: self._parse_sch_html(html, milestone=milestone, date_col=date_col),
            milestone,
        )

    def _fetch_pages(self, url, params, headers, parse, milestone):
        result = {}
        previous_ids = None
        rows_seen = 0
        for page in range(1, self._max_pages + 1):
            html = self._http.post_text(url, data={**params, "page": str(page)}, extra_headers=headers)
            soup = soupify(html)
            ids = {a.get("data-bill-id") or a.get("data-bill_id")
                   for a in soup.select("a[data-bill-id], a[data-bill_id]")}
            if not ids and not any(x in soup.get_text() for x in ("검색 결과가 없습니다", "조회된 결과가 없습니다", "검색된 결과가 없습니다", "조회된 데이터가 없습니다", "검색결과가 없습니다", "검색된 데이터가 없습니다")):
                raise ValueError(f"국회 {milestone}: 목록 구조 확인 필요")
            if ids and ids == previous_ids:
                raise ValueError(f"국회 {milestone}: 페이지 반복 응답")
            previous_ids = ids
            rows_seen += len(ids)
            for item in parse(html):
                result[item.source_item_key] = item
            marker = soup.select_one("#pager_pages_text")
            paging = re.search(r"(\d+)\s*/\s*(\d+)\s*페이지", str(marker or html))
            if not paging:
                if ids:
                    raise ValueError(f"국회 {milestone}: 페이지 정보 누락")
                break
            if page >= int(paging.group(2)):
                break
        else:
            raise ValueError(f"국회 {milestone}: {self._max_pages}페이지 초과, 수집 불완전")
        self.diagnostics.append({"stage": milestone, "pages": page, "rows": rows_seen,
                                 "matched": len(result)})
        return list(result.values())

    def _parse_sch_html(
        self, html: str, *, milestone: str, date_col: int,
        state_filter: str | None = None,
    ) -> list[FetchedItem]:
        """findSchPaging 공통 HTML 파서.
        date_col: 날짜가 있는 td 인덱스(0-based).
        state_filter: 지정 시 td[7](심사진행상태)가 이 값인 행만 수집."""
        cutoff = datetime.now() - timedelta(days=30)
        soup = soupify(html)
        items: list[FetchedItem] = []

        for tr in soup.select("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue

            # 심사진행상태 필터 (td[7])
            if state_filter is not None:
                if len(tds) < 8:
                    continue
                if tds[7].get_text(strip=True) != state_filter:
                    continue

            title_td = tds[1]
            a_tag = title_td.find("a")
            if not a_tag:
                continue

            bill_id, title = _extract_from_anchor(a_tag)
            if not bill_id or not title:
                continue

            if not _is_target_law(title):
                continue

            # 날짜 파싱
            published_at: datetime | None = None
            if len(tds) > date_col:
                date_text = normalize_ws(tds[date_col].get_text(" ", strip=True))
                published_at = parse_yyyy_mm_dd(date_text)

            # 최근 30일 필터
            if published_at and published_at.date() < cutoff.date():
                continue

            # raw_text는 기존 발송 해시에 포함되지 않는다. 단계도 제목에 표시하여
            # 같은 의안의 심사 단계 변경을 신규 버전으로 포착한다.
            stage = tds[7].get_text(strip=True) if milestone == '발의·제출' and len(tds) > 7 else ''
            stage_label = f'{milestone} · {stage}' if stage else milestone
            items.append(
                FetchedItem(
                    source="na",
                    category="legislation",
                    source_item_key=f"{milestone}:{bill_id}",
                    title=f"[{stage_label}] {title}",
                    url=_bill_url(bill_id),
                    published_at=published_at,
                    attachments=[],
                    raw_text=(f"심사진행상태: {tds[7].get_text(strip=True)}" if milestone == "발의·제출" and len(tds) > 7 else None),
                )
            )

        return items

    # ── API 2: 위원회안·대안 (위원회 대안 의결 → 법사위 체계자구심사 전) ──────

    def _fetch_plenary_propose(self) -> list[FetchedItem]:
        params = {
            "stateId": "suggest",
            "excQryId": "schAnBill",
            "anbillDivCd": "anbill",
            "page": "1",
            "age_from_sch": "22",
            "age_to_sch": "22",
        }
        return self._fetch_pages(
            f"{BASE}/bill/bi/bill/state/searchBillStatePaging.do", params, _AJAX_HEADERS_STATE,
            lambda html: self._parse_state_html(html, milestone="위원회안·대안"), "위원회안·대안",
        )

    # ── API 1: 본회의 부의 (법사위 체계자구심사 통과 → 본회의 상정 대기) ──────

    def _fetch_before_propose(self) -> list[FetchedItem]:
        """본회의 부의안건(beforePropose)을 수집.
        위원회 의결 후 법사위 체계자구심사를 거쳐 본회의에 부의된 의안으로,
        본회의 가결 직전 단계(통과 임박 신호)에 해당함."""
        params = {
            "stateId": "beforePropose",
            "excQryId": "schAnBill",
            "anbillDivCd": "anbill",
            "page": "1",
            "age_from_sch": "22",
            "age_to_sch": "22",
        }

        # beforePropose 목록의 유일한 날짜(td[3])는 '위원회 의결일자'로, 본회의 부의일이
        # 아님. 오래된 위원회 의결일로 30일 컷오프를 걸면 항상 0건이 되므로, 이 단계는
        # 날짜 없이(published_at=None) 신규 발견 시 1회 발송 + DB 중복제거에 맡김.
        return self._fetch_pages(
            f"{BASE}/bill/bi/bill/state/searchBillStatePaging.do", params, _AJAX_HEADERS_STATE,
            lambda html: self._parse_state_html(html, milestone="본회의 부의", date_col=None), "본회의 부의",
        )

    def _parse_state_html(
        self, html: str, *, milestone: str, date_col: int | None = 3,
    ) -> list[FetchedItem]:
        """searchBillStatePaging HTML 파서. td 5개+: [의안번호, 의안명, 소관위, 의결일자, 결과, ...].
        date_col=None이면 신뢰할 날짜가 없는 단계로 보아 published_at을 비우고 컷오프를 건너뜀."""
        cutoff = datetime.now() - timedelta(days=30)
        soup = soupify(html)
        items: list[FetchedItem] = []

        for tr in soup.select("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            title_td = tds[1]
            a_tag = title_td.find("a")
            if not a_tag:
                continue

            bill_id, title = _extract_from_anchor(a_tag)
            if not bill_id or not title:
                continue

            if not _is_target_law(title):
                continue

            # 의결일자 (td index 3). date_col=None인 단계는 신뢰할 날짜가 없어 비워 둠.
            published_at: datetime | None = None
            if date_col is not None and len(tds) > date_col:
                date_text = normalize_ws(tds[date_col].get_text(" ", strip=True))
                published_at = parse_yyyy_mm_dd(date_text)

            # 최근 30일 필터 (22대 전체 목록이므로 필수). 날짜 있는 단계에만 적용.
            if date_col is not None and published_at and published_at.date() < cutoff.date():
                continue

            items.append(
                FetchedItem(
                    source="na",
                    category="legislation",
                    source_item_key=f"{milestone}:{bill_id}",
                    title=f"[{milestone}] {title}",
                    url=_bill_url(bill_id),
                    published_at=published_at,
                    attachments=[],
                    raw_text=None,
                )
            )

        return items
