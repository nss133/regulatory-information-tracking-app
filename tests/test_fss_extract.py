"""금감원(fss) 본문 추출 회귀 테스트.

fss.or.kr 게시글은 메뉴·footer가 <nav>가 아닌 div(total-nav, gnb-set 등)라
범용 태그 제거만으로는 껍데기가 본문에 섞인다(2026-07-20 자가진단 감지 사례).
extract는 .dbdata/.bd-view 컨테이너를 우선 선택해야 한다.
fixture는 실제 페이지 저장본(tests/fixtures/) — 사이트 개편 시 갱신할 것.

실행: python -m unittest discover tests
"""
import sqlite3
import unittest
from pathlib import Path

from briefing.db import init_db, select_items_missing_body, update_body_text
from briefing.extract import extract_text_from_html

FIXTURES = Path(__file__).parent / "fixtures"

# 사이트 껍데기(메뉴·footer)에서만 나오는 문구 — 본문에 있으면 추출 실패
CHROME_MARKERS = ["주메뉴 바로가기", "이메일무단수집거부", "전체 메뉴", "금융민원상담 안내"]


class TestFssPressDetail(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "fss_press_detail.html").read_text()
        self.text = extract_text_from_html(html)

    def test_text_extracted(self):
        self.assertGreater(len(self.text), 100, "보도자료 본문이 비정상적으로 짧음")

    def test_no_site_chrome(self):
        for marker in CHROME_MARKERS:
            self.assertNotIn(marker, self.text, f"사이트 상용구 '{marker}'가 본문에 섞임")

    def test_body_content_present(self):
        # fixture 보도자료(금융소비자 간담회)의 핵심 문구가 살아있는지
        self.assertIn("금융감독원", self.text)
        self.assertIn("간담회", self.text)


class TestFallbackWithoutContainer(unittest.TestCase):
    def test_generic_page_falls_back_to_full_text(self):
        # 본문 컨테이너가 없는 일반 페이지는 기존 전체 추출로 폴백해야 함
        html = "<html><body><div><p>일반 페이지 본문입니다.</p></div></body></html>"
        self.assertIn("일반 페이지 본문입니다.", extract_text_from_html(html))


class TestFssKrdsDetail(unittest.TestCase):
    def test_new_body_container_excludes_global_navigation(self):
        body = (
            "금융감독원은 비상장주식 투자 전 회사정보와 투자권유자의 실체를 "
            "확인하도록 안내합니다. 전자공시시스템에서 재무상태와 공시서류를 "
            "확인하고 정식 인가·등록 금융회사인지 직접 확인해야 합니다."
        )
        html = f"""
        <html><body>
          <div class="global-menu">주메뉴 바로가기 금융민원상담 안내</div>
          <div class="krds-bd-view">
            <h3>비상장주식 투자 유의사항</h3>
            <div class="n-dbdata">{body}</div>
          </div>
          <div class="footer">이메일무단수집거부 전체 메뉴</div>
        </body></html>
        """
        self.assertEqual(extract_text_from_html(html), body)


class TestPollutedBodyRefresh(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.conn.execute(
            """
            INSERT INTO items (
              id, source, category, source_item_key, title, url,
              published_at, attachments_json, raw_text, content_hash,
              first_seen_at, last_seen_at, last_changed_at
            ) VALUES (
              'fss:test', 'fss', 'press', 'test', '제목', 'https://example.com',
              date('now'), '[]', '본문 이메일무단수집거부 전체 메뉴', 'hash',
              datetime('now'), datetime('now'), datetime('now')
            )
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_polluted_body_is_selected_and_replaced_once(self):
        rows = select_items_missing_body(self.conn, days=30, limit=10)
        self.assertEqual([row["id"] for row in rows], ["fss:test"])

        update_body_text(self.conn, item_id="fss:test", body_text="정상 본문")
        update_body_text(self.conn, item_id="fss:test", body_text="덮어쓰면 안 되는 본문")

        row = self.conn.execute(
            "SELECT raw_text FROM items WHERE id = 'fss:test'"
        ).fetchone()
        self.assertEqual(row["raw_text"], "정상 본문")


if __name__ == "__main__":
    unittest.main()
