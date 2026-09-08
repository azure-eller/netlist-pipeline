"""Stage 5: Freerouting every placed candidate via Specctra DSN/SES."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pipeline import pcb, storage
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
    routed_all = b""
    for cid, seed, key in rows:
        placed = d / f"placed{seed}.kicad_pcb"
        placed.write_bytes(storage.get(key))
        if cid == rows[0][0]:
            ctx.provenance(
                "freerouting", pcb.freerouting_version(), storage.sha256(placed.read_bytes())
            )
        r = pcb.route(placed, d / f"routed{seed}.kicad_pcb")
        data = (d / f"routed{seed}.kicad_pcb").read_bytes()
        routed_all += data
        new_key = storage.put(common.run_key(ctx, f"candidates/{seed}/routed.kicad_pcb"), data)
        with ctx.conn.cursor() as cur:
            cur.execute("update candidates set board_key = %s where id = %s", (new_key, cid))
        ctx.details[str(seed)] = {"unrouted": r["unrouted"], "seconds": r["seconds"]}
    ctx.details["passes"] = r["passes"]
    ctx.output_hash = storage.sha256(routed_all)
