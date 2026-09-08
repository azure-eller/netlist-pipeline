"""Dataset jobs through the queue. Filled in by the data-pipeline track; the worker dispatches
every job whose kind starts with "data:" here."""

from __future__ import annotations

import psycopg

from pipeline import jobs


def run_job(conn: psycopg.Connection, job: jobs.Job) -> None:
    raise NotImplementedError(job.kind)
