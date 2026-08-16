from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Deadline:
    label: str
    date: str
    source_text: str


_DATE = r"(?P<year>20\d{2})\s*(?:년|[.\-/])\s*(?P<month>\d{1,2})\s*(?:월|[.\-/])\s*(?P<day>\d{1,2})\s*(?:일)?"
_KEYWORDS = {
    "의견제출": ("의견제출", "의견 제출", "제출기한", "제출 기한", "의견수렴", "의견 수렴"),
    "시행일": ("시행일", "시행 일자", "시행 예정", "시행한다", "시행됩니다", "시행"),
    "신청기한": ("신청기한", "신청 기한", "신청기간", "신청 기간", "접수기간", "접수 기간"),
}


def _valid_iso(match: re.Match[str]) -> str | None:
    try:
        return date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        ).isoformat()
    except ValueError:
        return None


def extract_deadlines(text: str | None) -> list[Deadline]:
    """본문에서 의견제출·시행·신청 관련 명시적 날짜만 보수적으로 추출한다."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return []
    found: dict[tuple[str, str], Deadline] = {}
    for match in re.finditer(_DATE, compact):
        iso = _valid_iso(match)
        if not iso:
            continue
        start = max(0, match.start() - 45)
        end = min(len(compact), match.end() + 35)
        context = compact[start:end]
        label = None
        best_distance = 10**9
        date_offset = match.start() - start
        for candidate, keywords in _KEYWORDS.items():
            for keyword in keywords:
                for keyword_match in re.finditer(re.escape(keyword), context):
                    distance = min(
                        abs(keyword_match.end() - date_offset),
                        abs(keyword_match.start() - (match.end() - start)),
                    )
                    if distance < best_distance:
                        label = candidate
                        best_distance = distance
        if not label:
            continue
        excerpt = context.strip(" ,.;·-")
        found[(label, iso)] = Deadline(label=label, date=iso, source_text=excerpt)
    return sorted(found.values(), key=lambda value: (value.date, value.label))


def deadlines_to_json(values: list[Deadline]) -> str | None:
    if not values:
        return None
    return json.dumps([asdict(value) for value in values], ensure_ascii=False)


def deadline_urgency(deadline_date: str, run_date: str) -> str:
    try:
        due = date.fromisoformat(deadline_date)
        today = datetime.fromisoformat(run_date).date()
    except ValueError:
        return "normal"
    days = (due - today).days
    if days < 0:
        return "expired"
    if days <= 7:
        return "urgent"
    if days <= 30:
        return "soon"
    return "normal"
