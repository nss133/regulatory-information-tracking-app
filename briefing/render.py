from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from briefing.db import StoredItem
from briefing.sources.registry import SOURCE_SPECS


@dataclass(frozen=True)
class RenderAttachment:
    label: str
    url: str


@dataclass(frozen=True)
class RenderItem:
    source_code: str
    source_name_ko: str
    title: str
    url: str
    published_at: Optional[str]
    category_label: str
    importance: str
    reason: str
    summary: Optional[str]
    is_updated: bool
    attachments: list[RenderAttachment]


@dataclass(frozen=True)
class RenderSection:
    label: str
    items: list[RenderItem]


def _category_label(cat: str) -> str:
    return {
        "press": "보도자료",
        "legislation": "입법/예고",
        "admin_notice": "행정예고",
        "case_law": "주요판결",
        "other": "기타",
    }.get(cat, cat)


def _safe_json_loads(s: str) -> list[dict[str, Any]]:
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def render_email_html(
    *,
    items: list[StoredItem],
    template_dir: str | Path,
    subject: str,
    run_date: str,
    errors: list[str] | None = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template("email.html.j2")

    # 1단계: 개인정보 + (시민단체/간담회/행사) 조합은 목록에서 완전히 제외
    def _should_exclude(it: StoredItem) -> bool:
        title = (it.title or "").lower()
        if "개인정보" not in title:
            return False
        return any(kw in title for kw in ["시민단체", "간담회", "행사"])

    items = [it for it in items if not _should_exclude(it)]

    by_importance: dict[str, list[RenderItem]] = {"high": [], "medium": [], "low": []}
    # '규정/입법예고/법령/시행령/감독규정' 등 법·규정성 키워드가 있으면
    # 같은 중요도 안에서는 더 위에 보이도록 가중치를 줄 것입니다.
    legal_keywords = [
        "규정",
        "입법예고",
        "입법 예고",
        "법령",
        "법률",
        "시행령",
        "감독규정",
    ]
    for it in items:
        spec = SOURCE_SPECS.get(it.source)
        source_name_ko = spec.name_ko if spec else it.source
        atts = [
            RenderAttachment(label=a.get("label", "첨부"), url=a.get("url", ""))
            for a in _safe_json_loads(it.attachments_json)
            if a.get("url")
        ]
        is_updated = bool(it.last_sent_at) and (it.sent_hash != it.content_hash)
        importance = (it.importance or "low")
        text_for_legal = f"{it.title} {it.importance_reason or ''}"
        has_legal = any(k in text_for_legal for k in legal_keywords)
        by_importance.setdefault(importance, []).append(
            RenderItem(
                source_code=it.source,
                source_name_ko=source_name_ko,
                title=it.title,
                url=it.url,
                published_at=it.published_at,
                category_label=_category_label(it.category),
                importance=importance,
                reason=it.importance_reason or "",
                summary=it.summary,
                is_updated=is_updated,
                attachments=atts,
            )
        )

    # 소스 우선순위: FSC > FSS > 국회(입법) > KFTC > KoFIU > 대법원 > PIPC > MOEL > NHRCK
    source_order = {
        "fsc": 1,
        "fss": 2,
        "na": 3,
        "kftc": 4,
        "kofiu": 5,
        "scourt": 6,
        "pipc": 7,
        "moel": 8,
        "nhrck": 9,
    }

    def _sort_key(x: RenderItem):
        # 신규(처음 리포트) > 변경(기존 리포트 후 내용 변경)
        new_score = 1 if not x.is_updated else 0
        # published_at이 None이면 뒤로
        dt = x.published_at or "0000-00-00"
        # 법·규정 키워드가 있으면 같은 그룹 내에서 더 위에 배치
        legal_score = 1 if any(k in x.title or k in x.reason for k in legal_keywords) else 0
        return (new_score, legal_score, dt, -source_order.get(x.source_code, 999))

    # (1) 신규 여부 (2) 법·규정 키워드 유무 (3) 날짜 내림차순 (4) 소스 우선순위
    for k in list(by_importance.keys()):
        by_importance[k] = sorted(by_importance[k], key=_sort_key, reverse=True)

    # MEDIUM 중요도 내에서, 거의 동일한 제목(공백/기호 무시)이 중복될 경우
    # 가장 최신 항목만 남기고 나머지는 제거합니다.
    def _dedupe_medium(items: list[RenderItem]) -> list[RenderItem]:
        seen: set[str] = set()

        def _norm_title(t: str) -> str:
            # 한글/영문/숫자만 남기고 모두 제거하여, 기호·공백 차이는 무시
            return "".join(ch for ch in t if ch.isalnum()).lower()

        out: list[RenderItem] = []
        for it in items:
            key = _norm_title(it.title)
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    if by_importance.get("medium"):
        by_importance["medium"] = _dedupe_medium(by_importance["medium"])

    sections: list[RenderSection] = [
        RenderSection(label="HIGH", items=by_importance.get("high", [])),
        RenderSection(label="MEDIUM", items=by_importance.get("medium", [])),
        RenderSection(label="LOW", items=by_importance.get("low", [])),
    ]

    return tpl.render(subject=subject, run_date=run_date, sections=sections, errors=(errors or []))

