"""HTTP surface. Handlers store, enqueue, and read. Work happens in the worker."""

from typing import Any

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse

from pipeline import db, jobs, log, storage

log.configure()
logger = log.get("api")
app = FastAPI(title="netlist-pipeline", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.migrate()  # idempotent; the worker does the same, whichever boots first wins
    storage.ensure_bucket()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select 1")
    storage.ping()
    return {"status": "ok"}


def _start_run(cur: Any, design_id: int, mode: str, seeds: int, oracle: bool) -> int:
    cur.execute(
        "insert into runs (design_id, mode, seeds, oracle) values (%s, %s, %s, %s) returning id",
        (design_id, mode, seeds, oracle),
    )
    run_id = int(cur.fetchone()[0])
    jobs.enqueue(cur, "extract_netlist", {"run_id": run_id})
    return run_id


@app.post("/designs", status_code=201)
async def upload_design(
    file: UploadFile,
    mode: str | None = Query(default=None, pattern="^(judge|generate)$"),
    seeds: int = Query(default=1, ge=1, le=10),
    oracle: bool = False,
) -> dict[str, Any]:
    name = file.filename or "upload"
    if not (name.endswith(".kicad_sch") or name.endswith(".zip")):
        raise HTTPException(400, "upload a .kicad_sch or a .zip of the project directory")
    data = await file.read()
    sha = storage.sha256(data)
    has_board = name.endswith(".zip") and b".kicad_pcb" in data
    key = storage.put(f"designs/{sha}/{name}", data)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into designs (filename, sha256, object_key, has_board) values (%s, %s, %s, %s) "
            "on conflict (sha256) do update set filename = excluded.filename returning id",
            (name, sha, key, has_board),
        )
        design_id = int(cur.fetchone()[0])  # type: ignore[index]
        run_id = _start_run(
            cur, design_id, mode or ("judge" if has_board else "generate"), seeds, oracle
        )
        conn.commit()
    logger.info("design_uploaded", design_id=design_id, run_id=run_id, sha256=sha, bytes=len(data))
    return {"design_id": design_id, "run_id": run_id, "sha256": sha}


@app.post("/designs/{design_id}/runs", status_code=201)
def new_run(
    design_id: int,
    mode: str | None = Query(default=None, pattern="^(judge|generate)$"),
    seeds: int = Query(default=1, ge=1, le=10),
    oracle: bool = False,
) -> dict[str, Any]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select has_board from designs where id = %s", (design_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "design not found")
        run_id = _start_run(
            cur, design_id, mode or ("judge" if row[0] else "generate"), seeds, oracle
        )
        conn.commit()
    return {"design_id": design_id, "run_id": run_id}


@app.get("/designs/{design_id}")
def get_design(design_id: int) -> dict[str, Any]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, filename, sha256, has_board, created_at from designs where id = %s",
            (design_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "design not found")
        cur.execute(
            "select id, mode, status, created_at from runs where design_id = %s order by id",
            (design_id,),
        )
        runs = [
            dict(zip(["id", "mode", "status", "created_at"], r, strict=True))
            for r in cur.fetchall()
        ]
    return {
        "id": row[0],
        "filename": row[1],
        "sha256": row[2],
        "has_board": row[3],
        "created_at": row[4],
        "runs": runs,
    }


@app.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, design_id, mode, seeds, status, error, created_at, finished_at "
            "from runs where id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "run not found")
        out: dict[str, Any] = dict(
            zip(
                [
                    "id",
                    "design_id",
                    "mode",
                    "seeds",
                    "status",
                    "error",
                    "created_at",
                    "finished_at",
                ],
                row,
                strict=True,
            )
        )
        cur.execute(
            "select name, status, tool, tool_version, input_hash, output_hash, error, details, "
            "started_at, finished_at from stages where run_id = %s order by id",
            (run_id,),
        )
        cols = [
            "name",
            "status",
            "tool",
            "tool_version",
            "input_hash",
            "output_hash",
            "error",
            "details",
            "started_at",
            "finished_at",
        ]
        out["stages"] = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        cur.execute(
            "select source, body from constraints where run_id = %s order by id desc limit 1",
            (run_id,),
        )
        c = cur.fetchone()
        out["constraints"] = {"source": c[0], "body": c[1]} if c else None
        cur.execute(
            "select id, seed, proxy_cost, score, metrics, chosen from candidates "
            "where run_id = %s order by seed",
            (run_id,),
        )
        out["candidates"] = [
            dict(zip(["id", "seed", "proxy_cost", "score", "metrics", "chosen"], r, strict=True))
            for r in cur.fetchall()
        ]
        cur.execute(
            "select passed, drc, netlist_match, unrouted, in_bounds from verifications "
            "where run_id = %s order by id desc limit 1",
            (run_id,),
        )
        v = cur.fetchone()
        out["verification"] = (
            dict(zip(["passed", "drc", "netlist_match", "unrouted", "in_bounds"], v, strict=True))
            if v
            else None
        )
        cur.execute(
            "select name, sha256, bytes from artifacts where run_id = %s order by name", (run_id,)
        )
        out["artifacts"] = [
            dict(zip(["name", "sha256", "bytes"], r, strict=True)) for r in cur.fetchall()
        ]
    return out


@app.get("/runs/{run_id}/artifacts/{name}")
def get_artifact(run_id: int, name: str) -> RedirectResponse:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select object_key from artifacts where run_id = %s and name = %s", (run_id, name)
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "artifact not found")
    return RedirectResponse(storage.presign(row[0]), status_code=307)
