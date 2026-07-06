#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_court_index.py — 법원 판결 백로그를 vault '판례(근거)' 계층에 반영.

법원 클리핑(source=scourt OR category=case_law, 읽기전용 DB)을 다룬다.
  - 인덱스:    백로그 전부를 법영역별 요지 인덱스 1장으로 반영(전부 반영, 저비용).
  - 핵심승격:  주요판결(high) ∪ 도메인관련(보험·금융·컴플라이언스)만 개별 보존본으로 고정.
  - pull승격:  그 밖 판례는 인용 시점에 개별 승격.

판례는 토픽으로 거르지 않는다(일반민사·조세·가사·노동·형사 모두 법무 근거). 다만 개별
'선승격'은 클러터 방지를 위해 high∪도메인으로 한정하고, 나머지는 인덱스+pull로 커버.

  python3 build_court_index.py                 # 인덱스 생성 + 요약 출력
  python3 build_court_index.py --keys          # 핵심(high∪도메인) 인용키만 출력
  python3 build_court_index.py --promote-core  # 핵심 전부를 판례(근거)에 개별 승격
  python3 build_court_index.py --promote KEY   # 특정 인용키 1건 승격(예: scourt/case:413980)

DB는 읽기전용. vault 판례(근거)/에 .md를 쓴다(요지 = 1차근접 판례, 판결문 전문 아님).
"""
import os
import re
import sqlite3
import sys

DB = os.environ.get(
    "NEWS_DB_PATH",
    "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping/data/briefing.sqlite3",
)
CASE_DIR = os.environ.get(
    "CASE_PROMOTE_DIR",
    "/Users/nsss/obsidian/nss/08. 자원·참고자료/법령자료/판례(근거)",
)
OUT = os.path.join(CASE_DIR, "_법원주요판결_인덱스_2026H1.md")

# 법영역 분류(제목+본문 키워드, 위에서부터 우선) — 네비게이션용.
DOMAINS = [
    ("금융·자본시장", ["자본시장", "금융투자", "CFD", "차액결제", "시세조종", "펀드", "증권", "파생상품", "장외파생", "여신", "대부업"]),
    ("보험",          ["보험업법", "책임보험", "보험금", "손해보험", "공제금", "보험계약", "자기차량", "중복보험"]),
    ("신용·개인정보",  ["개인정보", "신용정보", "가명정보"]),
    ("공정거래",       ["공정거래", "과징금", "부당지원", "일감", "고객유인", "시정명령및과징금"]),
    ("자금세탁·제재",  ["자금세탁", "특정금융정보", "테러자금"]),
    ("조세",          ["상속세", "양도소득세", "법인세", "부과처분취소", "익금", "과세표준", "납세", "경정"]),
    ("가사·상속",      ["유류분", "유언", "기여분", "상속", "가족관계", "출생", "친생"]),
    ("노동",          ["임금", "소정근로", "퇴직", "근로자", "택시", "해고", "노동조합", "산업재해", "산재"]),
    ("형사",          ["범인도피", "공소사실", "피고인", "형의", "양형", "방조", "범죄단체"]),
    ("지식재산",       ["특허", "진보성", "상표", "디자인권", "저작"]),
    ("행정·기타",      ["처분 취소", "환수", "참여배제", "분담금", "급수공사", "보상금", "수용재결"]),
]

# 도메인 '직접관련' 플래그 — 제목(쟁점) 기준 핵심어만(엄격). 본문매칭은 과포함이라 제외.
FIN_TITLE = [
    "자본시장", "금융투자", "CFD", "차액결제", "시세조종", "불공정거래", "내부자",
    "보험", "책임보험", "보험금", "공제금",
    "개인정보", "신용정보", "자금세탁", "특정금융",
    "과징금", "공정거래", "고객유인", "부당지원", "일감", "약관의 규제",
    "금융소비자", "지배구조", "대부업",
]
CASE_NO = re.compile(r"20\d\d[가-힣]{1,3}\d+")


def is_fin(title):
    return any(k in (title or "") for k in FIN_TITLE)


def classify(title, body):
    # 제목만으로 분류한다. case_law의 raw_text는 판결요지가 아니라 법원도서관
    # 페이지 보일러플레이트(푸터 '개인정보처리방침' 등)라 body를 섞으면 case_law
    # 전건이 '개인정보'에 걸려 신용·개인정보로 오분류됨. 제목에 사건명+쟁점(<...>)이
    # 이미 담겨 있어 제목만으로 충분. (body 인자는 호출부 호환을 위해 유지)
    t = title or ""
    for name, kws in DOMAINS:
        if any(k in t for k in kws):
            return name
    return "일반민사"


def dedup_key(title, body):
    m = CASE_NO.search(body or "")
    if m:
        return "C:" + m.group(0)
    t = re.sub(r"^\s*\d+\.\s*", "", title or "")
    return "T:" + re.sub(r"\s+", " ", t).strip()[:40]


def clean_title(title):
    return re.sub(r"\s+", " ", re.sub(r"^\s*\d+\.\s*", "", title or "")).strip()


def clean_body(text):
    """법원도서관 페이지 chrome(메뉴·푸터) 제거 — 판시사항/이유 요지만 남김.
    마커 미발견 시 원문 유지(과삭제 방지)."""
    if not text:
        return "(본문 없음)"
    t = re.sub(r"\s+", " ", text).strip()
    for head in ["법원 대법원 선고일자", "법원 대법원 선고일", "◇", "선고일자"]:
        i = t.find(head)
        if i != -1:
            t = t[i:]
            break
    for tail in ["첨부파일 원문", "목록으로 첨부", "이전섹션으로", "첨부파일표", "개인정보처리방침 영상정보", "COPYRIGHT", "바구니 담기"]:
        j = t.find(tail)
        if j != -1:
            t = t[:j]
            break
    return t.strip() or "(본문 없음)"


def load_items():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT id, source, source_item_key, category, title, url,
                  COALESCE(NULLIF(published_at,''), first_seen_at) AS d,
                  COALESCE(NULLIF(importance,''),'-') AS imp, raw_text, attachments_json
           FROM items
           WHERE source='scourt' OR category='case_law'
           ORDER BY d DESC, id DESC"""
    ).fetchall()
    # 사건번호 dedup (case_law 우선 보존)
    seen = {}
    for r in rows:
        k = dedup_key(r["title"], r["raw_text"])
        prev = seen.get(k)
        if prev is None or (prev["category"] != "case_law" and r["category"] == "case_law"):
            seen[k] = r
    return sorted(seen.values(), key=lambda r: (r["d"] or ""), reverse=True), con


def is_core(r):
    # 핵심 선승격 = 주요판결(high) ∪ 도메인관련(제목)
    return r["imp"] == "high" or is_fin(r["title"])


def safe_name(s):
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def promote_one(con, key):
    src, sik = key.split("/", 1)
    r = con.execute(
        "SELECT source, source_item_key, category, title, url, "
        "COALESCE(NULLIF(published_at,''), first_seen_at) AS d, "
        "COALESCE(NULLIF(importance,''),'-') AS imp, raw_text, attachments_json "
        "FROM items WHERE source=? AND source_item_key=? LIMIT 1",
        (src, sik),
    ).fetchone()
    if not r:
        print(f"  [skip] DB 미존재: {key}")
        return None
    title = clean_title(r["title"])
    grade = "1차근접(판례 요지)" if r["category"] == "case_law" else "1차근접(판례 보도요지)"
    fname = f"판례_{safe_name(src + '-' + sik)}_{safe_name(title)[:34]}.md"
    path = os.path.join(CASE_DIR, fname)
    body = [
        "---",
        f"제목: {title}",
        "출처: 대법원 (scourt)",
        f"category: {r['category']}",
        f"등급: {grade}",
        f"선고·게시: {(r['d'] or '')[:10]}",
        f"중요도: {r['imp']}",
        f"url: {r['url']}",
        f"인용키: {src}/{sik}",
        "태그:\n  - \"#판례\"\n  - \"#scourt\"",
        "용도: 검증자료(근거·사법). J-ID가 인용한/인용가능한 판례 요지 보존본.",
        "---",
        "",
        "> ⚠️ 등급 1차근접(판례). 단 본문은 **판결속보 요지/보도요지**이며 판결문 전문이 아님. 정밀 인용 시 종합법률정보 판결문 원문 대조.",
        "",
        f"# {title}",
        "",
        "## 요지",
        "",
        clean_body(r["raw_text"]),
    ]
    os.makedirs(CASE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    link = fname[:-3]
    cite = f"  - [판례] {src}/{sik} — {title} (등급: {grade}) [[{link}]]"
    return cite


def main():
    args = sys.argv[1:]
    items, con = load_items()

    if "--keys" in args:
        for r in items:
            if is_core(r):
                print(f"{r['source']}/{r['source_item_key']}")
        return

    if "--promote" in args:
        key = args[args.index("--promote") + 1]
        cite = promote_one(con, key)
        if cite:
            print("승격 완료. J-ID 근거 인용줄(복사용):")
            print(cite)
        return

    if "--promote-core" in args:
        core = [r for r in items if is_core(r)]
        print(f"핵심 선승격 대상 {len(core)}건 → 판례(근거)/")
        n = 0
        for r in core:
            cite = promote_one(con, f"{r['source']}/{r['source_item_key']}")
            if cite:
                n += 1
        print(f"승격 완료 {n}건. (인용줄은 인덱스/개별파일 frontmatter 참조)")
        return

    # 기본: 인덱스 생성
    buckets = {}
    core_keys = []
    for r in items:
        dom = classify(r["title"], r["raw_text"])
        key = f"{r['source']}/{r['source_item_key']}"
        core = is_core(r)
        buckets.setdefault(dom, []).append((r["d"][:10] if r["d"] else "-", r["imp"], r["category"], r["title"], key, is_fin(r["title"]), core))
        if core:
            core_keys.append(key)

    fin_first = ["금융·자본시장", "보험", "신용·개인정보", "공정거래", "자금세탁·제재"]
    rest = ["조세", "가사·상속", "노동", "형사", "지식재산", "행정·기타", "일반민사"]
    order = fin_first + rest

    L = ["---", "제목: 법원 주요판결 인덱스 (2026 상반기 백로그)",
         "태그:\n  - \"#판례\"\n  - \"#인덱스\"",
         "용도: 검증자료(근거·사법) 인덱스. 법원 판결 백로그 전부를 요지로 반영. 핵심은 개별 승격, 그 외는 인용 시 pull.",
         "---", "",
         "# 법원 주요판결 인덱스 (2026 상반기 백로그)", "",
         f"> 자동생성(`build_court_index.py`) · scourt·case_law · 총 {len(items)}건(사건번호 중복제거) · 2026-02~06",
         "> ⚠️ **판결속보 요지**(1차근접 판례)이며 판결문 전문 아님. 정밀 인용 시 판결문 원문 대조.",
         f"> ★=도메인 직접관련 · ⬢=핵심 선승격(주요판결 high ∪ 도메인) 대상 {len(core_keys)}건 → `판례(근거)/`에 개별 보존.",
         "> 그 외는 인용 시 `python3 build_court_index.py --promote <인용키>`로 승격.", ""]

    for dom in order:
        recs = buckets.get(dom)
        if not recs:
            continue
        star = " ★" if dom in fin_first else ""
        L.append(f"\n## {dom}{star}  ({len(recs)}건)\n")
        L.append("| 날짜 | 중요 | cat | 핵심 | 사건명·쟁점 | 인용키 |")
        L.append("|------|------|-----|------|-------------|--------|")
        for d, imp, cat, title, key, fin, core in recs:
            tt = re.sub(r"\s+", " ", title or "").replace("|", "／")[:70]
            mark = ("★" if fin else "") + ("⬢" if core else "")
            L.append(f"| {d} | {imp} | {cat} | {mark} | {tt} | `{key}` |")

    os.makedirs(CASE_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"인덱스 작성: {OUT}")
    print(f"총 {len(items)}건(중복제거) · 핵심 선승격 대상(high∪도메인) {len(core_keys)}건")


if __name__ == "__main__":
    main()
