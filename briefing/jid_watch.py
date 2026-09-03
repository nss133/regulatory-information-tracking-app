"""판단자산(J-ID) 영향 감시 — 규제동향이 기존 법무판단의 근거를 건드리면 플래그.

vault의 판단 파일(J-*.md) frontmatter `근거:` 항목에서 법령·조문·판례번호를
인덱싱해 두고, 당일 발송 대상 중 규범 변화 신호가 있는 항목(입법예고·개정·판결 등)이
그 근거를 언급하면 해당 J-ID를 재검토 후보로 표시한다.

오탐 통제:
- 규범 변화 신호가 없는 일반 보도자료는 매칭하지 않음(TRIGGER_* 게이트)
- 다인용 법령(예: 개인정보 보호법)은 법령 단위 매치 시 건수만 요약하고,
  조문까지 일치할 때만 개별 J-ID를 나열
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from briefing.utils import now_iso

# ── 트리거 게이트: 규범 변화 신호 ──────────────────────────────
TRIGGER_CATEGORIES = {"legislation", "admin_notice", "case_law"}
TRIGGER_TITLE_KEYWORDS = [
    "개정", "제정", "폐지", "입법예고", "규정변경예고", "변경예고",
    "시행령", "시행규칙", "본회의", "의결", "판결", "선고", "재결",
]

# 법령 단위 매치만 있을 때(조문 불일치) 개별 나열을 포기하는 인용 건수 임계
LAW_SUMMARY_THRESHOLD = 10
# 개별 나열 시 최대 표시 건수
MAX_LISTED_JIDS = 5

# 약칭 ↔ 정식명 (공백 제거 후 비교). key=대표명, values=변형들
_BASE_LAW_ALIASES: dict[str, list[str]] = {
    "자본시장법": ["자본시장과금융투자업에관한법률"],
    "공정거래법": ["독점규제및공정거래에관한법률"],
    "금융소비자보호법": ["금융소비자보호에관한법률", "금소법"],
    "신용정보법": ["신용정보의이용및보호에관한법률", "신정법"],
    "금융회사지배구조법": ["금융회사의지배구조에관한법률", "지배구조법"],
    "퇴직급여법": ["근로자퇴직급여보장법", "근로자퇴직급여보장법"],
    "노동조합법": ["노동조합및노동관계조정법"],
}

# 시행령·시행규칙 변형 자동 파생 — "자본시장법 시행령" ↔ "자본시장과 금융투자업에
# 관한 법률 시행령"이 별개 키로 갈라지면 매칭이 누락됨(2026-07-22 검증에서 확인)
_LAW_ALIASES: dict[str, list[str]] = {}
for _canon, _variants in _BASE_LAW_ALIASES.items():
    _LAW_ALIASES[_canon] = _variants
    for _sfx in ("시행령", "시행규칙"):
        _LAW_ALIASES[f"{_canon} {_sfx}"] = [f"{v} {_sfx}" for v in _variants]

# 법령명으로 인정하는 접미사 (근거 항목에서 가이드·보도자료 등을 걸러냄)
_LAW_SUFFIXES = ("법", "법률", "시행령", "시행규칙", "규정", "규칙", "고시", "감독규정")

_CASE_NO_RE = re.compile(r"(?:19|20)\d{2}\s*[가-힣]{1,4}\s*\d{1,7}")
_COURT_HINT_RE = re.compile(r"대법원|법원|헌법재판소|헌재")
# 근거 항목: "법령명 §5-7 ③" / "법령명 §44조의2"
_CITE_ARTICLE_RE = re.compile(r"§\s*([0-9]+(?:-[0-9]+)?(?:조의[0-9]+|의[0-9]+)?)")
# 본문 텍스트: "제733조", "제44조의2", "제5-7조"
_TEXT_ARTICLE_RE = re.compile(r"제\s*([0-9]+(?:-[0-9]+)?)\s*조(?:\s*의\s*([0-9]+))?")


def _norm(s: str) -> str:
    return re.sub(r"[\s「」『』\"']", "", s)


def _norm_article(a: str) -> str:
    return a.replace("조의", "의")


@dataclass
class JidRef:
    jid: str          # J-2026-0004
    stem: str         # 파일명(확장자 제외) — vault 링크용
    articles: set[str] = field(default_factory=set)


@dataclass
class CitationIndex:
    # 법령(정규화명) → {jid → JidRef(조문 집합)}
    law_to_jids: dict[str, dict[str, JidRef]] = field(default_factory=dict)
    # 판례번호(정규화) → {jid → JidRef}
    case_to_jids: dict[str, dict[str, JidRef]] = field(default_factory=dict)
    # 정규화명 → 표시명
    law_display: dict[str, str] = field(default_factory=dict)
    # 정규화 변형(약칭 포함) → 정규화 대표명
    law_lookup: dict[str, str] = field(default_factory=dict)
    n_files: int = 0


def _parse_citation_entry(entry: str) -> tuple[str | None, str | None, list[str]]:
    """근거 한 줄 → (법령 정규화명, 조문, 판례번호 목록). 해당 없으면 None."""
    cases = []
    if _COURT_HINT_RE.search(entry):
        cases = [_norm(m) for m in _CASE_NO_RE.findall(entry)]
        if cases:
            return None, None, cases

    m = _CITE_ARTICLE_RE.search(entry)
    if m:
        name_part = entry[: m.start()].strip()
        article = _norm_article(m.group(1))
    else:
        name_part = entry.strip()
        article = None

    # 괄호 주석·태그 제거 후 법령명 판정
    name_part = re.sub(r"[\(\[（].*$", "", name_part).strip()
    name = _norm(name_part)
    if len(name) < 3 or not name.endswith(_LAW_SUFFIXES):
        return None, None, []
    return name, article, []


def build_citation_index(judgments_dir: str | Path) -> CitationIndex:
    idx = CitationIndex()
    for path in sorted(Path(judgments_dir).glob("J-*.md")):
        try:
            text = path.read_text()
        except Exception:
            continue
        idx.n_files += 1
        jid = path.stem.split("_")[0]
        stem = path.stem

        # frontmatter의 `근거:` 리스트만 추출 (현재 키 추적)
        in_fm = False
        current_key = None
        for line in text.splitlines():
            if line.strip() == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if not in_fm:
                continue
            key_m = re.match(r"^([^\s:][^:]*):", line)
            if key_m:
                current_key = key_m.group(1).strip()
                continue
            if current_key != "근거":
                continue
            item_m = re.match(r"^\s+-\s+(.*)$", line)
            if not item_m:
                continue
            law, article, cases = _parse_citation_entry(item_m.group(1))
            if law:
                ref = idx.law_to_jids.setdefault(law, {}).setdefault(jid, JidRef(jid, stem))
                if article:
                    ref.articles.add(article)
                idx.law_display.setdefault(law, law)
            for c in cases:
                idx.case_to_jids.setdefault(c, {}).setdefault(jid, JidRef(jid, stem))

    # 약칭 lookup 구성: 대표명·변형 모두 인덱스에 존재하는 대표명으로 수렴
    for canon, variants in _LAW_ALIASES.items():
        canon_n = _norm(canon)
        # 변형으로 인덱싱된 인용을 대표명으로 병합
        for v in variants:
            v_n = _norm(v)
            if v_n in idx.law_to_jids and v_n != canon_n:
                merged = idx.law_to_jids.setdefault(canon_n, {})
                for jid, ref in idx.law_to_jids.pop(v_n).items():
                    if jid in merged:
                        merged[jid].articles |= ref.articles
                    else:
                        merged[jid] = ref
                idx.law_display.pop(v_n, None)
        if canon_n in idx.law_to_jids:
            idx.law_display[canon_n] = canon
            idx.law_lookup[canon_n] = canon_n
            for v in variants:
                idx.law_lookup[_norm(v)] = canon_n
    # 나머지 법령은 자기 자신으로 lookup
    for law in idx.law_to_jids:
        idx.law_lookup.setdefault(law, law)
    return idx


@dataclass
class ImpactMatch:
    match_level: str
    priority: int
    evidence_text: str
    law_name: str | None = None
    article: str | None = None
    case_number: str | None = None
    jid: str | None = None
    jid_stem: str | None = None
    related_jid_count: int = 0


@dataclass
class ImpactAlert:
    item_id: str
    item_title: str
    item_url: str
    lines: list[str]        # 이메일용(플레인 텍스트)
    vault_lines: list[str]  # vault 로그용([[위키링크]])
    item_content_hash: str = ""
    matches: list[ImpactMatch] = field(default_factory=list)


# 법령명 등장 위치 뒤 이 거리(정규화 문자 수) 안의 "제N조"만 그 법령의 조문으로 인정.
# 항목 전체에서 조문을 모으면 다른 법령의 조문이 교차 결합됨 — 판례 요지 묶음에서
# 특별검사법 제2조가 개인정보보호법 §2로 오귀속된 사례(2026-07-22 검증).
# 창을 좁게 잡아 놓친 조문은 법령 단위 요약으로 폴백되므로 과소매칭 쪽이 안전함.
_ARTICLE_WINDOW = 100


def _articles_near(norm_text: str, variant: str) -> set[str]:
    out: set[str] = set()
    start = norm_text.find(variant)
    while start >= 0:
        end = start + len(variant)
        for num, ui in _TEXT_ARTICLE_RE.findall(norm_text[end : end + _ARTICLE_WINDOW]):
            out.add(f"{num}의{ui}" if ui else num)
        start = norm_text.find(variant, end)
    return out


def find_impacts(idx: CitationIndex, pending) -> list[ImpactAlert]:
    alerts: list[ImpactAlert] = []
    for it in pending:
        category = getattr(it, "category", "") or ""
        # 수집 중단된 법원도서관 개별 판결은 대법원 판례속보와 중복된다.
        # 메일 노출 정책과 동일하게 영향 큐에서도 제외한다.
        if getattr(it, "source", "") == "scourt" and category == "case_law":
            continue
        title = it.title or ""
        if category not in TRIGGER_CATEGORIES and not any(
            kw in title for kw in TRIGGER_TITLE_KEYWORDS
        ):
            continue

        text = f"{title}\n{it.raw_text or ''}"
        norm_text = _norm(text)

        lines: list[str] = []
        vault_lines: list[str] = []
        matches: list[ImpactMatch] = []

        # ── 법령 매치 (조문은 법령명 근접 범위에서만 수집) ──
        matched_canons: dict[str, set[str]] = {}
        for variant, canon in idx.law_lookup.items():
            if variant in norm_text:
                matched_canons.setdefault(canon, set()).update(
                    _articles_near(norm_text, variant)
                )
        for canon in sorted(matched_canons):
            refs = idx.law_to_jids.get(canon, {})
            if not refs:
                continue
            display = idx.law_display.get(canon, canon)
            item_articles = matched_canons[canon]
            hit_articles_by_jid = {
                jid: ref.articles & item_articles
                for jid, ref in refs.items()
            }
            exact = [(jid, refs[jid], arts) for jid, arts in hit_articles_by_jid.items() if arts]
            if exact:
                for position, (jid, ref, arts) in enumerate(sorted(exact)):
                    art_s = ", ".join(f"§{a}" for a in sorted(arts))
                    evidence = f"{display} {art_s} 일치 → {ref.stem}"
                    matches.append(
                        ImpactMatch(
                            match_level="exact_article",
                            priority=1,
                            law_name=display,
                            article=", ".join(sorted(arts)),
                            jid=jid,
                            jid_stem=ref.stem,
                            related_jid_count=1,
                            evidence_text=evidence,
                        )
                    )
                    if position < MAX_LISTED_JIDS:
                        lines.append(evidence)
                        vault_lines.append(f"{display} {art_s} 일치 → [[{ref.stem}]]")
                if len(exact) > MAX_LISTED_JIDS:
                    lines.append(f"  … 외 조문 일치 {len(exact) - MAX_LISTED_JIDS}건")
                    vault_lines.append(f"  … 외 조문 일치 {len(exact) - MAX_LISTED_JIDS}건")
            elif len(refs) > LAW_SUMMARY_THRESHOLD:
                msg = f"{display} — 인용 판단 {len(refs)}건 (조문 미특정, 개별 확인 필요)"
                lines.append(msg)
                vault_lines.append(msg)
                matches.append(
                    ImpactMatch(
                        match_level="law_only",
                        priority=3,
                        law_name=display,
                        related_jid_count=len(refs),
                        evidence_text=msg,
                    )
                )
            else:
                listed = sorted(refs.values(), key=lambda r: r.jid)[:MAX_LISTED_JIDS]
                rest = len(refs) - len(listed)
                suffix = f" 외 {rest}건" if rest > 0 else ""
                msg = f"{display} → " + ", ".join(r.stem for r in listed) + suffix
                lines.append(msg)
                vault_lines.append(
                    f"{display} → " + ", ".join(f"[[{r.stem}]]" for r in listed) + suffix
                )
                matches.append(
                    ImpactMatch(
                        match_level="law_only",
                        priority=3,
                        law_name=display,
                        related_jid_count=len(refs),
                        evidence_text=msg,
                    )
                )

        # ── 판례 매치 (항상 고신호) ──
        for case_no in sorted(set(_norm(m) for m in _CASE_NO_RE.findall(text))):
            refs = idx.case_to_jids.get(case_no, {})
            for ref in sorted(refs.values(), key=lambda r: r.jid):
                evidence = f"판례 {case_no} 인용 → {ref.stem}"
                lines.append(evidence)
                vault_lines.append(f"판례 {case_no} 인용 → [[{ref.stem}]]")
                matches.append(
                    ImpactMatch(
                        match_level="exact_case",
                        priority=1,
                        case_number=case_no,
                        jid=ref.jid,
                        jid_stem=ref.stem,
                        related_jid_count=1,
                        evidence_text=evidence,
                    )
                )

        if lines:
            alerts.append(
                ImpactAlert(
                    item_id=it.id,
                    item_title=title,
                    item_url=getattr(it, "url", "") or "",
                    lines=lines,
                    vault_lines=vault_lines,
                    item_content_hash=getattr(it, "content_hash", "") or "",
                    matches=matches,
                )
            )
    return alerts


_LOG_HEADER = """# 규제동향 → 판단자산 영향 감시 로그

> 자동 생성(briefing jid_watch). 매일 발송 파이프라인이 규범 변화 신호가 있는
> 수집 항목과 판단(J-ID) 근거의 겹침을 기록함. 재검토 후 처리한 항목은 취소선 처리 권장.

"""


def append_log(log_note: str | Path, alerts: list[ImpactAlert], run_date: str) -> None:
    p = Path(log_note)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_LOG_HEADER)
    with p.open("a") as f:
        f.write(f"\n## {run_date}\n\n")
        for a in alerts:
            f.write(f"- **{a.item_title}**\n")
            if a.item_url:
                f.write(f"  - 원문: {a.item_url}\n")
            for line in a.vault_lines:
                f.write(f"  - {line}\n")


@dataclass(frozen=True)
class ImpactPersistStats:
    total: int = 0
    exact: int = 0
    broad: int = 0


def _candidate_id(alert: ImpactAlert, match: ImpactMatch) -> str:
    identity = "\0".join(
        [
            alert.item_id,
            alert.item_content_hash,
            match.match_level,
            match.law_name or "",
            match.article or "",
            match.case_number or "",
            match.jid or "",
        ]
    )
    return "impact_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def persist_impact_candidates(
    conn: sqlite3.Connection,
    alerts: list[ImpactAlert],
    *,
    timezone: str,
    source_run_id: str | None = None,
) -> ImpactPersistStats:
    """현재 탐지 결과를 멱등 저장한다. 기존 사람 검수 상태는 재탐지해도 보존한다."""
    detected_at = now_iso(timezone)
    total = exact = broad = 0
    with conn:
        for alert in alerts:
            for match in alert.matches:
                candidate_id = _candidate_id(alert, match)
                conn.execute(
                    """
                    INSERT INTO jid_impact_candidates (
                      candidate_id, item_id, item_content_hash, item_title, item_url,
                      match_level, priority, law_name, article, case_number,
                      jid, jid_stem, related_jid_count, evidence_text,
                      first_detected_at, last_seen_at, source_run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                      item_title=excluded.item_title,
                      item_url=excluded.item_url,
                      related_jid_count=excluded.related_jid_count,
                      evidence_text=excluded.evidence_text,
                      last_seen_at=excluded.last_seen_at,
                      source_run_id=COALESCE(
                        excluded.source_run_id, jid_impact_candidates.source_run_id
                      ),
                      detection_count=jid_impact_candidates.detection_count + 1
                    """,
                    (
                        candidate_id,
                        alert.item_id,
                        alert.item_content_hash,
                        alert.item_title,
                        alert.item_url,
                        match.match_level,
                        match.priority,
                        match.law_name,
                        match.article,
                        match.case_number,
                        match.jid,
                        match.jid_stem,
                        match.related_jid_count,
                        match.evidence_text,
                        detected_at,
                        detected_at,
                        source_run_id,
                    ),
                )
                total += 1
                if match.match_level in {"exact_article", "exact_case"}:
                    exact += 1
                else:
                    broad += 1
    return ImpactPersistStats(total=total, exact=exact, broad=broad)


def select_impact_queue(
    conn: sqlite3.Connection,
    *,
    status: str = "unreviewed",
    exact_only: bool = False,
    limit: int = 50,
) -> list[sqlite3.Row]:
    allowed = {"unreviewed", "no_effect", "affected", "deferred"}
    if status not in allowed:
        raise ValueError(f"status는 {sorted(allowed)} 중 하나여야 합니다.")
    exact_sql = "AND c.match_level IN ('exact_article', 'exact_case')" if exact_only else ""
    return conn.execute(
        f"""
        SELECT c.*, i.source, i.category, i.published_at, i.importance,
               i.relevance_score
        FROM jid_impact_candidates c
        LEFT JOIN items i ON i.id = c.item_id
        WHERE c.status = ?
          {exact_sql}
        ORDER BY c.priority,
                 COALESCE(i.published_at, c.last_seen_at) DESC,
                 c.first_detected_at DESC,
                 c.candidate_id
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()


def prune_legacy_court_candidates(conn: sqlite3.Connection) -> int:
    """법원도서관 중복 수집분의 미검수 파생 후보만 제거한다.

    사람이 이미 판정한 후보와 검수 이력은 보존한다.
    """
    with conn:
        cur = conn.execute(
            """
            DELETE FROM jid_impact_candidates
            WHERE status = 'unreviewed'
              AND item_id IN (
                SELECT id FROM items
                WHERE source = 'scourt' AND category = 'case_law'
              )
            """
        )
    return cur.rowcount


def review_impact_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    decision: str,
    reason: str,
    reviewer: str,
    timezone: str,
) -> None:
    allowed = {"unreviewed", "no_effect", "affected", "deferred"}
    if decision not in allowed:
        raise ValueError(f"decision은 {sorted(allowed)} 중 하나여야 합니다.")
    if not reason.strip():
        raise ValueError("검수 사유는 비워둘 수 없습니다.")
    reviewed_at = now_iso(timezone)
    with conn:
        cur = conn.execute(
            """
            UPDATE jid_impact_candidates
            SET status=?, reviewed_at=?, reviewer=?, review_reason=?
            WHERE candidate_id=?
            """,
            (decision, reviewed_at, reviewer, reason.strip(), candidate_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"영향후보를 찾지 못했습니다: {candidate_id}")
        conn.execute(
            """
            INSERT INTO jid_impact_reviews (
              candidate_id, decision, reason, reviewer, reviewed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (candidate_id, decision, reason.strip(), reviewer, reviewed_at),
        )
