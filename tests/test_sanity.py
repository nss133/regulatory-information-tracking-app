"""발송 전 자가진단(briefing/sanity.py) 테스트.

실행: python -m unittest discover tests
"""
import sqlite3
import unittest
from types import SimpleNamespace

from briefing.sanity import run_sanity_checks
from briefing.db import init_db, record_source_fetches


def _item(title="제목", raw_text="정상 본문", importance="low"):
    return SimpleNamespace(title=title, raw_text=raw_text, importance=importance)


def _conn_with_history(highs_per_day: int, days: int = 10) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE items (importance TEXT, last_sent_at TEXT)")
    for d in range(1, days + 1):
        for _ in range(highs_per_day):
            conn.execute(
                "INSERT INTO items VALUES ('high', datetime('now', ?))",
                (f"-{d} day",),
            )
    return conn


class TestChromePollution(unittest.TestCase):
    def setUp(self):
        self.conn = _conn_with_history(highs_per_day=2)

    def tearDown(self):
        self.conn.close()

    def test_clean_items_no_warning(self):
        warnings = run_sanity_checks(self.conn, [_item(), _item()])
        self.assertEqual(warnings, [])

    def test_chrome_prefix_detected(self):
        pending = [_item(raw_text="주메뉴 바로가기 본문 바로가기 검색 ...")]
        warnings = run_sanity_checks(self.conn, pending)
        self.assertEqual(len(warnings), 1)
        self.assertIn("상용구 오염", warnings[0])

    def test_chrome_marker_detected(self):
        pending = [_item(raw_text="판결 요지 ... 이메일무단수집거부 영상정보처리기기")]
        warnings = run_sanity_checks(self.conn, pending)
        self.assertEqual(len(warnings), 1)

    def test_empty_raw_text_ok(self):
        warnings = run_sanity_checks(self.conn, [_item(raw_text=None)])
        self.assertEqual(warnings, [])


class TestHighSurge(unittest.TestCase):
    def test_surge_detected(self):
        conn = _conn_with_history(highs_per_day=1)  # 일평균 1건
        self.addCleanup(conn.close)
        pending = [_item(importance="high") for _ in range(8)]  # 오늘 8건(3배 초과)
        warnings = run_sanity_checks(conn, pending)
        self.assertEqual(len(warnings), 1)
        self.assertIn("HIGH 8건", warnings[0])

    def test_normal_high_count_ok(self):
        conn = _conn_with_history(highs_per_day=3)  # 일평균 3건
        self.addCleanup(conn.close)
        pending = [_item(importance="high") for _ in range(5)]  # 3배 미만
        warnings = run_sanity_checks(conn, pending)
        self.assertEqual(warnings, [])

    def test_below_absolute_min_ok(self):
        conn = _conn_with_history(highs_per_day=0, days=0)  # 이력 없음
        self.addCleanup(conn.close)
        pending = [_item(importance="high") for _ in range(4)]  # 절대치 5건 미만
        warnings = run_sanity_checks(conn, pending)
        self.assertEqual(warnings, [])


class TestSourceHealth(unittest.TestCase):
    def test_source_with_no_history_is_reported(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_db(conn)
        warnings = run_sanity_checks(conn, [])
        self.assertTrue(any("수집 이력이 전혀 없는 소스" in w for w in warnings))
        self.assertTrue(any("금융정보분석원" in w for w in warnings))

    def test_successful_zero_items_is_not_missing(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_db(conn)
        outcomes = {
            source: (0, None, 0.1)
            for source in (
                "fsc", "fss", "pipc", "moel", "nhrck", "kftc", "kofiu", "na", "scourt"
            )
        }
        record_source_fetches(conn, outcomes, tz_name="Asia/Seoul")
        warnings = run_sanity_checks(conn, [])
        self.assertFalse(any("수집 이력이 전혀 없는 소스" in w for w in warnings))
        self.assertFalse(any("금융정보분석원" in w for w in warnings))

if __name__ == "__main__":
    unittest.main()
