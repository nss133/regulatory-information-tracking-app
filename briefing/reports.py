"""본부 공용 상세 리포트. 외부 API 없이 작성 JSON을 검증하여 PDF로 배포한다."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

OFFICIAL_HOSTS = ('assembly.go.kr', 'fsc.go.kr', 'fss.or.kr', 'scourt.go.kr',
                  'law.go.kr', 'ftc.go.kr', 'kofiu.go.kr', 'pipc.go.kr',
                  'moel.go.kr', 'humanrights.go.kr')
KINDS = {'legislation', 'regulation', 'judgment', 'supervision', 'trend'}
STAGES = {'발의·제출', '입법예고', '행정예고', '심사', '본회의 가결', '공포', '시행',
          '의결 발표', '판결·분쟁조정', '감독·제재', '동향'}
PENDING = {'발의·제출', '입법예고', '행정예고', '심사'}


@dataclass(frozen=True)
class PdfAttachment:
    filename: str
    content: bytes
    report_id: str = ''
    item_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if Path(self.filename).name != self.filename or not self.filename.endswith('.pdf') or any(ord(c) < 32 for c in self.filename):
            raise ValueError('잘못된 PDF 첨부파일명')
        if not self.content.startswith(b'%PDF-'):
            raise ValueError('PDF 형식의 첨부파일만 허용')

    @property
    def sha256(self):
        return hashlib.sha256(self.content).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'리포트 {name}: 내용을 작성해야 함')
    return value.strip()


def validate_report(report: dict) -> dict:
    if not isinstance(report, dict):
        raise ValueError('리포트는 JSON 객체여야 함')
    for key in ('report_id', 'title', 'as_of', 'stage', 'verification_scope'):
        _text(report.get(key), key)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', report['report_id']):
        raise ValueError('report_id는 영문·숫자·하이픈·밑줄만 허용')
    date.fromisoformat(report['as_of'])
    if report.get('kind') not in KINDS or report['stage'] not in STAGES:
        raise ValueError('리포트 유형 또는 진행 단계가 올바르지 않음')
    if report.get('status') not in ('draft', 'ready'):
        raise ValueError('상태는 draft 또는 ready여야 함')
    if type(report.get('revision')) is not int or report['revision'] < 1:
        raise ValueError('revision은 1 이상의 정수여야 함')
    if report['revision'] > 1:
        _text(report.get('change_note'), '변경 내용')
    selection = report.get('selection', {})
    for key in ('relevance', 'explanation_value', 'evidence'):
        _text(selection.get(key), f'선정 사유 {key}')
    for key in ('overview', 'business_reference'):
        if not isinstance(report.get(key), list) or not report[key]:
            raise ValueError(f'{key}는 내용이 있는 목록이어야 함')
        for line in report[key]: _text(line, key)
    sources = report.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('공식 출처가 필요함')
    for src in sources:
        for key in ('item_id', 'content_hash', 'label', 'url'):
            _text(src.get(key), f'출처 {key}')
        if not re.fullmatch(r'[0-9a-f]{64}', src['content_hash']):
            raise ValueError('출처 content_hash가 올바르지 않음')
        u = urlparse(src['url'])
        if u.scheme != 'https' or not any(u.hostname == h or (u.hostname or '').endswith('.'+h) for h in OFFICIAL_HOSTS):
            raise ValueError('공식 기관 HTTPS 출처가 필요함')
    rows = report.get('key_points')
    if not isinstance(rows, list) or not rows:
        raise ValueError('핵심 내용 표가 필요함')
    legislative = report['kind'] == 'legislation'
    for row in rows:
        for key in (('topic','current','proposed','impact') if legislative else ('topic','detail','timing')):
            _text(row.get(key), key)
    if legislative:
        _text(report.get('industry_impact'), '보험업 관련 영향')
    return report


def report_candidates(items) -> list[dict]:
    """키워드는 검토 순서만 제안한다. 선정의 세 조건을 충족했다고 자동 판정하지 않는다."""
    candidates=[]
    for it in items:
        title=it.title or ''
        body=(it.raw_text or '') if it.source in ('na','scourt') else ''
        text=title+' '+body
        direct=any(t in text for t in ('보험업법','보험업감독규정','보험회사','보험계약','보험금','보험모집','계리가정'))
        related=any(t in text for t in ('금융소비자','내부통제','금융회사의 지배구조','신용정보','개인정보','자본시장'))
        issue=any(t in text for t in ('개정','법률안','입법','시행령','규정','판결','분쟁조정','제재','검사','감독','위반'))
        if not (direct or related) or not issue:
            continue
        kind='legislation' if it.source=='na' else ('regulation' if it.category in ('legislation','admin_notice') or any(t in title for t in ('개정','시행령','규정')) else ('judgment' if it.category=='case_law' or '분쟁조정' in title else 'supervision'))
        candidates.append({'item_id':it.id,'content_hash':it.content_hash,'title':title,
            'url':it.url,'published_at':it.published_at,'kind':kind,
            'priority':'보험업 직접 관련' if direct else '관련 법령·통제 업무',
            'status':'검토 후보', 'selection_checks':['업무 관련성 확인','메일 요약 외 설명 실익 확인','공식 근거·확인 범위 확인'],
            'raw_text_available':bool(it.raw_text)})
    return sorted(candidates,key=lambda x:(x['priority']!='보험업 직접 관련',x['title']))


def build_pdf(report: dict, out: str | Path, *, font_path: str | None = None,
              bold_font_path: str | None = None) -> Path:
    validate_report(report)
    from briefing.report_pdf import render_report_pdf
    return render_report_pdf(report, out, font_path=font_path, bold_font_path=bold_font_path)


def prepare_reports(items, *, report_dir='reports', output_dir='output/pdf') -> list[PdfAttachment]:
    """검수 완료(ready)이며 발송 대상 원문 버전과 일치하는 리포트만 첨부한다."""
    by_id={i.id:i for i in items}
    attachments=[]
    seen=set()
    for path in sorted(Path(report_dir).glob('*.json')):
        report=json.loads(path.read_text(encoding='utf-8'))
        if report.get('status')!='ready': continue
        sources=report.get('sources',[])
        if not any(s.get('item_id') in by_id for s in sources): continue
        validate_report(report)
        # 묶음 리포트도 모든 연결 원문의 버전이 확인될 때만 사용한다.
        if not all(s['item_id'] in by_id and by_id[s['item_id']].content_hash==s['content_hash'] for s in sources):
            raise ValueError(f"{path.name}: 원문 버전 변경 또는 연결 항목 미확인. 리포트 갱신 필요")
        if any(by_id[s['item_id']].url != s['url'] for s in sources):
            raise ValueError(f"{path.name}: 수집 원문 URL과 출처 불일치")
        key=report['report_id']
        if key in seen:raise ValueError(f'동일 리포트 중복: {key}')
        seen.add(key)
        name=f"{key}-v{report['revision']}.pdf"
        pdf=build_pdf(report,Path(output_dir)/name)
        attachments.append(PdfAttachment(name,pdf.read_bytes(),key,tuple(s['item_id'] for s in sources)))
    if sum(len(a.content) for a in attachments) > 20*1024*1024:
        raise ValueError('상세 리포트 첨부 합계가 20MB를 초과함')
    return attachments
