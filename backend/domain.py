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
    # Never turn the current grade into a made-up destination.  If there is no
    # next grade, only an explicit, valid career goal may define a trajectory.
    target = milestone or long_term
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


def _event_exclusion_reason(employee, event, records, completions, current, as_of):
    event_id = event["event_id"]
    history = [record for record in records if record["event_id"] == event_id]
    completed = [record for record in history if record["status"] == "completed"] + [
        completion for completion in completions if completion["event_id"] == event_id
    ]
    if event["mandatory"]:
        return "mandatory"
    if employee["role"] not in event["target_roles"] or employee["grade"] not in event["target_grades"]:
        return "audience"
    if any(current.get(skill_id, 0) < required for skill_id, required in event["prerequisites"].items()):
        return "prerequisites"
    if event_id != REPEATABLE_EVENT and completed:
        return "completed"
    if any(record["status"] == "in_progress" for record in history):
        return "in_progress"
    if event["format"] != "self_paced" and not _last_session(event, as_of):
        return "no_session"
    if event_id == REPEATABLE_EVENT and completed:
        latest = max((record.get("date") or record.get("completed_on")) for record in completed)
        if (date.fromisoformat(as_of) - date.fromisoformat(latest)).days < 21:
            return "repeat_cooldown"
    return None


def critical_skill_blockers(employee, records, completions, catalog, state=None):
    """Explain why a critical gap has no available voluntary activity."""
    state = state or progress(employee, records, completions, catalog)
    current = state["modelled_skills"]
    as_of = catalog["as_of_date"]
    skill_names = {skill["skill_id"]: skill["name"] for skill in catalog["skills"]}
    blocked = []
    for gap in state["gaps"]:
        if not gap["critical"] or gap["gap"] <= 0:
            continue
        events = []
        has_available_step = False
        for event in catalog["events"]:
            impact = next((item for item in event["develops_skills"] if item["skill_id"] == gap["skill_id"]), None)
            if impact is None:
                continue
            before = current.get(gap["skill_id"], 0)
            after = max(before, min(5, impact["max_level"], before + impact["gain"]))
            if after <= before:
                code = "max_level" if impact["max_level"] <= before else "no_gain"
                explanation = (
                    f"Предел этой активности — уровень {impact['max_level']}; текущий уровень уже {before}."
                    if code == "max_level" else "Активность не повышает текущий уровень навыка."
                )
            else:
                code = _event_exclusion_reason(employee, event, records, completions, current, as_of)
                if code is None:
                    has_available_step = True
                    continue
                explanation = {
                    "mandatory": "Это обязательная активность, она показана отдельно от рекомендаций.",
                    "audience": "Активность не подходит для вашей текущей роли или грейда.",
                    "completed": "Вы уже завершили активность; повторное прохождение не предусмотрено.",
                    "in_progress": "Активность уже находится в процессе.",
                    "no_session": "В каталоге пока нет будущей даты сессии.",
                    "repeat_cooldown": "Повтор станет доступен после паузы в 21 день.",
                }.get(code)
                if code == "prerequisites":
                    missing = [
                        f"{skill_names.get(skill_id, skill_id)} {current.get(skill_id, 0)}/{required}"
                        for skill_id, required in event["prerequisites"].items()
                        if current.get(skill_id, 0) < required
                    ]
                    explanation = "Не хватает входных навыков: " + ", ".join(missing) + "."
            events.append({"event_id": event["event_id"], "title": event["title"], "reason_code": code, "reason": explanation})
        if not has_available_step:
            blocked.append({
                "skill_id": gap["skill_id"],
                "name": gap["name"],
                "current": gap["modelled"],
                "required": gap["required"],
                "message": (
                    "В каталоге пока нет активности, развивающей этот навык."
                    if not events else "Сейчас нет доступной добровольной активности для повышения этого навыка."
                ),
                "events": events,
            })
    return blocked


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
    events_by_id = by_id(catalog["events"], "event_id")
    candidates = []
    exclusions = Counter()
    for event in catalog["events"]:
        event_id = event["event_id"]
        history = [r for r in records if r["event_id"] == event_id]
        reason = _event_exclusion_reason(employee, event, records, completions, current, as_of)
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
                    "long_term_required": long_requirements.get(skill_id, 0),
                    "gap_before": gap,
                    "closes_gap": closure,
                    "long_term_closes_gap": long_closure,
                    "critical": skill_id in critical,
                }
            )
        relevant_skills = set(requirements) | set(long_requirements)
        if not any(item["gain"] > 0 and item["skill_id"] in relevant_skills for item in impacts):
            exclusions["no_gain"] += 1
            continue
        if useful <= 0 and long_gain <= 0:
            exclusions["no_goal_gain"] += 1
            continue

        same_type = [r for r in records if r["status"] in ("completed", "dropped", "no_show") and
                     events_by_id.get(r["event_id"], {}).get("type") == event["type"]]
        same_type_completed = sum(r["status"] == "completed" for r in same_type)
        same_event_missed = sum(r["status"] in ("no_show", "dropped") for r in history)
        developed_skills = {item["skill_id"] for item in event["develops_skills"]}
        similar_history = [
            record for record in records
            if record["event_id"] == event_id or developed_skills.intersection(
                item["skill_id"] for item in events_by_id.get(record["event_id"], {}).get("develops_skills", [])
            )
        ]
        similar_missed = sum(r["status"] in ("no_show", "dropped") for r in similar_history)
        similar_declined = sum(r["status"] == "declined" for r in similar_history)
        history_signal = (
            min(same_type_completed, 3) * 1.5
            - min(similar_missed, 3) * 4
            - min(similar_declined, 3) * 2
        )
        duration_penalty = min(event["duration_hours"], 20) * 0.3
        score = round(useful * 20 + critical_gain * 8 + long_gain * 4 + history_signal - duration_penalty, 2)
        key_impact = max(
            (item for item in impacts if item["skill_id"] in relevant_skills),
            key=lambda item: (item["closes_gap"], item["long_term_closes_gap"], item["critical"], item["gain"]),
        )
        key_required = key_impact["required"] if key_impact["closes_gap"] else key_impact["long_term_required"]
        key_goal = "" if key_impact["closes_gap"] else "Для долгосрочной цели — "
        reasons = [
            {
                "code": "target",
                "text": (
                    f"Цель: {state['target']['role']} · {state['target']['grade']}"
                    if key_impact["closes_gap"] else
                    f"Долгосрочная цель: {state['long_term_goal']['role']} · {state['long_term_goal']['grade']}"
                ),
            },
            {
                "code": "skill_gap",
                "text": (
                    f"{key_goal}{key_impact['name']}: сейчас {key_impact['before']}, "
                    f"требуется {key_required}, после шага {key_impact['after']} "
                    f"(+{key_impact['gain']})"
                ),
            },
            {
                "code": "history",
                "text": (
                    f"Похожих завершено: {same_type_completed}; "
                    f"пропусков/прерываний по тем же навыкам: {similar_missed}; "
                    f"отказов: {similar_declined}"
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
                    "similar_missed": similar_missed,
                    "similar_declined": similar_declined,
                    "duration_penalty": round(duration_penalty, 2),
                },
                "impacts": impacts,
                "reasons": reasons,
            }
        )
    candidates.sort(key=lambda c: (-c["score"], c["event_id"]))
    return candidates, dict(exclusions)


def explain_candidate(candidate, candidate_count, local_rank, state):
    """Summarize the verified impact and ranking without attributing motives to AI."""
    focus = max(
        (impact for impact in candidate["impacts"] if impact["closes_gap"] or impact["long_term_closes_gap"]),
        key=lambda impact: (impact["closes_gap"], impact["long_term_closes_gap"], impact["critical"]),
    )
    if focus["closes_gap"]:
        kind = "критичный разрыв" if focus["critical"] else "разрыв"
        summary = (
            f"Сокращает {kind} по навыку {focus['name']}: "
            f"{focus['before']} → {focus['after']} при требовании {focus['required']}."
        )
    else:
        goal = state["long_term_goal"]
        goal_name = f"{goal['role']} · {goal['grade']}" if goal else "долгосрочной цели"
        summary = (
            f"Приближает долгосрочную цель {goal_name}: {focus['name']} "
            f"{focus['before']} → {focus['after']} при требовании {focus['long_term_required']}."
        )
    summary += f" По локальному рейтингу — место {local_rank} из {candidate_count} допустимых шагов."
    missed = candidate["factors"]["similar_missed"]
    if missed:
        summary += f" Пропуски похожих активностей ({missed}) уже снизили оценку этого шага."
    return summary


def choose_baseline(candidates, count=3):
    relevant = [
        candidate for candidate in candidates
        if candidate["factors"]["weighted_gap_closure"] > 0
        or candidate["factors"]["long_term_gap_closure"] > 0
    ]
    return relevant[:count]
