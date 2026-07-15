"""판단자산 영향 감시(briefing/jid_watch.py) 테스트.

실행: python -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from briefing.jid_watch import append_log, build_citation_index, find_impacts

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


def _item(title, category="press", raw_text="", url="http://example.com"):
    return SimpleNamespace(id="i1", title=title, category=category, raw_text=raw_text, url=url)


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

    def test_alias_in_item_text_matches(self):
        item = _item("금소법·자본시장법 개정안 입법예고", category="legislation")
        alerts = find_impacts(self.idx, [item])
        self.assertEqual(len(alerts), 1)
        self.assertTrue(any("자본시장법" in l for l in alerts[0].lines))

    def test_case_number_match(self):
        item = _item(
            "대법원 전원합의체 판결", category="case_law",
            raw_text="대법원 2001. 12. 24. 선고 2001다65755 판결을 변경",
        )
        alerts = find_impacts(self.idx, [item])
        self.assertEqual(len(alerts), 1)
        self.assertTrue(any("2001다65755" in l for l in alerts[0].lines))

    def test_no_trigger_no_alert(self):
        # 규범 변화 신호 없는 보도자료는 법령을 언급해도 침묵
        item = _item("개인정보 보호법 홍보 캠페인 발대식", category="press",
                     raw_text="개인정보 보호법 안내 행사")
        self.assertEqual(find_impacts(self.idx, [item]), [])

    def test_append_log_creates_and_appends(self):
        item = _item("보험업감독규정 개정", category="admin_notice", raw_text="제5-7조")
        alerts = find_impacts(self.idx, [item])
        log = Path(self.tmp.name) / "log.md"
        append_log(log, alerts, run_date="2026-07-15")
        content = log.read_text()
        self.assertIn("## 2026-07-15", content)
        self.assertIn("[[J-2026-0001_특별이익]]", content)  # vault용 위키링크


if __name__ == "__main__":
    unittest.main()
