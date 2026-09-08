"""Job loop. One process claims; each job runs in a fresh interpreter.

pcbnew's SWIG bindings lose their type state after a few boards in one process, and a KiCad
crash must not take the loop down, so every job is `python -m pipeline.worker --job <id>`.
Run more worker processes to scale."""

import subprocess
import sys
import time

from pipeline import db, jobs, log, stages, storage
from pipeline.config import settings

logger = log.get("worker")


def run_one(job_id: int) -> None:
    """Child: run one claimed job to completion. Stage/run rows record the outcome."""
    log.configure()
    stages.load_all()
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select id, kind, payload, attempts from jobs where id = %s", (job_id,))
            row = cur.fetchone()
        if row is None:
            raise SystemExit(f"job {job_id} not found")
        stages.run(conn, jobs.Job(row[0], row[1], row[2], row[3]))


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
            p = subprocess.run(
                [sys.executable, "-m", "pipeline.worker", "--job", str(job.id)],
                timeout=settings.job_stale_after_seconds,
            )
            jobs.finish(conn, job, error=None if p.returncode == 0 else f"exit {p.returncode}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        run_one(int(sys.argv[2]))
    else:
        main()
