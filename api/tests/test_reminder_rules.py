from datetime import datetime, timezone

from app.confirmations import create_reminder_change_candidate
from app.reminder_service import canonical_recurrence, next_occurrence
from app.tools import ToolContext


def test_monthly_rule_uses_explicit_day_and_skips_short_months():
    start = datetime(2026, 1, 15, 9, tzinfo=timezone.utc)
    rule = canonical_recurrence("每月十五号", start)
    assert rule == "FREQ=MONTHLY;BYMONTHDAY=15"
    result = next_occurrence(rule, start, datetime(2026, 1, 16, tzinfo=timezone.utc), "UTC")
    assert result == datetime(2026, 2, 15, 9, tzinfo=timezone.utc)


def test_weekly_rule_preserves_local_clock_across_dst_transition():
    start = datetime.fromisoformat("2026-03-01T08:00:00-05:00")
    rule = canonical_recurrence("每周日", start)
    result = next_occurrence(rule, start, datetime.fromisoformat("2026-03-01T09:00:00-05:00"), "America/New_York")
    assert result is not None
    assert result.hour == 8
    assert result.utcoffset().total_seconds() == -4 * 3600


def test_one_shot_and_explicit_repeat_rules():
    due = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    assert canonical_recurrence("就这一次，不重复", due) is None
    assert canonical_recurrence("每天", due) == "FREQ=DAILY"
    assert canonical_recurrence("每周三", due) == "FREQ=WEEKLY;BYDAY=WE"


def test_cancel_operation_is_rejected_without_dedicated_role_grant():
    result = create_reminder_change_candidate(
        {"reminder_id": "reminder-id", "expected_version": 1, "operation": "cancel"},
        ToolContext("family-test", "member-test", allowed_tools=frozenset({"propose_reminder_change"})),
        "conversation-id", "tool-call-id", "取消这条提醒",
    )
    assert result["ok"] is False
    assert "不能替您取消" in result["message"]
