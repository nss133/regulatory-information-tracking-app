import unittest
from datetime import datetime
from briefing.sources.na import NaAssemblyConnector


def page(bill, title='보험업법 일부개정법률안', total=2, current=1):
    today = datetime.now().strftime('%Y-%m-%d')
    return f'''<table><tr><td>2220001</td><td><a data-bill-id="{bill}" title="{title}">{title}</a></td><td>의원</td><td>{today}</td><td>{today}</td><td>원안가결</td><td></td><td>소관위접수</td></tr></table><input id="totalPages" value=" ({current}/{total} 페이지)">'''


class FakeHttp:
    def __init__(self): self.requests=[]
    def post_text(self, url, *, data, extra_headers=None):
        self.requests.append(dict(data))
        return page('B'+data['page'], current=int(data['page']))
    def get_text(self, *a, **kw): return '<pre>제안이유 및 주요내용: 보험계약자 보호</pre>'


class NaCollectionTests(unittest.TestCase):
    def test_pass_uses_official_codes_and_follows_pagination(self):
        http=FakeHttp()
        items=NaAssemblyConnector(http)._fetch_plenary_pass()
        self.assertEqual(http.requests[0]['mainResultCd'], 'PRC_0013')
        self.assertEqual(http.requests[0]['billKind'], 'BKC_0004')
        self.assertEqual(len(items), 2)
        self.assertIn('procFrom',http.requests[0])
        self.assertIn('procTo',http.requests[0])

    def test_new_proposals_use_proposal_date_and_dedupe(self):
        http=FakeHttp()
        items=NaAssemblyConnector(http)._fetch_recent_proposals()
        self.assertEqual(len(items),2)
        self.assertIn('fromPropDt',http.requests[0])
        self.assertTrue(items[0].source_item_key.startswith('발의·제출:'))
        self.assertIn('심사진행상태: 소관위접수', items[0].raw_text)

    def test_wrong_page_shape_is_not_silent_success(self):
        class Broken(FakeHttp):
            def post_text(self,*a,**kw): return '<html>로그인이 필요합니다</html>'
        with self.assertRaisesRegex(ValueError,'목록 구조'):
            NaAssemblyConnector(Broken())._fetch_plenary_pass()

    def test_law_match_does_not_collect_special_laws_containing_minbeop(self):
        c=NaAssemblyConnector(FakeHttp())
        self.assertEqual(c._parse_sch_html(page('x','난민법 일부개정법률안'),milestone='발의·제출',date_col=3),[])

    def test_stage_change_changes_content_version_but_keeps_bill_key(self):
        from briefing.utils import content_hash
        c=NaAssemblyConnector(FakeHttp())
        before=c._parse_sch_html(page('B1'),milestone='발의·제출',date_col=3)[0]
        after=c._parse_sch_html(page('B1').replace('소관위접수','소관위심사'),milestone='발의·제출',date_col=3)[0]
        self.assertEqual(before.source_item_key,after.source_item_key)
        self.assertNotEqual(content_hash(before.title,before.url,'[]'),
                            content_hash(after.title,after.url,'[]'))
