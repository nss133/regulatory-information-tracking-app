"""본부 공용 컬러 상세 리포트. 한글 글꼴을 내장한 1페이지 PDF를 생성한다."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape


def _font_paths(light_path: str | None, bold_path: str | None) -> tuple[Path, Path]:
    if bool(light_path) != bool(bold_path):
        raise ValueError('본문·제목용 TTF 글꼴을 --font와 --bold-font로 함께 지정하세요.')
    if light_path:
        pair = (Path(light_path), Path(bold_path))
    else:
        font_dir = Path.home() / 'Library/Fonts'
        pairs = [
            (font_dir / 'KoPubWorld Dotum Light.ttf', font_dir / 'KoPubWorld Dotum Bold.ttf'),
            (Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf'),
             Path('/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf')),
        ]
        pair = next((p for p in pairs if all(f.is_file() for f in p)), pairs[0])
    if not all(p.is_file() for p in pair):
        raise RuntimeError('한글 Light·Bold TTF 글꼴이 필요합니다. --font와 --bold-font로 지정하세요.')
    return pair


def render_report_pdf(report: dict, out: str | Path, *, font_path: str | None = None,
                      bold_font_path: str | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from briefing.reports import PENDING
    import hashlib

    light, bold = _font_paths(font_path, bold_font_path)
    font_names = []
    for role, path in (('Light', light), ('Bold', bold)):
        name = 'Briefing' + role + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
        font_names.append(name)
    light_name, bold_name = font_names
    navy, blue = colors.HexColor('#183957'), colors.HexColor('#245f8c')
    ink, muted = colors.HexColor('#253443'), colors.HexColor('#667787')
    line = colors.HexColor('#d8e2eb')
    width = A4[0] - 28 * mm
    styles = {
        'eyebrow': ParagraphStyle('eyebrow', fontName=bold_name, fontSize=9, leading=13,
                                 textColor=blue, spaceAfter=7),
        'title': ParagraphStyle('title', fontName=bold_name, fontSize=20, leading=27,
                               textColor=navy, spaceAfter=8, wordWrap='CJK'),
        'meta': ParagraphStyle('meta', fontName=light_name, fontSize=9, leading=14, textColor=muted),
        'head': ParagraphStyle('head', fontName=bold_name, fontSize=11, leading=16,
                              textColor=blue, spaceAfter=6, spaceBefore=14, keepWithNext=True),
        'body': ParagraphStyle('body', fontName=light_name, fontSize=10, leading=16,
                              textColor=ink, spaceAfter=5, wordWrap='CJK'),
        'bullet': ParagraphStyle('bullet', fontName=light_name, fontSize=10, leading=16,
                                textColor=ink, leftIndent=9, firstLineIndent=-9,
                                spaceAfter=5, wordWrap='CJK'),
        'cell': ParagraphStyle('cell', fontName=light_name, fontSize=9, leading=13.5,
                              textColor=ink, wordWrap='CJK'),
        'cellhead': ParagraphStyle('cellhead', fontName=bold_name, fontSize=9, leading=13,
                                  textColor=blue, wordWrap='CJK'),
        'status': ParagraphStyle('status', fontName=light_name, fontSize=9, leading=14,
                                textColor=colors.HexColor('#73501c'), wordWrap='CJK'),
        'source': ParagraphStyle('source', fontName=light_name, fontSize=8, leading=11.5,
                                textColor=muted, wordWrap='CJK', spaceAfter=3),
    }

    def paragraph(text: str, style: str = 'body') -> Paragraph:
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])

    legislative = report['kind'] == 'legislation'
    labels = {'legislation': '입법동향', 'regulation': '제도·규제동향',
              'judgment': '판결·분쟁조정', 'supervision': '감독·제재동향', 'trend': '주요 동향'}
    title = report.get('display_title') or report['title']
    metadata = [report.get('reference_label', ''), report['as_of'].replace('-', '.') + '. 기준']
    if report['status'] == 'draft':
        metadata.append('검토용 시안')
    story = [paragraph(labels[report['kind']] + '  ·  상세 리포트', 'eyebrow'),
             paragraph(title, 'title'),
             paragraph('  |  '.join(m for m in metadata if m), 'meta'), Spacer(1, 10)]
    if report['stage'] in PENDING:
        status = ('미확정 법안' if legislative else '미확정 사항') + '  ·  ' + report['stage']
        if report.get('stage_note'):
            status += '\n' + report['stage_note']
        status += '\n심사·의견수렴 과정에서 변경될 수 있으며, 의무와 시행일은 확정되지 않음.'
    elif report['stage'] == '본회의 가결':
        status = '국회 의결  ·  공포 여부·시행일 및 경과조치는 별도 확인이 필요함.'
    else:
        status = report['stage'] + '\n' + (report.get('stage_note') or '공식 자료의 확인 범위에 따라 주요 내용을 정리함.')
    notice = Table([[paragraph(status, 'status')]], colWidths=[width])
    notice.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff7e9')),
        ('LINEBEFORE', (0, 0), (0, -1), 2, colors.HexColor('#d8a250')),
        ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    story.append(notice)
    if report.get('change_note'):
        story.append(paragraph('변경: ' + report['change_note']))
    story.append(paragraph('■ 입법 취지·진행경과' if legislative else '■ 주요 내용·진행경과', 'head'))
    story.extend(paragraph(text) for text in report['overview'])
    story.append(paragraph('■ 주요 변경사항' if legislative else '■ 핵심 내용·확인사항', 'head'))
    headers = ('구분', '개정안 주요 내용', '업무 관련성') if legislative else ('구분', '주요 내용', '적용 시점·상태')
    keys = ('topic', 'proposed', 'impact') if legislative else ('topic', 'detail', 'timing')
    data = [[paragraph(text, 'cellhead') for text in headers]]
    for row in report['key_points']:
        data.append([paragraph(row[key], 'cellhead' if i == 0 else 'cell')
                     for i, key in enumerate(keys)])
    table = Table(data, colWidths=[width * x for x in (.15, .57, .28)], hAlign='LEFT', repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#edf4fa')),
        ('LINEABOVE', (0, 0), (-1, 0), .7, line), ('LINEBELOW', (0, 0), (-1, 0), .7, line),
        ('LINEBELOW', (0, 1), (-1, -1), .4, line), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    story.append(table)
    if legislative:
        story.extend([Spacer(1, 5), paragraph(report.get('comparison_basis') or
                      '현행 대비 변경은 발의안의 신·구조문대비표 기준이며, 주요 사항을 발췌함.', 'source'),
                      paragraph('■ 보험업 관련 영향', 'head'), paragraph(report['industry_impact'])])
    story.append(paragraph('■ 업무에 참고할 사항', 'head'))
    story.extend(paragraph('• ' + text, 'bullet') for text in report['business_reference'])
    source = [paragraph('공식 출처 및 확인 범위', 'cellhead')]
    for index, item in enumerate(report['sources'], 1):
        url = escape(item['url'], {'"': '&quot;'})
        label = escape(item['label'])
        source.append(Paragraph(f'{index}. <link href="{url}" color="#56728b">{label}</link>', styles['source']))
    source.append(paragraph(report['verification_scope'], 'source'))
    box = Table([[source]], colWidths=[width])
    box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f5f8fb')),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.extend([Spacer(1, 10), box])

    class OnePageCanvas(Canvas):
        def save(self):
            if self._pageNumber > 2:
                raise ValueError('리포트가 A4 1페이지를 초과함. 본문을 줄이거나 상세사항을 분리하세요.')
            super().save()

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix='.pdf', dir=out.parent)
    os.close(fd)
    try:
        SimpleDocTemplate(temporary, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                          topMargin=13 * mm, bottomMargin=13 * mm,
                          title=title.replace('\n', ' '), author='').build(story, canvasmaker=OnePageCanvas)
        Path(temporary).replace(out)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return out
