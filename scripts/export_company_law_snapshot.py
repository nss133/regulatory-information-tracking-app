#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comp_matching_auto 자산을 뉴스클리핑 relevance 채점용 정적 스냅샷으로 export.

산출물: data/company_relevance_snapshot.json
  - laws:      감시법령 유니버스(사규가 실제로 걸려있는 법령들) + 이표기(aliases)
  - soft_laws: 생보협회(KLIA) 규정·모범규준 + FSC 가이드라인(매칭 확정분에서 명칭 정제)
  - 각 항목에 사규 연계 수(확정 매칭 기준, 부분 커버리지)
뉴스클리핑은 이 JSON만 읽으며 comp_matching_auto에 런타임 의존하지 않는다.
사규·매칭 데이터가 갱신되면 본 스크립트를 재실행해 스냅샷을 갱신한다(분기 1회 권장).

실행(1회성·오프라인, pandas 필요 → 시스템 python3 사용):
  python3 scripts/export_company_law_snapshot.py
  python3 scripts/export_company_law_snapshot.py --comp-root <경로> --out <경로>

사규 연계 수(sagyu_links) 집계 기준:
  - output/ai_rule_source_*_ai_reviewed.xlsx 의 조문별 1순위 매칭 중
    review_tier가 자동확정 계층이거나 ai_decision='AI추천'인 것만 카운트.
  - 현재 3개 부서(감사실·소비자보호실·정보보호팀)분만 존재 → 부분 커버리지.
    따라서 소비 측(relevance.py)은 이 값을 '보조 가중치'로만 쓰고,
    기본 가중치는 유니버스 소속 여부에서 얻는다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone, timedelta

DEFAULT_COMP_ROOT = (
    "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/comp_matching_auto"
)
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "company_relevance_snapshot.json")

# 매칭 확정으로 인정하는 review_tier (comp_matching_auto의 자동확정 계층)
CONFIRMED_TIERS = {
    "인용확정",
    "절대점수+규범일치",
    "고임계점수차",
    "중임계+규범일치",
    "절대점수_규범미상",
}

# FSC 가이드라인 명칭 정제: 이 접미 유형으로 끝나는 실질 명칭만 채택
_INSTRUMENT_TAIL = re.compile(
    r"^(.{4,}?(?:모범규준|가이드라인|준칙|표준내부통제기준|내부통제기준|규준))"
)
_FSC_PREFIX = "FSC가이드라인:"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").strip())


def norm_key(s: str) -> str:
    """법령명 비교 키: NFC + 공백 제거."""
    return re.sub(r"\s+", "", nfc(s))


def split_parenthetical(name: str) -> tuple[str, list[str]]:
    """'국민기초생활보장법(기초생활보장법)' → ('국민기초생활보장법', ['기초생활보장법']).

    괄호 안이 약칭이 아닌 경우(숫자·조문 등)는 버린다.
    """
    m = re.match(r"^(.*?)\(([^)]+)\)\s*$", name)
    if not m:
        return name, []
    base, inner = m.group(1).strip(), m.group(2).strip()
    # 약칭으로 보이는 경우만: 한글 포함 + 2자 이상 + '법'/'규정'/'규칙'/'세칙' 포함
    if re.search(r"[가-힣]{2,}", inner) and re.search(r"(법|규정|규칙|세칙|예규)", inner):
        return base, [inner]
    return base, []


class Universe:
    """법령·협회규정 유니버스. norm_key로 중복 표기를 하나의 항목으로 병합한다."""

    def __init__(self):
        self.entries: dict[str, dict] = {}   # norm_key(canonical) → entry
        self.index: dict[str, str] = {}      # norm_key(모든 표기) → canonical의 norm_key

    def add(self, name: str, *, kind: str, article_count: int = 0,
            aliases: set[str] | None = None) -> None:
        name = nfc(name)
        base, abbrevs = split_parenthetical(name)
        alias_set = set(aliases or set()) | set(abbrevs)
        if name != base:
            alias_set.add(name)  # 원표기도 별칭으로 보존

        key = norm_key(base)
        existing_key = self.index.get(key)
        if existing_key is None:
            self.entries[key] = {
                "display": base, "kind": kind, "aliases": set(),
                "article_count": 0, "sagyu_links": 0, "citation_links": 0,
            }
            existing_key = key
            self.index[key] = key
        entry = self.entries[existing_key]
        # 표시명은 띄어쓰기 있는(자연스러운) 쪽을 선호
        if base != entry["display"] and " " in base and " " not in entry["display"]:
            entry["aliases"].add(entry["display"])
            entry["display"] = base
        elif base != entry["display"]:
            entry["aliases"].add(base)
        entry["article_count"] += int(article_count)
        for a in alias_set:
            if norm_key(a) != existing_key:
                entry["aliases"].add(nfc(a))
            self.index.setdefault(norm_key(a), existing_key)

    def find(self, name: str) -> dict | None:
        key = self.index.get(norm_key(name))
        return self.entries.get(key) if key else None


def load_law_db(uni: Universe, db_path: str, *, kind: str) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT law_name, COUNT(*) FROM law_articles GROUP BY law_name"
    ).fetchall()
    conn.close()
    for raw_name, cnt in rows:
        uni.add(raw_name, kind=kind, article_count=cnt)
    return len(rows)


def apply_equivalence(uni: Universe, eq_path: str) -> int:
    """law_name_equivalence.json의 동표기 그룹을 aliases에 병합."""
    if not os.path.exists(eq_path):
        return 0
    with open(eq_path, encoding="utf-8") as f:
        groups = json.load(f).get("equivalence_groups", [])
    merged = 0
    for group in groups:
        group = [nfc(g) for g in group]
        entry = next((e for g in group if (e := uni.find(g))), None)
        if not entry:
            continue
        key = norm_key(entry["display"])
        for g in group:
            if norm_key(g) != key and g not in entry["aliases"]:
                entry["aliases"].add(g)
                uni.index.setdefault(norm_key(g), key)
                merged += 1
    return merged


def clean_fsc_guideline_name(raw: str) -> str | None:
    """'FSC가이드라인:금융회사 지배구조 모범규준안' → '금융회사 지배구조 모범규준'.

    파일명·보도자료 제목 수준의 잡음은 버리고, 실질 명칭으로 끝나는 것만 채택.
    """
    name = nfc(raw)
    if name.startswith(_FSC_PREFIX):
        name = name[len(_FSC_PREFIX):].strip()
    if re.search(r"(\.hwp|조간|석간|브리핑|별첨|말씀|세미나|Q.?&.?A|QnA)", name, re.I):
        return None
    m = _INSTRUMENT_TAIL.match(name)
    return m.group(1).strip() if m else None


def count_sagyu_links(uni: Universe, output_glob: str) -> tuple[int, list[str]]:
    """확정 매칭 기준 항목별 사규 연계 수 집계. (총 확정건수, 사용 파일) 반환."""
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit(
            "pandas가 필요합니다. 시스템 python3로 실행하세요: "
            "python3 scripts/export_company_law_snapshot.py"
        )

    files = sorted(glob.glob(output_glob))
    total = 0
    unmatched: set[str] = set()
    for f in files:
        df = pd.read_excel(f)
        for _, row in df.iterrows():
            tier = nfc(str(row.get("review_tier") or ""))
            ai_dec = nfc(str(row.get("ai_decision") or ""))
            if not (tier in CONFIRMED_TIERS or ai_dec == "AI추천"):
                continue
            # AI가 다른 후보를 추천했으면 그 법령을, 아니면 1순위 매칭 법령을 사용
            law_name = row.get("ai_recommended_law_name")
            if not isinstance(law_name, str) or not law_name.strip():
                law_name = row.get("match_1_law_name")
            if not isinstance(law_name, str) or not law_name.strip():
                continue
            law_name = nfc(law_name)
            entry = uni.find(law_name)
            if entry is None and law_name.startswith(_FSC_PREFIX):
                cleaned = clean_fsc_guideline_name(law_name)
                if cleaned:
                    uni.add(cleaned, kind="soft_law")
                    entry = uni.find(cleaned)
            if entry is None:
                unmatched.add(law_name)
                continue
            entry["sagyu_links"] += 1
            if tier == "인용확정":
                entry["citation_links"] += 1
            total += 1
    if unmatched:
        print(f"  ⚠️ 유니버스에 없는 매칭 법령명 {len(unmatched)}건 (미집계):")
        for n in sorted(unmatched)[:10]:
            print(f"     - {n}")
    return total, [os.path.basename(f) for f in files]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comp-root", default=DEFAULT_COMP_ROOT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    law_db = os.path.join(args.comp_root, "data", "laws_monitored.sqlite")
    klia_db = os.path.join(args.comp_root, "data", "klia_regulations.sqlite")
    eq_json = os.path.join(args.comp_root, "data", "law_name_equivalence.json")
    match_glob = os.path.join(args.comp_root, "output", "ai_rule_source_*_ai_reviewed.xlsx")

    if not os.path.exists(law_db):
        raise SystemExit(f"법령 DB 없음: {law_db}")

    uni = Universe()

    print(f"[1/4] 감시법령 유니버스 로드: {law_db}")
    n = load_law_db(uni, law_db, kind="law")
    n_laws = sum(1 for e in uni.entries.values() if e["kind"] == "law")
    print(f"  법령명 {n}개 → 병합 후 {n_laws}개")

    print(f"[2/4] 생보협회(KLIA) 규정 로드: {klia_db}")
    if os.path.exists(klia_db):
        n = load_law_db(uni, klia_db, kind="soft_law")
        print(f"  협회규정 {n}개")
    else:
        print("  (없음 — 건너뜀)")

    print(f"[3/4] 이표기 병합: {eq_json}")
    merged = apply_equivalence(uni, eq_json)
    print(f"  별칭 {merged}건 병합")

    print(f"[4/4] 사규 연계 수 집계: {match_glob}")
    total, files = count_sagyu_links(uni, match_glob)
    print(f"  확정 매칭 {total}건 집계 (파일 {len(files)}개)")

    def dump(kind: str) -> dict:
        return {
            e["display"]: {
                "aliases": sorted(e["aliases"]),
                "article_count": e["article_count"],
                "sagyu_links": e["sagyu_links"],
                "citation_links": e["citation_links"],
            }
            for e in sorted(uni.entries.values(), key=lambda x: x["display"])
            if e["kind"] == kind
        }

    kst = timezone(timedelta(hours=9))
    laws_out, soft_out = dump("law"), dump("soft_law")
    out = {
        "_meta": {
            "generated_at": datetime.now(kst).isoformat(timespec="seconds"),
            "source_law_db": law_db,
            "source_klia_db": klia_db if os.path.exists(klia_db) else None,
            "match_files": files,
            "confirmed_tiers": sorted(CONFIRMED_TIERS),
            "coverage_note": (
                "sagyu_links는 매칭 완료 부서분만 반영된 부분 커버리지. "
                "관련성 채점 시 보조 가중치로만 사용할 것(기본 가중치는 유니버스 소속 여부)."
            ),
        },
        "laws": laws_out,
        "soft_laws": soft_out,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"저장: {args.out} (법령 {len(laws_out)}개 + 협회·연성규범 {len(soft_out)}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
