"""금융위·인권위·KoFIU 목록 구조 회귀 테스트."""
import json
import unittest

from briefing.sources.fsc import FscConnector
from briefing.sources.kofiu import KofiuConnector
from briefing.sources.nhrck import NhrckConnector
from briefing.sources.urls import KOFIU_BOARD_LIST_API, KOFIU_LAW_LIST_API


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses

    def get_text(self, url, *, params=None, extra_headers=None):
        key = (url, (params or {}).get("seCd"))
        return self.responses.get(key, self.responses.get(url, ""))


class TestFscRssDate(unittest.TestCase):
    def test_dc_date_is_used(self):
        rss = """<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
        <channel><item><title>금융위 자료</title>
        <link>https://www.fsc.go.kr/no010101/99999</link>
        <dc:date>2026-08-13 00:00:00</dc:date></item></channel></rss>"""
        c = FscConnector(FakeHttp({"https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111": rss}))
        items = c._fetch_press_rss()
        self.assertEqual(items[0].published_at.date().isoformat(), "2026-08-13")

    def test_legislation_date_and_attachment_from_list_item(self):
        html = """<ul><li><div class="inner"><div class="cont">
        <div class="subject"><a href="./po040301/view?noticeId=4164">입법예고</a></div>
        <div class="file"><div class="file-list"><span class="name">공고문.pdf</span>
        <a href="/comm/getFile?upperNo=4164&amp;fileNo=1">다운로드</a></div></div>
        </div><div class="day">2026-08-11</div></div></li></ul>"""
        c = FscConnector(FakeHttp({"https://www.fsc.go.kr/po040301": html}))
        items = c._fetch_legislation_list()
        self.assertEqual(items[0].published_at.date().isoformat(), "2026-08-11")
        self.assertEqual(items[0].attachments[0].label, "공고문.pdf")


class TestNhrckList(unittest.TestCase):
    def test_date_from_row_and_new_mark_removed(self):
        html = """<table><tr><td class="tit"><a class="td-title"
        href="/base/board/read?boardManagementNo=24&amp;boardNo=7612248">
        차별 시정 보도자료 <em class="new_mark">N</em></a></td>
        <td class="date">2026-08-13</td></tr></table>"""
        c = NhrckConnector(FakeHttp({"https://www.humanrights.go.kr/base/board/list": html}))
        items = c.fetch_latest()
        self.assertEqual(items[0].title, "차별 시정 보도자료")
        self.assertEqual(items[0].published_at.date().isoformat(), "2026-08-13")


class TestKofiuJsonApi(unittest.TestCase):
    def test_dynamic_board_apis_are_parsed(self):
        press = {
            "result": [{
                "ntcnYardOrdrNo": "412",
                "ntcnYardSjNm": "[보도자료] 신고매뉴얼 개정",
                "ntcnYardRgiDt": "2026-08-11 13:54:38",
                "fileList": [{
                    "fileId": "a+b/c=", "atchmnflOrginlNm": "보도자료.pdf"
                }],
            }]
        }
        law_base = {
            "lawordInfoOrdrNo": "89",
            "lawordInfoSjNm": "검사 및 제재규정 일부개정",
            "lawordInfoRgiDt": "2026-02-19 16:10:05",
            "lawordInfoCn": "<p>개정 이유와 주요 내용</p>",
            "fileList": [{
                "lawordInfoTySeCd": "001", "lawordInfoOrdrNo": "89",
                "atchmnflOrdrNo": "1", "atchmnflStreNm": "stored.hwpx",
                "atchmnflOrginlNm": "개정규정.hwpx",
            }],
        }
        legislation = dict(law_base)
        legislation.update({
            "lawordInfoOrdrNo": "37",
            "lawordInfoSjNm": "감독규정 변경예고",
            "fileList": [],
        })
        http = FakeHttp({
            (KOFIU_BOARD_LIST_API, "0001"): json.dumps(press),
            (KOFIU_LAW_LIST_API, "001"): json.dumps({"result": [law_base]}),
            (KOFIU_LAW_LIST_API, "002"): json.dumps({"result": [legislation]}),
        })

        items = KofiuConnector(http, max_items=10).fetch_latest()
        self.assertEqual(len(items), 3)
        self.assertEqual([i.category for i in items], ["press", "admin_notice", "legislation"])
        self.assertEqual(items[0].published_at.date().isoformat(), "2026-08-11")
        self.assertIn("fileId=a%2Bb%2Fc%3D", items[0].attachments[0].url)
        self.assertEqual(items[1].raw_text, "개정 이유와 주요 내용")
        self.assertIn("lawordInfoOrdrNo=89", items[1].url)


if __name__ == "__main__":
    unittest.main()
