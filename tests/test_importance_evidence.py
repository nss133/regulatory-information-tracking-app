from __future__ import annotations

import sqlite3
import unittest

from briefing.config import ComboRules, RankingConfig
from briefing.db import init_db, select_primary_attachment_text
from briefing.ranking import rank_item
from briefing.relevance import build_score_text


class TestAttachmentImportanceEvidence(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.conn.execute(
            """
            INSERT INTO item_content (
              item_id, source_url, extracted_text, extraction_status
            ) VALUES (?, ?, ?, 'success')
            """,
            ("fss:consumer-risk", "https://example.com/a.pdf", "짧은 안내"),
        )
        self.material = (
            "보험상품 브리핑 영업 과정에서 설명의무 미이행과 대리청약이 확인되어 "
            "금융감독원이 추가 암행점검과 내부통제 강화를 예고함"
        )
        self.conn.execute(
            """
            INSERT INTO item_content (
              item_id, source_url, extracted_text, extraction_status
            ) VALUES (?, ?, ?, 'success')
            """,
            ("fss:consumer-risk", "https://example.com/b.hwpx", self.material),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_longest_successful_attachment_is_used(self):
        self.assertEqual(
            select_primary_attachment_text(
                self.conn, item_id="fss:consumer-risk", max_chars=1000
            ),
            self.material,
        )

    def test_attachment_can_promote_generic_title(self):
        cfg = RankingConfig(
            high_keywords=["내부통제"],
            medium_keywords=["보험"],
            combo_rules=ComboRules(
                promote_to_high=[("보험상품", "설명의무")],
                promote_exclude=[],
                demote_to_medium=[],
                demote_to_low=[],
            ),
        )
        result = rank_item(
            title="제4차 소비자위험대응협의회 개최",
            raw_text=select_primary_attachment_text(
                self.conn, item_id="fss:consumer-risk"
            ),
            cfg=cfg,
        )
        self.assertEqual(result.importance, "high")

    def test_attachment_is_trusted_for_relevance_scoring_text(self):
        text = build_score_text(
            source="fss", raw_text="사이트 메뉴 보험금 퇴직연금",
            summary=None, supplemental_text=self.material,
        )
        self.assertEqual(text, self.material)

    def test_event_announcement_is_capped_at_medium(self):
        cfg = RankingConfig(
            high_keywords=["상법"],
            medium_keywords=[],
            combo_rules=ComboRules(
                promote_to_high=[],
                promote_exclude=[],
                demote_to_medium=[("설명회", "개최")],
                demote_to_low=[],
            ),
        )
        result = rank_item(
            title="기업공시제도 종합설명회 개최",
            raw_text="상법과 자본시장법 관련 공시제도를 안내합니다.",
            cfg=cfg,
        )
        self.assertEqual(result.importance, "medium")
        self.assertIn("키워드조합(중): 설명회, 개최", result.reason)


if __name__ == "__main__":
    unittest.main()
