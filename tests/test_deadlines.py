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


if __name__ == "__main__":
    unittest.main()
