import json
from pathlib import Path


EVALS = Path(__file__).resolve().parents[2] / "evals"


def test_evaluation_sets_have_the_required_case_counts_and_ids():
    reminders = json.loads((EVALS / "reminder_extraction.json").read_text(encoding="utf-8"))
    roles = json.loads((EVALS / "role_consistency.json").read_text(encoding="utf-8"))
    authorization = json.loads((EVALS / "authorization_cases.json").read_text(encoding="utf-8"))
    prompt_injection = json.loads((EVALS / "prompt_injection.json").read_text(encoding="utf-8"))
    assert len(reminders) == 50 and len({case["id"] for case in reminders}) == 50
    assert len(roles) == 20 and len({case["id"] for case in roles}) == 20
    assert len(authorization) == 20 and len({case["id"] for case in authorization}) == 20
    assert len(prompt_injection) == 10 and len({case["id"] for case in prompt_injection}) == 10
    assert any(case["category"] == "dialect" for case in reminders)
    assert any(case["category"] == "multiple_actions" for case in reminders)
    assert {case["source"] for case in prompt_injection} == {"role_description", "shared_fact", "memory"}
