"""Job loop. One process claims; each job runs in a fresh interpreter.

pcbnew's SWIG bindings lose their type state after a few boards in one process, and a KiCad
crash must not take the loop down, so every job is `python -m pipeline.worker --job <id>`.
Run more worker processes to scale."""

import os
import signal
import subprocess
import sys
import time

import psycopg

from pipeline import db, jobs, log, stages, storage
from pipeline.config import settings

logger = log.get("worker")


def run_one(job_id: int) -> None:
    """Child: run one claimed job to completion. Stage/run rows record the outcome."""
    log.configure()
    stages.load_all()
    logger.info("child_started", job_id=job_id)
    with db.connect() as conn:
        logger.info("child_db_connected", job_id=job_id)
        with conn.cursor() as cur:
            cur.execute("select id, kind, payload, attempts from jobs where id = %s", (job_id,))
            row = cur.fetchone()
        if row is None:
            raise SystemExit(f"job {job_id} not found")
        try:
            stages.run(conn, jobs.Job(row[0], row[1], row[2], row[3]))
        except Exception:  # noqa: BLE001
            # The stage and run rows already record the failure; a deterministic stage error
            # is not retried. Exit 0 so the parent marks the job done. Crashes and timeouts
            # (non-zero exit, no rows written) still retry.
            return


def main() -> None:
    log.configure()
    stages.load_all()
    db.migrate()
    storage.ensure_bucket()
    logger.info("worker_started", stages=sorted(stages.STAGES))
    last_sweep = 0.0
    while True:
        with db.connect() as conn:
            if time.monotonic() - last_sweep > 60:
                n = jobs.requeue_stale(conn)
                if n:
                    logger.warning("requeued_stale_jobs", count=n)
                last_sweep = time.monotonic()
            job = jobs.claim(conn)
            if job is None:
                time.sleep(settings.worker_poll_seconds)
                continue
            logger.info("job_claimed", job_id=job.id, kind=job.kind, attempt=job.attempts)
            error = run_child(job.id)
            if error and error.startswith("timeout"):
                fail_run(conn, job, error)
            jobs.finish(conn, job, error=error)


def run_child(job_id: int) -> str | None:
    """Run the job in a child interpreter in its own process group; kill the whole group
    (Freerouting included) on timeout. Returns an error string or None."""
    p = subprocess.Popen(
        [sys.executable, "-m", "pipeline.worker", "--job", str(job_id)], start_new_session=True
    )
    try:
        rc = p.wait(timeout=settings.job_timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        return f"timeout after {settings.job_timeout_seconds}s"
    return None if rc == 0 else f"exit {rc}"


def fail_run(conn: psycopg.Connection, job: jobs.Job, error: str) -> None:
    """The child could not record its own death; close the stage and run rows."""
    run_id = int(job.payload["run_id"])
    with conn.cursor() as cur:
        cur.execute(
            "update stages set status = 'failed', error = %s, finished_at = now() "
            "where run_id = %s and status = 'running'",
            (error, run_id),
        )
        cur.execute(
            "update runs set status = 'failed', error = %s, finished_at = now() where id = %s",
            (error, run_id),
        )
    conn.commit()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        run_one(int(sys.argv[2]))
    else:
        main()
