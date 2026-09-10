"""HTTP surface. Handlers store, enqueue, and read. Work happens in the worker."""

import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from pipeline import db, jobs, log, stages, storage

log.configure()
logger = log.get("api")
app = FastAPI(title="netlist-pipeline", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.migrate()  # idempotent; the worker does the same, whichever boots first wins
    storage.ensure_bucket()


NET_COLOR = {"power": "#f90", "high_speed": "#4af", "diff": "#4af"}


def _anneal_svg(run_id: int) -> str:
    """The annealer's latest snapshot, drawn like docs/media/anneal.gif: parts, rat's nest by
    net class, and the cost curve so far."""
    try:
        f = json.loads(storage.get(f"runs/{run_id}/anneal.json"))
    except Exception:  # noqa: BLE001 - not written yet, or a stale key: show nothing
        return ""
    x1, y1, x2, y2 = f["outline"]
    w, h = x2 - x1, y2 - y1
    pads = f["pads"]
    wires = "".join(
        f'<polyline points="{" ".join(f"{pads[r][p][0]:.1f},{pads[r][p][1]:.1f}" for r, p in pn)}" '
        f'fill="none" stroke="{NET_COLOR.get(cls, "#888")}" stroke-width=".2" '
        f'stroke-opacity="{".8" if cls in NET_COLOR else ".35"}"/>'
        for cls, pn in f["nets"]
    )
    parts = "".join(
        f'<rect x="{bx1:.1f}" y="{by1:.1f}" width="{bx2 - bx1:.1f}" height="{by2 - by1:.1f}" '
        f'fill="{"#556" if r in f["fixed"] else "#36a"}" fill-opacity=".8" stroke="#9bd" '
        f'stroke-width=".2"/><text x="{(bx1 + bx2) / 2:.1f}" y="{(by1 + by2) / 2 + 0.7:.1f}" '
        f'font-size="2" fill="#dde" text-anchor="middle">{r}</text>'
        for r, (bx1, by1, bx2, by2) in f["boxes"].items()
    )
    hist = f["history"]
    top = max(c for _, c in hist)
    curve = " ".join(f"{200 * i / f['n']:.1f},{100 - 100 * c / top:.1f}" for i, c in hist)
    board = (
        f'<svg viewBox="{x1 - 2} {y1 - 2} {w + 4} {h + 4}" width="{min(560, 3.5 * w):.0f}" '
        f'style="vertical-align:top"><rect x="{x1}" y="{y1}" width="{w}" height="{h}" '
        f'fill="#000" stroke="#fc4" stroke-width=".4"/>{wires}{parts}</svg>'
    )
    chart = (
        '<svg viewBox="-2 -2 204 104" width="280" style="vertical-align:top">'
        '<rect x="0" y="0" width="200" height="100" fill="#000" stroke="#444" stroke-width=".5"/>'
        f'<polyline points="{curve}" fill="none" stroke="#fc4" stroke-width="1"/></svg>'
    )
    return (
        f"{'':6}seed {f['seed']}  move {f['i']:,} of {f['n']:,}  temperature {f['t']:.3g}  "
        f"cost {f['cost']:,.0f}   <span class=n>orange power, blue high-speed, grey other;"
        f" right: cost per iteration</span>\n{'':6}{board}  {chart}"
    )


MARK = {"done": ("d", "&#10003;"), "running": ("r", "&#9654;"), "failed": ("f", "&#10007;")}
SHORT = {"extract_netlist": "netlist", "build_board": "build"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Status board: the last 30 runs, one cell per stage. Plain text, refreshes every 2 s."""
    cols = stages.ORDER["generate"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select r.id, r.mode, r.seeds, d.filename, r.status, "
            "coalesce(r.finished_at, now()) - r.created_at, "
            "(select jsonb_object_agg(s.name, jsonb_build_object('status', s.status, "
            "'details', s.details)) from stages s where s.run_id = r.id) "
            "from runs r join designs d on d.id = r.design_id order by r.id desc limit 30"
        )
        rows = cur.fetchall()
    lines = [
        f"{'run':>4}  {'mode':10} {'design':16} {'status':19} {'took':>6}   "
        + "".join(f"{SHORT.get(c, c):14}" for c in cols)
    ]
    for rid, mode, seeds, fn, status, took, st in rows:
        st = st or {}
        cells = []
        for c in cols:
            if c not in stages.ORDER[mode]:
                cells.append(f"<span class=n>{'':14}</span>")
            elif c in st:
                cls, mark = MARK[st[c]["status"]]
                cells.append(f"<span class={cls}>{mark} {SHORT.get(c, c):12}</span>")
            else:
                cells.append(f"<span class=n>&middot; {SHORT.get(c, c):12}</span>")
        board = f'<a href="/runs/{rid}/artifacts/board.png">board</a>' if status == "done" else ""
        cls = "f" if status.startswith("failed") else "r" if status == "running" else ""
        lines.append(
            f'<a href="/runs/{rid}">{rid:>4}</a>  {f"{mode}x{seeds}":10} {fn[:16]:16} '
            f'<span class="{cls}">{status:19}</span>{int(took.total_seconds()):>6}s   '
            f"{''.join(cells)} {board}"
        )
        if status == "running" and st:
            last = max(st, key=cols.index)
            detail = json.dumps(st[last]["details"])[:120]
            lines.append(f"{'':6}&#8627; {last}: <span class=n>{detail}</span>")
            if last == "place":
                lines.append(f'<span id="anneal" data-run="{rid}">{_anneal_svg(rid)}</span>')
    return (
        "<meta http-equiv=refresh content=2><title>netlist-pipeline</title>"
        "<style>body{background:#111;color:#ccc;font:13px/1.5 monospace;padding:1em}"
        "a{color:#8cf}.d{color:#6d6}.r{color:#fc4;font-weight:bold}.f{color:#f66}.n{color:#666}"
        "</style>"
        f"<pre>netlist-pipeline  runs  {datetime.now():%H:%M:%S}   "
        + "\n\n"
        + "\n".join(lines)
        + "</pre><script>const a=document.getElementById('anneal');if(a)setInterval(()=>"
        "fetch('/runs/'+a.dataset.run+'/anneal').then(r=>r.text()).then(t=>{a.innerHTML=t}),100)"
        "</script>"
    )


@app.get("/runs/{run_id}/anneal", response_class=HTMLResponse)
def get_anneal(run_id: int) -> str:
    """The annealer's latest frame as an HTML fragment; the status board polls it."""
    return _anneal_svg(run_id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select 1")
    storage.ping()
    return {"status": "ok"}


def _start_run(
    cur: Any, design_id: int, mode: str, seeds: int, oracle: bool, placer: str = "search"
) -> int:
    cur.execute(
        "insert into runs (design_id, mode, seeds, oracle, placer) "
        "values (%s, %s, %s, %s, %s) returning id",
        (design_id, mode, seeds, oracle, placer),
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
    placer: str = Query(default="search", pattern="^(search|claude)$"),
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
            cur, design_id, mode or ("judge" if has_board else "generate"), seeds, oracle, placer
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
    placer: str = Query(default="search", pattern="^(search|claude)$"),
) -> dict[str, Any]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select has_board from designs where id = %s", (design_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "design not found")
        run_id = _start_run(
            cur, design_id, mode or ("judge" if row[0] else "generate"), seeds, oracle, placer
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
            "select id, design_id, mode, seeds, status, error, created_at, finished_at, placer "
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
                    "placer",
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
