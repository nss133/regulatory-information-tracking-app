"""판단자산 영향 감시(briefing/jid_watch.py) 테스트.

실행: python -m unittest discover tests
"""
import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from briefing.db import init_db
from briefing.jid_watch import (
    append_log,
    build_citation_index,
    find_impacts,
    persist_impact_candidates,
    prune_legacy_court_candidates,
    review_impact_candidate,
    select_impact_queue,
)

JID_A = """---
id: J-2026-0001
쟁점: 특별이익 제공 판단
근거:
  - 보험업감독규정 §5-7 ③ [원문확인]
  - 대법원 2001. 12. 24. 선고 2001다65755
태그:
  - "#보험"
---
본문
"""

JID_B = """---
id: J-2026-0002
쟁점: 개인정보 위탁
근거:
  - 개인정보 보호법 §15
  - 자본시장과 금융투자업에 관한 법률 §251 ① [원문확인]
---
본문
"""

JID_C = """---
id: J-2026-0003
쟁점: 개인정보 정의
근거:
  - 개인정보 보호법 §2
---
본문
"""

JID_D = """---
id: J-2026-0004
쟁점: 목적 외 이용
근거:
  - 개인정보 보호법 §19
---
본문
"""

JID_E = """---
id: J-2026-0005
쟁점: 주요사항보고
근거:
  - 자본시장과 금융투자업에 관한 법률 시행령 §171
---
본문
"""


def _item(
    title,
    category="press",
    raw_text="",
    url="http://example.com",
    item_id="i1",
    content_hash="hash-v1",
    source="test",
):
    return SimpleNamespace(
        id=item_id,
        title=title,
        category=category,
        raw_text=raw_text,
        url=url,
        content_hash=content_hash,
        source=source,
    )


class TestJidWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "J-2026-0001_특별이익.md").write_text(JID_A)
        (d / "J-2026-0002_개인정보위탁.md").write_text(JID_B)
        self.idx = build_citation_index(d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_built(self):
        self.assertEqual(self.idx.n_files, 2)
        self.assertIn("보험업감독규정", self.idx.law_to_jids)
        self.assertIn("2001다65755", self.idx.case_to_jids)

    def test_alias_merged_to_canonical(self):
        # "자본시장과 금융투자업에 관한 법률"은 대표명 "자본시장법"으로 수렴
        self.assertIn("자본시장법", self.idx.law_to_jids)

    def test_article_match_lists_jid(self):
        item = _item(
            "보험업감독규정 일부개정 규정변경예고", category="admin_notice",
            raw_text="제5-7조(특별이익) 개정",
        )
        alerts = find_impacts(self.idx, [item])
        self.assertEqual(len(alerts), 1)
        self.assertTrue(any("§5-7 일치" in l for l in alerts[0].lines))
        self.assertTrue(any("J-2026-0001" in l for l in alerts[0].lines))
        self.assertEqual(alerts[0].matches[0].match_level, "exact_article")
        self.assertEqual(alerts[0].matches[0].jid, "J-2026-0001")

    def test_alias_in_item_text_matches(self):
        item = _item("금소법·자본시장법 개정안 입법예고", category="legislation")
        alerts = find_impacts(self.idx, [item])
        self.assertEqual(len(alerts), 1)
        self.assertTrue(any("자본시장법" in l for l in alerts[0].lines))
        self.assertEqual(alerts[0].matches[0].match_level, "law_only")
        self.assertIsNone(alerts[0].matches[0].jid)

    def test_case_number_match(self):
        item = _item(
            "대법원 전원합의체 판결", category="case_law",
            raw_text="대법원 2001. 12. 24. 선고 2001다65755 판결을 변경",
        )
        alerts = find_impacts(self.idx, [item])
        self.assertEqual(len(alerts), 1)
        self.assertTrue(any("2001다65755" in l for l in alerts[0].lines))
        case_matches = [m for m in alerts[0].matches if m.match_level == "exact_case"]
        self.assertEqual(len(case_matches), 1)
        self.assertEqual(case_matches[0].jid, "J-2026-0001")

    def test_no_trigger_no_alert(self):
        # 규범 변화 신호 없는 보도자료는 법령을 언급해도 침묵
        item = _item("개인정보 보호법 홍보 캠페인 발대식", category="press",
                     raw_text="개인정보 보호법 안내 행사")
        self.assertEqual(find_impacts(self.idx, [item]), [])

    def test_legacy_court_library_case_is_excluded(self):
        item = _item(
            "대법원 주요판결",
            category="case_law",
            raw_text="대법원 2001다65755 판결",
            source="scourt",
        )
        self.assertEqual(find_impacts(self.idx, [item]), [])

    def test_article_not_cross_paired_between_laws(self):
        # 다른 법령의 조문번호가 교차 결합되지 않아야 함 — 판례 요지 묶음에서
        # 특별검사법 제2조가 개인정보보호법 §2로 오귀속된 오탐 사례(2026-07-22)
        d = Path(self.tmp.name)
        (d / "J-2026-0003_개인정보정의.md").write_text(JID_C)
        (d / "J-2026-0004_목적외이용.md").write_text(JID_D)
        idx = build_citation_index(d)
        item = _item(
            "대법원 2026. 7. 16. 선고 중요판결 요지", category="case_law",
            raw_text=(
                "개인정보보호법위반 파기환송. "
                "구 「개인정보 보호법」 제19조에서 정하고 있는 제3자 제공의 의미. "
                + "다른 사건 설명이 길게 이어짐. " * 20
                + "특별검사법 제2조 제1항 제1호부터 제15호까지 규정된 의혹 사건"
            ),
        )
        alerts = find_impacts(idx, [item])
        self.assertEqual(len(alerts), 1)
        joined = "\n".join(alerts[0].lines)
        self.assertIn("§19 일치", joined)
        self.assertIn("J-2026-0004", joined)
        self.assertNotIn("§2 일치", joined)

    def test_decree_alias_merges(self):
        # "자본시장법 시행령" ↔ "자본시장과 금융투자업에 관한 법률 시행령" 수렴
        d = Path(self.tmp.name)
        (d / "J-2026-0005_주요사항보고.md").write_text(JID_E)
        idx = build_citation_index(d)
        self.assertIn("자본시장법시행령", idx.law_to_jids)
        item = _item(
            "자본시장법 시행령 개정", category="legislation",
            raw_text="「자본시장법 시행령」 개정안이 국무회의에서 의결되었다.",
        )
        alerts = find_impacts(idx, [item])
        self.assertEqual(len(alerts), 1)
        self.assertTrue(any("J-2026-0005" in l for l in alerts[0].lines))

    def test_append_log_creates_and_appends(self):
        item = _item("보험업감독규정 개정", category="admin_notice", raw_text="제5-7조")
        alerts = find_impacts(self.idx, [item])
        log = Path(self.tmp.name) / "log.md"
        append_log(log, alerts, run_date="2026-07-15")
        content = log.read_text()
        self.assertIn("## 2026-07-15", content)
        self.assertIn("[[J-2026-0001_특별이익]]", content)  # vault용 위키링크

    def test_persist_is_idempotent_and_preserves_human_review(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        item = _item(
            "보험업감독규정 일부개정 규정변경예고",
            category="admin_notice",
            raw_text="보험업감독규정 제5-7조를 개정한다.",
        )
        alerts = find_impacts(self.idx, [item])
        first = persist_impact_candidates(conn, alerts, timezone="Asia/Seoul")
        second = persist_impact_candidates(conn, alerts, timezone="Asia/Seoul")
        self.assertEqual((first.total, first.exact, first.broad), (1, 1, 0))
        self.assertEqual(second.total, 1)
        row = conn.execute("SELECT * FROM jid_impact_candidates").fetchone()
        self.assertEqual(row["detection_count"], 2)

        review_impact_candidate(
            conn,
            candidate_id=row["candidate_id"],
            decision="no_effect",
            reason="문구 정비에 그쳐 기존 결론은 유지",
            reviewer="tester",
            timezone="Asia/Seoul",
        )
        persist_impact_candidates(conn, alerts, timezone="Asia/Seoul")
        reviewed = conn.execute("SELECT * FROM jid_impact_candidates").fetchone()
        self.assertEqual(reviewed["status"], "no_effect")
        self.assertEqual(reviewed["detection_count"], 3)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM jid_impact_reviews").fetchone()[0], 1
        )

    def test_queue_prioritizes_exact_and_content_change_creates_candidate(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        broad_item = _item(
            "개인정보 보호법 개정안 입법예고",
            category="legislation",
            item_id="broad",
        )
        exact_item = _item(
            "보험업감독규정 개정",
            category="admin_notice",
            raw_text="보험업감독규정 제5-7조를 개정한다.",
            item_id="exact",
        )
        alerts = find_impacts(self.idx, [broad_item, exact_item])
        persist_impact_candidates(conn, alerts, timezone="Asia/Seoul")
        queue = select_impact_queue(conn)
        self.assertEqual(queue[0]["match_level"], "exact_article")
        self.assertEqual(queue[-1]["match_level"], "law_only")

        changed = _item(
            "보험업감독규정 개정",
            category="admin_notice",
            raw_text="보험업감독규정 제5-7조를 다시 개정한다.",
            item_id="exact",
            content_hash="hash-v2",
        )
        persist_impact_candidates(
            conn, find_impacts(self.idx, [changed]), timezone="Asia/Seoul"
        )
        exact_count = conn.execute(
            "SELECT COUNT(*) FROM jid_impact_candidates WHERE item_id='exact'"
        ).fetchone()[0]
        self.assertEqual(exact_count, 2)

    def test_prune_legacy_court_candidates_preserves_reviewed_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        conn.execute(
            """
            INSERT INTO items (
              id, source, category, source_item_key, title, url,
              attachments_json, content_hash, first_seen_at, last_seen_at,
              last_changed_at
            ) VALUES ('legacy', 'scourt', 'case_law', 'case:1', '판결', 'u',
                      '[]', 'h', 't', 't', 't')
            """
        )
        conn.executemany(
            """
            INSERT INTO jid_impact_candidates (
              candidate_id, item_id, item_content_hash, item_title, item_url,
              match_level, priority, case_number, jid, jid_stem, evidence_text,
              first_detected_at, last_seen_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "unreviewed", "legacy", "h", "판결", "u", "exact_case", 1,
                    "2001다65755", "J-1", "J-1_x", "근거", "t", "t", "unreviewed",
                ),
                (
                    "reviewed", "legacy", "h", "판결", "u", "exact_case", 1,
                    "2001다65755", "J-1", "J-1_x", "근거", "t", "t", "affected",
                ),
            ],
        )
        conn.commit()

        self.assertEqual(prune_legacy_court_candidates(conn), 1)
        rows = conn.execute(
            "SELECT candidate_id, status FROM jid_impact_candidates"
        ).fetchall()
        self.assertEqual(
            [(row["candidate_id"], row["status"]) for row in rows],
            [("reviewed", "affected")],
        )


if __name__ == "__main__":
    unittest.main()
