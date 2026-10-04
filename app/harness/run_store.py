"""RunStore：SQLite 持久化每次运行（agent / task），run id 即 trace_id。

设计取舍：M2 用 stdlib sqlite3 + WAL，零外部服务即可跑通最小版本；
接口只暴露 start/update/get/list，后续换 PostgreSQL 只改这一处。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"


@dataclass
class RunRecord:
    id: str
    kind: str  # "chat" | "task"
    name: str  # agent 名或 task 名
    status: str = STATUS_RUNNING
    input: Any = None
    output: str | None = None
    error: str | None = None
    steps: list[dict] = field(default_factory=list)
    attempts: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class RunStore:
    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：调度线程与 API 线程可共享同一连接
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                input TEXT,
                output TEXT,
                error TEXT,
                steps TEXT,
                attempts INTEGER DEFAULT 0,
                started_at REAL,
                finished_at REAL
            )
            """
        )
        self._conn.commit()

    def start(self, run: RunRecord) -> None:
        self._conn.execute(
            "INSERT INTO runs (id, kind, name, status, input, output, error, steps, attempts, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            self._tuple(run),
        )
        self._conn.commit()

    def update(self, run: RunRecord) -> None:
        self._conn.execute(
            "UPDATE runs SET status=?, output=?, error=?, steps=?, attempts=?, finished_at=? WHERE id=?",
            (run.status, run.output, run.error, self._dump(run.steps), run.attempts, run.finished_at, run.id),
        )
        self._conn.commit()

    def get(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list(self, limit: int = 50, kind: str | None = None) -> list[RunRecord]:
        if kind:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE kind=? ORDER BY started_at DESC LIMIT ?", (kind, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _dump(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _tuple(run: RunRecord) -> tuple:
        return (
            run.id, run.kind, run.name, run.status, RunStore._dump(run.input),
            run.output, run.error, RunStore._dump(run.steps), run.attempts,
            run.started_at, run.finished_at,
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RunRecord:
        def _load(s):
            if not s:
                return None
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return None

        return RunRecord(
            id=row["id"], kind=row["kind"], name=row["name"], status=row["status"],
            input=_load(row["input"]), output=row["output"], error=row["error"],
            steps=_load(row["steps"]) or [], attempts=row["attempts"],
            started_at=row["started_at"], finished_at=row["finished_at"],
        )
