import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from briefing.cli import cmd_run,cmd_resend_last
from briefing.review_gate import require_editorial,snapshot,save_review,validate_review

class ReviewGateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.p=Path(self.tmp.name);self.ed=self.p/'ed.json';self.ed.write_text(json.dumps({'as_of':'2026-11-13','status':'ready','issues':[]}));self.review=self.p/'review.json'
  self.item=SimpleNamespace(id='a',content_hash='v1',raw_text='원문')
  self.rendered=SimpleNamespace(html='<div class="reference-rail">검수한 첨부</div>')
  self.kw=dict(day='2026-11-13',rendered=self.rendered,items=[self.item],recipients=['user@example.com'],editorial_path=self.ed)
 def seal(self,**kw):
  return save_review(path=self.review,state=snapshot(**self.kw),note='본문·첨부·기사 검토',reviewed_at='2026-11-13T11:00:00+09:00',**kw)
 def test_scheduled_cli_stops_before_db_or_fetch(self):
  for fn in (cmd_run,cmd_resend_last):
   with patch('briefing.cli.connect') as db,patch('briefing.cli._fetch_all') as fetch,self.assertRaises(ValueError):fn(SimpleNamespace(manual_request=False))
   db.assert_not_called();fetch.assert_not_called()
 def test_missing_draft_or_old_editorial_blocks(self):
  with self.assertRaises(ValueError):require_editorial('2026-11-13',self.p/'missing')
  for d in ({'as_of':'2026-11-13','status':'draft'},{'as_of':'2026-11-12','status':'ready'}):
   self.ed.write_text(json.dumps(d))
   with self.assertRaises(ValueError):require_editorial('2026-11-13',self.ed)
 def test_missing_review_blocks(self):
  with self.assertRaises(ValueError):validate_review(**self.kw,path=self.review)
 def test_sealed_review_passes(self):
  self.seal();self.assertEqual(validate_review(**self.kw,path=self.review)['status'],'reviewed')
 def test_mutations_require_review_again(self):
  self.seal()
  for kw in (dict(rendered=SimpleNamespace(html='변경한 본문')),dict(recipients=['other@example.com']),dict(items=[SimpleNamespace(id='a',content_hash='v2',raw_text='바뀐 원문')])):
   with self.subTest(change=kw),self.assertRaises(ValueError):validate_review(**dict(self.kw,**kw),path=self.review)
  self.ed.write_text(self.ed.read_text()+' ')
  with self.assertRaises(ValueError):validate_review(**self.kw,path=self.review)
 def test_no_references_must_be_explained(self):
  self.rendered.html='본문만 존재'
  with self.assertRaises(ValueError):self.seal()
  self.seal(no_references_reason='공식 첨부와 관련 기사를 확인했으나 추가자료 없음')
  self.assertEqual(validate_review(**self.kw,path=self.review)['snapshot']['reference_columns'],0)
 def test_yesterday_review_rejected(self):
  r=self.seal();r['reviewed_at']='2026-11-12T11:00:00+09:00';self.review.write_text(json.dumps(r))
  with self.assertRaises(ValueError):validate_review(**self.kw,path=self.review)
 def test_direct_email_helper_also_blocks_before_network(self):
  from briefing.emailer import send_email
  with patch('briefing.emailer.smtplib.SMTP') as smtp,self.assertRaises(ValueError):
   send_email(cfg=SimpleNamespace(),subject='제목',html_body='본문',text_body='본문')
  smtp.assert_not_called()
 def test_pdf_metadata_cid_change_is_allowed_but_report_change_is_not(self):
  from briefing.reports import PdfAttachment
  from unittest.mock import patch
  import os
  cwd=os.getcwd()
  try:
   os.chdir(self.p);Path('reports').mkdir();f=Path('reports/custom-filename.json');f.write_text('{"report_id":"r","status":"ready"}')
   a=PdfAttachment('r.pdf',b'%PDF-one','r',(),b'\x89PNG\r\n\x1a\n1');b=PdfAttachment('r.pdf',b'%PDF-two','r',(),b'\x89PNG\r\n\x1a\n2')
   one=dict(self.kw,rendered=SimpleNamespace(html='cid:'+a.preview_cid),attachments=(a,))
   two=dict(self.kw,rendered=SimpleNamespace(html='cid:'+b.preview_cid),attachments=(b,))
   self.assertEqual(snapshot(**one),snapshot(**two))
   old=snapshot(**one);f.write_text('{"report_id":"r","status":"ready","title":"다른내용"}')
   self.assertNotEqual(snapshot(**one),old)
  finally:os.chdir(cwd)
