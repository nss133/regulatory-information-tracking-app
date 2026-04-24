from __future__ import annotations

import argparse
import sys
from pathlib import Path

from briefing.config import load_config
from briefing.db import (
    connect,
    init_db,
    mark_old_kofiu_announce_as_sent,
    mark_old_scourt_as_sent,
    mark_sent,
    reset_sent_after,
    select_last_sent_batch,
    select_pending_for_email,
    upsert_items,
    update_item_enrichment,
)
from briefing.emailer import send_email, send_error_alert
from briefing.extract import extract_main_text
from briefing.http import HttpClient
from briefing.ranking import rank_item
from briefing.render import render_email_html
from briefing.sources import build_connectors
from briefing.sources.registry import SOURCE_SPECS
from briefing.summarize import should_call_llm, summarize_with_llm
from briefing.utils import now_iso


def _render_text(items) -> str:
    lines: list[str] = []
    for it in items:
        spec = SOURCE_SPECS.get(it.source)
        src = spec.name_ko if spec else it.source
        tag = (it.importance or "low").upper()
        lines.append(f"[{src}] ({tag}) {it.title}")
        lines.append(f"- {it.url}")
        if it.published_at:
            lines.append(f"- 날짜: {it.published_at}")
        if it.importance_reason:
            lines.append(f"- 사유: {it.importance_reason}")
        if it.summary:
            lines.append(f"- 요약: {it.summary}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"

def _heuristic_summary(body: str) -> str | None:
    """
    LLM이 꺼져 있어도 '대략 무슨 내용인지'가 보이도록,
    본문에서 앞부분 문장 1~2개를 짧게 추출합니다.
    (공통 추출이라 메뉴/내비게이션이 섞일 수 있어 길이를 제한)
    """
    body = (body or "").strip()
    if len(body) < 80:
        return None

    # 너무 긴 공백 정리된 텍스트 기준, 문장 경계 후보로 나눔(한국어/영문 혼합 대비)
    parts: list[str] = []
    for sep in ["다. ", ". ", ")\n", "\n"]:
        if sep in body:
            parts = [p.strip() for p in body.split(sep) if p.strip()]
            break
    if not parts:
        parts = [body]

    s = parts[0]
    if len(parts) < 2:
        out = s
    else:
        out = f"{s}. {parts[1]}"

    out = out.replace("\n", " ").strip()
    if len(out) > 220:
        out = out[:220].rstrip() + "…"
    return out


def cmd_fetch(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    connectors = build_connectors(cfg.fetch)
    fetched = []
    errors = []
    for c in connectors:
        try:
            fetched.extend(c.fetch_latest())
        except Exception as e:
            errors.append(f"{c.code}: {e}")

    upserted = upsert_items(conn, fetched, tz_name=cfg.timezone)
    print(f"수집 {len(fetched)}건, DB 반영 {upserted}건")
    if errors:
        print("에러:")
        for e in errors:
            print(f"- {e}")
    return 0


def cmd_list(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )
    print(f"발송 대상 {len(pending)}건")
    for it in pending[:50]:
        print(f"- [{it.source}] {it.title} ({it.url})")
    return 0


def cmd_preview(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    # 최신 수집부터 수행(프리뷰는 항상 최신 상태를 보게)
    connectors = build_connectors(cfg.fetch)
    fetched = []
    errors: list[str] = []
    for c in connectors:
        try:
            fetched.extend(c.fetch_latest())
        except Exception as e:
            errors.append(f"{c.code}: {e}")
    upsert_items(conn, fetched, tz_name=cfg.timezone)
    mark_old_kofiu_announce_as_sent(conn, tz_name=cfg.timezone)
    mark_old_scourt_as_sent(conn, tz_name=cfg.timezone)

    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )
    _enrich(conn, cfg, pending)
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )

    subject = f"{cfg.email.subject_prefix} {now_iso(cfg.timezone)[:10]} (신규/변경 {len(pending)}건)"
    html = render_email_html(
        items=pending,
        template_dir=Path("templates"),
        subject=subject,
        run_date=now_iso(cfg.timezone),
        errors=errors,
    )
    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"프리뷰 저장: {out_path}")
    return 0


def _enrich(conn, cfg, items) -> None:
    http = HttpClient(user_agent=cfg.fetch.user_agent, timeout_seconds=cfg.fetch.request_timeout_seconds)
    for it in items:
        rank = rank_item(title=it.title, raw_text=None, cfg=cfg.ranking)
        importance = rank.importance
        reason = rank.reason
        summary = None

        # 대법원은 요약 없이 제목/중요도/첨부만 사용
        if it.source == "scourt":
            update_item_enrichment(
                conn,
                item_id=it.id,
                importance=importance,
                importance_reason=reason,
                summary=None,
            )
            continue

        # LLM 옵션: 켜져 있을 때만 요약/중요도 보정 (현재는 요약 비표시)
        if should_call_llm(llm=cfg.llm, current_importance=importance):
            body = extract_main_text(http, it.url)
            spec = SOURCE_SPECS.get(it.source)
            res = summarize_with_llm(
                llm=cfg.llm,
                title=it.title,
                body=body,
                source_name_ko=(spec.name_ko if spec else it.source),
            )
            if res.importance:
                importance = res.importance
            if res.reason:
                reason = res.reason
            if res.summary:
                summary = res.summary

        update_item_enrichment(
            conn,
            item_id=it.id,
            importance=importance,
            importance_reason=reason,
            summary=summary,
        )


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    # 1) 수집 (소스별 병렬 수집)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(c):
        try:
            return c.code, c.fetch_latest(), None
        except Exception as e:
            return c.code, [], str(e)

    connectors = build_connectors(cfg.fetch)
    fetched = []
    errors = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_one, c): c for c in connectors}
        for f in as_completed(futures):
            code, items, err = f.result()
            fetched.extend(items)
            if err:
                errors.append(f"{code}: {err}")

    upsert_items(conn, fetched, tz_name=cfg.timezone)

    # KoFIU 공고/고시/훈령/예규: 오늘 이전 게시분은 발송 제외, 앞으로 업데이트분만 반영
    n_marked_kofiu = mark_old_kofiu_announce_as_sent(conn, tz_name=cfg.timezone)
    if n_marked_kofiu:
        print(f"KoFIU 공고/고시/훈령/예규 기존 자료 {n_marked_kofiu}건 발송 제외(앞으로 업데이트분만 반영)")

    # 대법원 보도자료/주요판결: 오늘 이전 게시분은 발송 제외, 앞으로 업데이트분만 반영
    n_marked_scourt = mark_old_scourt_as_sent(conn, tz_name=cfg.timezone)
    if n_marked_scourt:
        print(f"대법원 기존 자료 {n_marked_scourt}건 발송 제외(앞으로 업데이트분만 반영)")

    # 2) 발송 대상
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )
    _enrich(conn, cfg, pending)
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )

    if not pending:
        print("발송 대상 없음(신규/변경 없음)")
        return 0

    subject = f"{cfg.email.subject_prefix} {now_iso(cfg.timezone)[:10]} (신규/변경 {len(pending)}건)"
    html = render_email_html(
        items=pending,
        template_dir=Path("templates"),
        subject=subject,
        run_date=now_iso(cfg.timezone),
        errors=errors,
    )
    text = _render_text(pending)

    # 수집 오류가 있으면 먼저 리포트하고, 발송 여부를 확인
    if errors:
        print("수집 오류:")
        for e in errors:
            print(f"  - {e}")
        send_anyway = getattr(args, "send_anyway", False)
        if not send_anyway:
            if sys.stdin.isatty():
                try:
                    answer = input("그래도 발송하시겠습니까? (y/N): ").strip().lower()
                    send_anyway = answer in ("y", "yes")
                except (EOFError, KeyboardInterrupt):
                    send_anyway = False
            if not send_anyway:
                print("수집 오류가 있어 발송을 건너뜁니다. 강제 발송: --send-anyway")
                return 1

    if cfg.email.enabled:
        send_email(cfg=cfg.email, subject=subject, html_body=html, text_body=text)
        mark_sent(conn, [it.id for it in pending], tz_name=cfg.timezone)
        print(f"발송 완료: {len(pending)}건")
    else:
        print("email.enabled=false 이므로 발송하지 않았습니다.")
        print(text)

    if errors:
        print("(참고) 수집 오류가 있었으나 발송은 완료되었습니다.")
        send_error_alert(cfg=cfg.email, errors=errors, run_date=now_iso(cfg.timezone)[:10])
    return 0


def cmd_reset_sent(args) -> int:
    """지정한 날짜 이후 발송 처리된 항목을 미발송으로 되돌립니다. (재발송 전 초기화용)"""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    n = reset_sent_after(conn, args.after_date)
    print(f"발송 상태 초기화: {args.after_date} 이후 발송분 {n}건 → 미발송으로 되돌림")
    return 0


def cmd_resend_last(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    batch = select_last_sent_batch(conn)
    if not batch:
        print("직전 발송 배치를 찾지 못했습니다. 먼저 `run`을 1회 실행하세요.")
        return 1

    # 요약/중요도는 최신 로직으로 다시 채움(LLM OFF이면 휴리스틱)
    _enrich(conn, cfg, batch)
    batch = select_last_sent_batch(conn)

    subject = f"{cfg.email.subject_prefix} {now_iso(cfg.timezone)[:10]} (재발송: {len(batch)}건)"
    html = render_email_html(
        items=batch,
        template_dir=Path("templates"),
        subject=subject,
        run_date=now_iso(cfg.timezone),
        errors=[],
    )
    text = _render_text(batch)

    if cfg.email.enabled:
        send_email(cfg=cfg.email, subject=subject, html_body=html, text_body=text)
        print(f"재발송 완료: {len(batch)}건")
    else:
        print("email.enabled=false 이므로 발송하지 않았습니다.")
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="briefing", description="Daily regulatory briefing")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in [
        ("fetch", cmd_fetch),
        ("list", cmd_list),
        ("preview", cmd_preview),
        ("run", cmd_run),
        ("reset-sent", cmd_reset_sent),
        ("resend-last", cmd_resend_last),
    ]:
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True, help="config.yaml 경로")
        if name == "preview":
            sp.add_argument("--out", required=True, help="HTML 저장 경로")
        if name == "run":
            sp.add_argument(
                "--send-anyway",
                action="store_true",
                dest="send_anyway",
                help="수집 오류가 있어도 확인 없이 발송 (cron/자동 실행용)",
            )
        if name == "reset-sent":
            sp.add_argument(
                "--after-date",
                required=True,
                metavar="YYYY-MM-DD",
                help="이 날짜 이후 발송된 항목을 미발송으로 되돌림",
            )
        sp.set_defaults(_fn=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args._fn(args))

