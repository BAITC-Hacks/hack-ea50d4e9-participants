"""Deterministic career progress and candidate ranking.

This module intentionally has no database or AI dependency so the rules can be
tested against the supplied dataset and the jury's additional profiles.
"""

from collections import Counter
from datetime import date


GRADES = ["Junior", "Middle", "Senior", "Lead"]
REPEATABLE_EVENT = "EV_036"


def by_id(items, key):
    return {item[key]: item for item in items}


def profile_for(catalog, role, grade):
    return next(
        (p for p in catalog["role_profiles"] if p["role"] == role and p["grade"] == grade),
        None,
    )


def choose_targets(employee, catalog):
    current_grade = employee["grade"]
    next_grade = GRADES[GRADES.index(current_grade) + 1] if current_grade in GRADES[:-1] else None
    milestone = profile_for(catalog, employee["role"], next_grade) if next_grade else None
    goal = employee.get("career_goal") or None
    long_term = (
        profile_for(catalog, goal["target_role"], goal["target_grade"])
        if goal and goal.get("target_role") and goal.get("target_grade")
        else None
    )
    target = milestone or long_term or profile_for(catalog, employee["role"], current_grade)
    return {
        "target": target,
        "milestone": milestone,
        "long_term": long_term,
        "mode": "next_grade" if milestone else "career_goal" if long_term else "maintain",
    }


def effective_skills(employee, records, completions, catalog):
    values = dict(employee.get("skills") or {})
    events = by_id(catalog["events"], "event_id")
    review = employee["last_review_date"]
    applied = []
    uncertain = []
    changes = []
    for record in records:
        if record["status"] != "completed":
            continue
        event = events.get(record["event_id"])
        if not event:
            continue
        when = record["date"]
        if when <= review:
            if event["format"] == "self_paced":
                uncertain.append(record["record_id"])
            continue
        changes.append((when, record["record_id"], event, "history"))
    for completion in completions:
        event = events.get(completion["event_id"])
        if event and completion["completed_on"] > review:
            changes.append(
                (completion["completed_on"], completion["occurrence_id"], event, "app")
            )
    for when, record_id, event, source in sorted(changes, key=lambda row: (row[0], row[1])):
        for impact in event["develops_skills"]:
            skill_id = impact["skill_id"]
            before = values.get(skill_id, 0)
            after = max(before, min(5, impact["max_level"], before + impact["gain"]))
            values[skill_id] = after
            if after != before:
                applied.append(
                    {
                        "event_id": event["event_id"],
                        "record_id": record_id,
                        "date": when,
                        "source": source,
                        "skill_id": skill_id,
                        "before": before,
                        "after": after,
                    }
                )
    return {"confirmed": employee.get("skills") or {}, "modelled": values, "applied": applied, "uncertain": uncertain}


def progress(employee, records, completions, catalog):
    targets = choose_targets(employee, catalog)
    effective = effective_skills(employee, records, completions, catalog)
    target = targets["target"]
    requirements = target["required_skills"] if target else {}
    skills = by_id(catalog["skills"], "skill_id")
    total = sum(requirements.values())
    covered = 0
    gaps = []
    for skill_id, required in requirements.items():
        actual = effective["modelled"].get(skill_id, 0)
        covered += min(actual, required)
        gaps.append(
            {
                "skill_id": skill_id,
                "name": skills.get(skill_id, {}).get("name", skill_id),
                "confirmed": effective["confirmed"].get(skill_id, 0),
                "modelled": actual,
                "required": required,
                "gap": max(0, required - actual),
                "critical": skill_id in target["critical_skills"],
            }
        )
    gaps.sort(key=lambda x: (-int(x["critical"]), -x["gap"], x["name"]))
    return {
        "as_of_date": catalog["as_of_date"],
        "target": {"role": target["role"], "grade": target["grade"]} if target else None,
        "milestone": (
            {"role": targets["milestone"]["role"], "grade": targets["milestone"]["grade"]}
            if targets["milestone"] else None
        ),
        "long_term_goal": (
            {"role": targets["long_term"]["role"], "grade": targets["long_term"]["grade"]}
            if targets["long_term"] else None
        ),
        "mode": targets["mode"],
        "coverage_pct": round(100 * covered / total) if total else 100,
        "gaps": gaps,
        "confirmed_skills": effective["confirmed"],
        "modelled_skills": effective["modelled"],
        "applied": effective["applied"],
        "uncertain_record_ids": effective["uncertain"],
    }


def _last_session(event, as_of):
    if event["format"] == "self_paced":
        return None
    future = sorted(day for day in event["upcoming_sessions"] if day >= as_of)
    return future[0] if future else None


def eligible_candidates(employee, records, completions, catalog, state=None):
    state = state or progress(employee, records, completions, catalog)
    current = state["modelled_skills"]
    target = choose_targets(employee, catalog)["target"]
    requirements = target["required_skills"] if target else {}
    critical = set(target["critical_skills"]) if target else set()
    long_term = choose_targets(employee, catalog)["long_term"]
    long_requirements = long_term["required_skills"] if long_term else {}
    as_of = catalog["as_of_date"]
    skill_names = {skill["skill_id"]: skill["name"] for skill in catalog["skills"]}
    candidates = []
    exclusions = Counter()
    for event in catalog["events"]:
        event_id = event["event_id"]
        history = [r for r in records if r["event_id"] == event_id]
        completed = [r for r in history if r["status"] == "completed"] + [
            c for c in completions if c["event_id"] == event_id
        ]
        reason = None
        if event["mandatory"]:
            reason = "mandatory"
        elif employee["role"] not in event["target_roles"] or employee["grade"] not in event["target_grades"]:
            reason = "audience"
        elif any(current.get(skill_id, 0) < required for skill_id, required in event["prerequisites"].items()):
            reason = "prerequisites"
        elif event_id != REPEATABLE_EVENT and completed:
            reason = "completed"
        elif any(r["status"] == "in_progress" for r in history):
            reason = "in_progress"
        elif event["format"] != "self_paced" and not _last_session(event, as_of):
            reason = "no_session"
        elif event_id == REPEATABLE_EVENT and completed:
            latest = max((r.get("date") or r.get("completed_on")) for r in completed)
            if (date.fromisoformat(as_of) - date.fromisoformat(latest)).days < 21:
                reason = "repeat_cooldown"
        if reason:
            exclusions[reason] += 1
            continue

        impacts = []
        useful = 0.0
        critical_gain = 0.0
        long_gain = 0.0
        for item in event["develops_skills"]:
            skill_id = item["skill_id"]
            before = current.get(skill_id, 0)
            after = max(before, min(5, item["max_level"], before + item["gain"]))
            actual_gain = after - before
            gap = max(0, requirements.get(skill_id, 0) - before)
            closure = min(actual_gain, gap)
            long_closure = min(actual_gain, max(0, long_requirements.get(skill_id, 0) - before))
            weight = 2.2 if skill_id in critical else 1.0
            useful += closure * weight
            critical_gain += closure if skill_id in critical else 0
            long_gain += long_closure
            impacts.append(
                {
                    "skill_id": skill_id,
                    "name": skill_names.get(skill_id, skill_id),
                    "before": before,
                    "after": after,
                    "gain": actual_gain,
                    "required": requirements.get(skill_id, 0),
                    "gap_before": gap,
                    "closes_gap": closure,
                    "critical": skill_id in critical,
                }
            )
        if not any(item["gain"] > 0 for item in impacts):
            exclusions["no_gain"] += 1
            continue

        same_type = [r for r in records if r["status"] in ("completed", "dropped", "no_show") and
                     next((e["type"] for e in catalog["events"] if e["event_id"] == r["event_id"]), None) == event["type"]]
        same_type_completed = sum(r["status"] == "completed" for r in same_type)
        same_event_missed = sum(r["status"] in ("no_show", "dropped") for r in history)
        history_signal = min(same_type_completed, 3) * 1.5 - min(same_event_missed, 3) * 4
        duration_penalty = min(event["duration_hours"], 20) * 0.3
        score = round(useful * 20 + critical_gain * 8 + long_gain * 4 + history_signal - duration_penalty, 2)
        reasons = [
            {
                "code": "target",
                "text": f"Цель: {state['target']['role']} · {state['target']['grade']}",
            },
            {
                "code": "skill_gap",
                "text": (
                    f"Сокращает разрыв на {sum(i['closes_gap'] for i in impacts)} ур."
                    if useful else "Развивает навыки сверх текущих требований"
                ),
            },
            {
                "code": "history",
                "text": (
                    f"Похожих завершено: {same_type_completed}; пропусков/прерываний этого события: {same_event_missed}"
                ),
            },
            {
                "code": "availability",
                "text": (
                    "Доступно самостоятельно"
                    if event["format"] == "self_paced"
                    else f"Ближайшая сессия: {_last_session(event, as_of)}"
                ),
            },
        ]
        candidates.append(
            {
                "event_id": event_id,
                "title": event["title"],
                "description": event["description"],
                "type": event["type"],
                "format": event["format"],
                "duration_hours": event["duration_hours"],
                "next_session": _last_session(event, as_of),
                "score": score,
                "factors": {
                    "weighted_gap_closure": round(useful, 2),
                    "critical_gap_closure": critical_gain,
                    "long_term_gap_closure": long_gain,
                    "same_type_completed": same_type_completed,
                    "same_event_missed": same_event_missed,
                    "duration_penalty": round(duration_penalty, 2),
                },
                "impacts": impacts,
                "reasons": reasons,
            }
        )
    candidates.sort(key=lambda c: (-c["score"], c["event_id"]))
    return candidates, dict(exclusions)


def choose_baseline(candidates, count=3):
    relevant = [
        candidate for candidate in candidates
        if candidate["factors"]["weighted_gap_closure"] > 0
        or candidate["factors"]["long_term_gap_closure"] > 0
    ]
    return (relevant or candidates)[:count]
