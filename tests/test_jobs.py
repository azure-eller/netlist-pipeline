"""Queue semantics against the compose Postgres (make up + migrate first)."""

import psycopg
import pytest

from pipeline import db, jobs


@pytest.fixture
def conn() -> psycopg.Connection:  # type: ignore[misc]
    try:
        c = db.connect()
    except psycopg.OperationalError:
        pytest.skip("postgres not running")
    with c.cursor() as cur:
        cur.execute("delete from jobs")
    c.commit()
    yield c
    c.close()


def test_claim_is_exclusive_and_finish_marks_done(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        jobs.enqueue(cur, "extract_netlist", {"run_id": 1})
    conn.commit()
    job = jobs.claim(conn)
    assert job is not None and job.kind == "extract_netlist" and job.attempts == 1
    assert jobs.claim(conn) is None  # nothing else queued; the running one is not re-claimable
    jobs.finish(conn, job)
    with conn.cursor() as cur:
        cur.execute("select status from jobs where id = %s", (job.id,))
        assert cur.fetchone()[0] == "done"  # type: ignore[index]


def test_stale_running_job_is_requeued(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        jobs.enqueue(cur, "x", {})
        cur.execute("update jobs set status = 'running', started_at = now() - interval '1 day'")
    conn.commit()
    assert jobs.requeue_stale(conn) == 1
    assert jobs.claim(conn) is not None
