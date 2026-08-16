from __future__ import annotations

import html
import sqlite3
from pathlib import Path

from briefing.maintenance import BackupStatus


def _e(value) -> str:
    return html.escape(str(value if value is not None else "-"))


def _seconds(value) -> str:
    return f"{float(value):.1f}s" if value is not None else "-"


def render_dashboard(
    conn: sqlite3.Connection, *, generated_at: str, backup: BackupStatus
) -> str:
    runs = conn.execute(
        """
        SELECT started_at, status, stage, fetched_count, candidate_count,
               visible_count, suppressed_count, duration_seconds, error
        FROM delivery_runs ORDER BY started_at DESC LIMIT 14
        """
    ).fetchall()
    sources = conn.execute(
        """
        SELECT source, last_success_at, last_item_count, last_duration_seconds,
               consecutive_failures, last_error
        FROM source_health ORDER BY source
        """
    ).fetchall()
    latest = runs[0] if runs else None
    status = latest["status"] if latest else "no-data"
    status_class = "ok" if status == "completed" else "bad"
    source_failures = sum(1 for row in sources if row["last_error"])
    backup_ok = backup.exists and backup.integrity == "ok" and (backup.age_days or 0) <= 8
    run_rows = "".join(
        "<tr>"
        f"<td>{_e(row['started_at'])}</td><td>{_e(row['status'])}/{_e(row['stage'])}</td>"
        f"<td>{row['fetched_count']}</td><td>{row['candidate_count']}</td>"
        f"<td>{row['visible_count']}</td><td>{row['suppressed_count']}</td>"
        f"<td>{_e(_seconds(row['duration_seconds']))}</td>"
        f"<td>{_e(row['error'])}</td></tr>"
        for row in runs
    ) or '<tr><td colspan="8">기록 없음</td></tr>'
    source_rows = "".join(
        "<tr>"
        f"<td>{_e(row['source'])}</td><td>{_e(row['last_success_at'])}</td>"
        f"<td>{row['last_item_count']}</td>"
        f"<td>{_e(_seconds(row['last_duration_seconds']))}</td>"
        f"<td>{row['consecutive_failures']}</td><td>{_e(row['last_error'])}</td></tr>"
        for row in sources
    ) or '<tr><td colspan="6">기록 없음</td></tr>'
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legal·Compliance Signal 운영 현황</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif;margin:24px;color:#172033;background:#f6f8fb}}
main{{max-width:1120px;margin:auto}} h1{{font-size:24px;margin:0 0 4px}} .meta{{color:#657085;margin-bottom:18px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}}
.card{{background:#fff;border:1px solid #dfe5ee;border-radius:10px;padding:14px}} .label{{font-size:12px;color:#657085}} .value{{font-size:22px;font-weight:700;margin-top:4px}}
.ok{{color:#16794a}} .bad{{color:#b42318}} section{{margin-top:24px}} h2{{font-size:17px}}
.table{{overflow-x:auto;background:#fff;border:1px solid #dfe5ee;border-radius:10px}} table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:9px 11px;border-bottom:1px solid #edf0f5;vertical-align:top}} th{{background:#f9fafc;color:#536075}} td:last-child{{max-width:320px;word-break:break-word}}
@media(max-width:640px){{body{{margin:14px}} th,td{{padding:8px}}}}
</style></head><body><main>
<h1>Legal·Compliance Signal 운영 현황</h1><div class="meta">갱신 {_e(generated_at)}</div>
<div class="stats">
<div class="card"><div class="label">최근 실행</div><div class="value {status_class}">{_e(status)}</div></div>
<div class="card"><div class="label">최근 발송</div><div class="value">{latest['visible_count'] if latest else 0}건</div></div>
<div class="card"><div class="label">오류 소스</div><div class="value {'bad' if source_failures else 'ok'}">{source_failures}개</div></div>
<div class="card"><div class="label">주간 백업</div><div class="value {'ok' if backup_ok else 'bad'}">{_e(backup.integrity)}</div><div class="label">{_e(f'{backup.age_days:.1f}일 전' if backup.age_days is not None else backup.path)}</div></div>
</div>
<section><h2>최근 실행</h2><div class="table"><table><thead><tr><th>시작</th><th>상태</th><th>수집</th><th>후보</th><th>발송</th><th>제외</th><th>소요</th><th>오류</th></tr></thead><tbody>{run_rows}</tbody></table></div></section>
<section><h2>소스 건강도</h2><div class="table"><table><thead><tr><th>소스</th><th>최근 성공</th><th>건수</th><th>소요</th><th>연속 실패</th><th>오류</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
</main></body></html>"""


def write_dashboard(path: str | Path, content: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
