from __future__ import annotations

import argparse
import json
import sys
from email.utils import make_msgid
from pathlib import Path
from types import SimpleNamespace

from briefing.config import load_config
from briefing.db import (
    AmbiguousDeliveryError,
    connect,
    create_delivery_run,
    has_delivery_watch_alert,
    init_db,
    mark_old_kofiu_announce_as_sent,
    mark_sent,
    mark_suppressed,
    prepare_delivery_attempt,
    record_delivery_watch_alert,
    rebuild_fts,
    record_source_fetches,
    reset_sent_after,
    search_content,
    select_primary_attachment_text,
    select_recent_delivery_runs,
    select_items_missing_body,
    select_last_sent_batch,
    select_pending_attachments,
    select_pending_for_email,
    update_attachment_result,
    update_body_text,
    upsert_attachment_record,
    upsert_items,
    update_item_enrichment,
    update_item_relevance,
    update_delivery_run,
)
from briefing.delivery import archive_delivery, delivery_fingerprint
from briefing.deadlines import deadlines_to_json, extract_deadlines
from briefing.dashboard import render_dashboard, write_dashboard
from briefing.emailer import send_admin_alert, send_email, send_error_alert
from briefing.extract import extract_main_text, extract_page_content
from briefing.http import HttpClient
from briefing.ranking import rank_item
from briefing.relevance import build_score_text, build_scorer, sector_signals
from briefing.jid_watch import (
    append_log as append_jid_log,
    build_citation_index,
    find_impacts,
    persist_impact_candidates,
    prune_legacy_court_candidates,
    review_impact_candidate,
    select_impact_queue,
)
from briefing.render import RELEVANCE_STAR_THRESHOLD
from briefing.render import render_email, render_email_html
from briefing.reports import prepare_reports, report_candidates
from briefing.reports_cli import register_commands as register_report_commands
from briefing.editorial import load_attachment_links, load_editorial
from briefing.review_gate import require_manual_request, require_editorial, validate_review
from briefing.editorial_cli import register_commands as register_editorial_commands, last_delivery_items
from briefing.sanity import append_log as append_sanity_log
from briefing.sanity import run_sanity_checks
from briefing.sources import build_connectors
from briefing.sources.registry import SOURCE_SPECS
from briefing.summarize import should_call_llm, summarize_with_llm
from briefing.tagging import (
    record_tag_review,
    run_shadow_tagging,
    search_items_by_tags,
    select_review_queue,
)
from briefing.utils import now_iso
from briefing.watch import assess_delivery_run
from briefing.maintenance import rotate_logs, verify_sqlite_backup


def _render_text(items) -> str:
    lines: list[str] = []
    for it in items:
        spec = SOURCE_SPECS.get(it.source)
        src = spec.name_ko if spec else it.source
        tag = (it.importance or "low").upper()
        star = "★ " if (it.relevance_score or 0) >= RELEVANCE_STAR_THRESHOLD else ""
        lines.append(f"{star}[{src}] ({tag}) {it.title}")
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


def _is_scourt_digest_title(title: str | None) -> bool:
    """대법원 판례속보의 선고일별 중요판결·결정 종합 자료인지 판정한다."""
    return bool(_re.search(r"중요\s*(?:판결|결정)\s*요지", title or ""))


def _heuristic_scourt_digest_summary(body: str) -> str | None:
    """LLM을 사용할 수 없을 때 중요판결요지의 최소한의 사건별 개요를 만든다."""
    text = (body or "").strip()
    if not text:
        return None
    case_numbers = list(dict.fromkeys(_re.findall(r"20\d{2}[가-힣]{1,8}\d+", text)))
    issues = []
    for value in _re.findall(r"\[([^\[\]]{10,220})\]", text):
        clean = " ".join(value.split()).strip()
        if clean in {"민사", "형사", "특별", "가사", "행정"}:
            continue
        if clean not in issues:
            issues.append(clean)
        if len(issues) >= 3:
            break
    if not issues:
        return _heuristic_summary(text)
    count_text = f"총 {len(case_numbers)}건" if case_numbers else "복수 사건"
    lines = [f"{count_text}의 중요판결·결정 요지를 묶은 자료임"]
    lines.extend(f"주요 쟁점: {issue.rstrip('.')}임" for issue in issues)
    return " / ".join(lines[:4])


def _rank_scourt_item(*, title, raw_text, category, scourt_cfg, relevance_cfg):
    """대법원 항목을 일반 법무 중요도와 보험 업권 적합성을 함께 분류한다."""
    hay = f"{title or ''}\n{raw_text or ''}".lower()
    has_low = any(k.lower() in hay for k in scourt_cfg.low_keywords)
    life_hits, nonlife_hits = sector_signals(hay, relevance_cfg)
    nonlife_only = bool(nonlife_hits) and not life_hits

    # 약관·불공정·표시광고는 전 산업에 등장하므로 보험 문맥 없이
    # 단독으로 '보험사 관련 HIGH'가 되지 않는다.
    broad_high = {"약관", "불공정", "표시·광고"}
    specific_high = any(
        k.lower() in hay
        for k in scourt_cfg.high_keywords
        if k not in broad_high
    )
    has_insurance_context = "보험" in hay or bool(life_hits)
    contextual_high = any(
        k.lower() in hay for k in scourt_cfg.high_keywords if k in broad_high
    ) and has_insurance_context
    has_high = specific_high or contextual_high
    has_medium = any(k.lower() in hay for k in scourt_cfg.medium_keywords)

    if category == "press":
        if "요지" in (title or ""):
            return "medium", "대법원 보도자료(요지 종합)"
        if has_low:
            return "low", "대법원 보도자료(무관 주제)"
        if has_high:
            return "high", "대법원 보도자료(보험사 관련)"
        return "medium", "대법원 보도자료"

    if nonlife_only:
        return "low", "대법원 판결(손해보험 전용)"
    if has_low:
        return "low", "대법원 판결(무관)"
    if has_high:
        return "high", "대법원 판결(보험사 관련)"
    if has_medium:
        return "medium", "대법원 판결(일반 법령)"
    return "low", "대법원 판결(키워드 매칭 없음)"


def _fetch_all(cfg) -> tuple[list, list[str], dict[str, tuple[int, str | None, float]]]:
    """모든 소스를 병렬 수집하고 소스별 소요시간을 출력합니다.

    Returns: (fetched_items, errors, source_outcomes)
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    connectors = build_connectors(cfg.fetch)

    def _one(c):
        t0 = _time.perf_counter()
        try:
            return c.code, c.fetch_latest(), None, _time.perf_counter() - t0
        except Exception as e:
            return c.code, [], str(e), _time.perf_counter() - t0
        finally:
            if getattr(c, 'diagnostics', None):
                print('[source-diagnostics] ' + json.dumps({'source':c.code,'stages':c.diagnostics}, ensure_ascii=False))

    fetched: list = []
    errors: list[str] = []
    timings: dict[str, float] = {}
    outcomes: dict[str, tuple[int, str | None, float]] = {}
    t0 = _time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(connectors))) as ex:
        for f in as_completed([ex.submit(_one, c) for c in connectors]):
            code, items, err, dt = f.result()
            fetched.extend(items)
            timings[code] = dt
            outcomes[code] = (len(items), err, dt)
            if err:
                errors.append(f"{code}: {err}")
    slow = "·".join(
        f"{k} {v:.1f}s" for k, v in sorted(timings.items(), key=lambda kv: -kv[1])[:3]
    )
    print(f"[timing] fetch {_time.perf_counter() - t0:.1f}s (느린 소스: {slow})")
    return fetched, errors, outcomes


def cmd_fetch(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    fetched, errors, outcomes = _fetch_all(cfg)
    record_source_fetches(conn, outcomes, tz_name=cfg.timezone)
    upserted = upsert_items(conn, fetched, tz_name=cfg.timezone)
    print(f"수집 {len(fetched)}건, DB 반영 {upserted}건")
    if errors:
        print("에러:")
        for e in errors:
            print(f"- {e}")
    return 1 if errors else 0


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


def cmd_delivery_status(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    try:
        init_db(conn)
        rows = select_recent_delivery_runs(conn, limit=args.limit)
    finally:
        conn.close()
    if not rows:
        print("기록된 발송 배치가 없습니다.")
        return 0
    for row in rows:
        duration = (
            f"{float(row['duration_seconds']):.1f}s"
            if row["duration_seconds"] is not None else "-"
        )
        print(
            f"{row['started_at']} | {row['status']}/{row['stage']} | "
            f"수집 {row['fetched_count']} · 후보 {row['candidate_count']} · "
            f"발송 {row['visible_count']} · 제외 {row['suppressed_count']} | "
            f"{duration} | {row['run_id']}"
        )
        if row["message_id"]:
            print(f"  Message-ID: {row['message_id']}")
        if row["archive_path"]:
            print(f"  보관: {row['archive_path']}")
        if row["error"]:
            print(f"  오류: {row['error']}")
    return 0


def cmd_delivery_watch(args) -> int:
    cfg = load_config(args.config)
    checked_at = now_iso(cfg.timezone)
    expected_date = args.date or checked_at[:10]
    conn = connect(cfg.storage.sqlite_path)
    try:
        init_db(conn)
        result = assess_delivery_run(
            conn,
            expected_date=expected_date,
            checked_at=checked_at,
            stale_after_minutes=args.stale_minutes,
        )
        if result.healthy:
            print(f"[delivery-watch] 정상: {result.reason}")
            return 0
        if has_delivery_watch_alert(conn, expected_date):
            print(
                f"[delivery-watch] 이미 경고함: {expected_date} · "
                f"{result.code} · {result.reason}"
            )
            return 1

        subject = f"[Legal·Compliance Signal] 브리핑 실행 확인 필요 ({expected_date})"
        text = (
            f"{expected_date} 브리핑 자동 실행을 확인해 주세요.\n\n"
            f"상태: {result.code}\n"
            f"사유: {result.reason}\n"
            f"배치 ID: {result.run_id or '-'}\n"
            f"확인 시각: {checked_at}\n\n"
            "운영본에서 delivery-status 명령과 launchd 로그를 확인하세요.\n"
        )
        message_id = make_msgid(
            idstring=f"briefing-watch.{expected_date}",
            domain=cfg.email.from_email.split("@")[-1],
        )
        if getattr(args, "dry_run", False):
            print(f"[delivery-watch] DRY-RUN 경고 대상: {subject}")
            print(text)
            return 1
        send_admin_alert(
            cfg=cfg.email, subject=subject, text_body=text, message_id=message_id
        )
        record_delivery_watch_alert(
            conn,
            alert_date=expected_date,
            detected_at=checked_at,
            reason_code=result.code,
            reason=result.reason,
            run_id=result.run_id,
            message_id=message_id,
        )
        print(f"[delivery-watch] 관리자 경고 발송: {result.reason}")
        return 1
    finally:
        conn.close()


def cmd_operations(args) -> int:
    cfg = load_config(args.config)
    if args.rotate_logs:
        rotated = rotate_logs(
            args.log_dir,
            max_bytes=max(1, args.max_log_mb) * 1024 * 1024,
            keep=max(1, args.keep_logs),
        )
        print(f"로그 순환: {', '.join(rotated) if rotated else '대상 없음'}")
    backup = verify_sqlite_backup(args.backup)
    print(
        f"백업 점검: {backup.integrity} · "
        f"{backup.size_bytes / 1024 / 1024:.1f}MB · "
        f"{backup.age_days:.1f}일 전" if backup.age_days is not None
        else f"백업 점검: {backup.integrity} · {backup.path}"
    )
    conn = connect(cfg.storage.sqlite_path)
    try:
        init_db(conn)
        content = render_dashboard(
            conn, generated_at=now_iso(cfg.timezone), backup=backup
        )
    finally:
        conn.close()
    target = write_dashboard(args.dashboard, content)
    print(f"운영 대시보드: {target}")
    return 0 if backup.integrity in ("ok", "missing") else 1


def cmd_preview(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    # 최신 수집부터 수행(프리뷰는 항상 최신 상태를 보게)
    fetched, errors, outcomes = _fetch_all(cfg)
    record_source_fetches(conn, outcomes, tz_name=cfg.timezone)
    upsert_items(conn, fetched, tz_name=cfg.timezone)
    mark_old_kofiu_announce_as_sent(conn, tz_name=cfg.timezone)

    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )
    _enrich(conn, cfg, pending)
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )

    run_date = now_iso(cfg.timezone)
    editorial_issues = load_editorial(Path('editorial') / f'{run_date[:10]}.json', pending, as_of=run_date)
    attachment_links = load_attachment_links(conn, pending)
    draft = render_email(
        items=pending,
        template_dir=Path("templates"),
        subject="",
        run_date=run_date,
        editorial_issues=editorial_issues, attachment_links=attachment_links,
    )
    report_attachments = tuple(prepare_reports([it for it in pending if it.id in draft.visible_item_ids]))
    subject = (
        f"{cfg.email.subject_prefix} {run_date[:10]} "
        f"(브리핑 {draft.visible_count}건 · 수집 {len(pending)}건)"
    )
    html = render_email_html(
        items=pending, template_dir=Path("templates"), subject=subject, run_date=run_date,
        report_attachments=report_attachments,
        editorial_issues=editorial_issues, attachment_links=attachment_links,
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

    # 미래에셋생명 관련성 채점기 — 등급 안 정렬과 ★ 표시에 사용한다.
    # 스냅샷 부재·비활성 시 None이 되어 파이프라인은 그대로 진행.
    scorer = build_scorer(cfg)

    def _save_relevance(item, summary_text, supplemental_text=None) -> None:
        if scorer is None:
            return
        try:
            body = build_score_text(
                source=item.source, raw_text=item.raw_text, summary=summary_text,
                supplemental_text=supplemental_text,
            )
            res = scorer.score(title=item.title, body=body, category=item.category)
            update_item_relevance(
                conn, item_id=item.id,
                relevance_score=res.score, relevance_evidence=res.evidence or None,
            )
        except Exception as e:
            print(f"[relevance] 채점 실패 {item.id}: {e}")

    for it in items:
        attachment_text = select_primary_attachment_text(conn, item_id=it.id)
        # 일반 기관 HTML raw_text에는 메뉴·푸터가 섞일 수 있다. 중요도는 제목에
        # 더해 검증된 첨부본문을 사용하고, 구조화 원문인 대법원·국회만 raw_text를
        # 함께 사용한다. 이로써 제목이 행사성인 감독자료의 실질 쟁점을 놓치지 않는다.
        ranking_parts = [attachment_text]
        if it.source in ("scourt", "na") and it.raw_text:
            ranking_parts.append(it.raw_text)
        ranking_text = "\n".join(part for part in ranking_parts if part)
        rank = rank_item(
            title=it.title, raw_text=ranking_text or None, cfg=cfg.ranking
        )
        importance = rank.importance
        reason = rank.reason
        summary = None
        deadline_text = f"{it.title or ''}\n{it.raw_text or ''}"
        # 시행일·의견제출일은 규범 변경 공고에만 의미가 있다. 판결문과 일반
        # 보도자료의 과거 법령 연혁을 현재 일정으로 오인하지 않도록 범위를 제한한다.
        deadline_labels = (
            {"의견제출", "시행일", "신청기한"}
            if it.category in ("legislation", "admin_notice")
            else set()
        )
        deadline_json = deadlines_to_json(
            extract_deadlines(deadline_text, allowed_labels=deadline_labels)
        )
        # 첨부본문을 앞에 두어 LLM의 본문 길이 제한 안에 핵심 내용이 들어가게 한다.
        body = "\n".join(
            part for part in (attachment_text, it.raw_text or "") if part
        )
        attachment_links = []

        # 입법·행정예고는 중요도와 무관하게 상세 원문에서 명시적 기한을 확인한다.
        # 실패해도 기존 분류·발송 흐름은 유지한다.
        if it.category in ("legislation", "admin_notice") and it.source != "na":
            try:
                page_body, attachment_links = extract_page_content(http, it.url)
                if page_body:
                    body = page_body
                    update_body_text(conn, item_id=it.id, body_text=page_body)
                deadline_json = deadlines_to_json(
                    extract_deadlines(
                        f"{deadline_text}\n{page_body}",
                        allowed_labels=deadline_labels,
                    )
                )
                for label, att_url in attachment_links:
                    upsert_attachment_record(
                        conn, item_id=it.id, source_url=att_url,
                        label=label, tz_name=cfg.timezone,
                    )
            except Exception as e:
                print(f"[deadline] 원문 확인 실패 {it.id}: {e}", file=sys.stderr)

        # 개인정보위의 JS 첨부 버튼도 기존 요약 유무와 무관하게 확인한다.
        if it.source == 'pipc' and importance in ('high', 'medium'):
            try:
                page_body, links = extract_page_content(http, it.url)
                if page_body:
                    update_body_text(conn, item_id=it.id, body_text=page_body)
                for label, att_url in links:
                    upsert_attachment_record(conn, item_id=it.id, source_url=att_url,
                                             label=label, tz_name=cfg.timezone)
            except Exception as e:
                print(f'[attachments] 개인정보위 첨부 확인 실패 {it.id}: {e}', file=sys.stderr)

        # 국회(na) 본회의 가결 의안은 실제 법령 통과를 의미하므로 HIGH 강제
        if it.source == "na" and (it.title or "").startswith("[본회의 가결]"):
            importance = "high"
            reason = "국회 본회의 가결"
        # 법사위 회부는 심사 전 단계이므로 MEDIUM 강제
        elif it.source == "na" and (it.title or "").startswith("[법사위 회부]"):
            importance = "medium"
            reason = "국회 법사위 회부"

        # 대법원은 키워드 기반으로 분류하되, 선고일별 중요판결·결정 요지는
        # 여러 사건이 한 제목에 묶이므로 LLM 요약을 붙여 가시성을 확보한다.
        # 제목이 서술형('일시적으로 2주택을 보유한 원고가 종전 주택의 양도에…')이라
        # 제목만으로는 세금·행정 등 무관 주제를 놓치기 쉬우므로, 본문(raw_text)까지
        # 함께 매칭한다(low → high → medium 순).
        # - 보도자료(press): low(무관)→LOW, high(보험사 관련)→HIGH, 그 외 기본 MEDIUM
        #   (선고일에 세금·행정 판결이 한꺼번에 쏟아져 MEDIUM을 도배하던 문제를
        #    low_keywords 적용으로 차단. 종전엔 press에 low를 적용하지 않았음)
        # - 주요판결(case_law): low → high → medium → 기본 LOW
        if it.source == "scourt":
            importance, reason = _rank_scourt_item(
                title=it.title,
                raw_text=it.raw_text,
                category=it.category,
                scourt_cfg=cfg.scourt_ranking,
                relevance_cfg=cfg.relevance,
            )
            final_summary = it.summary
            if (
                it.category == "press"
                and _is_scourt_digest_title(it.title)
                and not final_summary
            ):
                if should_call_llm(
                    llm=cfg.llm, current_importance=importance
                ):
                    if _llm_call_count > 0 and _llm_sleep > 0:
                        _time.sleep(_llm_sleep)
                    spec = SOURCE_SPECS.get(it.source)
                    res = summarize_with_llm(
                        llm=cfg.llm,
                        title=it.title,
                        body=body,
                        source_name_ko=(spec.name_ko if spec else it.source),
                    )
                    _llm_call_count += 1
                    final_summary = res.summary
                if not final_summary:
                    final_summary = _heuristic_scourt_digest_summary(body)
            update_item_enrichment(
                conn,
                item_id=it.id,
                importance=importance,
                importance_reason=reason,
                summary=final_summary,
                deadlines_json=deadline_json,
            )
            _save_relevance(it, final_summary, attachment_text)
            continue

        if should_call_llm(llm=cfg.llm, current_importance=importance) and not it.summary:
            if _llm_call_count > 0 and _llm_sleep > 0:
                _time.sleep(_llm_sleep)

            # 국회(na)는 의안 상세페이지가 일정 정보 위주라, 별도 fetch한
            # 제안이유·주요내용(raw_text)을 LLM 본문으로 사용
            if it.source == "na" and it.raw_text:
                body = it.raw_text
                attachment_links = []
            elif it.source == "na" and not it.raw_text and (it.title or "").startswith("[법사위 회부]"):
                summary = "법사위 상정 건으로 링크 확인 바람"
                update_item_enrichment(
                    conn, item_id=it.id,
                    importance=importance, importance_reason=reason,
                    summary=summary,
                    deadlines_json=deadline_json,
                )
                _save_relevance(it, summary, attachment_text)
                continue
            else:
                if not body:
                    try:
                        body, attachment_links = extract_page_content(http, it.url)
                    except Exception:
                        body = ""
                        attachment_links = []
            # body text 저장(비어 있거나 상용구로 오염된 경우에만 교체하여 idempotent)
            if body:
                update_body_text(conn, item_id=it.id, body_text=body)
                deadline_json = deadlines_to_json(
                    extract_deadlines(
                        f"{it.title or ''}\n{it.raw_text or ''}\n{body}",
                        allowed_labels=deadline_labels,
                    )
                )
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
            # 사규 연계 신호를 LLM 판단 참고자료로 주입 (충분한 신호가 있을 때만)
            company_context = None
            if scorer is not None:
                pre = scorer.score(
                    title=it.title,
                    body=build_score_text(
                        source=it.source, raw_text=it.raw_text, summary=it.summary,
                        supplemental_text=attachment_text,
                    ),
                    category=it.category,
                )
                if pre.score >= 20 and pre.evidence:
                    company_context = pre.evidence
            res = summarize_with_llm(
                llm=cfg.llm,
                title=it.title,
                body=body,
                source_name_ko=(spec.name_ko if spec else it.source),
                company_context=company_context,
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
            deadlines_json=deadline_json,
        )
        _save_relevance(it, final_summary, attachment_text)

    if _llm_call_count:
        print(f"[timing] enrich 중 LLM 호출 {_llm_call_count}회 "
              f"(호출 간 대기 {_llm_sleep:.1f}s)")


def _cmd_run(args, cfg, conn, run_id: str) -> int:
    require_manual_request(args)

    import time as _time
    _t_run = _time.perf_counter()
    _t_stage = _time.perf_counter()

    # 1) 수집 (소스별 병렬 수집)
    fetched, errors, outcomes = _fetch_all(cfg)
    record_source_fetches(conn, outcomes, tz_name=cfg.timezone)
    upsert_items(conn, fetched, tz_name=cfg.timezone)
    update_delivery_run(
        conn, run_id, stage="fetched", fetched_count=len(fetched),
        error_count=len(errors),
    )

    # KoFIU 최초 활성화 기준선: 오늘 이전 누적 자료는 제외, 이후 신규/변경분만 반영
    n_marked_kofiu = mark_old_kofiu_announce_as_sent(conn, tz_name=cfg.timezone)
    if n_marked_kofiu:
        print(f"KoFIU 기존 누적 자료 {n_marked_kofiu}건 발송 제외(앞으로 신규/변경분만 반영)")

    # 대법원: 과거 mark_old_scourt_as_sent 호출은 매번 실행되어 신규
    # 판결까지 silent 처리해버리는 문제가 있어 제거. 7일 필터로 충분히
    # 통제되며, 대법원은 HIGH로 강제 노출하는 정책 유지.

    # 2) 발송 대상
    _t_stage = _time.perf_counter()
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )
    # 중요도·요약 전에 공식 본문과 첨부를 확보한다. 종전에는 발송 후 harvest하여
    # 제목이 평범한 금감원 자료의 핵심 쟁점이 LOW로 고정되는 구조였다.
    if cfg.archive.enabled:
        _t_stage = _time.perf_counter()
        try:
            harvest_content(conn, cfg)
        except Exception as e:
            print(f"[pre-enrich harvest] 오류 (무시): {e}")
        print(f"[timing] pre-enrich harvest {_time.perf_counter() - _t_stage:.1f}s")
        pending = select_pending_for_email(
            conn, max_days_since_published=cfg.filter_config.max_days_since_published
        )
    update_delivery_run(conn, run_id, stage="enriched", candidate_count=len(pending))
    _enrich(conn, cfg, pending)
    pending = select_pending_for_email(
        conn, max_days_since_published=cfg.filter_config.max_days_since_published
    )
    print(f"[timing] enrich {_time.perf_counter() - _t_stage:.1f}s ({len(pending)}건)")

    # 법률 태그는 검색·검수용 shadow 데이터다. 실패해도 메일 판정·발송은 그대로 진행한다.
    if cfg.tagging.enabled and pending:
        try:
            tag_stats = run_shadow_tagging(
                conn, cfg.tagging, item_ids=[item.id for item in pending]
            )
            print(
                f"[tagging] shadow {tag_stats.processed}/{tag_stats.selected}건, "
                f"태그 {tag_stats.tags}개, 오류 {tag_stats.errors}건"
            )
        except Exception as e:
            print(f"[tagging] shadow 오류 (발송 영향 없음): {e}", file=sys.stderr)

    # 발송 대상 유무와 관계없이 소스 신선도 등 운영 자가진단은 매 실행 수행한다.
    sanity_warnings = run_sanity_checks(conn, pending)
    for w in sanity_warnings:
        print(f"[sanity] ⚠ {w}", file=sys.stderr)
    if sanity_warnings and cfg.sanity.log_note:
        try:
            append_sanity_log(
                cfg.sanity.log_note, sanity_warnings,
                run_date=now_iso(cfg.timezone)[:10],
            )
        except Exception as e:
            print(f"[sanity] 로그 기록 오류 (무시): {e}", file=sys.stderr)

    if not pending:
        print("발송 대상 없음(신규/변경 없음)")
        if errors:
            for e in errors:
                print(f"수집 오류: {e}", file=sys.stderr)
            send_error_alert(
                cfg=cfg.email, errors=errors, run_date=now_iso(cfg.timezone)[:10]
            )
        update_delivery_run(
            conn, run_id, status="failed" if errors else "completed",
            stage="fetch_error" if errors else "no_candidates",
            error="; ".join(errors) if errors else None,
            duration_seconds=_time.perf_counter() - _t_run,
            completed=True, tz_name=cfg.timezone,
        )
        return 1 if errors else 0

    # 판단자산(J-ID) 영향 감시 — 구조화 검수 큐와 vault 로그에 기록한다.
    # J-ID 원문은 자동 변경하지 않으며, 실패해도 메일 발송에는 영향이 없다.
    jid_alerts = []
    if cfg.jid_watch.enabled and cfg.jid_watch.judgments_dir:
        try:
            jid_index = build_citation_index(cfg.jid_watch.judgments_dir)
            jid_alerts = find_impacts(jid_index, pending)
            impact_stats = persist_impact_candidates(
                conn,
                jid_alerts,
                timezone=cfg.timezone,
                source_run_id=run_id,
            )
            if jid_alerts and cfg.jid_watch.log_note:
                append_jid_log(
                    cfg.jid_watch.log_note, jid_alerts,
                    run_date=now_iso(cfg.timezone)[:10],
                )
            print(
                f"[jid-watch] 판단 {jid_index.n_files}건 인덱싱, "
                f"영향 항목 {len(jid_alerts)}건 · "
                f"정확 {impact_stats.exact}건 · 광역 {impact_stats.broad}건"
            )
        except Exception as e:
            print(f"[jid-watch] 오류 (무시): {e}", file=sys.stderr)

    run_date = now_iso(cfg.timezone)
    editorial_issues = load_editorial(Path('editorial') / f'{run_date[:10]}.json', pending, as_of=run_date)
    attachment_links = load_attachment_links(conn, pending)
    draft = render_email(
        items=pending,
        template_dir=Path("templates"),
        subject="",
        run_date=run_date,
        editorial_issues=editorial_issues, attachment_links=attachment_links,
    )
    pending_by_id = {it.id: it for it in pending}
    visible_pending = [
        pending_by_id[item_id]
        for item_id in draft.visible_item_ids
        if item_id in pending_by_id
    ]
    visible_ids = set(draft.visible_item_ids)
    suppressed_ids = [it.id for it in pending if it.id not in visible_ids]
    candidates = report_candidates(visible_pending)
    candidate_path = Path('logs') / 'report-candidates' / f'{run_id}.json'
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[reports] 검토 후보 {len(candidates)}건: {candidate_path}')
    report_attachments = tuple(prepare_reports(visible_pending))
    subject = (
        f"{cfg.email.subject_prefix} {run_date[:10]} "
        f"(브리핑 {draft.visible_count}건 · 수집 {len(pending)}건)"
    )
    rendered = render_email(
        items=pending,
        template_dir=Path("templates"),
        subject=subject,
        run_date=run_date,
        report_attachments=report_attachments,
        editorial_issues=editorial_issues, attachment_links=attachment_links,
    )
    html = rendered.html
    text = rendered.text
    if report_attachments:
        text += "\n상세 리포트(PDF 첨부): " + ", ".join(a.filename for a in report_attachments) + "\n"
    print(
        f"[briefing] 수집 후보 {len(pending)}건, "
        f"메일 노출 {len(visible_pending)}건, 정책 제외 {len(suppressed_ids)}건"
    )
    update_delivery_run(
        conn, run_id, stage="rendered", candidate_count=len(pending),
        visible_count=len(visible_pending), suppressed_count=len(suppressed_ids),
        subject=subject, recipients_json=json.dumps(cfg.email.to, ensure_ascii=False),
    )

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
                send_error_alert(
                    cfg=cfg.email, errors=errors, run_date=run_date[:10]
                )
                update_delivery_run(
                    conn, run_id, status="failed", stage="fetch_error",
                    error="; ".join(errors), completed=True, tz_name=cfg.timezone,
                )
                return 1

    delivery_review = None
    if cfg.email.enabled and visible_pending:
        delivery_review = validate_review(day=run_date, rendered=rendered, items=visible_pending,
                        recipients=cfg.email.to, editorial_path=Path('editorial') / f'{run_date[:10]}.json',
                        attachments=report_attachments)

    if cfg.email.enabled:
        if not visible_pending:
            mark_suppressed(conn, suppressed_ids, tz_name=cfg.timezone)
            print(f"메일 노출 대상 없음(정책 제외 {len(suppressed_ids)}건)")
            update_delivery_run(
                conn, run_id, status="completed", stage="no_visible_items",
                duration_seconds=_time.perf_counter() - _t_run,
                completed=True, tz_name=cfg.timezone,
            )
        else:
            _t_stage = _time.perf_counter()
            fingerprint = delivery_fingerprint(visible_pending, cfg.email.to)
            message_id = make_msgid(
                idstring=f"briefing.{run_id}",
                domain=cfg.email.from_email.split("@")[-1],
            )
            archive_path = archive_delivery(
                archive_root=Path("logs") / "deliveries",
                run_date=run_date,
                run_id=run_id,
                subject=subject,
                message_id=message_id,
                recipients=cfg.email.to,
                html=html,
                items=visible_pending,
                attachments=report_attachments,
                editorial_issues=editorial_issues,
                delivery_review=delivery_review,
            )
            update_delivery_run(conn, run_id, archive_path=str(archive_path))
            prepare_delivery_attempt(
                conn, run_id, fingerprint=fingerprint, message_id=message_id,
            )
            try:
                send_email(
                    cfg=cfg.email, subject=subject, html_body=html, text_body=text,
                    message_id=message_id,
                    attachments=report_attachments,
                    review_context=dict(day=run_date, rendered=rendered, items=visible_pending,
                                        editorial_path=Path('editorial') / f'{run_date[:10]}.json'),
                )
            except Exception as smtp_error:
                # 명시적인 SMTP 예외는 재시도 가능한 실패다. send_email이 정상 반환한
                # 뒤의 장애와 구분하기 위해 이 지점에서 상태를 먼저 확정한다.
                update_delivery_run(
                    conn, run_id, status="failed", stage="smtp_failed",
                    error=f"{type(smtp_error).__name__}: {smtp_error}",
                )
                raise
            update_delivery_run(conn, run_id, status="smtp_accepted", stage="smtp_accepted")
            mark_sent(conn, [it.id for it in visible_pending], tz_name=cfg.timezone)
            mark_suppressed(conn, suppressed_ids, tz_name=cfg.timezone)
            update_delivery_run(
                conn, run_id, status="completed", stage="delivered",
                completed=True, tz_name=cfg.timezone,
            )
            print(
                f"발송 완료: {len(visible_pending)}건 "
                f"(정책 제외 별도 기록 {len(suppressed_ids)}건)"
            )
            print(f"[timing] 발송 {_time.perf_counter() - _t_stage:.1f}s")
    else:
        print("email.enabled=false 이므로 발송하지 않았습니다.")
        print(text)
        update_delivery_run(
            conn, run_id, status="completed", stage="email_disabled",
            duration_seconds=_time.perf_counter() - _t_run,
            completed=True, tz_name=cfg.timezone,
        )

    if errors:
        print("(참고) 수집 오류가 있었으나 발송은 완료되었습니다.")
        send_error_alert(cfg=cfg.email, errors=errors, run_date=run_date[:10])

    # 4) 콘텐츠 아카이브 (이메일 발송 후 실행, 실패해도 종료코드 영향 없음)
    if cfg.archive.enabled:
        _t_stage = _time.perf_counter()
        try:
            harvest_content(conn, cfg)
        except Exception as e:
            print(f"[harvest] 오류 (무시): {e}")
        print(f"[timing] harvest {_time.perf_counter() - _t_stage:.1f}s")

    update_delivery_run(
        conn, run_id, duration_seconds=_time.perf_counter() - _t_run,
        completed=True, tz_name=cfg.timezone,
    )

    return 0


def cmd_run(args) -> int:
    require_manual_request(args)
    cfg = load_config(args.config)
    require_editorial(now_iso(cfg.timezone))
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    run_id = create_delivery_run(conn, tz_name=cfg.timezone)
    try:
        return _cmd_run(args, cfg, conn, run_id)
    except BaseException as exc:
        row = conn.execute(
            "SELECT status, stage FROM delivery_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        status = row["status"] if row else "running"
        stage = row["stage"] if row else "started"
        # 정상적인 Exception(SMTP 거절·연결 실패 등)은 명시적 실패로 재시도 가능하다.
        # KeyboardInterrupt/SystemExit처럼 결과를 확정할 수 없는 중단과, 이전의
        # 불명확 배치를 감지한 경우만 확인 필요 상태로 둔다.
        ambiguous = status in ("sending", "smtp_accepted", "ambiguous")
        try:
            update_delivery_run(
                conn,
                run_id,
                status="ambiguous" if ambiguous else "failed",
                stage=(
                    "delivery_uncertain" if ambiguous
                    else stage if status == "failed"
                    else "failed"
                ),
                error=f"{type(exc).__name__}: {exc}",
                completed=True,
                tz_name=cfg.timezone,
            )
        except Exception as record_error:
            print(f"[delivery] 실패 이력 기록 오류: {record_error}", file=sys.stderr)
        if isinstance(exc, AmbiguousDeliveryError):
            print(f"[delivery] 중복 발송 차단: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


def harvest_content(conn, cfg) -> None:
    """
    body text 수집 + 첨부파일 kordoc 추출 + FTS 재구축.
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
    pending = select_pending_attachments(
        conn, limit=arc.attachment_harvest_limit, sources=arc.sources_for_attachments
    )
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
    require_manual_request(args)
    cfg = load_config(args.config)
    require_editorial(now_iso(cfg.timezone))
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    batch = last_delivery_items(conn)
    if not batch:
        print("직전 발송 배치를 찾지 못했습니다. 먼저 `run`을 1회 실행하세요.")
        return 1

    # 검수한 보관본을 재현한다. 발송 직전 재요약으로 검수 내용을 바꾸지 않는다.

    run_date = now_iso(cfg.timezone)
    subject = f"{cfg.email.subject_prefix} {run_date[:10]} (재발송: {len(batch)}건)"
    editorial_issues = load_editorial(Path('editorial') / f'{run_date[:10]}.json', batch, as_of=run_date)
    attachment_links = load_attachment_links(conn, batch)
    draft = render_email(items=batch, template_dir=Path('templates'), subject=subject, run_date=run_date,
                         editorial_issues=editorial_issues, attachment_links=attachment_links)
    reports = tuple(prepare_reports([it for it in batch if it.id in draft.visible_item_ids]))
    rendered = render_email(items=batch, template_dir=Path('templates'), subject=subject, run_date=run_date,
                            report_attachments=reports, editorial_issues=editorial_issues,
                            attachment_links=attachment_links)
    html, text = rendered.html, rendered.text
    if cfg.email.enabled:
        validate_review(day=run_date, rendered=rendered, items=[it for it in batch if it.id in rendered.visible_item_ids],
                        recipients=cfg.email.to, editorial_path=Path('editorial') / f'{run_date[:10]}.json',
                        attachments=reports)

    if cfg.email.enabled:
        send_email(cfg=cfg.email, subject=subject, html_body=html, text_body=text, attachments=reports,
                   review_context=dict(day=run_date, rendered=rendered, items=[it for it in batch if it.id in rendered.visible_item_ids],
                                       editorial_path=Path('editorial') / f'{run_date[:10]}.json'))
        print(f"재발송 완료: {len(batch)}건")
    else:
        print("email.enabled=false 이므로 발송하지 않았습니다.")
        print(text)
    return 0


def cmd_relevance_backfill(args) -> int:
    """기존 전체 항목에 relevance 점수를 (재)채점합니다. 오프라인·네트워크 불필요.

    스냅샷·lexicon·가중치를 바꾼 뒤 재실행하면 전체가 새 기준으로 갱신됨
    (Phase 2 백테스트·캘리브레이션용).
    """
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)

    scorer = build_scorer(cfg)
    if scorer is None:
        print("relevance 비활성(enabled=false) 또는 스냅샷 없음 — 종료")
        return 1

    rows = conn.execute(
        "SELECT id, source, category, title, raw_text, summary FROM items"
    ).fetchall()
    updates = []
    dist = {"0": 0, "1-19": 0, "20-39": 0, "40-59": 0, "60+": 0}
    for r in rows:
        body = build_score_text(
            source=r["source"], raw_text=r["raw_text"], summary=r["summary"]
        )
        res = scorer.score(title=r["title"], body=body, category=r["category"])
        updates.append(
            {"id": r["id"], "score": res.score, "evidence": res.evidence or None}
        )
        b = ("0" if res.score == 0 else "1-19" if res.score < 20
             else "20-39" if res.score < 40 else "40-59" if res.score < 60 else "60+")
        dist[b] += 1
    conn.executemany(
        """
        UPDATE items
        SET relevance_score = :score, relevance_evidence = :evidence
        WHERE id = :id
        """,
        updates,
    )
    conn.commit()
    print(f"relevance backfill 완료: {len(updates)}건")
    print(f"분포: {dist}")
    return 0


def cmd_tag_backfill(args) -> int:
    """전체 또는 일부 항목에 법률 태그를 shadow 저장한다."""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    stats = run_shadow_tagging(
        conn,
        cfg.tagging,
        force=args.force,
        limit=max(0, args.limit),
    )
    print(
        f"tag backfill 완료: 선택 {stats.selected}건 · 처리 {stats.processed}건 · "
        f"태깅 항목 {stats.tagged_items}건 · 태그 {stats.tags}개 · 오류 {stats.errors}건"
    )
    return 1 if stats.errors else 0


def cmd_tag_review_queue(args) -> int:
    """다의어·저신뢰 태그와 미태깅 ★ 항목을 검수 순서로 표시한다."""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    rows = select_review_queue(conn, limit=max(1, args.limit))
    if not rows:
        print("태그 검수 대기 항목 없음.")
        return 0
    print(f"태그 검수 큐: {len(rows)}건\n{'─' * 70}")
    for row in rows:
        reason = {
            1: "미태깅 ★",
            2: "다의어",
            3: "저신뢰",
        }.get(row["priority"], "검토")
        tag = row["hashtag"] or "(태그 없음)"
        tag_id = row["tag_id"] or "-"
        print(f"[{reason}] {row['title']}")
        print(
            f"  item={row['item_id']}  tag={tag} ({tag_id})  "
            f"score={row['score'] if row['score'] is not None else '-'}"
        )
        print(f"  {row['url']}\n")
    return 0


def cmd_tag_review(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    record_tag_review(
        conn,
        item_id=args.item_id,
        tag_id=args.tag_id,
        decision=args.decision,
        replacement_tag_id=args.replacement_tag_id,
        reason_code=args.reason_code,
        note=args.note,
        reviewer=args.reviewer,
        timezone=cfg.timezone,
    )
    print(f"태그 검수 기록 완료: {args.item_id} / {args.tag_id or '-'} / {args.decision}")
    return 0


def cmd_tag_search(args) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    rows = search_items_by_tags(
        conn,
        tags=args.tag,
        tag_types=args.type,
        match_all=args.match_all,
        limit=max(1, args.limit),
    )
    if not rows:
        print("태그 검색 결과 없음.")
        return 0
    print(f"태그 검색 결과: {len(rows)}건\n{'─' * 70}")
    for row in rows:
        print(f"[{row['source']}] {row['title']}")
        print(f"  태그: {row['matched_tags']}  날짜: {row['published_at'] or '-'}")
        print(f"  {row['url']}\n")
    return 0


def cmd_jid_impact_backfill(args) -> int:
    """기존 뉴스에서 J-ID 영향후보를 다시 찾아 구조화 큐에 적재한다."""
    cfg = load_config(args.config)
    if not cfg.jid_watch.judgments_dir:
        print("jid_watch.judgments_dir가 설정되지 않았습니다.", file=sys.stderr)
        return 1
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    sql = """
        SELECT id, title, url, category, raw_text, content_hash
        FROM items
        WHERE COALESCE(published_at, substr(last_changed_at, 1, 10))
              >= date('now', ?)
          AND NOT (source = 'scourt' AND category = 'case_law')
        ORDER BY COALESCE(published_at, last_changed_at) DESC, id
    """
    params: list[object] = [f"-{max(0, args.days)} days"]
    if args.limit > 0:
        sql += " LIMIT ?"
        params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    items = [SimpleNamespace(**dict(row)) for row in rows]
    index = build_citation_index(cfg.jid_watch.judgments_dir)
    alerts = find_impacts(index, items)
    stats = persist_impact_candidates(conn, alerts, timezone=cfg.timezone)
    pruned = prune_legacy_court_candidates(conn)
    print(
        f"J-ID 영향후보 백필 완료: 판단 {index.n_files}건 · 뉴스 {len(rows)}건 검사 · "
        f"영향 뉴스 {len(alerts)}건 · 정확 {stats.exact}건 · 광역 {stats.broad}건 · "
        f"법원도서관 중복 정리 {pruned}건"
    )
    return 0


def cmd_jid_impact_queue(args) -> int:
    """정확 일치를 우선해 J-ID 영향후보 검수 큐를 표시한다."""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    rows = select_impact_queue(
        conn,
        status=args.status,
        exact_only=args.exact_only,
        limit=max(1, args.limit),
    )
    if not rows:
        print("조건에 맞는 J-ID 영향후보가 없습니다.")
        return 0
    labels = {
        "exact_article": "정확 조문",
        "exact_case": "정확 판례",
        "law_only": "법령명 광역",
    }
    print(f"J-ID 영향후보 검수 큐: {len(rows)}건 (상태: {args.status})\n{'─' * 70}")
    for row in rows:
        print(f"[{labels[row['match_level']]}] {row['item_title']}")
        print(f"  후보: {row['candidate_id']}")
        print(f"  근거: {row['evidence_text']}")
        if row["jid"]:
            print(f"  판단: {row['jid']} ({row['jid_stem'] or '-'})")
        else:
            print(f"  관련 판단 수: {row['related_jid_count']} (개별 영향 미특정)")
        print(f"  원문: {row['item_url'] or '-'}\n")
    print("※ affected 판정도 J-ID 파일을 자동 수정하지 않습니다.")
    return 0


def cmd_jid_impact_review(args) -> int:
    """영향후보에 사람 판정을 기록하고 변경 이력을 남긴다."""
    cfg = load_config(args.config)
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    review_impact_candidate(
        conn,
        candidate_id=args.candidate_id,
        decision=args.decision,
        reason=args.reason,
        reviewer=args.reviewer,
        timezone=cfg.timezone,
    )
    print(f"J-ID 영향후보 검수 완료: {args.candidate_id} / {args.decision}")
    if args.decision == "affected":
        print("J-ID 원문은 변경하지 않았습니다. 승인된 후속 검토에서 반영하세요.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="briefing", description="Daily regulatory briefing")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in [
        ("fetch", cmd_fetch),
        ("list", cmd_list),
        ("delivery-status", cmd_delivery_status),
        ("delivery-watch", cmd_delivery_watch),
        ("operations", cmd_operations),
        ("preview", cmd_preview),
        ("run", cmd_run),
        ("reset-sent", cmd_reset_sent),
        ("resend-last", cmd_resend_last),
        ("harvest", cmd_harvest),
        ("search", cmd_search),
        ("relevance-backfill", cmd_relevance_backfill),
        ("tag-backfill", cmd_tag_backfill),
        ("tag-review-queue", cmd_tag_review_queue),
        ("tag-review", cmd_tag_review),
        ("tag-search", cmd_tag_search),
        ("jid-impact-backfill", cmd_jid_impact_backfill),
        ("jid-impact-queue", cmd_jid_impact_queue),
        ("jid-impact-review", cmd_jid_impact_review),
    ]:
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True, help="config.yaml 경로")
        if name == "preview":
            sp.add_argument("--out", required=True, help="HTML 저장 경로")
        if name in ("run", "resend-last"):
            sp.add_argument("--manual-request", action="store_true", help="명시적인 수동 발송 요청")
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
        if name == "tag-backfill":
            sp.add_argument("--limit", type=int, default=0, help="처리 상한(0=전체)")
            sp.add_argument("--force", action="store_true", help="입력 해시가 같아도 다시 태깅")
        if name == "tag-review-queue":
            sp.add_argument("--limit", type=int, default=50, help="표시할 검수 후보 수")
        if name == "tag-review":
            sp.add_argument("--item-id", required=True)
            sp.add_argument("--tag-id", help="missed 판정이면 생략 가능")
            sp.add_argument(
                "--decision", required=True, choices=("accept", "reject", "edit", "missed")
            )
            sp.add_argument("--replacement-tag-id")
            sp.add_argument("--reason-code")
            sp.add_argument("--note")
            sp.add_argument("--reviewer", default="nsss")
        if name == "tag-search":
            sp.add_argument("--tag", action="append", required=True, help="태그명 또는 #해시태그")
            sp.add_argument("--type", action="append", help="law·provision·issue 등 유형")
            sp.add_argument("--match-all", action="store_true", help="지정 태그를 모두 가진 항목만")
            sp.add_argument("--limit", type=int, default=20)
        if name == "jid-impact-backfill":
            sp.add_argument("--days", type=int, default=60, help="최근 검사 기간(기본: 60일)")
            sp.add_argument("--limit", type=int, default=0, help="검사 상한(0=전체)")
        if name == "jid-impact-queue":
            sp.add_argument(
                "--status",
                choices=("unreviewed", "no_effect", "affected", "deferred"),
                default="unreviewed",
            )
            sp.add_argument("--exact-only", action="store_true", help="정확 조문·판례만 표시")
            sp.add_argument("--limit", type=int, default=50)
        if name == "jid-impact-review":
            sp.add_argument("--candidate-id", required=True)
            sp.add_argument(
                "--decision",
                required=True,
                choices=("unreviewed", "no_effect", "affected", "deferred"),
            )
            sp.add_argument("--reason", required=True, help="판정 근거 또는 보류 사유")
            sp.add_argument("--reviewer", default="nsss")
        if name == "delivery-status":
            sp.add_argument("--limit", type=int, default=10, help="표시할 최근 실행 수")
        if name == "delivery-watch":
            sp.add_argument("--date", help="점검 날짜(기본: 오늘, YYYY-MM-DD)")
            sp.add_argument(
                "--stale-minutes", type=int, default=45,
                help="실행 중 상태를 고장으로 볼 경과시간(기본: 45분)",
            )
            sp.add_argument(
                "--dry-run", action="store_true",
                help="경고 조건만 점검하고 메일과 이력을 남기지 않음",
            )
        if name == "operations":
            sp.add_argument("--dashboard", default="logs/status.html")
            sp.add_argument("--backup", default="data_backup/briefing_weekly.sqlite3")
            sp.add_argument("--log-dir", default="logs")
            sp.add_argument("--rotate-logs", action="store_true")
            sp.add_argument("--max-log-mb", type=int, default=5)
            sp.add_argument("--keep-logs", type=int, default=5)
        sp.set_defaults(_fn=fn)
    register_report_commands(sub)
    register_editorial_commands(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args._fn(args))
