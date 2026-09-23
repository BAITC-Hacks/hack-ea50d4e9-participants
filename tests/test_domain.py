import copy
import csv
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from domain import critical_skill_blockers, choose_baseline, effective_skills, eligible_candidates, explain_candidate, progress  # noqa: E402
from ingestion import validate_batch  # noqa: E402


def dataset():
    data = ROOT / "data"
    skills = json.loads((data / "skills.json").read_text(encoding="utf-8"))
    events = json.loads((data / "events.json").read_text(encoding="utf-8"))
    employees = json.loads((data / "employees.json").read_text(encoding="utf-8"))["employees"]
    with (data / "activity_history.csv").open(encoding="utf-8-sig", newline="") as stream:
        history = list(csv.DictReader(stream))
    catalog = {
        "version": skills["meta"]["version"],
        "as_of_date": skills["meta"]["as_of_date"],
        "skills": skills["skills"],
        "role_profiles": skills["role_profiles"],
        "events": events["events"],
    }
    return catalog, employees, history


class CareerRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.employees, cls.history = dataset()

    def test_dataset_is_loaded_at_expected_size(self):
        self.assertEqual((len(self.employees), len(self.catalog["events"]), len(self.catalog["skills"]), len(self.history)), (200, 40, 60, 2743))

    def test_completion_after_review_applies_once_and_never_lowers_skill(self):
        employee = copy.deepcopy(self.employees[0])
        employee["skills"]["SK_SYSTEM_DESIGN"] = 5
        employee["last_review_date"] = "2026-09-01"
        records = [{"record_id": "new", "event_id": "EV_005", "date": "2026-09-15", "status": "completed"}]
        result = effective_skills(employee, records, [], self.catalog)
        self.assertEqual(result["modelled"]["SK_SYSTEM_DESIGN"], 5)

    def test_self_paced_enrollment_before_review_is_uncertain(self):
        employee = copy.deepcopy(self.employees[0])
        employee["last_review_date"] = "2026-09-15"
        records = [{"record_id": "ambiguous", "event_id": "EV_005", "date": "2026-09-01", "status": "completed"}]
        self_paced = next(event for event in self.catalog["events"] if event["format"] == "self_paced" and event["develops_skills"])
        records[0]["event_id"] = self_paced["event_id"]
        affected_skill = self_paced["develops_skills"][0]["skill_id"]
        before = employee["skills"].get(affected_skill, 0)
        result = effective_skills(employee, records, [], self.catalog)
        self.assertEqual(result["modelled"].get(affected_skill, 0), before)
        self.assertIn("ambiguous", result["uncertain"])

    def test_recommendation_uses_critical_gap_not_lowest_skill(self):
        employee = copy.deepcopy(next(e for e in self.employees if e["role"] == "Backend Engineer" and e["grade"] == "Middle"))
        senior = next(p for p in self.catalog["role_profiles"] if p["role"] == "Backend Engineer" and p["grade"] == "Senior")
        employee["skills"] = dict(senior["required_skills"])
        employee["skills"]["SK_SYSTEM_DESIGN"] = 2
        employee["skills"]["SK_PUBLIC_SPEAKING"] = 0
        employee["last_review_date"] = self.catalog["as_of_date"]
        records = [{"record_id": f"miss{i}", "event_id": "EV_036", "date": f"2026-0{i+3}-01", "status": "no_show"} for i in range(3)]
        state = progress(employee, records, [], self.catalog)
        candidates, _ = eligible_candidates(employee, records, [], self.catalog, state)
        selected = choose_baseline(candidates)
        self.assertTrue(selected)
        self.assertIn(selected[0]["event_id"], {"EV_005", "EV_006", "EV_007"})
        self.assertNotEqual(selected[0]["event_id"], "EV_036")
        self.assertTrue(all(not next(e for e in self.catalog["events"] if e["event_id"] == c["event_id"])["mandatory"] for c in candidates))

    def test_e0028_explains_unavailable_system_design_step(self):
        employee = next(e for e in self.employees if e["employee_id"] == "E0028")
        records = [record for record in self.history if record["employee_id"] == employee["employee_id"]]
        state = progress(employee, records, [], self.catalog)
        candidates, _ = eligible_candidates(employee, records, [], self.catalog, state)
        blockers = critical_skill_blockers(employee, records, [], self.catalog, state)
        system_design = next(blocker for blocker in blockers if blocker["skill_id"] == "SK_SYSTEM_DESIGN")
        self.assertEqual((system_design["current"], system_design["required"]), (3, 4))
        self.assertEqual(
            {event["event_id"]: event["reason_code"] for event in system_design["events"]},
            {"EV_005": "max_level", "EV_006": "completed", "EV_007": "completed"},
        )
        self.assertEqual([candidate["event_id"] for candidate in candidates], ["EV_037", "EV_010", "EV_009"])
        summary = explain_candidate(candidates[0], len(candidates), 1, state)
        self.assertIn("Mentoring", summary)
        self.assertIn("2 → 3", summary)
        self.assertIn("место 1 из 3", summary)

    def test_blocker_disappears_when_relevant_event_is_available(self):
        employee = next(e for e in self.employees if e["employee_id"] == "E0028")
        catalog = copy.deepcopy(self.catalog)
        event = next(event for event in catalog["events"] if event["event_id"] == "EV_005")
        event["develops_skills"][0]["max_level"] = 4
        records = [record for record in self.history if record["employee_id"] == employee["employee_id"]]
        blockers = critical_skill_blockers(employee, records, [], catalog)
        self.assertNotIn("SK_SYSTEM_DESIGN", {blocker["skill_id"] for blocker in blockers})

    def test_blocker_states_when_catalog_has_no_developing_event(self):
        employee = next(e for e in self.employees if e["employee_id"] == "E0028")
        catalog = copy.deepcopy(self.catalog)
        for event in catalog["events"]:
            event["develops_skills"] = [impact for impact in event["develops_skills"] if impact["skill_id"] != "SK_SYSTEM_DESIGN"]
        records = [record for record in self.history if record["employee_id"] == employee["employee_id"]]
        blocker = next(blocker for blocker in critical_skill_blockers(employee, records, [], catalog) if blocker["skill_id"] == "SK_SYSTEM_DESIGN")
        self.assertEqual(blocker["events"], [])
        self.assertIn("нет активности", blocker["message"])

    def test_import_rejects_duplicate_employee_id(self):
        employee = copy.deepcopy(self.employees[0])
        errors = validate_batch([employee], [], self.catalog, {employee["employee_id"]}, set())
        self.assertTrue(any("уже существует" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
