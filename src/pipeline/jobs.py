"""Postgres-backed job queue. One table, SKIP LOCKED, forward-only states."""

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from pipeline.config import settings


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int


def enqueue(cur: psycopg.Cursor[Any], kind: str, payload: dict[str, Any]) -> int:
    cur.execute(
        "insert into jobs (kind, payload) values (%s, %s) returning id",
        (kind, json.dumps(payload)),
    )
    return int(cur.fetchone()[0])  # type: ignore[index]


def claim(conn: psycopg.Connection) -> Job | None:
    with conn.cursor() as cur:
        cur.execute(
            "update jobs set status = 'running', started_at = now(), attempts = attempts + 1 "
            "where id = (select id from jobs where status = 'queued' "
            "order by id for update skip locked limit 1) "
            "returning id, kind, payload, attempts"
        )
        row = cur.fetchone()
    conn.commit()
    return Job(row[0], row[1], row[2], row[3]) if row else None


def finish(conn: psycopg.Connection, job: Job, error: str | None = None) -> None:
    retry = error is not None and job.attempts < settings.job_max_attempts
    status = "queued" if retry else ("failed" if error else "done")
    with conn.cursor() as cur:
        cur.execute(
            "update jobs set status = %s, error = %s, finished_at = now() where id = %s",
            (status, error, job.id),
        )
    conn.commit()


def requeue_stale(conn: psycopg.Connection) -> int:
    """A worker that died mid-job leaves it 'running'; give it back to the queue."""
    with conn.cursor() as cur:
        cur.execute(
            "update jobs set status = 'queued' where status = 'running' "
            "and started_at < now() - make_interval(secs => %s)",
            (settings.job_timeout_seconds,),
        )
        n = cur.rowcount
    conn.commit()
    return n
