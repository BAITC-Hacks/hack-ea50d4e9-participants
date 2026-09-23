import unittest
import sys
import types

# The pure response validator does not need the HTTP client.  Keeping this
# tiny stub lets the unit tests run before backend dependencies are installed
# (for example in a fresh CI checkout).
try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.HTTPError = Exception
    httpx_stub.AsyncClient = object
    sys.modules["httpx"] = httpx_stub

from ai_provider import _validate
from domain import eligible_candidates, progress


def catalog(events, profiles=None):
    return {
        "as_of_date": "2026-10-01",
        "version": "test",
        "skills": [
            {"skill_id": "system", "name": "System Design"},
            {"skill_id": "public", "name": "Public Speaking"},
        ],
        "role_profiles": profiles if profiles is not None else [
            {"role": "Engineer", "grade": "Middle", "required_skills": {"system": 2}, "critical_skills": []},
            {"role": "Engineer", "grade": "Senior", "required_skills": {"system": 4}, "critical_skills": ["system"]},
        ],
        "events": events,
    }


def employee(grade="Middle", goal=None):
    return {
        "employee_id": "E1",
        "role": "Engineer",
        "grade": grade,
        "skills": {"system": 2, "public": 0},
        "last_review_date": "2026-01-01",
        "career_goal": goal,
    }


def event(event_id, skill):
    return {
        "event_id": event_id,
        "title": event_id,
        "description": "test",
        "type": "course",
        "format": "self_paced",
        "duration_hours": 1,
        "mandatory": False,
        "target_roles": ["Engineer"],
        "target_grades": ["Middle"],
        "develops_skills": [{"skill_id": skill, "gain": 1, "max_level": 5}],
        "prerequisites": {},
        "upcoming_sessions": [],
    }


class DomainRequirementsTests(unittest.TestCase):
    def test_max_grade_without_goal_has_no_invented_trajectory(self):
        lead = employee("Lead")
        lead_profile = [{
            "role": "Engineer", "grade": "Lead",
            "required_skills": {"system": 5}, "critical_skills": ["system"],
        }]
        state = progress(lead, [], [], catalog([], profiles=lead_profile))
        self.assertIsNone(state["target"])
        self.assertEqual("maintain", state["mode"])

    def test_irrelevant_activity_is_not_a_candidate(self):
        candidates, excluded = eligible_candidates(
            employee(), [], [], catalog([event("PUBLIC", "public")])
        )
        self.assertEqual([], candidates)
        self.assertEqual(1, excluded["no_gain"])

    def test_explanation_contains_current_required_and_expected_levels(self):
        candidates, _ = eligible_candidates(
            employee(), [], [], catalog([event("SYSTEM", "system")])
        )
        reason = next(item for item in candidates[0]["reasons"] if item["code"] == "skill_gap")
        self.assertIn("сейчас 2", reason["text"])
        self.assertIn("требуется 4", reason["text"])
        self.assertIn("(+1)", reason["text"])

    def test_ai_choice_requires_three_independent_explanation_factors(self):
        candidates, _ = eligible_candidates(
            employee(), [], [], catalog([event("SYSTEM", "system")])
        )
        response = {
            "choices": [{
                "event_id": "SYSTEM",
                "reason_codes": ["skill_gap"],
                "evidence_ids": ["SYSTEM:skill_gap"],
            }]
        }
        with self.assertRaises(ValueError):
            _validate(response, candidates)


if __name__ == "__main__":
    unittest.main()
