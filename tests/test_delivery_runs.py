"""발송 배치 이력·중복 보호·발송본 보관 테스트."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from briefing.db import (
    AmbiguousDeliveryError,
    StoredItem,
    create_delivery_run,
    init_db,
    prepare_delivery_attempt,
    select_recent_delivery_runs,
    update_delivery_run,
)
from briefing.delivery import archive_delivery, delivery_fingerprint


def _item(item_id="fsc:1", content_hash="hash-v1") -> StoredItem:
    return StoredItem(
        id=item_id,
        source="fsc",
        category="press",
        source_item_key=item_id,
        title="테스트 자료",
        url="https://example.com/1",
        published_at="2026-08-14",
        attachments_json="[]",
        content_hash=content_hash,
        importance="high",
        importance_reason="보험",
        summary="요약",
        last_changed_at="2026-08-14T08:00:00+09:00",
        last_sent_at=None,
        sent_hash=None,
    )


class TestDeliveryRuns(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_completed_run_keeps_audit_fields(self):
        run_id = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        update_delivery_run(
            self.conn,
            run_id,
            status="completed",
            stage="delivered",
            fetched_count=20,
            visible_count=16,
            message_id="<message@example.com>",
            completed=True,
            tz_name="Asia/Seoul",
        )
        row = self.conn.execute(
            "SELECT * FROM delivery_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["stage"], "delivered")
        self.assertEqual(row["visible_count"], 16)
        self.assertIsNotNone(row["completed_at"])
        recent = select_recent_delivery_runs(self.conn, limit=1)
        self.assertEqual(recent[0]["run_id"], run_id)

    def test_ambiguous_same_payload_blocks_retry(self):
        fingerprint = delivery_fingerprint([_item()], ["USER@example.com"])
        first = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        prepare_delivery_attempt(
            self.conn, first, fingerprint=fingerprint, message_id="<first@example.com>"
        )
        second = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        with self.assertRaises(AmbiguousDeliveryError):
            prepare_delivery_attempt(
                self.conn,
                second,
                fingerprint=fingerprint,
                message_id="<second@example.com>",
            )

    def test_completed_payload_does_not_block_new_attempt(self):
        fingerprint = delivery_fingerprint([_item()], ["user@example.com"])
        first = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        prepare_delivery_attempt(
            self.conn, first, fingerprint=fingerprint, message_id="<first@example.com>"
        )
        update_delivery_run(
            self.conn, first, status="completed", stage="delivered",
            completed=True, tz_name="Asia/Seoul",
        )
        second = create_delivery_run(self.conn, tz_name="Asia/Seoul")
        prepare_delivery_attempt(
            self.conn, second, fingerprint=fingerprint, message_id="<second@example.com>"
        )
        status = self.conn.execute(
            "SELECT status FROM delivery_runs WHERE run_id = ?", (second,)
        ).fetchone()[0]
        self.assertEqual(status, "sending")

    def test_archive_writes_html_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = archive_delivery(
                archive_root=tmp,
                run_date="2026-08-14T08:14:23+09:00",
                run_id="abcdef123456",
                subject="브리핑",
                message_id="<message@example.com>",
                recipients=["user@example.com"],
                html="<html>본문</html>",
                items=[_item()],
            )
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"][0]["content_hash"], "hash-v1")
            self.assertEqual(manifest["message_id"], "<message@example.com>")
            self.assertEqual(path.with_suffix(".html").read_text(), "<html>본문</html>")

    def test_init_imports_latest_legacy_sent_batch_once(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        conn.execute(
            """
            INSERT INTO items (
              id, source, category, source_item_key, title, url,
              attachments_json, content_hash, first_seen_at, last_seen_at,
              last_changed_at, sent_hash, last_sent_at
            ) VALUES (
              'fsc:legacy', 'fsc', 'press', 'legacy', '과거 발송',
              'https://example.com', '[]', 'h', '2026-08-14', '2026-08-14',
              '2026-08-14', 'h', '2026-08-14T08:14:23+09:00'
            )
            """
        )
        conn.commit()
        init_db(conn)
        init_db(conn)
        rows = select_recent_delivery_runs(conn, limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "legacy_import")
        self.assertEqual(rows[0]["visible_count"], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
