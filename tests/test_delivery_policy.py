"""메일 노출 정책과 발송/제외 상태 분리 회귀 테스트."""
import sqlite3
import unittest
from pathlib import Path

from briefing.db import init_db, mark_sent, mark_suppressed, select_pending_for_email
from briefing.render import render_email


ROOT = Path(__file__).resolve().parents[1]


def _insert_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source: str = "fsc",
    importance: str = "high",
    content_hash: str = "hash-v1",
    sent_hash=None,
    last_sent_at=None,
    relevance_score: int = 0,
    relevance_evidence=None,
) -> None:
    conn.execute(
        """
        INSERT INTO items (
          id, source, category, source_item_key, title, url, published_at,
          attachments_json, raw_text, content_hash, summary, importance,
          importance_reason, first_seen_at, last_seen_at, last_changed_at,
          sent_hash, last_sent_at, relevance_score, relevance_evidence
        ) VALUES (?, ?, 'press', ?, ?, 'https://example.com', '2099-01-01',
                  '[]', '본문', ?, NULL, ?, '분류 사유',
                  '2026-08-13T08:00:00+09:00', '2026-08-13T08:00:00+09:00',
                  '2026-08-13T08:00:00+09:00', ?, ?, ?, ?)
        """,
        (
            item_id, source, item_id, f"제목 {item_id}", content_hash, importance,
            sent_hash, last_sent_at, relevance_score, relevance_evidence,
        ),
    )
    conn.commit()


class TestDeliveryState(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        _insert_item(self.conn, item_id="fsc:1")

    def tearDown(self):
        self.conn.close()

    def test_suppressed_hash_hides_only_current_version(self):
        pending = select_pending_for_email(self.conn, max_days_since_published=99999)
        self.assertEqual([it.id for it in pending], ["fsc:1"])

        mark_suppressed(self.conn, ["fsc:1"], tz_name="Asia/Seoul")
        self.assertEqual(
            select_pending_for_email(self.conn, max_days_since_published=99999), []
        )

        self.conn.execute(
            "UPDATE items SET content_hash = 'hash-v2' WHERE id = 'fsc:1'"
        )
        self.conn.commit()
        pending = select_pending_for_email(self.conn, max_days_since_published=99999)
        self.assertEqual([it.id for it in pending], ["fsc:1"])

    def test_mark_sent_clears_suppressed_state(self):
        mark_suppressed(self.conn, ["fsc:1"], tz_name="Asia/Seoul")
        mark_sent(self.conn, ["fsc:1"], tz_name="Asia/Seoul")
        row = self.conn.execute(
            "SELECT sent_hash, suppressed_hash FROM items WHERE id = 'fsc:1'"
        ).fetchone()
        self.assertEqual(row["sent_hash"], "hash-v1")
        self.assertIsNone(row["suppressed_hash"])


class TestRenderedDeliveryPolicy(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_only_new_items_get_status_badge(self):
        _insert_item(
            self.conn,
            item_id="fsc:visible",
            sent_hash="old-hash",
            last_sent_at="2026-08-12T08:00:00+09:00",
            relevance_score=60,
            relevance_evidence="법령: 보험업법 | 키워드: 보험회사",
        )
        _insert_item(self.conn, item_id="fsc:new")
        _insert_item(
            self.conn, item_id="moel:hidden", source="moel", importance="low"
        )
        items = select_pending_for_email(
            self.conn, max_days_since_published=99999
        )
        result = render_email(
            items=items,
            template_dir=ROOT / "templates",
            subject="테스트",
            run_date="2026-08-13T08:00:00+09:00",
        )

        self.assertEqual(set(result.visible_item_ids), {"fsc:visible", "fsc:new"})
        self.assertEqual(result.html.count('class="tag tag-new"'), 1)
        self.assertNotIn('class="tag tag-updated"', result.html)
        self.assertNotIn(">변경</span>", result.html)
        self.assertIn("★ 당사 관련:", result.html)
        self.assertIn("보험업법", result.html)
        self.assertNotIn("오늘의 검토 필요", result.html)
        self.assertNotIn('class="review-box"', result.html)
        self.assertNotIn("moel:hidden", result.html)


if __name__ == "__main__":
    unittest.main()
