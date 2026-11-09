import unittest
from types import SimpleNamespace
from briefing.legislation import reportable_legislation
from briefing.sources.na import NaAssemblyConnector
from test_na_collection import FakeHttp, page


class LegislationPolicyTests(unittest.TestCase):
    def test_committee_reception_or_review_is_not_reported(self):
        for state in ('접수','소관위접수','소관위심사','위원회심사','법사위소관'):
            i=SimpleNamespace(source='na',source_item_key='발의·제출:x',raw_text='심사진행상태: '+state)
            self.assertFalse(reportable_legislation(i),state)

    def test_systematic_review_and_later_stages_are_reported(self):
        for state in ('체계자구','체계자구상정','체계자구심사','본회의부의','정부이송','공포'):
            i=SimpleNamespace(source='na',source_item_key='발의·제출:x',raw_text='심사진행상태: '+state)
            self.assertTrue(reportable_legislation(i),state)
        for key in ('체계·자구심사:x','본회의 가결:x','본회의 부의:x'):
            self.assertTrue(reportable_legislation(SimpleNamespace(source='na',source_item_key=key,raw_text=None)))

    def test_other_agency_legislation_not_affected(self):
        self.assertTrue(reportable_legislation(SimpleNamespace(source='fsc')))

    def test_systematic_review_uses_own_code_without_old_proposal_cutoff(self):
        class ReviewHttp(FakeHttp):
            def post_text(self,url,*,data,extra_headers=None):
                self.requests.append(data)
                return page('B1',total=1).replace('소관위접수','체계자구상정').replace(__import__('datetime').datetime.now().strftime('%Y-%m-%d'),'2024-01-01')
        http=ReviewHttp();items=NaAssemblyConnector(http)._fetch_systematic_review()
        self.assertEqual(http.requests[0]['srngPrgrsSttsCd'],'PRGRS_0003')
        self.assertEqual(len(items),1)
        self.assertIsNone(items[0].published_at)
        self.assertIn('제안일자: 2024-01-01',items[0].raw_text)
        self.assertTrue(reportable_legislation(items[0]))

    def test_wrong_response_stage_is_not_relabelled_as_systematic_review(self):
        with self.assertRaisesRegex(ValueError,'응답 상태'):
            NaAssemblyConnector(FakeHttp())._fetch_systematic_review()
