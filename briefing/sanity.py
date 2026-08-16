"""발송 전 자가진단 — 조용한 고장(silent failure) 감지.

파이프라인이 죽지 않고 틀린 결과를 내는 유형의 고장을 발송 직전에 잡는다.
경고는 발송을 막지 않고(수집 오류와 달리) stderr와 vault 자가진단 로그로만 알린다.
메일 본문에는 싣지 않는다 — 운영 정보라 수신자 전체에게 보일 이유가 없음.

배경: 2026-06~07 대법원 판결 본문에 사이트 상용구가 섞여 전 판결이
'보험사 관련 HIGH'로 승격되는 도배가 한 달간 무증상 재발했던 사례.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from briefing.sources.registry import SOURCE_SPECS

# 페이지 본문이 아니라 사이트 껍데기(메뉴·footer)가 추출됐음을 나타내는 마커.
# '개인정보처리방침' 같은 단어는 보도자료 본문에 정당하게 등장할 수 있어 쓰지 않고,
# 껍데기에서만 나오는 문구를 사용한다.
_CHROME_PREFIX = "주메뉴 바로가기"
_CHROME_MARKERS = ("이메일무단수집거부", "영상정보처리기기 운영방침")

# HIGH 급증 판정: 최근 30일 일평균의 3배 초과 & 절대 5건 이상
_HIGH_ABS_MIN = 5
_HIGH_RATIO = 3.0
# 주말을 정상 정지로 보므로 5일 이상 관측이 끊겼을 때 경고한다.
_SOURCE_STALE_DAYS = 5
_MISSING_DATE_RATIO = 0.8
_MISSING_DATE_MIN_ITEMS = 10


def _check_chrome_pollution(pending) -> list[str]:
    polluted = []
    for it in pending:
        text = it.raw_text or ""
        if not text:
            continue
        if text.startswith(_CHROME_PREFIX) or any(m in text for m in _CHROME_MARKERS):
            polluted.append(it)
    if not polluted:
        return []
    sample = (polluted[0].title or "")[:40]
    return [
        f"본문 상용구 오염 의심 {len(polluted)}건 (예: {sample}) — "
        f"소스 페이지 구조 변경 가능성, 파서 점검 필요"
    ]


def _check_high_surge(conn: sqlite3.Connection, pending) -> list[str]:
    high_today = sum(1 for it in pending if it.importance == "high")
    if high_today < _HIGH_ABS_MIN:
        return []
    row = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT substr(last_sent_at, 1, 10))
        FROM items
        WHERE importance = 'high'
          AND last_sent_at >= datetime('now', '-30 day')
        """
    ).fetchone()
    n_high, n_days = row or (0, 0)
    if not n_days:
        return []
    avg = n_high / n_days
    if high_today > avg * _HIGH_RATIO:
        return [
            f"HIGH {high_today}건 — 최근 30일 일평균({avg:.1f}건)의 3배 초과. "
            f"분류 오염 여부 확인 필요"
        ]
    return []


def _check_source_health(conn: sqlite3.Connection) -> list[str]:
    """소스 전체 누락·수집 정지·게시일 파싱 붕괴를 조기에 감지한다."""
    try:
        health_rows = conn.execute(
            """
            SELECT source, last_attempt_at, last_success_at, last_item_count,
                   last_error, consecutive_failures
            FROM source_health
            """
        ).fetchall()
        rows = conn.execute(
            """
            WITH latest AS (
              SELECT source, MAX(last_seen_at) AS latest_seen
              FROM items
              GROUP BY source
            )
            SELECT i.source, COUNT(*) AS n, l.latest_seen,
                   SUM(CASE WHEN i.published_at IS NULL THEN 1 ELSE 0 END) AS missing_date
            FROM items i
            JOIN latest l
              ON l.source = i.source AND l.latest_seen = i.last_seen_at
            GROUP BY i.source, l.latest_seen
            """
        ).fetchall()
    except sqlite3.OperationalError:
        # 단위 테스트나 구버전 최소 스키마에서는 다른 진단을 계속 수행한다.
        return []

    by_source = {row[0]: row for row in rows}
    warnings: list[str] = []
    by_health = {row[0]: row for row in health_rows}
    observed_sources = set(by_health) if by_health else set(by_source)
    missing_sources = sorted(set(SOURCE_SPECS) - observed_sources)
    if missing_sources:
        names = ", ".join(SOURCE_SPECS[s].name_ko for s in missing_sources)
        warnings.append(f"수집 이력이 전혀 없는 소스: {names} — 커넥터 점검 필요")

    failed: list[str] = []
    for source, row in sorted(by_health.items()):
        if source not in SOURCE_SPECS or not row[4]:
            continue
        failed.append(
            f"{SOURCE_SPECS[source].name_ko} {int(row[5] or 0)}회"
        )
    if failed:
        warnings.append("최근 수집 실패: " + ", ".join(failed))

    stale: list[str] = []
    now = datetime.now(timezone.utc)
    for source in sorted(set(SOURCE_SPECS) & observed_sources):
        # 정상 0건 응답도 성공으로 기록되므로 source_health가 있으면 이를 우선한다.
        latest_seen = by_health[source][2] if by_health else by_source[source][2]
        if not latest_seen:
            stale.append(SOURCE_SPECS[source].name_ko)
            continue
        try:
            seen_at = datetime.fromisoformat(str(latest_seen))
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
            if seen_at.astimezone(timezone.utc) < now - timedelta(days=_SOURCE_STALE_DAYS):
                stale.append(SOURCE_SPECS[source].name_ko)
        except ValueError:
            stale.append(SOURCE_SPECS[source].name_ko)
    if stale:
        warnings.append(
            f"최근 {_SOURCE_STALE_DAYS}일 수집 흔적이 없는 소스: {', '.join(stale)}"
        )

    date_quality: list[str] = []
    for source, row in sorted(by_source.items()):
        if source not in SOURCE_SPECS:
            continue
        n = int(row[1] or 0)
        missing = int(row[3] or 0)
        if n >= _MISSING_DATE_MIN_ITEMS and missing / n >= _MISSING_DATE_RATIO:
            pct = round(100 * missing / n)
            date_quality.append(f"{SOURCE_SPECS[source].name_ko} {pct}%")
    if date_quality:
        warnings.append(
            "게시일 누락률 과다: " + ", ".join(date_quality)
            + " — 기간 필터·정렬 정확도 점검 필요"
        )
    return warnings


def run_sanity_checks(conn: sqlite3.Connection, pending) -> list[str]:
    """발송 대상(pending)에 대한 자가진단. 경고 문자열 목록을 반환(정상이면 빈 목록)."""
    warnings: list[str] = []
    warnings.extend(_check_chrome_pollution(pending))
    warnings.extend(_check_high_surge(conn, pending))
    warnings.extend(_check_source_health(conn))
    return warnings


_LOG_HEADER = """# 브리핑 자가진단 로그

> 발송 전 자가진단 경고 기록 — briefing이 자동 생성함(append 전용).
> 경고는 발송을 막지 않음. 점검 후 처리한 항목은 취소선 처리 권장.

"""


def append_log(log_note: str | Path, warnings: list[str], run_date: str) -> None:
    p = Path(log_note)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_LOG_HEADER)
    with p.open("a") as f:
        f.write(f"\n## {run_date}\n\n")
        for w in warnings:
            f.write(f"- ⚠ {w}\n")
