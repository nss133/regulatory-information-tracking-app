import sqlite3
import unittest
from pathlib import Path

from briefing.db import StoredItem, init_db
from briefing.deadlines import deadline_urgency, deadlines_to_json, extract_deadlines
from briefing.render import render_email


ROOT = Path(__file__).resolve().parents[1]


class TestDeadlines(unittest.TestCase):
    def test_extracts_explicit_submission_and_effective_dates(self):
        values = extract_deadlines(
            "의견 제출기한은 2026년 8월 21일까지이며, 이 규정은 2026. 9. 1. 시행한다."
        )
        self.assertEqual(
            [(value.label, value.date) for value in values],
            [("의견제출", "2026-08-21"), ("시행일", "2026-09-01")],
        )

    def test_ignores_unrelated_dates_and_invalid_date(self):
        self.assertEqual(extract_deadlines("보도일자는 2026년 8월 14일입니다."), [])
        self.assertEqual(extract_deadlines("시행일은 2026년 13월 40일입니다."), [])

    def test_inherits_year_for_effective_date_after_promulgation(self):
        values = extract_deadlines(
            "법률 제21358호, 2026. 2. 19. 공포, 8. 20. 시행됨에 따라"
        )
        self.assertEqual(
            [(value.label, value.date) for value in values],
            [("시행일", "2026-08-20")],
        )

    def test_does_not_bind_promulgation_date_to_later_effective_keyword(self):
        values = extract_deadlines("2026. 2. 19. 공포, 8. 20. 시행 예정")
        self.assertNotIn(
            ("시행일", "2026-02-19"),
            [(value.label, value.date) for value in values],
        )

    def test_category_allowlist_can_disable_case_law_dates(self):
        text = (
            "2016. 3. 18. 개정되어 2016. 4. 1.부터 시행된 구 "
            "보험업감독업무시행세칙"
        )
        self.assertEqual(extract_deadlines(text, allowed_labels=set()), [])

    def test_urgency(self):
        run_date = "2026-08-14T08:00:00+09:00"
        self.assertEqual(deadline_urgency("2026-08-20", run_date), "urgent")
        self.assertEqual(deadline_urgency("2026-09-01", run_date), "soon")
        self.assertEqual(deadline_urgency("2026-08-01", run_date), "expired")

    def test_email_shows_date_and_deadline(self):
        item = StoredItem(
            id="fsc:deadline", source="fsc", category="legislation",
            source_item_key="deadline", title="보험업법 시행령 입법예고",
            url="https://example.com", published_at="2026-08-14",
            attachments_json="[]", content_hash="hash", importance="high",
            importance_reason="보험업법", summary="개정안 요약",
            last_changed_at="2026-08-14T08:00:00+09:00",
            last_sent_at=None, sent_hash=None,
            deadlines_json=deadlines_to_json(extract_deadlines(
                "의견제출 기한 2026년 8월 21일"
            )),
        )
        result = render_email(
            items=[item], template_dir=ROOT / "templates", subject="테스트",
            run_date="2026-08-14T08:00:00+09:00",
        )
        self.assertIn("의견제출 2026-08-21", result.html)
        self.assertNotIn("권장:", result.html)
        self.assertIn("2026-08-14", result.html)

    def test_email_hides_expired_deadline(self):
        item = StoredItem(
            id="scourt:expired", source="scourt", category="case_law",
            source_item_key="expired", title="과거 법령 연혁을 인용한 판결",
            url="https://example.com", published_at="2026-08-20",
            attachments_json="[]", content_hash="hash", importance="low",
            importance_reason="", summary=None,
            last_changed_at="2026-08-20T08:00:00+09:00",
            last_sent_at=None, sent_hash=None,
            deadlines_json=deadlines_to_json(extract_deadlines(
                "2016. 4. 1.부터 시행"
            )),
        )
        result = render_email(
            items=[item], template_dir=ROOT / "templates", subject="테스트",
            run_date="2026-08-20T08:00:00+09:00",
        )
        self.assertNotIn("2016-04-01", result.html)
        self.assertNotIn("deadline-expired", result.html)


if __name__ == "__main__":
    unittest.main()
