from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable, Optional


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

# 날짜와 사건(시행/공포/개정 등)을 느슨한 근접거리만으로 연결하면 판결문에
# 인용된 과거 법령 연혁을 현재 일정으로 오인한다. 날짜와 시행 키워드 사이에
# 아래 사건어 또는 다른 날짜가 있으면 그 날짜는 시행일 후보에서 제외한다.
_EVENT_BLOCKERS = ("공포", "개정", "결정", "선고", "의결")
_PARTIAL_DATE = re.compile(
    r"\d{1,2}\s*(?:월|[.\-/])\s*\d{1,2}\s*(?:일)?"
)

# "2026. 2. 19. 공포, 8. 20. 시행"처럼 두 번째 날짜에 연도가 생략된
# 법령 문구를 지원한다. 반드시 두 번째 날짜 바로 뒤에 시행 표현이 있어야 한다.
_INHERITED_YEAR_EFFECTIVE = re.compile(
    r"(?P<year>20\d{2})\s*(?:년|[.\-/])\s*\d{1,2}\s*"
    r"(?:월|[.\-/])\s*\d{1,2}\s*(?:일)?\s*[.]?"
    r"(?P<middle>.{0,45}?)"
    r"(?P<month>\d{1,2})\s*(?:월|[.\-/])\s*(?P<day>\d{1,2})\s*(?:일)?\s*[.]?"
    r"\s*(?:부터\s*)?(?:시행일|시행\s*예정|시행한다|시행됩니다|시행)",
)


def _valid_iso(match: re.Match[str]) -> str | None:
    try:
        return date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        ).isoformat()
    except ValueError:
        return None


def _has_explicit_relation(
    *, compact: str, match: re.Match[str], keyword: str, label: str
) -> tuple[bool, int]:
    """날짜와 일정 키워드가 같은 짧은 구문에서 직접 연결되는지 판정한다."""
    before_start = max(0, match.start() - 35)
    after_end = min(len(compact), match.end() + 35)
    before = compact[before_start:match.start()]
    after = compact[match.end():after_end]

    best = 10**9
    # "시행일은 2026. 9. 1." / "의견 제출기한 2026. 8. 21." 형태
    before_pos = before.rfind(keyword)
    if before_pos >= 0:
        gap = before[before_pos + len(keyword):]
        if len(gap) <= 16 and not _PARTIAL_DATE.search(gap):
            best = min(best, len(gap))

    # "2026. 9. 1.부터 시행" 형태. 다른 날짜나 공포·개정 등 사건어가
    # 사이에 끼면 앞 날짜를 시행일로 연결하지 않는다.
    after_pos = after.find(keyword)
    if after_pos >= 0:
        gap = after[:after_pos]
        blocked = _PARTIAL_DATE.search(gap) is not None
        if label == "시행일":
            blocked = blocked or any(word in gap for word in _EVENT_BLOCKERS)
        if len(gap) <= 16 and not blocked:
            best = min(best, len(gap))
    return best < 10**9, best


def extract_deadlines(
    text: str | None, *, allowed_labels: Optional[Iterable[str]] = None
) -> list[Deadline]:
    """본문에서 의견제출·시행·신청 관련 명시적 날짜만 보수적으로 추출한다."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return []
    allowed = set(allowed_labels) if allowed_labels is not None else set(_KEYWORDS)
    found: dict[tuple[str, str], Deadline] = {}
    for match in re.finditer(_DATE, compact):
        iso = _valid_iso(match)
        if not iso:
            continue
        start = max(0, match.start() - 45)
        end = min(len(compact), match.end() + 45)
        context = compact[start:end]
        label = None
        best_distance = 10**9
        for candidate, keywords in _KEYWORDS.items():
            if candidate not in allowed:
                continue
            for keyword in keywords:
                related, distance = _has_explicit_relation(
                    compact=compact, match=match, keyword=keyword, label=candidate
                )
                if related and distance < best_distance:
                    label = candidate
                    best_distance = distance
        if not label:
            continue
        excerpt = context.strip(" ,.;·-")
        found[(label, iso)] = Deadline(label=label, date=iso, source_text=excerpt)

    # 연도 생략 시행일은 전체 날짜 순회로는 만들 수 없으므로 별도 보강한다.
    if "시행일" in allowed:
        for match in _INHERITED_YEAR_EFFECTIVE.finditer(compact):
            try:
                iso = date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ).isoformat()
            except ValueError:
                continue
            start = max(0, match.start() - 20)
            end = min(len(compact), match.end() + 20)
            excerpt = compact[start:end].strip(" ,.;·-")
            found[("시행일", iso)] = Deadline(
                label="시행일", date=iso, source_text=excerpt
            )
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
