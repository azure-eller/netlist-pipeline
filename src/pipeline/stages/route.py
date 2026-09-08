"""Stage 5: Freerouting every placed candidate via Specctra DSN/SES."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pipeline import pcb, storage
from pipeline.config import settings
from pipeline.stages import Ctx, common, stage


@stage("route")
def route(ctx: Ctx) -> None:
    with ctx.conn.cursor() as cur:
        cur.execute(
            "select id, seed, board_key from candidates where run_id = %s order by seed",
            (ctx.run_id,),
        )
        rows = cur.fetchall()
    if not rows:
        raise RuntimeError("no candidates to route (place stage has not run)")
    d = Path(tempfile.mkdtemp(prefix=f"run{ctx.run_id}-route-"))
    placed = {seed: d / f"placed{seed}.kicad_pcb" for _, seed, _ in rows}
    for _, seed, key in rows:
        placed[seed].write_bytes(storage.get(key))
    ctx.provenance(
        "freerouting", pcb.freerouting_version(), storage.sha256(placed[rows[0][1]].read_bytes())
    )

    def route_one(seed: int) -> dict[str, Any]:
        # seeds are independent candidates: one Freerouting process each, in parallel
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "pipeline.pcb",
                "route",
                str(placed[seed]),
                str(d / f"routed{seed}.kicad_pcb"),
            ],
            capture_output=True,
            text=True,
            timeout=settings.freerouting_timeout_seconds + 120,
        )
        if p.returncode != 0:
            raise RuntimeError(f"route seed {seed} failed: {p.stderr[-1500:]}")
        return json.loads(p.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]

    with ThreadPoolExecutor(max_workers=min(len(rows), os.cpu_count() or 1)) as pool:
        results = dict(zip(placed, pool.map(route_one, placed), strict=True))

    routed_all = b""
    for cid, seed, _ in rows:
        data = (d / f"routed{seed}.kicad_pcb").read_bytes()
        routed_all += data
        new_key = storage.put(common.run_key(ctx, f"candidates/{seed}/routed.kicad_pcb"), data)
        with ctx.conn.cursor() as cur:
            cur.execute("update candidates set board_key = %s where id = %s", (new_key, cid))
        r = results[seed]
        ctx.details[str(seed)] = {"unrouted": r["unrouted"], "seconds": r["seconds"]}
    ctx.details["passes"] = settings.freerouting_passes
    ctx.output_hash = storage.sha256(routed_all)
