import copy
import csv
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from domain import choose_baseline, effective_skills, eligible_candidates, progress  # noqa: E402
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

    def test_import_rejects_duplicate_employee_id(self):
        employee = copy.deepcopy(self.employees[0])
        errors = validate_batch([employee], [], self.catalog, {employee["employee_id"]}, set())
        self.assertTrue(any("уже существует" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
