from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import mcp_server
from briefing.db import init_db
from briefing.tagging import (
    load_verified_manifest,
    run_shadow_tagging,
    search_items_by_tags,
    select_review_queue,
)


ROOT = Path(__file__).resolve().parents[1]


class TaggingTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.cfg = SimpleNamespace(
            enabled=True,
            node_path="node",
            bridge_path=str(ROOT / "scripts" / "news_tag_bridge.mjs"),
            manifest_path=str(ROOT / "vendor" / "legal-opinion-tagger" / "manifest.json"),
            trusted_body_sources=["scourt", "na"],
            max_text_chars=12000,
            batch_size=20,
            timeout_seconds=30,
            timezone="Asia/Seoul",
        )

    def tearDown(self):
        self.conn.close()
        mcp_server._conn = None

    def _insert(
        self,
        item_id: str,
        title: str,
        *,
        source: str = "fsc",
        category: str = "legislation",
        summary: str | None = None,
        relevance_score: int = 0,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO items (
              id, source, category, source_item_key, title, url, published_at,
              attachments_json, content_hash, summary, importance,
              first_seen_at, last_seen_at, last_changed_at, relevance_score
            ) VALUES (?, ?, ?, ?, ?, ?, '2026-08-30', '[]', ?, ?, 'high',
                      '2026-08-30T08:00:00+09:00', '2026-08-30T08:00:00+09:00',
                      '2026-08-30T08:00:00+09:00', ?)
            """,
            (
                item_id, source, category, item_id, title,
                f"https://example.com/{item_id}", f"hash-{item_id}", summary,
                relevance_score,
            ),
        )
        self.conn.commit()

    def test_manifest_is_hash_verified(self):
        manifest = load_verified_manifest(self.cfg.manifest_path)
        self.assertEqual(manifest["profileVersion"], "news-v1.0.0")
        self.assertEqual(manifest["producerCommit"][:7], "8d17a98")

    def test_shadow_tagging_is_idempotent_and_does_not_change_ranking(self):
        self._insert(
            "law-1",
            "보험업법 제95조 설명의무 개정안",
            summary="보험회사의 설명의무 기준을 정비하는 입법예고임",
            relevance_score=73,
        )
        first = run_shadow_tagging(self.conn, self.cfg)
        self.assertEqual(first.processed, 1)
        self.assertGreater(first.tags, 0)

        laws = self.conn.execute(
            """
            SELECT tr.canonical, tr.tag_type, it.source_fields_json
            FROM item_tags it JOIN tag_registry tr ON tr.tag_id = it.tag_id
            WHERE it.item_id = 'law-1'
            """
        ).fetchall()
        self.assertIn("보험업법", {row["canonical"] for row in laws})
        item = self.conn.execute(
            "SELECT importance, relevance_score, sent_hash FROM items WHERE id='law-1'"
        ).fetchone()
        self.assertEqual((item["importance"], item["relevance_score"], item["sent_hash"]),
                         ("high", 73, None))

        second = run_shadow_tagging(self.conn, self.cfg)
        self.assertEqual(second.selected, 0)
        self.assertEqual(second.processed, 0)

    def test_ambiguous_tag_enters_review_queue(self):
        self._insert("amb-1", "근로소득 세액 공제 제도 개선", category="press")
        stats = run_shadow_tagging(self.conn, self.cfg)
        self.assertEqual(stats.errors, 0)
        row = self.conn.execute(
            """
            SELECT it.candidate_status
            FROM item_tags it JOIN tag_registry tr ON tr.tag_id = it.tag_id
            WHERE it.item_id='amb-1' AND tr.canonical='공제'
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["candidate_status"], "다의어 검토")
        queue = select_review_queue(self.conn, limit=10)
        self.assertTrue(any(entry["item_id"] == "amb-1" for entry in queue))

    def test_tag_search_and_mcp_tag_only_search(self):
        self._insert("search-1", "개인정보 보호법상 동의 제도 개선", source="pipc")
        run_shadow_tagging(self.conn, self.cfg)
        rows = search_items_by_tags(self.conn, tags=["개인정보 보호법"], limit=10)
        self.assertEqual([row["id"] for row in rows], ["search-1"])

        mcp_server._conn = self.conn
        rendered = mcp_server._search({"tags": ["개인정보 보호법"], "top_k": 5})
        self.assertIn("search-1", rendered)
        self.assertIn("법률태그:", rendered)

    def test_starred_item_without_tags_is_reviewed_as_missed(self):
        self._insert("star-1", "IFRS17 보험부채 할인율 개선", relevance_score=70)
        run_shadow_tagging(self.conn, self.cfg)
        self.conn.execute("DELETE FROM item_tags WHERE item_id='star-1'")
        self.conn.commit()
        queue = select_review_queue(self.conn, limit=10)
        missed = [row for row in queue if row["item_id"] == "star-1"]
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["priority"], 1)


if __name__ == "__main__":
    unittest.main()
