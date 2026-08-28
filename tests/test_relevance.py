from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from briefing.cli import _rank_scourt_item
from briefing.config import RelevanceConfig, ScourtRankingConfig
from briefing.relevance import RelevanceScorer, sector_signals


class TestSectorAwareRelevance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.snapshot = Path(self.tmp.name) / "snapshot.json"
        self.snapshot.write_text(
            json.dumps({
                "laws": {
                    "상법": {"aliases": ["상법"], "sagyu_links": 6},
                },
                "soft_laws": {},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        self.cfg = RelevanceConfig(
            enabled=True,
            snapshot_path=str(self.snapshot),
            term_weights={
                "생명보험": 20,
                "변액": 30,
                "보험회사": 8,
                "보험금": 8,
            },
            life_terms=["생명보험", "변액", "종신보험"],
            nonlife_terms=["자동차보험", "대차료", "대인배상", "대물배상"],
        )
        self.scorer = RelevanceScorer(snapshot_path=self.snapshot, cfg=self.cfg)
        self.scourt_cfg = ScourtRankingConfig(
            high_keywords=["약관", "불공정", "보험금", "보험계약"],
            medium_keywords=["손해배상", "민사소송법"],
            low_keywords=["공제"],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_nonlife_only_case_is_capped_below_star_threshold(self):
        result = self.scorer.score(
            title="자동차보험 대차료 판결",
            body="상법상 보험회사의 보험금 지급기준이 문제된 사건",
            category="case_law",
        )
        self.assertLess(result.score, 50)
        self.assertLessEqual(result.score, 29)
        self.assertIn("손보 전용", result.evidence)

    def test_life_signal_can_reach_company_relevance_threshold(self):
        result = self.scorer.score(
            title="생명보험 변액상품 제도 개선",
            body=None,
            category="admin_notice",
        )
        self.assertGreaterEqual(result.score, 50)
        self.assertIn("생보 신호", result.evidence)

    def test_sector_signals_are_separate(self):
        life, nonlife = sector_signals(
            "생명보험과 자동차보험 업권 공통 규정", self.cfg
        )
        self.assertEqual(life, ["생명보험"])
        self.assertEqual(nonlife, ["자동차보험"])

    def test_scourt_auto_case_is_demoted_as_nonlife(self):
        importance, reason = _rank_scourt_item(
            title="임대료 청구의 소",
            raw_text="자동차보험 대차료와 보험금 지급기준이 문제된 사건",
            category="case_law",
            scourt_cfg=self.scourt_cfg,
            relevance_cfg=self.cfg,
        )
        self.assertEqual((importance, reason), ("low", "대법원 판결(손해보험 전용)"))

    def test_generic_terms_do_not_promote_unrelated_cases(self):
        association = _rank_scourt_item(
            title="손해배상(기)",
            raw_text="공인중개사협회 공제금 약관의 해석원칙",
            category="case_law",
            scourt_cfg=self.scourt_cfg,
            relevance_cfg=self.cfg,
        )
        import_case = _rank_scourt_item(
            title="약정금",
            raw_text="병행수입상품 관련 불공정무역행위 판정",
            category="case_law",
            scourt_cfg=self.scourt_cfg,
            relevance_cfg=self.cfg,
        )
        self.assertEqual(association[0], "low")
        self.assertEqual(import_case[0], "low")


if __name__ == "__main__":
    unittest.main()
