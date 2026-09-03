#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news-search MCP 서버 — 정부동향(입법·사법·행정) 클리핑 DB를 검증자료 채널로 노출.

판단(J-ID)과 분리된 '근거(검증자료)' 전용 채널이다. 판단 작성 시 vault_search(J-ID)와
나란히 호출해, 관련 법령 제·개정·판례·감독규정·보도자료를 FTS(부분문자열)로 검색한다.

설계 원칙
  - stdlib만 사용(브리핑 패키지·임베딩 불필요). DB는 읽기전용으로 열어 파이프라인 쓰기와 무충돌.
  - 결과에 등급(1차 근접/2차)·인용키를 부착 → J-ID `근거:` 인용 규약과 직접 연결.
  - MCP stdio(JSON-RPC, 줄단위)를 직접 구현. stdout=JSON-RPC만, 로그=stderr.
"""
import json
import os
import re
import sqlite3
import sys

DB_PATH = os.environ.get(
    "NEWS_DB_PATH",
    "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping/data/briefing.sqlite3",
)

SERVER_INFO = {"name": "news-search", "version": "1.1.0"}

# 소스코드 → 한글명(표시용). 미등록 코드는 그대로 노출.
SOURCE_KO = {
    "fsc": "금융위", "fss": "금감원", "kftc": "공정위", "moel": "고용노동부",
    "scourt": "대법원", "pipc": "개인정보위", "nhrck": "인권위", "knia": "손해보험협회",
    "na": "국회", "nts": "국세청", "molit": "국토부",
}

# 카테고리 → 자료 등급(인용 시 규범/사실 분리).
GRADE = {
    "legislation": "1차근접(입법)", "case_law": "1차근접(판례)",
    "admin_notice": "1차근접(행정예고·감독규정)",
    "press": "2차(보도자료·언론)", "other": "기타",
}

TOOLS = [{
    "name": "news_search",
    "description": (
        "정부동향(입법·사법·행정) 클리핑 DB를 전문검색(FTS, 부분문자열)으로 조회한다. "
        "금융위·금감원·공정위·대법원·개인정보위·고용노동부 등의 보도자료·법령 제개정·판례·감독규정 "
        "및 그 첨부 원문(kordoc 추출)을 대상으로 한다. "
        "이는 판단(J-ID)이 아니라 '검증자료(근거)' 채널이다 — 법무 판단 작성 시 vault_search(J-ID)와 "
        "나란히 호출해 최신 규제·정책 근거를 확인하라. 결과의 [등급]을 존중할 것"
        "(1차근접=법령/판례/감독규정 원문은 신뢰, 2차=보도자료/언론은 사실·입장이지 규범 단정 금지)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색어(3자 이상 부분문자열 매칭). 예: '책무구조도', '가명정보 결합'"},
            "top_k": {"type": "integer", "description": "반환 개수(기본 8)"},
            "importance": {"type": "array", "items": {"type": "string"},
                           "description": "중요도 필터(예: ['high','medium']). 생략 시 전체"},
            "category": {"type": "array", "items": {"type": "string"},
                         "description": "카테고리 필터(legislation·case_law·admin_notice·press·other)"},
            "source": {"type": "array", "items": {"type": "string"},
                       "description": "소스 필터(예: ['fsc','fss','scourt'])"},
            "since": {"type": "string", "description": "이 날짜 이후만(YYYY-MM-DD)"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "법률 태그 필터(예: ['보험업법','설명의무']). #은 생략 가능"},
            "tag_types": {"type": "array", "items": {"type": "string"},
                          "description": "태그 유형 필터(law·provision·issue·legal_relation 등)"},
            "tag_mode": {"type": "string", "enum": ["any", "all"],
                         "description": "여러 태그 중 하나(any, 기본) 또는 모두(all)"},
        },
        "required": [],
    },
}]

_conn = None


def _db():
    global _conn
    if _conn is None:
        # 읽기전용 — 파이프라인 쓰기와 충돌 없음
        _conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        _conn.row_factory = sqlite3.Row
    return _conn


def _build_match(query: str) -> str:
    """trigram FTS용 MATCH 문자열. 공백 분리 후 3자 이상 토큰을 인용부호로 묶어 AND.
    특수문자에 의한 FTS 구문오류를 막고 부분문자열 매칭을 보장한다."""
    terms = [t for t in re.split(r"\s+", query.strip()) if len(t) >= 3]
    if not terms:
        terms = [query.strip()]
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _search(args: dict) -> str:
    query = (args.get("query") or "").strip()
    tags = [str(value).lstrip("#").strip() for value in (args.get("tags") or []) if str(value).strip()]
    if not query and not tags:
        return "검색어 또는 법률 태그가 필요함."
    k = int(args.get("top_k") or 8)
    has_tag_schema = _db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='item_tags'"
    ).fetchone() is not None
    if tags and not has_tag_schema:
        return "법률 태그 인덱스가 아직 초기화되지 않음. tag-backfill을 먼저 실행할 것."

    where = []
    params = {"k": k}
    if query:
        where.append("content_fts MATCH :q")
        params["q"] = _build_match(query)

    def _in(col, key, vals):
        vals = [v for v in (vals or []) if v]
        if not vals:
            return
        names = []
        for i, v in enumerate(vals):
            p = f"{key}{i}"
            params[p] = v
            names.append(f":{p}")
        where.append(f"i.{col} IN ({','.join(names)})")

    _in("importance", "imp", args.get("importance"))
    _in("category", "cat", args.get("category"))
    _in("source", "src", args.get("source"))
    if args.get("since"):
        params["since"] = args["since"]
        where.append("substr(i.published_at,1,10) >= :since")

    if tags:
        tag_names = []
        for i, value in enumerate(tags):
            key = f"tag{i}"
            params[key] = value
            tag_names.append(f":{key}")
        tag_type_clause = ""
        tag_types = [str(value).strip() for value in (args.get("tag_types") or []) if str(value).strip()]
        if tag_types:
            type_names = []
            for i, value in enumerate(tag_types):
                key = f"tagtype{i}"
                params[key] = value
                type_names.append(f":{key}")
            tag_type_clause = f"AND tr2.tag_type IN ({','.join(type_names)})"
        having = ""
        if args.get("tag_mode") == "all":
            params["tag_count"] = len(set(tags))
            having = "HAVING COUNT(DISTINCT tr2.canonical) = :tag_count"
        where.append(
            f"""i.id IN (
              SELECT it2.item_id
              FROM item_tags it2
              JOIN tag_registry tr2 ON tr2.tag_id = it2.tag_id
              WHERE (tr2.canonical IN ({','.join(tag_names)})
                     OR LTRIM(tr2.hashtag, '#') IN ({','.join(tag_names)}))
                {tag_type_clause}
              GROUP BY it2.item_id
              {having}
            )"""
        )

    from_sql = (
        "FROM content_fts JOIN items i ON i.id = content_fts.item_id"
        if query else "FROM items i"
    )
    body_snip = (
        "snippet(content_fts, 2, '《', '》', ' … ', 18)" if query else "''"
    )
    attach_snip = (
        "snippet(content_fts, 3, '《', '》', ' … ', 18)" if query else "''"
    )
    order_sql = "rank" if query else "COALESCE(i.published_at, '0000-00-00') DESC"
    legal_tags_sql = (
        """(SELECT GROUP_CONCAT(tr.hashtag, ' ')
             FROM item_tags it JOIN tag_registry tr ON tr.tag_id = it.tag_id
             WHERE it.item_id = i.id)"""
        if has_tag_schema else "NULL"
    )
    sql = f"""
        SELECT i.id, i.source, i.category, i.title, i.url,
               i.published_at, i.importance, i.source_item_key,
               {body_snip} AS body_snip,
               {attach_snip} AS attach_snip,
               {legal_tags_sql} AS legal_tags
        {from_sql}
        WHERE {' AND '.join(where)}
        ORDER BY {order_sql}
        LIMIT :k
    """
    rows = _db().execute(sql, params).fetchall()
    if not rows:
        label = query or "태그 " + ", ".join(tags)
        return f"질의: {label}\n검색결과 0건. (검색어·태그 또는 필터를 완화해 볼 것)"

    blocks = []
    for n, r in enumerate(rows, 1):
        src = SOURCE_KO.get(r["source"], r["source"])
        grade = GRADE.get(r["category"], r["category"] or "기타")
        imp = (r["importance"] or "-").upper()
        snip = (r["attach_snip"] or "").strip() or (r["body_snip"] or "").strip()
        snip = " ".join(snip.split())[:400]
        cite = r["source_item_key"] or r["id"]
        tag_line = f"\n  법률태그: {r['legal_tags']}" if r["legal_tags"] else ""
        blocks.append(
            f"[{n}] {src} · {r['published_at'] or '-'} · 중요도 {imp} · [등급:{grade}]\n"
            f"  제목: {r['title']}\n"
            f"  발췌: {snip}\n"
            f"  URL: {r['url'] or '-'}{tag_line}\n"
            f"  인용키: {r['source']}/{cite}"
        )
    return (
        f"질의: {query or '태그 ' + ', '.join(tags)}  (검증자료 채널 · 정부동향)\n"
        f"검색결과 {len(rows)}건 ({'FTS rank순' if query else '최신순'}):\n\n"
        + "\n\n".join(blocks)
    )


def _send(_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": _id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        _id = req.get("id")

        if method == "initialize":
            pv = req.get("params", {}).get("protocolVersion", "2024-11-05")
            _send(_id, {
                "protocolVersion": pv,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _send(_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {}) or {}
            if name == "news_search":
                try:
                    text = _search(args)
                    _send(_id, {"content": [{"type": "text", "text": text}]})
                except Exception as e:  # noqa: BLE001
                    _send(_id, {"content": [{"type": "text", "text": f"검색 오류: {e}"}],
                                "isError": True})
            else:
                _send(_id, error={"code": -32601, "message": f"알 수 없는 도구: {name}"})
        elif method == "ping":
            _send(_id, {})
        elif _id is not None:
            _send(_id, error={"code": -32601, "message": f"알 수 없는 메서드: {method}"})


if __name__ == "__main__":
    main()
