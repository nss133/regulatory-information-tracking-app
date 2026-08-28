"""대법원(scourt) 본문 추출 회귀 테스트.

사이트 DOM이 바뀌어 추출이 body 폴백으로 떨어지면 메뉴·footer 상용구가
본문에 섞이고, 키워드 분류가 오염된다(2026-07 HIGH 도배 사례).
fixture는 실제 페이지 저장본(tests/fixtures/) — 사이트 개편 시 갱신할 것.

실행: python -m unittest discover tests
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from briefing.sources.scourt import (
    ScourtConnector,
    parse_decision_date,
    parse_major_detail_html,
    parse_press_detail_html,
)

FIXTURES = Path(__file__).parent / "fixtures"

# 사이트 껍데기(메뉴·footer)에서만 나오는 문구 — 본문에 있으면 추출 실패
CHROME_MARKERS = ["주메뉴 바로가기", "개인정보처리방침", "이메일무단수집거부"]


class TestMajorDetail(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "scourt_judg_detail.html").read_text()
        self.attachments, self.text = parse_major_detail_html(
            html, "https://library.scourt.go.kr/search/judg/judgDetail?seqNo=414099"
        )

    def test_text_extracted(self):
        self.assertIsNotNone(self.text)
        self.assertGreater(len(self.text), 200, "판결 본문이 비정상적으로 짧음")

    def test_no_site_chrome(self):
        for marker in CHROME_MARKERS:
            self.assertNotIn(marker, self.text, f"사이트 상용구 '{marker}'가 본문에 섞임")

    def test_judgment_content_present(self):
        # fixture 판결(성년후견 개시 사건)의 핵심 문구가 살아있는지
        self.assertIn("성년후견", self.text)
        self.assertIn("대법원", self.text)

    def test_attachment_is_source_link(self):
        self.assertEqual(len(self.attachments), 1)
        self.assertEqual(self.attachments[0].label, "원문 보기")

    def test_decision_date_is_parsed(self):
        value = parse_decision_date(self.text)
        self.assertIsNotNone(value)
        self.assertEqual(value.date().isoformat(), "2026-07-07")


class TestPressDetail(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "scourt_press_detail.html").read_text()
        self.attachments, self.text = parse_press_detail_html(
            html, "https://www.scourt.go.kr/portal/news/NewsViewAction.work?seqnum=11198"
        )

    def test_text_extracted(self):
        self.assertIsNotNone(self.text)
        self.assertGreater(len(self.text), 100, "보도자료 본문이 비정상적으로 짧음")

    def test_no_full_page_fallback(self):
        # body 폴백이면 페이지 최상단 스킵 링크가 앞부분에 나타난다
        self.assertFalse(self.text.startswith("주메뉴 바로가기"), "body 폴백 의심")


class TestScourtSourcePolicy(unittest.TestCase):
    def test_fetch_latest_does_not_fetch_court_library_duplicates(self):
        connector = ScourtConnector(None)  # type: ignore[arg-type]
        with patch.object(connector, "_fetch_press", return_value=["press"]), patch.object(
            connector,
            "_fetch_major_decisions",
            side_effect=AssertionError("법원도서관을 호출하면 안 됨"),
        ):
            self.assertEqual(connector.fetch_latest(), ["press"])


if __name__ == "__main__":
    unittest.main()
