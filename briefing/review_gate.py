"""수동 발송 요청과 당일 최종 편집 검수를 SMTP 전에 확인한다."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from briefing.editorial import body_fingerprint


def require_manual_request(args):
    if not getattr(args, 'manual_request', False):
        raise ValueError('자동발송 중단: 명시적인 발송 요청 후 --manual-request로 실행하세요.')


def require_editorial(day, path=None):
    path = Path(path or f'editorial/{day[:10]}.json')
    if not path.exists():
        raise ValueError('당일 편집본이 없어 발송을 차단함. 편집·검수 후 진행하세요.')
    data = json.loads(path.read_text())
    if data.get('status') != 'ready' or data.get('as_of') != day[:10]:
        raise ValueError('당일 ready 편집본이 필요함. 초안·과거 편집본은 발송할 수 없음.')
    return path


def snapshot(*, day, rendered, items, recipients, editorial_path, attachments=()):
    html = rendered.html
    for a in attachments:
        if a.preview_cid:
            html = html.replace(a.preview_cid, 'reviewed-pdf-' + a.report_id)
    reports = []
    for a in attachments:
        paths = [p for p in Path('reports').glob('*.json')
                 if json.loads(p.read_text()).get('report_id') == a.report_id
                 and json.loads(p.read_text()).get('status') == 'ready']
        if len(paths) != 1:
            raise ValueError('PDF 작성 근거를 찾을 수 없어 검수 중단')
        path = paths[0]
        reports.append((a.report_id, hashlib.sha256(path.read_bytes()).hexdigest()))
    soup = BeautifulSoup(rendered.html, 'html.parser')
    return {
        'day': day[:10],
        'html_sha256': hashlib.sha256(html.encode()).hexdigest(),
        'editorial_sha256': hashlib.sha256(Path(editorial_path).read_bytes()).hexdigest(),
        'sources': sorted((i.id, i.content_hash, body_fingerprint(i.raw_text)) for i in items),
        'recipients': sorted(a.strip().lower() for a in recipients),
        'reports': sorted(reports),
        'reference_columns': len(soup.select('.reference-rail')),
        'related_articles': len(soup.select('.ref-why')),
    }


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save_review(*, path, state, note, no_references_reason='', reviewed_at):
    if not note.strip():
        raise ValueError('본문·첨부·기사·PDF 검수 내용을 기록해야 함')
    if not state['reference_columns'] and not no_references_reason.strip():
        raise ValueError('오른쪽 자료가 0개임. 첨부·기사 확인 후 생략 사유를 기록해야 함')
    record = {'status':'reviewed', 'reviewed_at':reviewed_at, 'note':note,
              'no_references_reason':no_references_reason, 'snapshot':state, 'digest':digest(state)}
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def validate_review(*, day, rendered, items, recipients, editorial_path, attachments=(), path=None):
    require_editorial(day, editorial_path)
    path = Path(path or f'logs/reviews/{day[:10]}.json')
    if not path.exists():
        raise ValueError('최종 검수 기록이 없어 발송을 차단함. delivery-review를 먼저 실행하세요.')
    record = json.loads(path.read_text())
    if record.get('status') != 'reviewed' or not record.get('note', '').strip():
        raise ValueError('검수 미완료')
    if datetime.fromisoformat(record['reviewed_at']).date().isoformat() != day[:10]:
        raise ValueError('당일 검수 기록이 아님')
    current = snapshot(day=day, rendered=rendered, items=items, recipients=recipients,
                       editorial_path=editorial_path, attachments=attachments)
    if record.get('digest') != digest(current) or digest(record.get('snapshot')) != digest(current):
        raise ValueError('검수 후 본문·원문·첨부·편집·수신자가 변경됨. 재검수 필요')
    if not current['reference_columns'] and not record.get('no_references_reason', '').strip():
        raise ValueError('오른쪽 자료 미확인')
    return record
