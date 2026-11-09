import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from briefing.config import load_config
from briefing.delivery import archive_delivery
from briefing.emailer import send_email
from briefing.render import render_email
from briefing.reports import PdfAttachment, build_pdf, prepare_reports, report_candidates, validate_report
from briefing.db import StoredItem

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/'reports/examples/insurance-supervision-20261104.json'


def item(r,**overrides):
    src=r['sources'][0]
    return StoredItem(**dict(dict(id=src['item_id'],source='fsc',category='press',source_item_key='88081',
        title=r['title'],url=src['url'],published_at='2026-11-04',attachments_json='[]',
        content_hash=src['content_hash'],importance='high',importance_reason='',summary='요약',
        last_changed_at='2026-11-04',last_sent_at=None,sent_hash=None),**overrides))


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self.report=json.loads(SAMPLE.read_text())
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.d=self.root/'reports';self.d.mkdir()

    def save(self,report):
        (self.d/'report.json').write_text(json.dumps(report,ensure_ascii=False))

    def test_draft_is_not_attached_and_irrelevant_report_is_not_attached(self):
        self.save(self.report)
        self.assertEqual(prepare_reports([item(self.report)],report_dir=self.d),[])
        self.report['status']='ready';self.save(self.report)
        self.assertEqual(prepare_reports([],report_dir=self.d),[])

    def test_ready_report_mime_archive_and_render_preserve_identical_pdf(self):
        r=self.report;r['status']='ready';self.save(r);it=item(r)
        atts=tuple(prepare_reports([it],report_dir=self.d,output_dir=self.root/'pdf'))
        self.assertEqual(len(atts),1)
        rendered=render_email(items=[it],template_dir=ROOT/'templates',subject='검증',run_date='2026-11-09',report_attachments=atts)
        self.assertIn('상세 리포트 PDF가 첨부되어 있습니다',rendered.html)
        self.assertIn(atts[0].filename,rendered.html)
        cfg=load_config(ROOT/'config.yaml').email
        with patch('briefing.emailer.smtplib.SMTP') as smtp, patch.object(type(cfg.smtp),'password',return_value='test'):
            send_email(cfg=cfg,subject='검증',html_body=rendered.html,text_body='검증',attachments=atts)
        msg=smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
        mime=list(msg.iter_attachments())
        self.assertEqual(mime[0].get_content_type(),'application/pdf')
        self.assertEqual(mime[0].get_filename(),atts[0].filename)
        self.assertEqual(mime[0].get_payload(decode=True),atts[0].content)
        images=[part for part in msg.walk() if part.get_content_type()=='image/png']
        self.assertEqual(len(images),1)
        self.assertEqual(images[0].get_content_disposition(),'inline')
        self.assertEqual(str(images[0]['Content-ID']),'<'+atts[0].preview_cid+'>')
        self.assertEqual(images[0].get_payload(decode=True),atts[0].preview_png)
        self.assertIn('cid:'+atts[0].preview_cid,msg.get_body(preferencelist=('html',)).get_content())
        self.assertNotIn('상세 리포트 PDF 1건 첨부',rendered.html)
        self.assertIn('PDF 미리보기 펼치기 / 접기',rendered.html)
        self.assertIn('max-width:2480px',rendered.html)

        manifest=archive_delivery(archive_root=self.root/'archive',run_date='2026-11-09T12:00:00+09:00',
            run_id='test123456',subject='검증',message_id='<test>',recipients=['test@example.com'],html=rendered.html,items=[it],attachments=atts)
        a=json.loads(manifest.read_text())['attachments'][0]
        self.assertEqual(a['sha256'],atts[0].sha256)
        self.assertEqual(a['preview']['cid'],atts[0].preview_cid)
        self.assertEqual((manifest.parent/a['preview']['archive_file']).read_bytes(),atts[0].preview_png)

        self.assertEqual((manifest.parent/a['archive_file']).read_bytes(),atts[0].content)
        self.assertEqual(rendered.visible_item_ids,(it.id,))

    def test_stale_or_wrong_source_does_not_attach(self):
        self.report['status']='ready';self.save(self.report)
        with self.assertRaisesRegex(ValueError,'원문 버전'):
            prepare_reports([item(self.report,content_hash='b'*64)],report_dir=self.d)
        with self.assertRaisesRegex(ValueError,'URL'):
            prepare_reports([item(self.report,url='https://www.fsc.go.kr/wrong')],report_dir=self.d)

    def test_legislation_requires_comparison_and_industry_impact(self):
        r=self.report;r['kind']='legislation';r['stage']='발의·제출'
        with self.assertRaises(ValueError):validate_report(r)
        r['key_points']=[{'topic':'보고 절차','current':'현행 원문 확인 필요','proposed':'개정안의 보고 절차','impact':'확정될 경우 업무절차 검토 필요'}]
        with self.assertRaisesRegex(ValueError,'보험업 관련 영향'):validate_report(r)
        r['industry_impact']='해당 안이 확정될 경우를 전제로 영향 확인 필요'
        validate_report(r)
        build_pdf(r,self.root/'legislation.pdf')

    def test_evidence_missing_or_external_source_cannot_be_ready(self):
        for field in ('relevance','explanation_value','evidence'):
            r=copy.deepcopy(self.report);r['selection'][field]=''
            with self.assertRaises(ValueError):validate_report(r)
        r=copy.deepcopy(self.report);r['sources'][0]['url']='https://www.fsc.go.kr.evil.example/a'
        with self.assertRaises(ValueError):validate_report(r)

    def test_overlong_pdf_failure_preserves_existing_file(self):
        target=self.root/'report.pdf';target.write_bytes(b'original')
        self.report['business_reference']=['긴 문장 '*40]*35
        with self.assertRaisesRegex(ValueError,'1페이지'):
            build_pdf(self.report,target)
        self.assertEqual(target.read_bytes(),b'original')

    def test_screening_does_not_select_high_event_or_assert_eligibility(self):
        event=item(self.report,title='보험회사 시상식 개최')
        self.assertEqual(report_candidates([event]),[])
        c=report_candidates([item(self.report)])
        self.assertEqual(c[0]['status'],'검토 후보')
        self.assertEqual(len(c[0]['selection_checks']),3)

    def test_attachment_filename_and_pdf_are_validated(self):
        for name,content in [('../x.pdf',b'%PDF-1.4'),('x.pdf',b'HTML')]:
            with self.assertRaises(ValueError):PdfAttachment(name,content)

class ReportRunIntegrationTests(unittest.TestCase):
    def test_run_sends_archives_and_marks_same_pdf_without_real_smtp(self):
        import os
        import sqlite3
        from dataclasses import replace
        from datetime import datetime
        from briefing.cli import _cmd_run
        from briefing.db import init_db, upsert_items, select_pending_for_email, create_delivery_run
        from briefing.types import FetchedItem
        cfg=load_config(ROOT/'config.yaml')
        cfg=replace(cfg, archive=replace(cfg.archive,enabled=False),
                    jid_watch=replace(cfg.jid_watch,enabled=False),
                    tagging=replace(cfg.tagging,enabled=False))
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        self.addCleanup(conn.close);init_db(conn)
        r=json.loads(SAMPLE.read_text())
        upsert_items(conn,[FetchedItem(source='fsc',category='press',source_item_key='88081',
                     title='보험업감독규정 개정',url=r['sources'][0]['url'],
                     published_at=datetime.now(),raw_text='공식 본문')],tz_name=cfg.timezone)
        conn.execute("UPDATE items SET importance='high',summary='핵심 요약'");conn.commit()
        it=select_pending_for_email(conn,max_days_since_published=99999)[0]
        r['status']='ready';r['sources'][0]['item_id']=it.id;r['sources'][0]['content_hash']=it.content_hash
        run_id=create_delivery_run(conn,tz_name=cfg.timezone)
        with tempfile.TemporaryDirectory() as tmp:
            cwd=os.getcwd()
            try:
                os.chdir(tmp);Path('templates').symlink_to(ROOT/'templates',target_is_directory=True)
                Path('reports').mkdir();Path('reports/ready.json').write_text(json.dumps(r,ensure_ascii=False))
                with patch('briefing.cli._fetch_all',return_value=([],[],{})), \
                     patch('briefing.cli._enrich'), patch('briefing.cli.run_sanity_checks',return_value=[]), \
                     patch('briefing.cli.send_email',return_value='<test>') as send:
                    self.assertEqual(_cmd_run(SimpleNamespace(send_anyway=False),cfg,conn,run_id),0)
                attachments=send.call_args.kwargs['attachments']
                ledger=conn.execute('SELECT * FROM delivery_runs WHERE run_id=?',(run_id,)).fetchone()
                self.assertEqual(ledger['stage'],'delivered')
                manifest=json.loads(Path(ledger['archive_path']).read_text())
                self.assertEqual(manifest['attachments'][0]['sha256'],attachments[0].sha256)
                self.assertIn(attachments[0].filename,send.call_args.kwargs['html_body'])
                self.assertEqual(conn.execute('SELECT sent_hash FROM items').fetchone()[0],it.content_hash)
            finally:os.chdir(cwd)

class ColorReportLayoutTests(unittest.TestCase):
    def test_approved_colors_headings_and_no_department_label(self):
        import fitz
        r=json.loads((ROOT/'reports/examples/insurance-sales-company-bill-2221395.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            out=build_pdf(r,Path(tmp)/'color.pdf')
            with fitz.open(out) as doc:
                self.assertEqual(len(doc),1)
                text=doc[0].get_text()
                for value in ('미확정 법안','주요 변경사항','보험업 관련 영향','업무에 참고할 사항'):
                    self.assertIn(value,text)
                self.assertNotIn('법무팀',text)
                self.assertFalse(doc.metadata.get('author'))
                fonts={f[3] for f in doc[0].get_fonts()}
                self.assertTrue(any('KoPubWorld' in f for f in fonts))
                fills=[d.get('fill') for d in doc[0].get_drawings() if d.get('fill')]
                self.assertTrue(any(c[0] > .95 and c[1] > .9 and c[2] < .95 for c in fills))
                self.assertTrue(any(c[2] > c[0] for c in fills))

    def test_regulation_and_ready_status_are_not_labelled_pending_or_draft(self):
        import fitz
        r=json.loads(SAMPLE.read_text());r['status']='ready'
        with tempfile.TemporaryDirectory() as tmp:
            with fitz.open(build_pdf(r,Path(tmp)/'ready.pdf')) as doc:
                text=doc[0].get_text()
                self.assertIn('의결 발표',text)
                self.assertNotIn('미확정 법안',text)
                self.assertNotIn('검토용 시안',text)
                self.assertNotIn('법무팀',text)

    def test_custom_fonts_must_be_supplied_as_pair(self):
        r=json.loads(SAMPLE.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'함께 지정'):
                build_pdf(r,Path(tmp)/'bad.pdf',font_path='custom.ttf')
