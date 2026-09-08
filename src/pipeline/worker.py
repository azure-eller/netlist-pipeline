"""Job loop. One process, one job at a time; run more processes to scale."""

import time

from pipeline import db, jobs, log, stages, storage
from pipeline.config import settings

logger = log.get("worker")


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
            try:
                stages.run(conn, job)
            except Exception as e:  # noqa: BLE001
                jobs.finish(conn, job, error=f"{type(e).__name__}: {e}")
            else:
                jobs.finish(conn, job)


if __name__ == "__main__":
    main()
