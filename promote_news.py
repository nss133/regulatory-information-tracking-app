#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""promote_news.py — 정부동향 클리핑 항목을 vault '근거(검증자료)' 노트로 승격(B).

수요기반 승격: 대부분의 뉴스는 DB에 두고 news_search로 조회하되, 판단(J-ID)이
실제로 인용한 항목만 이 스크립트로 vault에 영구 보존본을 만든다.
  (a) 판단의 근거가 뉴스DB 변경과 무관하게 보존되고
  (b) [[위키링크]]로 연결·grep 가능한 1급 근거가 된다.
전량 적재가 아니라 '인용 시점' 승격 — 검색 희석을 피하고 코퍼스를 깨끗이 유지한다.

사용: python3 promote_news.py <인용키>      예) python3 promote_news.py fsc/4139
DB는 읽기전용. vault에 .md 한 개를 쓴다.
"""
import os
import re
import sqlite3
import sys
import unicodedata

NEWS_DB = os.environ.get(
    "NEWS_DB_PATH",
    "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping/data/briefing.sqlite3",
)
VAULT_DIR = os.environ.get(
    "NEWS_PROMOTE_DIR",
    "/Users/nsss/obsidian/nss/08. 자원·참고자료/법령자료/정부동향(근거)",
)

SOURCE_KO = {
    "fsc": "금융위", "fss": "금감원", "kftc": "공정위", "moel": "고용노동부",
    "scourt": "대법원", "pipc": "개인정보위", "nhrck": "인권위", "knia": "손해보험협회",
    "na": "국회", "nts": "국세청", "molit": "국토부",
}
GRADE = {
    "legislation": "1차근접(입법)", "case_law": "1차근접(판례)",
    "admin_notice": "1차근접(행정예고·감독규정)",
    "press": "2차(보도자료·언론)", "other": "기타",
}


def _slug(s: str, n: int = 36) -> str:
    s = re.sub(r"[\\/:*?\"<>|\[\]]", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n].strip().replace(" ", "_") or "무제"


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python3 promote_news.py <인용키>   예) fsc/4139")
        return 1
    key = sys.argv[1].strip()
    source, item_key = (key.split("/", 1) + [None])[:2] if "/" in key else (None, key)

    conn = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    item = None
    if source and item_key:
        item = conn.execute(
            "SELECT * FROM items WHERE source=? AND source_item_key=?",
            (source, item_key),
        ).fetchone()
    if item is None:
        item = conn.execute("SELECT * FROM items WHERE id=?", (key,)).fetchone()
    if item is None:
        print(f"항목을 찾을 수 없음: {key}")
        return 1

    contents = conn.execute(
        """SELECT label, extracted_text FROM item_content
           WHERE item_id=? AND extraction_status='success'
             AND extracted_text IS NOT NULL AND extracted_text != ''""",
        (item["id"],),
    ).fetchall()

    src = item["source"]
    src_ko = SOURCE_KO.get(src, src)
    grade = GRADE.get(item["category"], item["category"] or "기타")
    title = item["title"] or "(제목없음)"
    skey = item["source_item_key"] or item["id"]
    cite = f"{src}/{skey}"

    body = []
    if item["raw_text"] and item["raw_text"].strip():
        body.append("## 본문\n\n" + item["raw_text"].strip())
    for c in contents:
        body.append(f"## 첨부: {c['label'] or '-'}\n\n" + (c["extracted_text"] or "").strip())
    if not body and item["summary"]:
        body.append("## 요약 (LLM 생성 · 참고용 · 인용금지)\n\n" + item["summary"].strip())
    if not body:
        body.append("_(추출된 본문·첨부 텍스트 없음 — url 원문 참조)_")

    fm = (
        "---\n"
        f"제목: {title}\n"
        f"출처: {src_ko} ({src})\n"
        f"카테고리: {item['category'] or '-'}\n"
        f"등급: {grade}\n"
        f"발표일: {item['published_at'] or '-'}\n"
        f"중요도: {item['importance'] or '-'}\n"
        f"url: {item['url'] or '-'}\n"
        f"인용키: {cite}\n"
        "태그:\n"
        '  - "#정부동향"\n'
        f'  - "#{src}"\n'
        "용도: 검증자료(근거). J-ID가 인용한 정부동향 항목의 영구 보존본.\n"
        "---\n\n"
        f"> ⚠️ 등급 {grade}. 1차근접(법령·판례·감독규정 원문)은 신뢰, "
        "2차(보도자료·언론)는 사실·입장이지 규범 단정 금지. 정밀 인용 시 url 원문 대조. "
        "추출본은 kordoc 파싱 결과로 표·서식이 흐트러질 수 있음.\n\n"
        f"# {title}\n\n"
    )
    note = fm + "\n\n".join(body) + "\n"

    os.makedirs(VAULT_DIR, exist_ok=True)
    fname = unicodedata.normalize("NFC", f"정부동향_{src}-{skey}_{_slug(title)}.md")
    path = os.path.join(VAULT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(note)

    wikilink = f"[[{fname[:-3]}]]"
    print(f"승격 완료: {path}")
    print("J-ID 근거 인용줄(복사용):")
    print(f"  - [정부동향] {cite} — {title} (등급: {grade}) {wikilink}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
