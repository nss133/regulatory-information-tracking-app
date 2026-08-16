"""일일 브리핑 미실행·실패 감시 판정 테스트."""
import sqlite3
import unittest

from briefing.db import (
    create_delivery_run,
    has_delivery_watch_alert,
    init_db,
    record_delivery_watch_alert,
    update_delivery_run,
)
from briefing.watch import assess_delivery_run


class TestDeliveryWatch(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_missing_run_is_unhealthy(self):
        result = assess_delivery_run(
            self.conn,
            expected_date="2026-08-15",
            checked_at="2026-08-15T09:00:00+09:00",
        )
        self.assertFalse(result.healthy)
        self.assertEqual(result.code, "missing")

    def test_completed_run_is_healthy(self):
        run_id = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        self.conn.execute(
            "UPDATE delivery_runs SET run_date = '2026-08-15' WHERE run_id = ?",
            (run_id,),
        )
        update_delivery_run(
            self.conn, run_id, status="completed", stage="no_candidates",
            completed=True, tz_name="Asia/Seoul",
        )
        result = assess_delivery_run(
            self.conn,
            expected_date="2026-08-15",
            checked_at="2026-08-15T09:00:00+09:00",
        )
        self.assertTrue(result.healthy)
        self.assertEqual(result.run_id, run_id)

    def test_stale_running_is_unhealthy(self):
        run_id = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        self.conn.execute(
            """
            UPDATE delivery_runs
            SET run_date = '2026-08-15', started_at = '2026-08-15T08:00:00+09:00'
            WHERE run_id = ?
            """,
            (run_id,),
        )
        self.conn.commit()
        result = assess_delivery_run(
            self.conn,
            expected_date="2026-08-15",
            checked_at="2026-08-15T09:00:00+09:00",
            stale_after_minutes=45,
        )
        self.assertFalse(result.healthy)
        self.assertEqual(result.code, "stale")

    def test_recent_running_is_temporarily_healthy(self):
        run_id = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        self.conn.execute(
            """
            UPDATE delivery_runs
            SET run_date = '2026-08-15', started_at = '2026-08-15T08:40:00+09:00'
            WHERE run_id = ?
            """,
            (run_id,),
        )
        self.conn.commit()
        result = assess_delivery_run(
            self.conn,
            expected_date="2026-08-15",
            checked_at="2026-08-15T09:00:00+09:00",
        )
        self.assertTrue(result.healthy)
        self.assertEqual(result.code, "in_progress")

    def test_alert_is_recorded_once_per_date(self):
        self.assertFalse(has_delivery_watch_alert(self.conn, "2026-08-15"))
        record_delivery_watch_alert(
            self.conn,
            alert_date="2026-08-15",
            detected_at="2026-08-15T09:00:00+09:00",
            reason_code="missing",
            reason="실행 없음",
            run_id=None,
            message_id="<watch@example.com>",
        )
        record_delivery_watch_alert(
            self.conn,
            alert_date="2026-08-15",
            detected_at="2026-08-15T09:10:00+09:00",
            reason_code="missing",
            reason="실행 없음",
            run_id=None,
            message_id="<watch2@example.com>",
        )
        self.assertTrue(has_delivery_watch_alert(self.conn, "2026-08-15"))
        count = self.conn.execute("SELECT COUNT(*) FROM delivery_watch_alerts").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
