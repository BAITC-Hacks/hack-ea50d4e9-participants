"""Pure HR aggregations shared by the API and regression tests."""

from collections import Counter, defaultdict


def summarize_participation(records, completions, selected_ids, event_names):
    participation = defaultdict(Counter)
    for record in records:
        if record["employee_id"] in selected_ids:
            participation[record["event_id"]][record["status"]] += 1
    for completion in completions:
        if completion["employee_id"] in selected_ids:
            participation[completion["event_id"]]["completed"] += 1

    activities = [
        {"event_id": event_id, "title": event_names.get(event_id, event_id), "total": sum(counts.values()),
         "completed": counts["completed"], "no_show": counts["no_show"], "dropped": counts["dropped"],
         "declined": counts["declined"], "overdue": counts["overdue"],
         "completion_pct": round(100 * counts["completed"] / sum(counts.values()))}
        for event_id, counts in participation.items()
    ]
    activities.sort(key=lambda item: -item["total"])
    return activities
