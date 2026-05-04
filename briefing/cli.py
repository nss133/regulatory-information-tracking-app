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
    rebuild_fts,
    reset_sent_after,
    search_content,
    select_items_missing_body,
    select_last_sent_batch,
    select_pending_attachments,
    select_pending_for_email,
    update_attachment_result,
    update_body_text,
    upsert_attachment_record,
    upsert_items,
    update_item_enrichment,
)
from briefing.emailer import send_email, send_error_alert
from briefing.extract import extract_main_text, extract_page_content
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

import re as _re

_NAV_SKIP_KW = (
    "바로가기", "바로 가기", "본문으로", "주메뉴", "메뉴열기", "메뉴 닫기",
    "사이트맵", "로그인", "회원가입", "페이스북", "트위터", "유튜브",
    "인스타그램", "블로그", "화면 확대", "화면 축소", "글자크기",
    "누리집", "홈페이지", "Language", "통합검색", "검색버튼",
    "이전 페이지", "다음 페이지", "목록으로", "프린트",
)


def _heuristic_summary(body: str) -> str | None:
    """
    LLM이 꺼져 있어도 '대략 무슨 내용인지'가 보이도록,
    본문에서 실질적 내용이 담긴 첫 문장을 추출합니다.
    문장 종결어미(다. / 니다. 등) 기준으로 분리 후 내비게이션 문장을 건너뜁니다.
    """
    body = (body or "").strip()
    if len(body) < 80:
        return None

    # 한국어 문장 종결 패턴으로 분리
    raw_sentences = _re.split(r'(?<=다)\.\s+|(?<=니다)\.\s+|(?<=습니다)\.\s+', body)
    sentences = [s.replace("\n", " ").strip() for s in raw_sentences if s.strip()]

    for s in sentences:
        if len(s) < 25:
            continue
        if any(kw in s for kw in _NAV_SKIP_KW):
            continue
        # 메뉴 리스트성 문장 제거: 짧은 토큰이 전체의 70% 초과
        tokens = s.split()
        if tokens and sum(1 for t in tokens if len(t) <= 4) / len(tokens) > 0.7:
            continue
        out = s[:220].rstrip() + ("…" if len(s) > 220 else "")
        return out

    return None


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
    import time as _time
    http = HttpClient(user_agent=cfg.fetch.user_agent, timeout_seconds=cfg.fetch.request_timeout_seconds)
    _llm_call_count = 0

    # provider별 호출 간 대기시간(초). Groq는 TPM 한도가 작아 15초 유지,
    # DeepSeek/OpenAI는 한도가 커서 1초로 충분.
    _llm_sleep_by_provider = {
        "groq": 15.0,
        "deepseek": 1.0,
        "openai": 1.0,
        "anthropic": 1.0,
    }
    _llm_sleep = _llm_sleep_by_provider.get(cfg.llm.provider, 1.0)

    for it in items:
        rank = rank_item(title=it.title, raw_text=None, cfg=cfg.ranking)
        importance = rank.importance
        reason = rank.reason
        summary = None

        # 대법원은 요약 없이 제목/중요도/첨부만 사용
        # 보도자료/주요판결 모두 판결 소개 성격이므로 HIGH 강제
        if it.source == "scourt":
            importance = "high"
            reason = "대법원 판결 소개 자료"
            update_item_enrichment(
                conn,
                item_id=it.id,
                importance=importance,
                importance_reason=reason,
                summary=None,
            )
            continue

        if should_call_llm(llm=cfg.llm, current_importance=importance) and not it.summary:
            if _llm_call_count > 0 and _llm_sleep > 0:
                _time.sleep(_llm_sleep)

            # 국회(na)는 의안 상세페이지가 일정 정보 위주라, 별도 fetch한
            # 제안이유·주요내용(raw_text)을 LLM 본문으로 사용
            if it.source == "na" and it.raw_text:
                body = it.raw_text
                attachment_links = []
            else:
                try:
                    body, attachment_links = extract_page_content(http, it.url)
                except Exception:
                    body = ""
                    attachment_links = []
            # body text 저장 (update_body_text 내부 WHERE raw_text IS NULL로 idempotent)
            if body:
                update_body_text(conn, item_id=it.id, body_text=body)
            # 첨부파일 링크 등록 (pending 상태로)
            for label, att_url in attachment_links:
                try:
                    upsert_attachment_record(
                        conn, item_id=it.id, source_url=att_url,
                        label=label, tz_name=cfg.timezone,
                    )
                except Exception:
                    pass
            spec = SOURCE_SPECS.get(it.source)
            res = summarize_with_llm(
                llm=cfg.llm,
                title=it.title,
                body=body,
                source_name_ko=(spec.name_ko if spec else it.source),
            )
            _llm_call_count += 1
            # LLM 성공(importance 파싱됨)한 경우만 중요도·사유 보정
            if res.importance:
                importance = res.importance
                if res.reason:
                    reason = res.reason
            if res.summary:
                summary = res.summary

        # 기존 요약이 있으면 새 요약이 None이어도 덮어쓰지 않음
        final_summary = summary if summary is not None else it.summary
        update_item_enrichment(
            conn,
            item_id=it.id,
            importance=importance,
            importance_reason=reason,
            summary=final_summary,
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
        # 발송 대상이 없어도 harvest는 실행 (body/첨부 수집 + FTS 갱신)
        if cfg.archive.enabled:
            try:
                harvest_content(conn, cfg)
            except Exception as e:
                print(f"[harvest] 오류 (무시): {e}")
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

    # 4) 콘텐츠 아카이브 (이메일 발송 후 실행, 실패해도 종료코드 영향 없음)
    if cfg.archive.enabled:
        try:
            harvest_content(conn, cfg)
        except Exception as e:
            print(f"[harvest] 오류 (무시): {e}")

    return 0


def harvest_content(conn, cfg) -> None:
    """
    이메일 발송 후 실행: body text 수집 + 첨부파일 kordoc 추출 + FTS 재구축.
    파이프라인 실패를 막지 않도록 모든 예외를 내부에서 처리합니다.
    """
    import time as _time
    from briefing.kordoc import download_and_extract

    arc = cfg.archive
    http = HttpClient(
        user_agent=cfg.fetch.user_agent,
        timeout_seconds=60,
    )

    # 1) body text 미수집 항목 처리
    items_no_body = select_items_missing_body(
        conn, days=arc.body_harvest_days, limit=arc.body_harvest_limit
    )
    body_ok = 0
    for row in items_no_body:
        try:
            body, attachment_links = extract_page_content(http, row["url"])
            if body:
                update_body_text(conn, item_id=row["id"], body_text=body)
                body_ok += 1
            for label, att_url in attachment_links:
                upsert_attachment_record(
                    conn, item_id=row["id"], source_url=att_url,
                    label=label, tz_name=cfg.timezone,
                )
            _time.sleep(0.3)
        except Exception as e:
            print(f"  [harvest] body 수집 실패 {row['id']}: {e}")

    # 2) 첨부파일 추출 (pending 상태)
    pending = select_pending_attachments(conn, limit=arc.attachment_harvest_limit)
    att_ok = att_fail = 0
    for rec in pending:
        # 지정 소스만 첨부파일 추출
        if rec["source"] not in arc.sources_for_attachments:
            continue
        try:
            mime_type, text, error = download_and_extract(
                http,
                rec["source_url"],
                cli_path=arc.kordoc_cli_path,
                sleep_seconds=arc.attachment_sleep_seconds,
            )
            status = "success" if error is None and text else "failed"
            update_attachment_result(
                conn,
                item_id=rec["item_id"],
                source_url=rec["source_url"],
                mime_type=mime_type,
                extracted_text=text or None,
                status=status,
                error=error,
            )
            if status == "success":
                att_ok += 1
            else:
                att_fail += 1
        except Exception as e:
            print(f"  [harvest] 첨부 추출 실패 {rec['source_url']}: {e}")
            att_fail += 1

    # 3) FTS 재구축
    fts_count = rebuild_fts(conn)
    print(
        f"[harvest] body {body_ok}/{len(items_no_body)}건 수집, "
        f"첨부 {att_ok}건 성공/{att_fail}건 실패, "
        f"FTS {fts_count}건 인덱싱 완료"
    )


def cmd_harvest(args) -> int:
    """수동으로 콘텐츠 아카이브 수집을 실행합니다."""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    harvest_content(conn, cfg)
    return 0


def cmd_search(args) -> int:
    """아카이브 전문검색."""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    results = search_content(conn, query=args.query, limit=args.limit)
    if not results:
        print("검색 결과 없음.")
        return 0

    print(f"검색 결과: {len(results)}건\n{'─'*60}")
    for r in results:
        spec = SOURCE_SPECS.get(r["source"])
        src_name = spec.name_ko if spec else r["source"]
        imp = (r["importance"] or "low").upper()
        print(f"[{src_name}] ({imp}) {r['title']}")
        print(f"  날짜: {r['published_at'] or '-'}  URL: {r['url']}")
        if r["body_snippet"]:
            print(f"  본문: ...{r['body_snippet']}...")
        if r["attach_snippet"]:
            print(f"  첨부: ...{r['attach_snippet']}...")
        print()
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
        ("harvest", cmd_harvest),
        ("search", cmd_search),
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
        if name == "search":
            sp.add_argument("--query", required=True, help="검색어 (예: '과징금 보험업법')")
            sp.add_argument("--limit", type=int, default=20, help="최대 결과 수 (기본: 20)")
        sp.set_defaults(_fn=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args._fn(args))

