CREATE TABLE IF NOT EXISTS catalog (
    id integer PRIMARY KEY CHECK (id = 1),
    version text NOT NULL,
    as_of_date date NOT NULL,
    skills jsonb NOT NULL,
    role_profiles jsonb NOT NULL,
    events jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id text PRIMARY KEY,
    label text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    employee_count integer NOT NULL,
    activity_count integer NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    employee_id text PRIMARY KEY,
    batch_id text NOT NULL REFERENCES import_batches(id),
    profile jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_records (
    record_id text PRIMARY KEY,
    employee_id text NOT NULL REFERENCES employees(employee_id),
    event_id text NOT NULL,
    batch_id text NOT NULL REFERENCES import_batches(id),
    activity jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS activity_employee_idx ON activity_records(employee_id);
CREATE INDEX IF NOT EXISTS employee_batch_idx ON employees(batch_id);

CREATE TABLE IF NOT EXISTS completions (
    employee_id text NOT NULL REFERENCES employees(employee_id),
    event_id text NOT NULL,
    occurrence_id text NOT NULL,
    completed_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(employee_id, event_id, occurrence_id)
);

CREATE INDEX IF NOT EXISTS completions_employee_idx ON completions(employee_id);
