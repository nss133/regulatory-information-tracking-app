#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recommend_news.py — 정부동향 클리핑 중 '근거 승격 후보'를 선제 추천(digest 출력).

promote_news.py가 '인용 시점' 수동 승격이라면, 이 스크립트는 그 앞단에서 최근
수집분 중 규범관련성 높은 항목을 골라 사람이 검토할 후보 목록을 만든다.
자동 승격은 하지 않는다(인용 시점 승격 원칙 유지) — 출력된 promote 명령을 사람이
검토 후 실행.

포함 기준(금융·보험 규제 집중):
  - 소스: fsc(금융위)·fss(금감원) 우선, pipc(개인정보위)·kftc(공정위) 보조.
  - 1차근접(legislation 입법예고 · admin_notice 감독규정·행정지도): 등급 불문 전부.
  - 2차(press 보도자료): high만.
  - 이미 '정부동향(근거)/'에 승격된 항목은 제외.
  - 최근 N일(기본 7일, first_seen_at 기준) 이내.

사용:
  python3 recommend_news.py                # 최근 7일, 마크다운 digest를 stdout
  python3 recommend_news.py --days 14      # 창(window) 조정
  python3 recommend_news.py --out FILE     # 파일로도 저장
DB는 읽기전용.
"""
import argparse
import os
import re
import sqlite3
import unicodedata

NEWS_DB = os.environ.get(
    "NEWS_DB_PATH",
    "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping/data/briefing.sqlite3",
)
PROMOTE_DIR = os.environ.get(
    "NEWS_PROMOTE_DIR",
    "/Users/nsss/obsidian/nss/08. 자원·참고자료/법령자료/정부동향(근거)",
)

# 금융·보험 규제 집중 — 소스 우선순위(작을수록 위).
SOURCE_RANK = {"fsc": 0, "fss": 0, "pipc": 1, "kftc": 1}
SOURCE_KO = {"fsc": "금융위", "fss": "금감원", "pipc": "개인정보위", "kftc": "공정위"}
GRADE = {
    "legislation": "1차근접(입법)",
    "admin_notice": "1차근접(행정예고·감독규정)",
    "press": "2차(보도자료)",
    "other": "기타",
}
# 1차근접 = 등급 불문 전부, 그 외(press/other) = high만.
PRIMARY_CATS = {"legislation", "admin_notice"}
IMP_RANK = {"high": 0, "medium": 1, None: 2, "low": 3}


def norm_title(t: str) -> str:
    """제목 정규화 키(공백·기호 제거 후 앞 30자). dedup·승격제외 공용."""
    t = unicodedata.normalize("NFC", t or "")
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t)
    return t[:30]


def load_promoted():
    """이미 승격된 항목의 (키프리픽스 집합, 제목정규화 집합).

    키프리픽스는 exact 소스키 제외용, 제목정규화는 동일 공시가 다른 소스로
    교차게시된 경우(예: fsc↔fss)까지 제외하기 위함. 제목은 각 승격본의
    frontmatter '제목:' 줄에서 원문 그대로 읽어 정확히 비교.
    """
    prefixes, titles = set(), set()
    if not os.path.isdir(PROMOTE_DIR):
        return prefixes, titles
    for fn in os.listdir(PROMOTE_DIR):
        if not fn.endswith(".md"):
            continue
        nfn = unicodedata.normalize("NFC", fn)
        m = re.match(r"^정부동향_(.+?)_", nfn)
        if m:
            prefixes.add(m.group(1))
        try:
            with open(os.path.join(PROMOTE_DIR, fn), encoding="utf-8") as f:
                started = False  # 여는 '---'를 지났는지(파일별)
                for line in f:
                    if line.startswith("제목:"):
                        titles.add(norm_title(line[len("제목:"):].strip()))
                        break
                    if line.strip() == "---":
                        if started:  # 닫는 펜스 — 제목 못 찾고 프론트매터 끝
                            break
                        started = True
        except OSError:
            pass
    return prefixes, titles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="최근 N일 창(first_seen_at 기준)")
    ap.add_argument("--out", default=None, help="digest를 파일로도 저장")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    srcs = tuple(SOURCE_RANK)
    rows = conn.execute(
        f"""SELECT source, source_item_key, category, title, url, summary,
                   published_at, importance,
                   COALESCE(published_at, substr(first_seen_at,1,10)) AS d,
                   substr(first_seen_at,1,10) AS seen
            FROM items
            WHERE source IN ({','.join('?'*len(srcs))})
              AND (
                    category IN ('legislation','admin_notice')      -- 1차근접 전부
                 OR (category IN ('press','other') AND importance='high')  -- 2차 high만
                  )
              AND date(substr(first_seen_at,1,10)) >= date('now','localtime',?)
            """,
        (*srcs, f"-{args.days} day"),
    ).fetchall()

    promoted_keys, promoted_titles = load_promoted()
    cands = []
    for r in rows:
        skey = r["source_item_key"]
        prefix = unicodedata.normalize("NFC", f"{r['source']}-{skey}")
        if prefix in promoted_keys:
            continue
        if norm_title(r["title"]) in promoted_titles:  # 교차게시 쌍둥이 제외
            continue
        cands.append(r)

    def sortkey(r):
        primary = 0 if r["category"] in PRIMARY_CATS else 1
        return (
            primary,
            SOURCE_RANK.get(r["source"], 9),
            IMP_RANK.get(r["importance"], 4),
            # 최근 것 먼저: 날짜 역순 → 음수 문자열 정렬 대신 reverse 키
            r["seen"] or "",
        )

    # 안정정렬 조합: 최신 먼저 깔고 → 등급·소스·중요도로 덮어씀.
    cands.sort(key=lambda r: (r["seen"] or ""), reverse=True)
    cands.sort(key=lambda r: sortkey(r)[:3])

    # 제목 정규화 dedup(동일 공시가 여러 소스/해시로 재수집됨). 상위 인스턴스 유지.
    seen_titles, deduped = set(), []
    for r in cands:
        nk = norm_title(r["title"])
        if nk and nk in seen_titles:
            continue
        seen_titles.add(nk)
        deduped.append(r)
    cands = deduped

    lines = []
    lines.append(f"# 정부동향 승격후보 (최근 {args.days}일 · 금융·보험 규제 집중)")
    lines.append("")
    lines.append(
        f"> 자동생성(`recommend_news.py`) · 후보 {len(cands)}건 · "
        "1차근접(입법·감독규정) 전부 + 2차(보도자료) high · 이미 승격분 제외"
    )
    lines.append("> ⚠️ **후보 제안일 뿐 자동 승격 아님.** 검토 후 아래 promote 명령을 실행.")
    lines.append("> 2차(보도자료)는 사실·입장이지 규범 단정 금지. 1차근접도 예고≠시행 유의.")
    lines.append("")

    if not cands:
        lines.append("_해당 창에 신규 후보 없음._")
    else:
        # 1차근접 / 2차 두 묶음으로 제시
        prim = [r for r in cands if r["category"] in PRIMARY_CATS]
        sec = [r for r in cands if r["category"] not in PRIMARY_CATS]
        for label, group in (("① 1차근접 (입법예고·감독규정·행정지도)", prim),
                             ("② 2차 (보도자료 · high)", sec)):
            if not group:
                continue
            lines.append(f"## {label}  ({len(group)}건)")
            lines.append("")
            for r in group:
                src_ko = SOURCE_KO.get(r["source"], r["source"])
                grade = GRADE.get(r["category"], r["category"] or "기타")
                key = f"{r['source']}/{r['source_item_key']}"
                title = re.sub(r"\s+", " ", r["title"] or "(제목없음)").strip()
                summ = re.sub(r"\s+", " ", (r["summary"] or "")).strip()
                summ = (summ[:120] + "…") if len(summ) > 120 else summ
                lines.append(f"### {title}")
                lines.append(
                    f"- {src_ko}({r['source']}) · {grade} · 중요도 {r['importance'] or '-'} "
                    f"· 발표/수집 {r['d'] or '-'} / {r['seen'] or '-'}"
                )
                if summ:
                    lines.append(f"- 요약: {summ}")
                if r["url"]:
                    lines.append(f"- url: {r['url']}")
                lines.append(f"- 인용키: `{key}`")
                lines.append(f"- 승격: `python3 promote_news.py {key}`")
                lines.append("")

    out = "\n".join(lines) + "\n"
    print(out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"(저장: {args.out})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
