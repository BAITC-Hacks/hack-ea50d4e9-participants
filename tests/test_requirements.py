import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# The pure response validator does not need the HTTP client. This stub keeps
# the rule tests runnable before backend dependencies are installed.
try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.HTTPError = Exception
    httpx_stub.AsyncClient = object
    sys.modules["httpx"] = httpx_stub

from ai_provider import CHOICE_SCHEMA, _ai_pool, _request_payload, _validate  # noqa: E402
from analytics import summarize_participation  # noqa: E402
from domain import choose_baseline, eligible_candidates, next_step_status, progress  # noqa: E402


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


def employee(grade="Middle"):
    return {
        "employee_id": "E1",
        "role": "Engineer",
        "grade": grade,
        "skills": {"system": 2, "public": 0},
        "last_review_date": "2026-01-01",
        "career_goal": None,
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
        lead_profile = [{
            "role": "Engineer", "grade": "Lead",
            "required_skills": {"system": 5}, "critical_skills": ["system"],
        }]
        state = progress(employee("Lead"), [], [], catalog([], profiles=lead_profile))
        self.assertIsNone(state["target"])
        self.assertEqual("maintain", state["mode"])
        self.assertIsNone(state["coverage_pct"])

    def test_irrelevant_activity_is_not_a_candidate(self):
        candidates, excluded = eligible_candidates(
            employee(), [], [], catalog([event("PUBLIC", "public")])
        )
        self.assertEqual([], candidates)
        self.assertEqual(1, excluded["no_gain"])

    def test_activity_must_close_a_remaining_goal_gap(self):
        ready = employee()
        ready["skills"]["system"] = 4
        data = catalog([event("SYSTEM", "system")])
        data["role_profiles"][1]["required_skills"]["public"] = 2
        self.assertLess(progress(ready, [], [], data)["coverage_pct"], 100)
        candidates, excluded = eligible_candidates(
            ready, [], [], data
        )
        self.assertEqual([], candidates)
        self.assertEqual(1, excluded["no_goal_gain"])

    def test_missed_similar_activity_reduces_score(self):
        course = event("SYSTEM", "system")
        workshop = event("OTHER_SYSTEM", "system")
        workshop["type"] = "workshop"
        unrelated = event("PUBLIC", "public")
        base, _ = eligible_candidates(employee(), [], [], catalog([course, workshop, unrelated]))
        base_score = next(item["score"] for item in base if item["event_id"] == "SYSTEM")
        misses = [
            {"record_id": f"miss-{i}", "event_id": "OTHER_SYSTEM", "date": "2026-09-01", "status": "no_show"}
            for i in range(3)
        ]
        candidates, _ = eligible_candidates(employee(), misses, [], catalog([course, workshop, unrelated]))
        chosen = next(item for item in candidates if item["event_id"] == "SYSTEM")
        self.assertEqual(base_score - 12, chosen["score"])
        self.assertEqual(3, chosen["factors"]["similar_missed"])
        self.assertIn("пропусков/прерываний по тем же навыкам: 3", chosen["reasons"][2]["text"])

        unrelated_misses = [
            {"record_id": "other", "event_id": "PUBLIC", "date": "2026-09-01", "status": "no_show"}
        ]
        unaffected, _ = eligible_candidates(employee(), unrelated_misses, [], catalog([course, workshop, unrelated]))
        self.assertEqual(base_score, next(item["score"] for item in unaffected if item["event_id"] == "SYSTEM"))

    def test_demo_completion_is_counted_in_hr_participation(self):
        records = [
            {"employee_id": "E1", "event_id": "SYSTEM", "status": "no_show"},
            {"employee_id": "E2", "event_id": "SYSTEM", "status": "completed"},
        ]
        completions = [{"employee_id": "E1", "event_id": "SYSTEM"}]
        activities = summarize_participation(records, completions, {"E1"}, {"SYSTEM": "System Design"})
        self.assertEqual(1, len(activities))
        self.assertEqual(2, activities[0]["total"])
        self.assertEqual(1, activities[0]["completed"])
        self.assertEqual(1, activities[0]["no_show"])
        self.assertEqual(50, activities[0]["completion_pct"])

    def test_demo_completion_informs_next_recommendation_history(self):
        data = catalog([event("FIRST", "system"), event("SECOND", "system")])
        finished = {**employee(), "skills": {"system": 3, "public": 0}}
        without_completion, _ = eligible_candidates(finished, [], [], data)
        completion = [{"event_id": "FIRST", "occurrence_id": "once", "completed_on": "2026-09-01"}]
        with_completion, _ = eligible_candidates(employee(), [], completion, data)
        before = next(item for item in without_completion if item["event_id"] == "SECOND")
        after = next(item for item in with_completion if item["event_id"] == "SECOND")
        self.assertEqual(after["score"], before["score"] + 1.5)
        self.assertEqual(after["factors"]["same_type_completed"], 1)
        self.assertIn("Активностей такого типа завершено: 1", after["reasons"][2]["text"])

    def test_hr_step_status_separates_missing_goal_from_catalog_gap(self):
        lead = [{"role": "Engineer", "grade": "Lead", "required_skills": {"system": 5}, "critical_skills": ["system"]}]
        self.assertEqual("no_goal", next_step_status(progress(employee("Lead"), [], [], catalog([], lead)), []))
        self.assertEqual("catalog_gap", next_step_status(progress(employee(), [], [], catalog([])), []))
        ready = employee()
        ready["skills"]["system"] = 4
        self.assertEqual("goal_met", next_step_status(progress(ready, [], [], catalog([])), []))
        data = catalog([event("SYSTEM", "system")])
        candidates, _ = eligible_candidates(employee(), [], [], data)
        self.assertEqual("has_step", next_step_status(progress(employee(), [], [], data), candidates))

    def test_explanation_has_current_required_and_expected_levels(self):
        candidates, _ = eligible_candidates(
            employee(), [], [], catalog([event("SYSTEM", "system")])
        )
        reason = next(item for item in candidates[0]["reasons"] if item["code"] == "skill_gap")
        self.assertIn("сейчас 2", reason["text"])
        self.assertIn("требуется 4", reason["text"])
        self.assertIn("(+1)", reason["text"])

    def test_ai_choice_requires_three_explanation_factors(self):
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

    def test_critical_step_is_first_even_when_local_rank_exceeds_ai_limit(self):
        critical = event("CRITICAL", "system")
        critical["format"] = "online"
        critical["duration_hours"] = 20
        critical["upcoming_sessions"] = ["2026-10-02"]
        public = [event(f"PUBLIC_{index}", "public") for index in range(9)]
        for item in public:
            item["develops_skills"][0]["gain"] = 2
        data = catalog(public + [critical])
        data["role_profiles"][1]["required_skills"]["public"] = 2
        misses = [
            {"record_id": f"miss-{index}", "event_id": "CRITICAL", "date": "2026-09-01", "status": "no_show"}
            for index in range(3)
        ]
        candidates, _ = eligible_candidates(employee(), misses, [], data)
        self.assertGreater(next(index for index, item in enumerate(candidates) if item["event_id"] == "CRITICAL"), 7)
        self.assertEqual("CRITICAL", choose_baseline(candidates)[0]["event_id"])
        self.assertIn("CRITICAL", {item["event_id"] for item in _ai_pool(candidates)})
        self.assertIn("CRITICAL", {item["event_id"] for item in _request_payload(progress(employee(), misses, [], data), candidates)["candidates"]})
        def choice(event_id):
            codes = ["target", "skill_gap", "history"]
            return {"event_id": event_id, "reason_codes": codes, "evidence_ids": [f"{event_id}:{code}" for code in codes]}
        with self.assertRaisesRegex(ValueError, "critical-gap"):
            _validate({"choices": [choice("PUBLIC_0"), choice("CRITICAL")]}, candidates)
        selected = _validate({"choices": [choice("CRITICAL"), choice("PUBLIC_0")]}, candidates)
        self.assertEqual("CRITICAL", selected[0][0]["event_id"])

    def test_openai_schema_leaves_array_constraints_to_validator(self):
        reason_codes = CHOICE_SCHEMA["properties"]["choices"]["items"]["properties"]["reason_codes"]
        self.assertNotIn("minItems", reason_codes)
        self.assertNotIn("uniqueItems", reason_codes)


if __name__ == "__main__":
    unittest.main()
