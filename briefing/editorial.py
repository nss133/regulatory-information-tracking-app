"""검토한 사안·요약·참고기사를 원문 버전에 연결한다. 네트워크·DB 쓰기 없음."""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from briefing.legislation import reportable_legislation


@dataclass(frozen=True)
class CuratedArticle:
    title: str
    url: str
    publisher: str
    published_at: str
    why_read: str
    checked_at: str


@dataclass(frozen=True)
class EditorialIssue:
    primary_item_id: str
    sources: tuple[dict, ...]
    summary: str | None = None
    stage: str = ""
    articles: tuple[CuratedArticle, ...] = ()
    deadlines: tuple[dict, ...] = ()

    def manifest(self):
        return asdict(self)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"편집 {label}: 내용을 작성해야 함")
    return value.strip()


def _url(value):
    value = _text(value, "URL")
    u = urlparse(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("편집 URL은 HTTPS 주소여야 함")
    return value


def body_fingerprint(text):
    """본문 검증값에서 조회수 등 명시적인 표시용 숫자만 제외한다."""
    text = re.sub(r'조회\s*수?\s*:\s*[\d,]+', '', text or '')
    return hashlib.sha256(re.sub(r'\s+', ' ', text).strip().encode()).hexdigest()


def load_editorial(path, items, *, as_of: str) -> tuple[EditorialIssue, ...]:
    """당일 ready 편집본만 사용한다. 부분 원문 누락·버전 변경은 발송 전에 중단한다."""
    path = Path(path)
    if not path.exists():
        return ()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") == "draft":
        return ()
    if data.get("status") != "ready" or data.get("as_of") != as_of[:10]:
        raise ValueError("편집 상태 또는 기준일 불일치")
    today = date.fromisoformat(as_of[:10])
    by_id = {i.id: i for i in items}
    claimed = set()
    result = []
    if not isinstance(data.get("issues"), list):
        raise ValueError("편집 issues 목록 필요")
    for row in data["issues"]:
        sources = row.get("sources", [])
        if not sources or not isinstance(sources, list):
            raise ValueError("편집 공식 원문이 필요함")
        ids = [s["item_id"] for s in sources]
        if not any(i in by_id for i in ids):
            continue  # 이 배치와 무관하거나 이미 발송된 사안
        if len(set(ids)) != len(ids) or claimed.intersection(ids):
            raise ValueError("동일 원문의 편집 사안 중복")
        if row.get("primary_item_id") not in ids:
            raise ValueError("편집 대표 원문이 연결 목록에 없음")
        for src in sources:
            it = by_id.get(src["item_id"])
            if (it is None or it.content_hash != src.get("content_hash")
                    or it.url != src.get("url")):
                raise ValueError("편집 원문 버전 또는 URL 변경. 편집본 재검토 필요")
            if src.get('body_sha256') and src['body_sha256'] != body_fingerprint(it.raw_text):
                raise ValueError('편집 본문 변경. 편집본 재검토 필요')
            if not reportable_legislation(it):
                raise ValueError("편집 대상이 입법 노출 기준을 충족하지 않음")
        articles = []
        seen = {s["url"] for s in sources}
        raw_articles = row.get("articles", [])
        if not isinstance(raw_articles, list) or len(raw_articles) > 2:
            raise ValueError("사안별 관련 기사는 최대 2건")
        for article in raw_articles:
            values = {k: _text(article.get(k), k) for k in CuratedArticle.__dataclass_fields__}
            values["url"] = _url(values["url"])
            if values["url"] in seen:
                raise ValueError("제목 원문 또는 관련 기사 URL 중복")
            pub = date.fromisoformat(values["published_at"])
            checked = date.fromisoformat(values["checked_at"])
            if not pub <= checked <= today:
                raise ValueError("기사 발행일·확인일·기준일 순서 불일치")
            articles.append(CuratedArticle(**values))
            seen.add(values["url"])
        summary = row.get("summary")
        if summary is not None:
            summary = _text(summary, "요약")
        stage = row.get("stage", "")
        if stage not in ("", "입법예고 중", "행정예고 중", "의결 발표", "공포", "시행", "판결·분쟁조정", "감독·제재", "동향"):
            raise ValueError("편집 진행 상태 확인 필요")
        deadlines = []
        for d in row.get('deadlines', []):
            if d.get('label') not in ('의견제출', '시행일', '신청기한') or d.get('source_item_id') not in ids:
                raise ValueError('편집 일정의 유형·출처 확인 필요')
            day = date.fromisoformat(d['date'])
            proof = _text(d.get('source_text'), '일정 근거')
            compact = lambda s: re.sub(r'\s+', '', s)
            if compact(proof) not in compact(by_id[d['source_item_id']].raw_text or ''):
                raise ValueError('편집 일정 근거가 원문에 없음')
            pattern = rf'{day.year}\s*[년./-]\s*0?{day.month}\s*[월./-]\s*0?{day.day}(?:\s*일|\b)'
            if not re.search(pattern, proof):
                source_day = by_id[d['source_item_id']].published_at or ''
                partial = rf'(?<!\d)0?{day.month}\s*월\s*0?{day.day}\s*일'
                if not (d.get('year_from_publication') is True and source_day.startswith(str(day.year)+'-')
                        and re.search(partial, proof)):
                    raise ValueError('편집 일정과 근거 날짜 불일치')
            deadlines.append(d)
        if not any(('low' if i.last_sent_at and i.sent_hash != i.content_hash else i.importance or 'low') != 'low' for i in (by_id[k] for k in ids)):
            raise ValueError('LOW-only 사안은 기존 표시를 유지함')
        result.append(EditorialIssue(row["primary_item_id"], tuple(sources), summary, stage, tuple(articles), tuple(deadlines)))
        claimed.update(ids)
    return tuple(result)


def load_attachment_links(conn, items) -> dict[str, list[dict]]:
    """상세페이지에 이름이 확인되는 개별 파일만 사용한다. 오래된 첨부를 되살리지 않는다."""
    result = {}
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='item_content'").fetchone():
        return result
    for item in items:
        matches = {}
        body = re.sub(r"\s+", "", item.raw_text or "")
        for row in conn.execute(
            "SELECT source_url,label FROM item_content WHERE item_id=? ORDER BY fetched_at DESC, rowid DESC",
            (item.id,),
        ):
            label, url = row["label"], row["source_url"]
            filename = re.split(r"\.(pdf|hwpx|hwp)\b", label or "", maxsplit=1, flags=re.I)
            if len(filename) < 3:
                continue
            name = filename[0] + "." + filename[1]
            host = urlparse(url).hostname or ""
            item_host = urlparse(item.url).hostname or ""
            if host != item_host or re.sub(r"\s+", "", name) not in body:
                continue
            matches.setdefault(name, {"label": label, "url": url})
        if matches:
            result[item.id] = list(matches.values())
    return result


def regulation_identity(item):
    """일반 키워드 유사도가 아니라 명시된 개정 대상·예고 종류로만 자동 연결한다."""
    if item.source_code == "na":
        return None
    t = item.title
    stage = next((s for s in ("행정예고", "입법예고", "규정변경예고") if s in t), None)
    if not stage:
        return None
    t = re.sub(r"[「」『』｢｣\"'“”]", "", t)
    match = re.fullmatch(r"(.+?)(?:\s+고시)?\s*(?:일부|전부)?개정안?\s*" + stage, t)
    if not match:
        return None
    subject = re.sub(r"\s+", "", match.group(1))
    if len(subject) < 5:
        return None
    return item.source_code, subject, stage


def can_pair(press, notice):
    """같은 기관의 보도와 예고, 같은 단계, 게시일 차이 3일 이내일 때만 묶는다."""
    if press.category_label != "보도자료" or notice.category_label not in ("입법/예고", "행정예고"):
        return False
    if not regulation_identity(press) or regulation_identity(press) != regulation_identity(notice):
        return False
    try:
        dates = [date.fromisoformat(i.published_at) for i in (press, notice)]
    except (TypeError, ValueError):
        return False
    return abs((dates[0] - dates[1]).days) <= 3
