"""PostgreSQL persistence and idempotent seed loading."""

import csv
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config import DATABASE_URL


DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_database():
    schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(schema)
        exists = conn.execute("SELECT 1 FROM import_batches WHERE id = 'base'").fetchone()
        if exists:
            return

        skills = json.loads((DATA_DIR / "skills.json").read_text(encoding="utf-8"))
        employees = json.loads((DATA_DIR / "employees.json").read_text(encoding="utf-8"))["employees"]
        events = json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))
        with (DATA_DIR / "activity_history.csv").open(encoding="utf-8-sig", newline="") as stream:
            activities = list(csv.DictReader(stream))

        conn.execute(
            """INSERT INTO catalog(id, version, as_of_date, skills, role_profiles, events)
               VALUES (1, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                skills["meta"]["version"],
                skills["meta"]["as_of_date"],
                Jsonb(skills["skills"]),
                Jsonb(skills["role_profiles"]),
                Jsonb(events["events"]),
            ),
        )
        conn.execute(
            "INSERT INTO import_batches(id,label,employee_count,activity_count) VALUES ('base','Стартовый набор',%s,%s)",
            (len(employees), len(activities)),
        )
        conn.executemany(
            "INSERT INTO employees(employee_id,batch_id,profile) VALUES (%s,'base',%s)",
            [(item["employee_id"], Jsonb(item)) for item in employees],
        )
        conn.executemany(
            """INSERT INTO activity_records(record_id,employee_id,event_id,batch_id,activity)
               VALUES (%s,%s,%s,'base',%s)""",
            [
                (item["record_id"], item["employee_id"], item["event_id"], Jsonb(item))
                for item in activities
            ],
        )


def get_catalog(conn):
    row = conn.execute("SELECT * FROM catalog WHERE id = 1").fetchone()
    return {
        "version": row["version"],
        "as_of_date": row["as_of_date"].isoformat(),
        "skills": row["skills"],
        "role_profiles": row["role_profiles"],
        "events": row["events"],
    }


def list_employees(conn, batch_id=None):
    if batch_id:
        rows = conn.execute(
            "SELECT profile FROM employees WHERE batch_id = %s ORDER BY employee_id", (batch_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT profile FROM employees ORDER BY employee_id").fetchall()
    return [row["profile"] for row in rows]


def get_employee(conn, employee_id):
    row = conn.execute(
        "SELECT profile,batch_id FROM employees WHERE employee_id = %s", (employee_id,)
    ).fetchone()
    return row if row else None


def get_records(conn, employee_id):
    rows = conn.execute(
        "SELECT activity FROM activity_records WHERE employee_id = %s ORDER BY activity->>'date', record_id",
        (employee_id,),
    ).fetchall()
    return [row["activity"] for row in rows]


def get_completions(conn, employee_id):
    rows = conn.execute(
        "SELECT event_id,occurrence_id,completed_on FROM completions WHERE employee_id = %s ORDER BY completed_on,event_id",
        (employee_id,),
    ).fetchall()
    return [
        {**row, "completed_on": row["completed_on"].isoformat()} for row in rows
    ]


def get_all_records(conn):
    rows = conn.execute("SELECT activity FROM activity_records").fetchall()
    return [row["activity"] for row in rows]


def get_all_completions(conn):
    rows = conn.execute("SELECT employee_id,event_id,occurrence_id,completed_on FROM completions").fetchall()
    return [
        {**row, "completed_on": row["completed_on"].isoformat()} for row in rows
    ]


def get_batches(conn):
    rows = conn.execute(
        "SELECT id,label,created_at,employee_count,activity_count FROM import_batches ORDER BY created_at"
    ).fetchall()
    return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]


def insert_batch(conn, batch_id, label, employees, activities):
    conn.execute(
        "INSERT INTO import_batches(id,label,employee_count,activity_count) VALUES (%s,%s,%s,%s)",
        (batch_id, label, len(employees), len(activities)),
    )
    if employees:
        conn.executemany(
            "INSERT INTO employees(employee_id,batch_id,profile) VALUES (%s,%s,%s)",
            [(employee["employee_id"], batch_id, Jsonb(employee)) for employee in employees],
        )
    if activities:
        conn.executemany(
            """INSERT INTO activity_records(record_id,employee_id,event_id,batch_id,activity)
               VALUES (%s,%s,%s,%s,%s)""",
            [
                (activity["record_id"], activity["employee_id"], activity["event_id"], batch_id, Jsonb(activity))
                for activity in activities
            ],
        )


def delete_batch(conn, batch_id):
    if batch_id == "base":
        raise ValueError("Стартовый набор нельзя удалить")
    conn.execute(
        "DELETE FROM completions WHERE employee_id IN (SELECT employee_id FROM employees WHERE batch_id = %s)",
        (batch_id,),
    )
    conn.execute("DELETE FROM activity_records WHERE batch_id = %s", (batch_id,))
    conn.execute("DELETE FROM employees WHERE batch_id = %s", (batch_id,))
    conn.execute("DELETE FROM import_batches WHERE id = %s", (batch_id,))
