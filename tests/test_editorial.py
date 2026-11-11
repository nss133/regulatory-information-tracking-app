import copy
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from briefing.db import StoredItem, init_db
from briefing.delivery import archive_delivery
from briefing.editorial import load_editorial, load_attachment_links
from briefing.render import render_email
from briefing.summarize import select_summary_evidence

ROOT = Path(__file__).resolve().parents[1]


def item(key='kftc:1', **kw):
    return StoredItem(**dict(dict(id=key, source='kftc', category='press', source_item_key=key,
        title='기업결합 시정조치 부과기준 고시 개정안 행정예고', url='https://www.ftc.go.kr/'+key,
        published_at='2026-11-09', attachments_json='[]', content_hash='a'*64,
        importance='medium', importance_reason='', summary='기존 설명', raw_text='공식 본문',
        last_changed_at='2026-11-09', last_sent_at=None, sent_hash=None), **kw))


def review(items):
    return {'as_of':'2026-11-11','status':'ready','issues':[{
        'primary_item_id':items[-1].id,
        'sources':[{'item_id':i.id,'content_hash':i.content_hash,'url':i.url} for i in items],
        'summary':'구체적인 변경 내용임 / 조건을 보존한 개정안임', 'stage':'행정예고 중',
        'articles':[{'title':'검토한 기사 <제목>', 'url':'https://news.example.com/story',
                     'publisher':'언론사', 'published_at':'2026-11-09','why_read':'변경 조건을 설명함',
                     'checked_at':'2026-11-11'}]}]}


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'edit.json'
        self.press=item(last_sent_at='2026-11-10',sent_hash='b'*64)
        self.notice=item('kftc:2',category='legislation',
            title='「기업결합 시정조치 부과기준」 일부개정안 행정예고',published_at='2026-11-10')
        self.items=[self.press,self.notice]

    def save(self,data):
        self.path.write_text(json.dumps(data,ensure_ascii=False));return self.path

    def render(self,items=None,**kw):
        return render_email(items=items or self.items,template_dir=ROOT/'templates',
                            subject='검증',run_date='2026-11-11',**kw)

    def test_cross_priority_group_preserves_both_sources_and_one_summary(self):
        r=self.render();s=BeautifulSoup(r.html,'html.parser')
        self.assertEqual(set(r.visible_item_ids),{i.id for i in self.items})
        self.assertEqual(len(s.select('.item')),1)
        self.assertEqual(len(s.select('.item-summary')),1)
        title=s.select_one('.item-title');rail=s.select_one('.reference-rail')
        self.assertEqual(title['href'],self.notice.url)
        self.assertNotIn(title['href'],[a['href'] for a in rail.select('a')])
        self.assertIn(self.press.url,[a['href'] for a in rail.select('a')])
        self.assertIn('원문 2건',s.select_one('.sec-header').get_text())

    def test_similar_subject_different_stage_date_or_authority_is_not_merged(self):
        cases=[replace(self.notice,title='기업결합 시정조치 부과기준 개정·시행'),
               replace(self.notice,title='기업결합 시정조치 운영기준 개정안 행정예고'),
               replace(self.notice,published_at='2026-10-20'),
               replace(self.notice,source='fsc')]
        for notice in cases:
            with self.subTest(title=notice.title,date=notice.published_at):
                self.assertEqual(len(BeautifulSoup(self.render([self.press,notice]).html,'html.parser').select('.item')),2)

    def test_low_content_keeps_original_attachment_and_no_extra_links(self):
        low=item('kftc:low',importance='low',title='참고할 별도 자료',
                 attachments_json=json.dumps([{'label':'원본.hwp','url':'https://www.ftc.go.kr/low.hwp'}]))
        html=self.render([low],attachment_links={low.id:[{'label':'다른.pdf','url':'https://www.ftc.go.kr/other'}]}).html
        soup=BeautifulSoup(html,'html.parser')
        self.assertFalse(soup.select('.reference-rail'))
        self.assertEqual(soup.select_one('.item-attach a')['href'],'https://www.ftc.go.kr/low.hwp')
        self.assertEqual(soup.select_one('.item-summary').get_text(),'기존 설명')

    def test_ready_curation_has_one_summary_article_provenance_and_text_parity(self):
        data=review(self.items);issues=load_editorial(self.save(data),self.items,as_of='2026-11-11')
        r=self.render(editorial_issues=issues);s=BeautifulSoup(r.html,'html.parser')
        self.assertEqual(len(s.select('.item-summary')),1)
        self.assertIn('구체적인 변경 내용임',s.select_one('.item-summary').get_text())
        self.assertIn('검토한 기사 <제목>',s.get_text());self.assertFalse(s.select('script'))
        for text in ('언론사','변경 조건을 설명함','https://news.example.com/story','구체적인 변경 내용임'):
            self.assertIn(text,r.text)
        self.assertNotIn('기존 설명',r.text)
        archived=archive_delivery(archive_root=self.tmp.name,run_date='2026-11-11T10:00:00',
            run_id='audit',subject='검증',message_id='<audit>',recipients=[],html=r.html,items=self.items,editorial_issues=issues)
        saved=json.loads(archived.read_text());self.assertEqual(saved['editorial'][0]['articles'][0]['why_read'],'변경 조건을 설명함')

    def test_stale_partial_or_wrong_url_curation_fails_before_rendering(self):
        for change in ('hash','url','missing'):
            data=review(self.items)
            if change=='hash':data['issues'][0]['sources'][0]['content_hash']='c'*64
            if change=='url':data['issues'][0]['sources'][0]['url']='https://www.ftc.go.kr/changed'
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_editorial(self.save(data), self.items[1:] if change=='missing' else self.items,as_of='2026-11-11')

    def test_article_needs_review_date_why_and_nonduplicate_https_url(self):
        cases=[('why_read',''),('url','javascript:alert(1)'),('url',self.notice.url),
               ('published_at','2026-11-12'),('checked_at','2026-11-08')]
        for field,value in cases:
            data=review(self.items);data['issues'][0]['articles'][0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                load_editorial(self.save(data),self.items,as_of='2026-11-11')
        data=review(self.items);data['issues'][0]['articles']*=3
        with self.assertRaises(ValueError):load_editorial(self.save(data),self.items,as_of='2026-11-11')

    def test_early_bill_cannot_enter_through_reviewed_link(self):
        early=item('na:1',source='na',category='legislation',title='[소관위 접수] 보험업법 일부개정법률안',raw_text='심사진행상태: 소관위접수')
        with self.assertRaises(ValueError):load_editorial(self.save(review([early])),[early],as_of='2026-11-11')

    def test_draft_wrong_day_and_unrelated_files(self):
        d=review(self.items);d['status']='draft'
        self.assertEqual(load_editorial(self.save(d),self.items,as_of='2026-11-11'),())
        d['status']='ready'
        with self.assertRaises(ValueError):load_editorial(self.save(d),self.items,as_of='2026-11-12')
        self.assertEqual(load_editorial(self.save(d),[item('unrelated')],as_of='2026-11-11'),())

    def test_attachments_use_verified_current_names_not_old_or_other_hosts(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.execute('create table item_content(item_id text,source_url text,label text,fetched_at text)')
        c.executemany('insert into item_content values(?,?,?,?)',[
            ('kftc:1','https://www.ftc.go.kr/good','개정안(전문).hwpx','2026-11-11'),
            ('kftc:1','https://www.ftc.go.kr/old','과거.pdf','2026-11-09'),
            ('kftc:1','https://bad.example/good','개정안(전문).hwpx','2026-11-11')])
        result=load_attachment_links(c,[replace(self.press,raw_text='첨부파일 개정안(전문).hwpx (hwpx, 30KB)')])
        self.assertEqual([a['url'] for a in result[self.press.id]],['https://www.ftc.go.kr/good']);c.close()


class SummaryEvidenceTests(unittest.TestCase):
    def test_short_text_is_not_changed(self):
        self.assertEqual(select_summary_evidence('다만 적용 예외를 보존함'),'다만 적용 예외를 보존함')

    def test_long_text_keeps_exception_context_and_transition_at_end(self):
        text='제도 개정 주요 내용. '+('일반 설명. '*1000)+'다만 기존 계약은 적용 대상에서 제외한다. '+('별도 설명. '*1000)+'부칙: 2027년 1월 1일 시행하며 종전 계약은 종전 규정을 따른다.'
        result=select_summary_evidence(text)
        self.assertIn('다만 기존 계약은 적용 대상에서 제외한다.',result)
        self.assertIn('종전 계약은 종전 규정을 따른다.',result)
        self.assertLessEqual(len(result),6000)


class EditorialEvidenceTests(unittest.TestCase):
    setUp = EditorialTests.setUp
    save = EditorialTests.save
    render = EditorialTests.render
    def test_body_changes_fail_but_view_counter_does_not(self):
        from briefing.editorial import body_fingerprint
        data=review(self.items)
        data['issues'][0]['sources'][0]['body_sha256']=body_fingerprint(self.press.raw_text)
        self.assertEqual(body_fingerprint('본문 조회수 : 12'),body_fingerprint('본문 조회수 : 45'))
        with self.assertRaisesRegex(ValueError,'본문 변경'):
            load_editorial(self.save(data),[replace(self.press,raw_text='다른 의무가 추가됨'),self.notice],as_of='2026-11-11')

    def test_curated_date_needs_matching_source_quote(self):
        text='2026년 12월 01일까지 의견서를 제출하여 주시기 바랍니다.'
        changed=[self.press,replace(self.notice,raw_text=text)]
        data=review(changed);data['issues'][0]['deadlines']=[{'label':'의견제출','date':'2026-12-01','source_item_id':self.notice.id,'source_text':text}]
        issues=load_editorial(self.save(data),changed,as_of='2026-11-11')
        rendered=self.render(changed,editorial_issues=issues)
        self.assertIn('의견제출 2026-12-01',rendered.html)
        data['issues'][0]['deadlines'][0]['date']='2026-12-02'
        with self.assertRaises(ValueError):load_editorial(self.save(data),changed,as_of='2026-11-11')

    def test_year_inference_requires_explicit_flag_and_same_publication_year(self):
        changed=[self.press,replace(self.notice,raw_text='11월 26일까지 의견을 받는다.')]
        data=review(changed);d={'label':'의견제출','date':'2026-11-26','source_item_id':self.notice.id,'source_text':'11월 26일까지'};data['issues'][0]['deadlines']=[d]
        with self.assertRaises(ValueError):load_editorial(self.save(data),changed,as_of='2026-11-11')
        d['year_from_publication']=True
        self.assertEqual(len(load_editorial(self.save(data),changed,as_of='2026-11-11')[0].deadlines),1)
        d['date']='2027-11-26'
        with self.assertRaises(ValueError):load_editorial(self.save(data),changed,as_of='2026-11-11')

    def test_group_retains_pdf_notice_for_secondary_source(self):
        from briefing.reports import PdfAttachment
        a=PdfAttachment('report.pdf',b'%PDF-test','r',(self.press.id,))
        r=self.render(report_attachments=(a,))
        self.assertIn('report.pdf',r.html)
        self.assertIn('report.pdf',r.text)
        self.assertEqual(set(r.visible_item_ids),{self.press.id,self.notice.id})


class ArchiveReplayTests(unittest.TestCase):
    def test_replay_preserves_updated_low_after_database_marks_sent(self):
        from briefing.editorial_cli import restore_archive_presentation
        from types import SimpleNamespace
        before=item('kftc:older',importance='high',last_sent_at='2026-11-09',sent_hash='b'*64,
                    title='과거 변경 자료',summary='발송 당시의 내용')
        rendered=render_email(items=[before],template_dir=ROOT/'templates',subject='원본',run_date='2026-11-11')
        after=SimpleNamespace(**dict(before.__dict__,sent_hash=before.content_hash,summary='이후 요약'))
        restored=restore_archive_presentation([after],{'run_date':'2026-11-11','items':[{'id':before.id}]},rendered.html)
        result=render_email(items=restored,template_dir=ROOT/'templates',subject='복원',run_date='2026-11-11')
        s=BeautifulSoup(result.html,'html.parser')
        self.assertIsNotNone(s.select_one('.section-low .item'))
        self.assertIsNone(s.select_one('.tag-new'))
        self.assertIn('발송 당시의 내용',result.text)
        self.assertNotIn('이후 요약',result.text)

    def test_new_manifests_freeze_presentation_before_mark_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            i=item(summary='발송 시점 요약')
            path=archive_delivery(archive_root=tmp,run_date='2026-11-11T12:00:00',run_id='frozen',subject='검증',
                                  message_id='<test>',recipients=[],html='본문',items=[i])
            record=json.loads(path.read_text())['items'][0]['presentation']
            self.assertEqual(record['summary'],'발송 시점 요약')
            self.assertIsNone(record['last_sent_at'])


class SummaryPromptTests(unittest.TestCase):
    def test_prompt_preserves_stage_conditions_and_body_tail(self):
        from briefing.config import load_config
        from briefing.summarize import summarize_with_llm
        from types import SimpleNamespace
        cfg=load_config(ROOT/'config.yaml').llm
        body='새 규정의 주요 변경임. '+('일반 설명. '*2500)+'부칙: 종전 계약은 적용하지 않는다.'
        with patch.object(type(cfg),'api_key',return_value='test'),patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"importance":"medium","summary_ko":"조건을 보존한 개정안임"}'))])
            summarize_with_llm(llm=replace(cfg,provider='openai'),title='감독규정 개정안 행정예고',body=body,source_name_ko='기관')
            prompt=client.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
            self.assertIn('종전 계약은 적용하지 않는다.',prompt)
            self.assertIn('적용 대상·조건·예외',prompt)
            self.assertIn('예고·발의·심사 중',prompt)
            self.assertIn('주체별 행위와 제재',prompt)

if __name__=='__main__':unittest.main()
