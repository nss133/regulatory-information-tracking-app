from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WatchResult:
    healthy: bool
    code: str
    reason: str
    run_id: str | None = None


def assess_delivery_run(
    conn: sqlite3.Connection,
    *,
    expected_date: str,
    checked_at: str,
    stale_after_minutes: int = 45,
) -> WatchResult:
    """해당 날짜의 최신 브리핑 배치가 정상적으로 끝났는지 판정한다."""
    row = conn.execute(
        """
        SELECT run_id, started_at, completed_at, status, stage, error
        FROM delivery_runs
        WHERE run_date = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (expected_date,),
    ).fetchone()
    if row is None:
        return WatchResult(
            False, "missing",
            f"{expected_date} 브리핑 실행 기록이 없습니다.",
        )

    run_id = row["run_id"]
    status = row["status"]
    stage = row["stage"]
    if status == "completed":
        return WatchResult(
            True, "completed",
            f"정상 완료({stage})", run_id,
        )
    if status in ("failed", "ambiguous"):
        detail = f": {row['error']}" if row["error"] else ""
        return WatchResult(
            False, status,
            f"브리핑 실행이 {status}/{stage} 상태입니다{detail}", run_id,
        )
    if status == "smtp_accepted":
        return WatchResult(
            False, "ambiguous",
            "SMTP 접수 후 발송 완료 처리가 끝나지 않아 확인이 필요합니다.", run_id,
        )

    try:
        started_at = datetime.fromisoformat(row["started_at"])
        now = datetime.fromisoformat(checked_at)
        stale = started_at < now - timedelta(minutes=stale_after_minutes)
    except (TypeError, ValueError):
        stale = True
    if stale:
        return WatchResult(
            False, "stale",
            f"브리핑이 {status}/{stage} 상태로 {stale_after_minutes}분 넘게 종료되지 않았습니다.",
            run_id,
        )
    return WatchResult(
        True, "in_progress",
        f"브리핑이 아직 진행 중입니다({status}/{stage}).", run_id,
    )
