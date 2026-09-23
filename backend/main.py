"""Career Quest HTTP API and built frontend host."""

import hashlib
import json
import logging
import os
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
from analytics import summarize_participation
from ai_provider import rerank
from config import DEMO_MODE
from domain import REPEATABLE_EVENT, choose_baseline, eligible_candidates, progress
from ingestion import parse_employees, parse_history, validate_batch


LOGGER = logging.getLogger(__name__)
_recommendation_cache = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_database()
    yield


app = FastAPI(title="Career Quest API", lifespan=lifespan)


def check_access(role, selected_employee, employee_id=None, hr=False):
    if not DEMO_MODE:
        raise HTTPException(503, "Требуется корпоративная авторизация; демо-режим выключен")
    if role not in {"employee", "hr"}:
        raise HTTPException(403, "Неизвестная демо-роль")
    if hr and role != "hr":
        raise HTTPException(403, "Доступ только для HR")
    if employee_id and role != "hr" and selected_employee != employee_id:
        raise HTTPException(403, "Нет доступа к профилю другого сотрудника")


def load_employee(conn, employee_id):
    row = database.get_employee(conn, employee_id)
    if not row:
        raise HTTPException(404, "Сотрудник не найден")
    return row["profile"]


@app.get("/api/health")
def health():
    with database.connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "demo_mode": DEMO_MODE}


@app.get("/api/meta")
def meta():
    with database.connect() as conn:
        catalog = database.get_catalog(conn)
        batches = database.get_batches(conn)
    return {"as_of_date": catalog["as_of_date"], "dataset_version": catalog["version"], "batches": batches, "demo_mode": DEMO_MODE}


@app.get("/api/employees")
def employees(
    batch_id: str | None = None,
    x_demo_role: str = Header("employee"),
    x_demo_employee: str | None = Header(None),
):
    check_access(x_demo_role, None)
    with database.connect() as conn:
        result = database.list_employees(conn, batch_id)
    if x_demo_role != "hr":
        if not x_demo_employee:
            raise HTTPException(403, "Не выбран профиль сотрудника")
        result = [item for item in result if item["employee_id"] == x_demo_employee]
    return [
        {key: item.get(key) for key in ("employee_id", "full_name", "role", "grade", "department", "preferred_language")}
        for item in result
    ]


@app.get("/api/employees/{employee_id}")
def employee_detail(
    employee_id: str,
    x_demo_role: str = Header("employee"),
    x_demo_employee: str | None = Header(None),
):
    check_access(x_demo_role, x_demo_employee, employee_id)
    with database.connect() as conn:
        employee = load_employee(conn, employee_id)
        records = database.get_records(conn, employee_id)
        completions = database.get_completions(conn, employee_id)
        catalog = database.get_catalog(conn)
    events = {event["event_id"]: event for event in catalog["events"]}
    mandatory = [
        {"event_id": event["event_id"], "title": event["title"], "status": next(
            (r["status"] for r in reversed(records) if r["event_id"] == event["event_id"]), "not_assigned"
        )}
        for event in catalog["events"] if event["mandatory"] and employee["role"] in event["target_roles"]
    ]
    history = [
        {**record, "event_title": events.get(record["event_id"], {}).get("title", record["event_id"])}
        for record in records[-30:]
    ]
    history.extend(
        {"event_id": completion["event_id"], "event_title": events.get(completion["event_id"], {}).get("title", completion["event_id"]),
         "date": completion["completed_on"], "status": "completed", "source": "app"}
        for completion in completions
    )
    history.sort(key=lambda row: row["date"], reverse=True)
    return {"profile": employee, "history": history[:30], "mandatory": mandatory}


@app.get("/api/employees/{employee_id}/progress")
def employee_progress(
    employee_id: str,
    x_demo_role: str = Header("employee"),
    x_demo_employee: str | None = Header(None),
):
    check_access(x_demo_role, x_demo_employee, employee_id)
    with database.connect() as conn:
        employee = load_employee(conn, employee_id)
        return progress(
            employee,
            database.get_records(conn, employee_id),
            database.get_completions(conn, employee_id),
            database.get_catalog(conn),
        )


@app.get("/api/employees/{employee_id}/recommendations")
async def recommendations(
    employee_id: str,
    x_demo_role: str = Header("employee"),
    x_demo_employee: str | None = Header(None),
):
    check_access(x_demo_role, x_demo_employee, employee_id)
    started = time.monotonic()
    with database.connect() as conn:
        employee = load_employee(conn, employee_id)
        records = database.get_records(conn, employee_id)
        completions = database.get_completions(conn, employee_id)
        catalog = database.get_catalog(conn)
    state = progress(employee, records, completions, catalog)
    candidates, exclusions = eligible_candidates(employee, records, completions, catalog, state)
    relevant = [candidate for candidate in candidates if candidate["factors"]["weighted_gap_closure"] > 0 or candidate["factors"]["long_term_gap_closure"] > 0]
    if not relevant:
        return {"items": [], "provider": "no_candidates", "excluded": exclusions, "candidate_count": 0, "duration_ms": round((time.monotonic() - started) * 1000)}
    rankable = relevant
    key_data = [employee, records, completions, catalog["version"], catalog["as_of_date"]]
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cached = _recommendation_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return {**cached[1], "cached": True}
    chosen, provider = await rerank(state, rankable)
    if chosen is None:
        chosen = [(candidate, [reason["code"] for reason in candidate["reasons"]]) for candidate in choose_baseline(rankable)]
    items = [
        {**candidate, "selected_reason_codes": codes, "explanation": " · ".join(reason["text"] for reason in candidate["reasons"] if reason["code"] in codes)}
        for candidate, codes in chosen
    ]
    result = {"items": items, "provider": provider, "excluded": exclusions, "candidate_count": len(candidates), "duration_ms": round((time.monotonic() - started) * 1000), "cached": False}
    if len(_recommendation_cache) > 500:
        _recommendation_cache.clear()
    _recommendation_cache[key] = (time.monotonic() + 300, result)
    return result


class CompletionRequest(BaseModel):
    occurrence_id: str | None = None


@app.post("/api/employees/{employee_id}/activities/{event_id}/complete")
def complete_activity(
    employee_id: str,
    event_id: str,
    body: CompletionRequest,
    x_demo_role: str = Header("employee"),
    x_demo_employee: str | None = Header(None),
):
    check_access(x_demo_role, x_demo_employee, employee_id)
    with database.connect() as conn:
        employee = load_employee(conn, employee_id)
        records = database.get_records(conn, employee_id)
        completions = database.get_completions(conn, employee_id)
        catalog = database.get_catalog(conn)
        candidates, _ = eligible_candidates(employee, records, completions, catalog)
        candidate = next((item for item in candidates if item["event_id"] == event_id), None)
        if not candidate:
            raise HTTPException(409, "Активность сейчас недоступна или уже завершена")
        occurrence_id = f"demo:{catalog['as_of_date']}" if event_id == REPEATABLE_EVENT else "once"
        try:
            conn.execute(
                "INSERT INTO completions(employee_id,event_id,occurrence_id,completed_on) VALUES (%s,%s,%s,%s)",
                (employee_id, event_id, occurrence_id, catalog["as_of_date"]),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(409, "Завершение уже учтено") from exc
        updated = progress(employee, records, database.get_completions(conn, employee_id), catalog)
    _recommendation_cache.clear()
    return {"event_id": event_id, "occurrence_id": occurrence_id, "progress": updated}


async def _read_import_files(employees_file, history_file):
    if not employees_file and not history_file:
        raise HTTPException(400, "Выберите JSON сотрудников и/или CSV истории")
    try:
        employee_bytes = await employees_file.read(3_000_001) if employees_file else b""
        history_bytes = await history_file.read(3_000_001) if history_file else b""
        return parse_employees(employee_bytes), parse_history(history_bytes), employee_bytes, history_bytes
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _validate_import(conn, employees, history):
    catalog = database.get_catalog(conn)
    employee_ids = {row["employee_id"] for row in conn.execute("SELECT employee_id FROM employees")}
    record_ids = {row["record_id"] for row in conn.execute("SELECT record_id FROM activity_records")}
    return validate_batch(employees, history, catalog, employee_ids, record_ids)


@app.post("/api/import/preview")
async def import_preview(
    employees_file: UploadFile | None = File(None),
    history_file: UploadFile | None = File(None),
    x_demo_role: str = Header("employee"),
):
    check_access(x_demo_role, None, hr=True)
    employees, history, _, _ = await _read_import_files(employees_file, history_file)
    with database.connect() as conn:
        errors = _validate_import(conn, employees, history)
    return {"employee_count": len(employees), "activity_count": len(history), "valid": not errors, "errors": errors}


@app.post("/api/import/commit")
async def import_commit(
    employees_file: UploadFile | None = File(None),
    history_file: UploadFile | None = File(None),
    label: str = Form("Проверочный набор"),
    x_demo_role: str = Header("employee"),
):
    check_access(x_demo_role, None, hr=True)
    employees, history, employee_bytes, history_bytes = await _read_import_files(employees_file, history_file)
    batch_id = "jury-" + hashlib.sha256(employee_bytes + b"\x00" + history_bytes).hexdigest()[:16]
    with database.connect() as conn:
        exists = conn.execute("SELECT id FROM import_batches WHERE id = %s", (batch_id,)).fetchone()
        if exists:
            return {"batch_id": batch_id, "already_imported": True}
        errors = _validate_import(conn, employees, history)
        if errors:
            raise HTTPException(422, {"errors": errors})
        database.insert_batch(conn, batch_id, label[:120], employees, history)
    _recommendation_cache.clear()
    return {"batch_id": batch_id, "already_imported": False, "employee_count": len(employees), "activity_count": len(history)}


@app.delete("/api/import/{batch_id}")
def import_delete(batch_id: str, x_demo_role: str = Header("employee")):
    check_access(x_demo_role, None, hr=True)
    with database.connect() as conn:
        if not conn.execute("SELECT 1 FROM import_batches WHERE id = %s", (batch_id,)).fetchone():
            raise HTTPException(404, "Набор не найден")
        try:
            database.delete_batch(conn, batch_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    _recommendation_cache.clear()
    return {"deleted": batch_id}


@app.get("/api/hr/overview")
def hr_overview(
    role: str | None = None,
    grade: str | None = None,
    department: str | None = None,
    x_demo_role: str = Header("employee"),
):
    check_access(x_demo_role, None, hr=True)
    with database.connect() as conn:
        catalog = database.get_catalog(conn)
        employees = database.list_employees(conn)
        records = database.get_all_records(conn)
        completions = database.get_all_completions(conn)
    selected = [employee for employee in employees if (not role or employee["role"] == role) and
                (not grade or employee["grade"] == grade) and
                (not department or employee["department"] == department)]
    record_map = defaultdict(list)
    completion_map = defaultdict(list)
    for record in records:
        record_map[record["employee_id"]].append(record)
    for completion in completions:
        completion_map[completion["employee_id"]].append(completion)
    gap_count = Counter()
    gap_denominator = Counter()
    no_step = []
    for employee in selected:
        employee_id = employee["employee_id"]
        state = progress(employee, record_map[employee_id], completion_map[employee_id], catalog)
        for gap in state["gaps"]:
            gap_denominator[gap["skill_id"]] += 1
            if gap["gap"]:
                gap_count[gap["skill_id"]] += 1
        candidates, exclusions = eligible_candidates(employee, record_map[employee_id], completion_map[employee_id], catalog, state)
        if not candidates:
            no_step.append({"employee_id": employee_id, "full_name": employee["full_name"], "role": employee["role"], "grade": employee["grade"], "reasons": exclusions})
    skill_names = {skill["skill_id"]: skill["name"] for skill in catalog["skills"]}
    gaps = [
        {"skill_id": skill_id, "name": skill_names.get(skill_id, skill_id), "employee_count": count,
         "denominator": gap_denominator[skill_id], "share_pct": round(100 * count / gap_denominator[skill_id])}
        for skill_id, count in gap_count.most_common()
    ]
    selected_ids = {employee["employee_id"] for employee in selected}
    event_names = {event["event_id"]: event["title"] for event in catalog["events"]}
    activities = summarize_participation(records, completions, selected_ids, event_names)
    return {"employee_count": len(selected), "gaps": gaps, "without_step": no_step,
            "without_step_count": len(no_step), "participation": activities,
            "filters": {"roles": sorted({e["role"] for e in employees}), "grades": ["Junior", "Middle", "Senior", "Lead"],
                        "departments": sorted({e["department"] for e in employees})}}


FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parents[1] / "frontend" / "dist"))
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        file_path = FRONTEND_DIST / path
        if path and file_path.is_file() and file_path.resolve().is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
