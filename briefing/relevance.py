from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from briefing.config import RelevanceConfig

# 항목당 점수 상한 (법령축 + 키워드축 합산 후 100 캡)
_LAW_AXIS_CAP = 60
_TERM_AXIS_CAP = 60

# 유니버스 소속 기본 가중치 (kind별)
_BASE_WEIGHT = {"law": 20, "soft_law": 25}

# 일반 기본법전: 판결문·보도자료에 의례적으로 인용되어 회사 특이성이 약함 → 할인
_GENERIC_CODES = {
    "헌법", "민법", "형법", "상법", "민사소송법", "민사소송규칙",
    "형사소송법", "형사소송규칙", "행정소송법", "행정절차법",
}
_GENERIC_BASE = 8

# 세법류: 사규가 참조해 유니버스에 있으나, 세금 판결·보도는 대부분 회사 무관 → 할인
# (scourt_ranking low_keywords의 취득세·양도세 판단과 같은 취지)
_TAX_LAW_PREFIXES = (
    "법인세법", "소득세법", "교육세법", "부가가치세법", "상속세", "증여세",
    "지방세", "조세특례제한법", "국세기본법",
)

# 입법예고·행정예고(감독규정 변경예고 포함)는 규범 변경의 직접 신호 →
# 회사 관련 흔적(법령·키워드 매칭)이 있을 때 가산
_PRIMARY_CATEGORIES = {"legislation", "admin_notice"}
_PRIMARY_BONUS = 15


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _link_bonus(sagyu_links: int) -> int:
    """사규 연계 수 보조 가중치. 부분 커버리지이므로 상한을 낮게 유지."""
    if sagyu_links >= 50:
        return 15
    if sagyu_links >= 10:
        return 10
    if sagyu_links >= 1:
        return 5
    return 0


def _term_present(term: str, hay: str) -> bool:
    term = _nfc(term)
    if re.fullmatch(r"[A-Za-z0-9\-]+", term):
        return re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", hay, re.I
        ) is not None
    return term.lower() in hay.lower()


def sector_signals(text: str, cfg: RelevanceConfig) -> tuple[list[str], list[str]]:
    """텍스트에서 생보 직접 신호와 손보 전용 신호를 분리한다."""
    return (
        [term for term in cfg.life_terms if _term_present(term, text)],
        [term for term in cfg.nonlife_terms if _term_present(term, text)],
    )


@dataclass(frozen=True)
class RelevanceResult:
    score: int              # 0~100
    evidence: str           # 사람이 읽는 근거 (이메일 주석·검수용)


@dataclass(frozen=True)
class _Pattern:
    canonical: str          # 대표 표기 (근거 표시용)
    alias_nospace: str      # 공백 제거 매칭 키
    kind: str               # law | soft_law
    weight: int
    sagyu_links: int


class RelevanceScorer:
    """미래에셋생명 관련성(relevance) 채점기.

    - L1: comp_matching_auto 스냅샷(감시법령 + 협회규정) 명칭 감지
    - L2: config의 생보 특화 키워드(term_weights)
    점수는 중요도 등급과 독립적으로 계산하며, 등급 안의 정렬과 ★ 표시에 사용한다.
    """

    def __init__(self, *, snapshot_path: str | Path, cfg: RelevanceConfig):
        self.cfg = cfg
        self._patterns: list[_Pattern] = []
        self._short_patterns: list[tuple[re.Pattern, _Pattern]] = []
        self._load_snapshot(Path(snapshot_path))

    def _load_snapshot(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(
                f"relevance 스냅샷 없음: {path} "
                "(scripts/export_company_law_snapshot.py 실행 필요)"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        for kind, section in (("law", "laws"), ("soft_law", "soft_laws")):
            for canonical, e in data.get(section, {}).items():
                if canonical in _GENERIC_CODES or canonical.startswith(_TAX_LAW_PREFIXES):
                    base = _GENERIC_BASE
                else:
                    base = _BASE_WEIGHT[kind]
                weight = base + _link_bonus(int(e.get("sagyu_links", 0)))
                names = {canonical, *e.get("aliases", [])}
                for name in names:
                    name = _nfc(name)
                    if len(name) < 2:
                        continue
                    p = _Pattern(
                        canonical=canonical,
                        alias_nospace=_nospace(name),
                        kind=kind,
                        weight=weight,
                        sagyu_links=int(e.get("sagyu_links", 0)),
                    )
                    # 4자 이하 축약 명칭('상법','민법','금소법' 등)은 앞글자 경계 확인
                    # ('손해배상법'의 '상법' 오탐 방지). 원문(공백 유지)에서 검사.
                    if len(name) <= 4:
                        rx = re.compile(rf"(?<![가-힣]){re.escape(name)}")
                        self._short_patterns.append((rx, p))
                    else:
                        self._patterns.append(p)
        # 긴 명칭 우선 매칭(포함관계 중복 억제에 활용)
        self._patterns.sort(key=lambda p: -len(p.alias_nospace))

    def score(self, *, title: str, body: Optional[str],
              category: Optional[str] = None) -> RelevanceResult:
        """title/body 텍스트로 관련성 채점.

        body에는 '깨끗한' 텍스트만 넣을 것 — LLM 요약, 대법원 판결요지,
        국회 제안이유 등. HTML 스크랩 원문(raw_text)은 사이트 메뉴·푸터
        보일러플레이트('퇴직연금 비교공시' 등)가 섞여 오탐을 만들므로
        보일러플레이트 제거 전까지 넣지 않는다(build_score_text 참조).

        category: items.category (legislation/admin_notice/press/case_law/other).
        입법·행정예고는 규범 변경 직접 신호라 관련 흔적이 있으면 가산.
        """
        hay = _nfc(f"{title or ''}\n{body or ''}")
        hay_nospace = _nospace(hay)

        # ── L1: 법령·협회규정 감지 ─────────────────────────────────
        matched: dict[str, _Pattern] = {}       # canonical → 패턴 (중복 1회)
        consumed: list[str] = []                # 이미 매칭된 긴 명칭(nospace)
        for p in self._patterns:
            if p.canonical in matched:
                continue
            n_occur = hay_nospace.count(p.alias_nospace)
            if n_occur == 0:
                continue
            # 더 긴 매칭 명칭에 딸려 잡힌 경우 억제: 짧은 명칭의 출현이 전부
            # 이미 매칭된 긴 명칭 내부라면 독립 언급이 없는 것
            # (예: 본문에 '보험업법 시행령'만 있고 '보험업법' 단독 언급이 없는 경우)
            n_inside = sum(
                c.count(p.alias_nospace) * hay_nospace.count(c)
                for c in consumed
                if p.alias_nospace in c
            )
            if n_occur <= n_inside:
                continue
            matched[p.canonical] = p
            consumed.append(p.alias_nospace)
        for rx, p in self._short_patterns:
            if p.canonical in matched:
                continue
            if rx.search(hay):
                matched[p.canonical] = p

        law_score = min(_LAW_AXIS_CAP, sum(p.weight for p in matched.values()))

        # ── L2: 생보 특화 키워드 ──────────────────────────────────
        term_hits: list[str] = []
        term_score = 0
        for term, w in self.cfg.term_weights.items():
            t = _nfc(term)
            if _term_present(t, hay):
                term_hits.append(t)
                term_score += int(w)
        term_score = min(_TERM_AXIS_CAP, term_score)

        primary_bonus = (
            _PRIMARY_BONUS
            if category in _PRIMARY_CATEGORIES and (law_score + term_score) > 0
            else 0
        )
        score = min(100, law_score + term_score + primary_bonus)
        life_hits, nonlife_hits = sector_signals(hay, self.cfg)
        # 자동차보험·대차료 등 손보 전용 사안은 일반 보험어와 상법만으로
        # 당사 관련 ★가 되지 않도록 제한한다. 생보 직접 신호가 함께 있으면
        # 업권 공통 사안일 수 있으므로 제한하지 않는다.
        nonlife_only = bool(nonlife_hits) and not life_hits
        if nonlife_only:
            score = min(score, 29)

        # ── 근거 문자열 ───────────────────────────────────────────
        parts: list[str] = []
        laws = [p for p in matched.values() if p.kind == "law"]
        softs = [p for p in matched.values() if p.kind == "soft_law"]
        if laws:
            parts.append("법령: " + "·".join(
                f"{p.canonical}(사규연계 {p.sagyu_links})" if p.sagyu_links else p.canonical
                for p in sorted(laws, key=lambda x: -x.weight)[:4]
            ))
        if softs:
            parts.append("협회·연성규범: " + "·".join(
                p.canonical for p in sorted(softs, key=lambda x: -x.weight)[:3]
            ))
        if term_hits:
            parts.append("키워드: " + "·".join(term_hits[:6]))
        if primary_bonus:
            parts.append("입법·행정예고")
        if life_hits:
            parts.append("생보 신호: " + "·".join(life_hits[:5]))
        if nonlife_only:
            parts.append("손보 전용: " + "·".join(nonlife_hits[:5]))
        evidence = " | ".join(parts)

        return RelevanceResult(score=score, evidence=evidence)


# raw_text(페이지 추출 본문)를 채점에 써도 되는 소스: 별도 fetch한 실질 텍스트만
# (scourt=판결요지·보도자료 본문, na=의안 제안이유·주요내용).
# HTML 스크랩 소스(fss·fsc 등)는 메뉴/푸터가 섞여 있어 제외.
BODY_OK_SOURCES = {"scourt", "na"}


def build_score_text(*, source: str, raw_text: Optional[str],
                     summary: Optional[str]) -> Optional[str]:
    """채점용 body 텍스트 조립 (요약 + 허용 소스의 원문)."""
    parts = []
    if summary:
        parts.append(summary)
    if raw_text and source in BODY_OK_SOURCES:
        parts.append(raw_text)
    return "\n".join(parts) if parts else None


def build_scorer(cfg, *, base_dir: str | Path = ".") -> Optional[RelevanceScorer]:
    """AppConfig에서 scorer 생성. 비활성/스냅샷 부재 시 None (파이프라인 무중단)."""
    rel = cfg.relevance
    if not rel.enabled:
        return None
    snapshot = Path(base_dir) / rel.snapshot_path
    try:
        return RelevanceScorer(snapshot_path=snapshot, cfg=rel)
    except FileNotFoundError as e:
        print(f"[relevance] 비활성화: {e}")
        return None
