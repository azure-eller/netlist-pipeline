"""Stage runner. A stage is a function(ctx) that reads what it needs from S3/Postgres,
does one thing, records provenance, and returns. The runner chains stages per run mode."""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from pipeline import jobs, log

logger = log.get("stages")

ORDER: dict[str, list[str]] = {
    "judge": ["extract_netlist", "constraints", "build_board", "judge", "verify", "export"],
    "generate": [
        "extract_netlist",
        "constraints",
        "build_board",
        "place",
        "route",
        "judge",
        "verify",
        "export",
    ],
}


def next_stage(mode: str, name: str) -> str | None:
    order = ORDER[mode]
    i = order.index(name)
    return order[i + 1] if i + 1 < len(order) else None


@dataclass
class Ctx:
    conn: psycopg.Connection
    run_id: int
    design_id: int
    mode: str
    seeds: int
    oracle: bool
    stage_id: int
    placer: str = "search"
    tool: str | None = None
    tool_version: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    # set by verify to stop the chain without marking the run "failed"
    run_status: str | None = None

    def provenance(self, tool: str, version: str, input_hash: str | None = None) -> None:
        self.tool, self.tool_version, self.input_hash = tool, version, input_hash


StageFn = Callable[[Ctx], None]
STAGES: dict[str, StageFn] = {}


def stage(name: str) -> Callable[[StageFn], StageFn]:
    def deco(fn: StageFn) -> StageFn:
        STAGES[name] = fn
        return fn

    return deco


def run(conn: psycopg.Connection, job: jobs.Job) -> None:
    run_id = int(job.payload["run_id"])
    name = job.kind
    with conn.cursor() as cur:
        cur.execute(
            "select design_id, mode, seeds, oracle, placer from runs where id = %s", (run_id,)
        )
        design_id, mode, seeds, oracle, placer = cur.fetchone()  # type: ignore[misc]
        cur.execute("update runs set status = 'running' where id = %s", (run_id,))
        cur.execute(
            "insert into stages (run_id, name, status) values (%s, %s, 'running') returning id",
            (run_id, name),
        )
        stage_id = int(cur.fetchone()[0])  # type: ignore[index]
    conn.commit()
    ctx = Ctx(conn, run_id, design_id, mode, seeds, oracle, stage_id, placer=placer)
    slog = logger.bind(run_id=run_id, stage=name)
    started = datetime.now(UTC)
    try:
        STAGES[name](ctx)
    except Exception as e:  # noqa: BLE001 - any stage failure fails the run, recorded
        conn.rollback()
        err = f"{type(e).__name__}: {e}"
        slog.error("stage_failed", error=err, tb=traceback.format_exc()[-2000:])
        with conn.cursor() as cur:
            cur.execute(
                "update stages set status = 'failed', error = %s, finished_at = now(), "
                "details = %s where id = %s",
                (err, json.dumps(ctx.details), stage_id),
            )
            cur.execute(
                "update runs set status = 'failed', error = %s, finished_at = now() where id = %s",
                (err, run_id),
            )
        conn.commit()
        raise
    with conn.cursor() as cur:
        cur.execute(
            "update stages set status = 'done', tool = %s, tool_version = %s, input_hash = %s, "
            "output_hash = %s, details = %s, finished_at = now() where id = %s",
            (
                ctx.tool,
                ctx.tool_version,
                ctx.input_hash,
                ctx.output_hash,
                json.dumps(ctx.details),
                stage_id,
            ),
        )
        nxt = None if ctx.run_status else next_stage(mode, name)
        if nxt:
            jobs.enqueue(cur, nxt, {"run_id": run_id})
        else:
            cur.execute(
                "update runs set status = %s, finished_at = now() where id = %s",
                (ctx.run_status or "done", run_id),
            )
    conn.commit()
    slog.info("stage_done", seconds=(datetime.now(UTC) - started).total_seconds(), next=nxt)


def load_all() -> None:
    """Import stage modules so their @stage decorators register."""
    from pipeline.stages import (  # noqa: F401
        build_board,
        constraints,
        export,
        extract_netlist,
        judge,
        place,
        route,
        verify,
    )
