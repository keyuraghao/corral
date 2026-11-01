"""Optional persistence for jobs and tasks.

The controller keeps the live node registry in memory, because a node is only real while its socket is
open. Jobs and tasks, though, are history worth keeping: with a store, the controller can be restarted
and still answer for every job it has run. Without one (the default in tests), everything lives in
memory and is lost on exit.

This uses the standard library sqlite3, so it adds no dependency. Jobs and tasks are stored as JSON
blobs with a few promoted columns for listing and filtering. All access goes through a single lock,
which is ample for a controller coordinating a human-scale fleet.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import Job, Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,
    name       TEXT,
    state      TEXT NOT NULL,
    created_at REAL NOT NULL,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    state      TEXT NOT NULL,
    created_at REAL NOT NULL,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_job ON tasks(job_id);
"""


class JobStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_job(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO jobs(job_id, name, state, created_at, data) VALUES (?, ?, ?, ?, ?)",
                (job.job_id, job.spec.name, job.state.value, job.created_at, job.model_dump_json()),
            )
            self._conn.commit()

    def save_task(self, task: Task) -> None:
        with self._lock:
            cols = "task_id, job_id, node_id, state, created_at, data"
            self._conn.execute(
                f"INSERT OR REPLACE INTO tasks({cols}) VALUES (?, ?, ?, ?, ?, ?)",
                (task.task_id, task.job_id, task.node_id, task.state.value, task.created_at, task.model_dump_json()),
            )
            self._conn.commit()

    def load_jobs(self) -> list[Job]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM jobs ORDER BY created_at").fetchall()
        return [Job.model_validate(json.loads(r[0])) for r in rows]

    def load_tasks(self) -> list[Task]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM tasks ORDER BY created_at").fetchall()
        return [Task.model_validate(json.loads(r[0])) for r in rows]
