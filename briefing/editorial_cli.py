"""편집본 초안·검증·보관본 재현. 이 명령들은 수집·SMTP·발송 상태를 변경하지 않는다."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from bs4 import BeautifulSoup

from briefing.config import load_config
from briefing.db import select_pending_for_email
from briefing.editorial import body_fingerprint, load_attachment_links, load_editorial
from briefing.render import render_email
from briefing.utils import now_iso


def _connect(cfg):
    conn = sqlite3.connect(Path(cfg.storage.sqlite_path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _items(conn, ids):
    result = []
    for key in dict.fromkeys(ids):
        row = conn.execute('SELECT * FROM items WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError(f'수집 원문 미확보: {key}')
        result.append(SimpleNamespace(**dict(row)))
    return result


def restore_archive_presentation(items, manifest, html):
    """DB의 발송 후 상태를 발송 직전 상태로 오인하지 않도록 보관 표현을 복원한다."""
    records = {r['id']: r for r in manifest['items']}
    soup = BeautifulSoup(html, 'html.parser')
    titles = {a['href']: a for a in soup.select('.item-title[href]')}
    for item in items:
        saved = records[item.id].get('presentation')
        if saved is not None:
            for key, value in saved.items():
                if hasattr(item, key):
                    setattr(item, key, value)
            continue
        # 11/11 이전 보관본은 표시 스냅샷이 없어 실제 발송 HTML을 대조한다.
        title = titles.get(item.url)
        if title is None:
            raise ValueError('보관 HTML에서 원문 표시 상태를 확인할 수 없음')
        block = title.find_parent(class_='child-item') or title.find_parent(class_='item')
        section = title.find_parent(class_='section')
        if block is None or section is None:
            raise ValueError('보관 HTML의 항목·섹션 확인 필요')
        priority = next((c.removeprefix('section-') for c in section.get('class', [])
                         if c in ('section-high', 'section-medium', 'section-low')), None)
        if priority:
            item.importance = priority
        was_new = bool(block.select_one('.tag-new'))
        item.last_sent_at = None if was_new else manifest['run_date']
        item.sent_hash = None if was_new else 'archive-previous-version'
        summary = block.select_one('.item-summary')
        item.summary = summary.get_text(' / ', strip=True) if summary else None
        attachments = block.select('.item-attach a')
        item.attachments_json = json.dumps([{'label':a.get('title',a.get_text(strip=True)),
                                            'url':a['href']} for a in attachments], ensure_ascii=False)
    return items


def last_delivery_items(conn):
    row = conn.execute("SELECT archive_path FROM delivery_runs WHERE stage='delivered' ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row or not row['archive_path']:
        raise ValueError('검수할 실제 발송 보관본이 없음')
    path = Path(row['archive_path']); manifest = json.loads(path.read_text())
    items = _items(conn, [r['id'] for r in manifest['items']])
    versions = {r['id']:r['content_hash'] for r in manifest['items']}
    if any(i.content_hash != versions[i.id] for i in items):
        raise ValueError('발송 후 원문 버전 변경. 재발송 자료 재검토 필요')
    return restore_archive_presentation(items, manifest, (path.parent / manifest['html_file']).read_text())


def cmd_init(args):
    cfg = load_config(args.config)
    with closing(_connect(cfg)) as conn:
        items = _items(conn, args.item_id)
    primary = args.primary_item_id or items[0].id
    if primary not in {i.id for i in items}:
        raise ValueError('대표 원문은 --item-id 목록에 포함해야 함')
    data = {'as_of': args.date or now_iso(cfg.timezone)[:10], 'status': 'draft', 'issues': [{
        'primary_item_id': primary,
        'sources': [{'item_id': i.id, 'content_hash': i.content_hash, 'url': i.url,
                     'body_sha256': body_fingerprint(i.raw_text)} for i in items],
        'summary': None, 'stage': '', 'articles': [], 'deadlines': [],
    }]}
    target = Path(args.out); target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'편집 초안: {target} (공식 자료 대조·기사 검토 후 ready로 변경)')
    return 0


def cmd_check(args):
    cfg = load_config(args.config)
    data = json.loads(Path(args.input).read_text())
    ids = [s['item_id'] for i in data.get('issues', []) for s in i.get('sources', [])]
    with closing(_connect(cfg)) as conn:
        items = _items(conn, ids)
    if data.get('status') != 'ready':
        raise ValueError('ready 편집본만 검증 가능')
    issues = load_editorial(args.input, items, as_of=data['as_of'])
    rendered = render_email(items=items, template_dir='templates', subject='편집 검증',
                            run_date=data['as_of'], editorial_issues=issues)
    if set(rendered.visible_item_ids) != set(ids):
        raise ValueError('편집 원문에 메일 정책 제외 항목이 있음')
    print(f'편집 검증 완료: {len(issues)}개 사안 · 관련 보도 {sum(len(i.articles) for i in issues)}건')
    return 0


def cmd_preview(args):
    cfg = load_config(args.config)
    day = args.date or now_iso(cfg.timezone)[:10]
    with closing(_connect(cfg)) as conn:
        if args.archive:
            manifest = json.loads(Path(args.archive).read_text())
            day = manifest['run_date'][:10]
            items = _items(conn, [r['id'] for r in manifest['items']])
            versions = {r['id']: r['content_hash'] for r in manifest['items']}
            if any(i.content_hash != versions[i.id] for i in items):
                raise ValueError('보관본 이후 원문 버전 변경: 해당 시점 자료로 재검토 필요')
            html_path = Path(args.archive).parent / manifest['html_file']
            items = restore_archive_presentation(items, manifest, html_path.read_text())
        else:
            items = select_pending_for_email(conn, max_days_since_published=cfg.filter_config.max_days_since_published)
        issues = load_editorial(args.input or f'editorial/{day}.json', items, as_of=day)
        attachments = load_attachment_links(conn, items)
    result = render_email(items=items, template_dir='templates', subject=f'{day} 브리핑 검토',
                          run_date=day, editorial_issues=issues, attachment_links=attachments)
    path = Path(args.out); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.html, encoding='utf-8')
    path.with_suffix('.txt').write_text(result.text, encoding='utf-8')
    print(f'발송 없는 미리보기: {path} · 원문 {result.visible_count}건')
    return 0


def cmd_review(args):
    from briefing.review_gate import require_editorial, snapshot, save_review
    from briefing.reports import prepare_reports
    cfg = load_config(args.config)
    stamp = now_iso(cfg.timezone)
    path = require_editorial(stamp)
    with closing(_connect(cfg)) as conn:
        items = (last_delivery_items(conn) if args.resend_last else
                 select_pending_for_email(conn, max_days_since_published=cfg.filter_config.max_days_since_published))
        issues = load_editorial(path, items, as_of=stamp)
        links = load_attachment_links(conn, items)
    draft = render_email(items=items, template_dir='templates', subject='', run_date=stamp,
                         editorial_issues=issues, attachment_links=links)
    visible = [i for i in items if i.id in draft.visible_item_ids]
    if not visible:
        raise ValueError('발송할 검수 대상이 없음')
    reports = tuple(prepare_reports(visible))
    subject = (f'{cfg.email.subject_prefix} {stamp[:10]} (재발송: {len(items)}건)' if args.resend_last else
               f'{cfg.email.subject_prefix} {stamp[:10]} (브리핑 {draft.visible_count}건 · 수집 {len(items)}건)')
    rendered = render_email(items=items, template_dir='templates', subject=subject, run_date=stamp,
                            report_attachments=reports, editorial_issues=issues, attachment_links=links)
    state = snapshot(day=stamp, rendered=rendered, items=visible, recipients=cfg.email.to,
                     editorial_path=path, attachments=reports)
    review_path = Path('logs/reviews') / f'{stamp[:10]}.json'
    review_path.parent.mkdir(parents=True, exist_ok=True)
    output = review_path.with_suffix('.html'); output.write_text(rendered.html)
    save_review(path=review_path, state=state, note=args.note,
                no_references_reason=args.no_references_reason, reviewed_at=stamp)
    print(f'최종 검수 기록: {review_path} · 오른쪽 자료 {state["reference_columns"]}개 · 관련 보도 {state["related_articles"]}건')
    print(f'발송 없는 최종 본문: {output}')
    return 0


def register_commands(sub):
    p = sub.add_parser('delivery-review', help='당일 최종 본문·원문·첨부·수신자 검수 기록(발송 없음)')
    p.add_argument('--config', required=True); p.add_argument('--note', required=True)
    p.add_argument('--no-references-reason', default='')
    p.add_argument('--resend-last', action='store_true')
    p.set_defaults(_fn=cmd_review)
    p = sub.add_parser('editorial-init', help='사안·기사 큐레이션 초안 작성(읽기 전용)')
    p.add_argument('--config', required=True); p.add_argument('--date')
    p.add_argument('--item-id', action='append', required=True)
    p.add_argument('--primary-item-id'); p.add_argument('--out', required=True)
    p.set_defaults(_fn=cmd_init)
    p = sub.add_parser('editorial-check', help='편집 원문 버전·기사·노출 정책 검증')
    p.add_argument('--config', required=True); p.add_argument('--input', required=True)
    p.set_defaults(_fn=cmd_check)
    p = sub.add_parser('editorial-preview', help='수집·발송 없이 실제 보관본 또는 발송 후보로 미리보기')
    p.add_argument('--config', required=True); p.add_argument('--date'); p.add_argument('--input')
    p.add_argument('--archive'); p.add_argument('--out', required=True)
    p.set_defaults(_fn=cmd_preview)
