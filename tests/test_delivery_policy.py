"""메일 노출 정책과 발송/제외 상태 분리 회귀 테스트."""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from briefing.cli import _enrich, _heuristic_scourt_digest_summary
from briefing.config import load_config
from briefing.db import init_db, mark_sent, mark_suppressed, select_pending_for_email
from briefing.render import render_email
from briefing.summarize import LlmResult


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
    category: str = "press",
    title=None,
    summary=None,
) -> None:
    conn.execute(
        """
        INSERT INTO items (
          id, source, category, source_item_key, title, url, published_at,
          attachments_json, raw_text, content_hash, summary, importance,
          importance_reason, first_seen_at, last_seen_at, last_changed_at,
          sent_hash, last_sent_at, relevance_score, relevance_evidence
        ) VALUES (?, ?, ?, ?, ?, 'https://example.com', '2099-01-01',
                  '[]', '본문', ?, ?, ?, '분류 사유',
                  '2026-08-13T08:00:00+09:00', '2026-08-13T08:00:00+09:00',
                  '2026-08-13T08:00:00+09:00', ?, ?, ?, ?)
        """,
        (
            item_id, source, category, item_id, title or f"제목 {item_id}",
            content_hash, summary, importance,
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

    def test_legacy_court_library_cases_are_excluded(self):
        _insert_item(
            self.conn, item_id="scourt:1", source="scourt", category="case_law",
            importance="high", title="1. 보험 관련 판결",
        )
        _insert_item(
            self.conn, item_id="scourt:2", source="scourt", category="case_law",
            importance="low", title="2. 일반 판결",
        )
        items = select_pending_for_email(self.conn, max_days_since_published=99999)
        result = render_email(
            items=items,
            template_dir=ROOT / "templates",
            subject="테스트",
            run_date="2099-01-01T08:00:00+09:00",
        )

        self.assertEqual(result.visible_item_ids, ())
        self.assertNotIn("1. 보험 관련 판결", result.html)
        self.assertNotIn("2. 일반 판결", result.html)

    def test_scourt_importance_digest_is_summarized(self):
        _insert_item(
            self.conn,
            item_id="scourt:press:digest",
            source="scourt",
            category="press",
            title="대법원 2099. 1. 1. 선고 중요판결 요지",
        )
        items = select_pending_for_email(self.conn, max_days_since_published=99999)
        cfg = load_config(ROOT / "config.yaml")
        with patch("briefing.cli.build_scorer", return_value=None), patch(
            "briefing.cli.summarize_with_llm",
            return_value=LlmResult(
                importance="medium",
                summary="판결 세 건의 핵심 쟁점을 정리한 자료임 / 민사·형사 쟁점이 포함됨 / 세부 법리는 원문에 제시됨",
                reason="테스트",
            ),
        ):
            _enrich(self.conn, cfg, items)

        row = self.conn.execute(
            "SELECT summary FROM items WHERE id = 'scourt:press:digest'"
        ).fetchone()
        self.assertIn("핵심 쟁점", row["summary"])

    def test_scourt_digest_has_non_llm_fallback(self):
        body = (
            "대법원 중요판결 요지 2026다12345 손해배상 "
            "[보험계약의 해석 기준이 문제된 사건] "
            "2026도67890 사기 [고의의 인정 기준이 문제된 사건]"
        )
        summary = _heuristic_scourt_digest_summary(body)
        self.assertIn("총 2건", summary)
        self.assertIn("보험계약", summary)

    def test_grouped_scourt_digest_displays_its_summary(self):
        _insert_item(
            self.conn,
            item_id="scourt:press:digest",
            source="scourt",
            title="대법원 중요판결 요지",
            summary="중요판결 세 건의 핵심 쟁점 요약임",
        )
        for number in (1, 2):
            _insert_item(
                self.conn,
                item_id=f"scourt:press:{number}",
                source="scourt",
                title=f"개별 판결 {number}",
            )
        items = select_pending_for_email(self.conn, max_days_since_published=99999)
        result = render_email(
            items=items,
            template_dir=ROOT / "templates",
            subject="테스트",
            run_date="2099-01-01T08:00:00+09:00",
        )
        self.assertIn("대법원 보도자료 · 2099-01-01 선고·결정 3건", result.html)
        self.assertIn("중요판결 세 건의 핵심 쟁점 요약임", result.html)


if __name__ == "__main__":
    unittest.main()
