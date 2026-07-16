"""발송 전 자가진단 — 조용한 고장(silent failure) 감지.

파이프라인이 죽지 않고 틀린 결과를 내는 유형의 고장을 발송 직전에 잡는다.
경고는 발송을 막지 않고(수집 오류와 달리) stderr와 vault 자가진단 로그로만 알린다.
메일 본문에는 싣지 않는다 — 운영 정보라 수신자 전체에게 보일 이유가 없음.

배경: 2026-06~07 대법원 판결 본문에 사이트 상용구가 섞여 전 판결이
'보험사 관련 HIGH'로 승격되는 도배가 한 달간 무증상 재발했던 사례.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# 페이지 본문이 아니라 사이트 껍데기(메뉴·footer)가 추출됐음을 나타내는 마커.
# '개인정보처리방침' 같은 단어는 보도자료 본문에 정당하게 등장할 수 있어 쓰지 않고,
# 껍데기에서만 나오는 문구를 사용한다.
_CHROME_PREFIX = "주메뉴 바로가기"
_CHROME_MARKERS = ("이메일무단수집거부", "영상정보처리기기 운영방침")

# HIGH 급증 판정: 최근 30일 일평균의 3배 초과 & 절대 5건 이상
_HIGH_ABS_MIN = 5
_HIGH_RATIO = 3.0


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


def run_sanity_checks(conn: sqlite3.Connection, pending) -> list[str]:
    """발송 대상(pending)에 대한 자가진단. 경고 문자열 목록을 반환(정상이면 빈 목록)."""
    warnings: list[str] = []
    warnings.extend(_check_chrome_pollution(pending))
    warnings.extend(_check_high_surge(conn, pending))
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
