from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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


@dataclass
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
    child_items: list["RenderItem"] = field(default_factory=list)


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

    # 1단계: 일부 항목은 목록에서 완전히 제외
    def _should_exclude(it: StoredItem) -> bool:
        title = (it.title or "")
        title_lower = title.lower()
        if "시상" in title_lower:
            return True
        if any(kw in title_lower for kw in ["개소식", "공모전", "이러닝", "무료 상담", "무료상담", "응시"]):
            return True
        if "개인정보" in title_lower and any(kw in title_lower for kw in ["시민단체", "간담회", "행사"]):
            return True
        # 고용노동부는 HIGH만 표시 (LOW/MEDIUM은 노이즈가 많아 제외)
        if it.source == "moel" and (it.importance or "low") in ("low", "medium"):
            return True
        return False

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
        # 고용노동부: 체불 + 제재 동시에 나오면 LOW
        if it.source == "moel" and it.title:
            t = it.title
            if "체불" in t and "제재" in t:
                importance = "low"
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
                child_items=[],
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

    # 금융감독원(FSS) 자료가 금융위원회(FSC)와 60% 이상 제목 일치 시 FSC 하위로 들여쓰기
    def _title_words(s: str) -> set[str]:
        return set(re.findall(r"[\w]+", (s or "").lower()))

    def _title_similarity(a: str, b: str) -> float:
        wa, wb = _title_words(a), _title_words(b)
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / min(len(wa), len(wb))

    for k in list(by_importance.keys()):
        L = by_importance[k]
        matched_fss_ids: set[int] = set()
        for item in L:
            if item.source_code != "fss":
                continue
            best_fsc: Optional[RenderItem] = None
            best_sim = 0.0
            for other in L:
                if other.source_code != "fsc":
                    continue
                sim = _title_similarity(item.title, other.title)
                if sim >= 0.6 and sim > best_sim:
                    best_sim = sim
                    best_fsc = other
            if best_fsc is not None:
                best_fsc.child_items.append(item)
                matched_fss_ids.add(id(item))
        by_importance[k] = [i for i in L if id(i) not in matched_fss_ids]

    # 같은 부처 내에서 보도자료와 입법/행정예고의 제목 유사도가 60% 이상이면
    # 입법/행정예고를 보도자료 하위로 들여쓰기
    for k in list(by_importance.keys()):
        L = by_importance[k]
        child_ids: set[int] = set()
        for item in L:
            if item.category_label != "보도자료":
                continue
            for other in L:
                if id(other) == id(item):
                    continue
                if other.source_code != item.source_code:
                    continue
                if other.category_label not in ("입법/예고", "행정예고"):
                    continue
                if id(other) in child_ids:
                    continue
                if _title_similarity(item.title, other.title) >= 0.6:
                    item.child_items.append(other)
                    child_ids.add(id(other))
        by_importance[k] = [i for i in L if id(i) not in child_ids]

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

    # LOW 섹션: 신규 우선, 신규+기존 합쳐서 최대 10건만 표시
    if by_importance.get("low"):
        by_importance["low"] = by_importance["low"][:10]

    sections: list[RenderSection] = [
        RenderSection(label="HIGH", items=by_importance.get("high", [])),
        RenderSection(label="MEDIUM", items=by_importance.get("medium", [])),
        RenderSection(label="LOW", items=by_importance.get("low", [])),
    ]

    return tpl.render(subject=subject, run_date=run_date, sections=sections, errors=(errors or []))

