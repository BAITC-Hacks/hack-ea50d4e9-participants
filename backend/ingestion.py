"""Validation for jury profiles and activity history uploads."""

import csv
import io
import json
from datetime import date


STATUSES = {"completed", "in_progress", "dropped", "no_show", "declined", "overdue"}
GRADES = {"Junior", "Middle", "Senior", "Lead"}
FORMATS = {"office", "hybrid", "remote"}
LANGUAGES = {"kk", "ru", "en"}
MAX_BYTES = 3_000_000


def parse_employees(contents):
    if not contents:
        return []
    if len(contents) > MAX_BYTES:
        raise ValueError("Файл сотрудников больше 3 МБ")
    try:
        root = json.loads(contents.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Некорректный JSON сотрудников: {exc}") from exc
    if isinstance(root, list):
        return root
    if isinstance(root, dict) and isinstance(root.get("employees"), list):
        return root["employees"]
    if isinstance(root, dict) and "employee_id" in root:
        return [root]
    raise ValueError("Ожидается профиль, массив профилей или объект с employees[]")


def parse_history(contents):
    if not contents:
        return []
    if len(contents) > MAX_BYTES:
        raise ValueError("Файл истории больше 3 МБ")
    try:
        stream = io.StringIO(contents.decode("utf-8-sig"))
        reader = csv.DictReader(stream)
        required = {"record_id", "employee_id", "event_id", "date", "due_date", "status", "completion_pct", "score", "feedback_rating", "assigned_by"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError("В CSV не хватает столбцов истории")
        return list(reader)
    except UnicodeDecodeError as exc:
        raise ValueError("История должна быть CSV в UTF-8") from exc


def _valid_date(value):
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _int_range(value, lower, upper, allow_empty=False):
    if allow_empty and value in ("", None):
        return True
    try:
        number = int(value)
        return lower <= number <= upper and str(number) == str(value)
    except (TypeError, ValueError):
        return False


def validate_batch(employees, history, catalog, known_employee_ids, known_record_ids):
    errors = []
    skill_ids = {s["skill_id"] for s in catalog["skills"]}
    event_ids = {e["event_id"] for e in catalog["events"]}
    profiles = {(p["role"], p["grade"]) for p in catalog["role_profiles"]}
    new_ids = set()
    new_record_ids = set()
    for position, employee in enumerate(employees, 1):
        prefix = f"Сотрудник #{position}"
        if not isinstance(employee, dict):
            errors.append(f"{prefix}: ожидается объект")
            continue
        employee_id = employee.get("employee_id")
        if not isinstance(employee_id, str) or not employee_id.strip():
            errors.append(f"{prefix}: отсутствует employee_id")
        elif employee_id in known_employee_ids or employee_id in new_ids:
            errors.append(f"{prefix}: employee_id {employee_id} уже существует")
        else:
            new_ids.add(employee_id)
        if not isinstance(employee.get("full_name"), str) or not employee["full_name"].strip():
            errors.append(f"{prefix}: требуется full_name")
        if (employee.get("role"), employee.get("grade")) not in profiles:
            errors.append(f"{prefix}: неизвестная пара role/grade")
        if employee.get("grade") not in GRADES:
            errors.append(f"{prefix}: неизвестный грейд")
        if employee.get("work_format") not in FORMATS:
            errors.append(f"{prefix}: неверный work_format")
        if employee.get("preferred_language") not in LANGUAGES:
            errors.append(f"{prefix}: неверный preferred_language")
        if not _valid_date(employee.get("hire_date")) or not _valid_date(employee.get("last_review_date")):
            errors.append(f"{prefix}: неверная дата")
        if not _int_range(employee.get("tenure_months"), 0, 1200):
            errors.append(f"{prefix}: неверный tenure_months")
        skills = employee.get("skills")
        if not isinstance(skills, dict):
            errors.append(f"{prefix}: skills должен быть объектом")
        else:
            for skill_id, level in skills.items():
                if skill_id not in skill_ids or not _int_range(level, 0, 5):
                    errors.append(f"{prefix}: неверный навык {skill_id} или уровень")
        goal = employee.get("career_goal")
        if goal is not None and (
            not isinstance(goal, dict)
            or (goal.get("target_role"), goal.get("target_grade")) not in profiles
        ):
            errors.append(f"{prefix}: неверная career_goal")
    all_employee_ids = known_employee_ids | new_ids
    for position, activity in enumerate(history, 2):
        prefix = f"История, строка {position}"
        record_id = activity.get("record_id")
        if not record_id or record_id in known_record_ids or record_id in new_record_ids:
            errors.append(f"{prefix}: пустой или повторный record_id")
        else:
            new_record_ids.add(record_id)
        if activity.get("employee_id") not in all_employee_ids:
            errors.append(f"{prefix}: неизвестный employee_id")
        if activity.get("event_id") not in event_ids:
            errors.append(f"{prefix}: неизвестный event_id")
        if not _valid_date(activity.get("date")):
            errors.append(f"{prefix}: неверная date")
        if activity.get("due_date") and not _valid_date(activity["due_date"]):
            errors.append(f"{prefix}: неверная due_date")
        if activity.get("status") not in STATUSES:
            errors.append(f"{prefix}: неверный status")
        if activity.get("assigned_by") not in {"self", "manager", "hr"}:
            errors.append(f"{prefix}: неверный assigned_by")
        if not _int_range(activity.get("completion_pct"), 0, 100):
            errors.append(f"{prefix}: неверный completion_pct")
        if not _int_range(activity.get("score"), 0, 100, allow_empty=True):
            errors.append(f"{prefix}: неверный score")
        if not _int_range(activity.get("feedback_rating"), 1, 5, allow_empty=True):
            errors.append(f"{prefix}: неверный feedback_rating")
        if activity.get("status") == "completed" and activity.get("completion_pct") != "100":
            errors.append(f"{prefix}: completed требует completion_pct=100")
    return errors[:100]
