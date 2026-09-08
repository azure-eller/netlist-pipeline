"""Stage 3: judge mode passes the uploaded board through as candidate 0; generate mode builds
the unplaced board from the netlist (and the uploaded board as a template when present)."""

from __future__ import annotations

import json

from pipeline import pcb, storage
from pipeline.stages import Ctx, common, stage


@stage("build_board")
def build_board(ctx: Ctx) -> None:
    u = common.unpack(ctx)
    nl = common.load_netlist(ctx)
    c = common.load_constraints(ctx)
    if ctx.mode == "judge":
        if u.pcb is None:
            raise ValueError("judge mode needs a .kicad_pcb in the upload")
        data = u.pcb.read_bytes()
        key = storage.put(common.run_key(ctx, "candidates/0/board.kicad_pcb"), data)
        with ctx.conn.cursor() as cur:
            cur.execute(
                "insert into candidates (run_id, seed, board_key, chosen) values (%s, 0, %s, true)",
                (ctx.run_id, key),
            )
        ctx.provenance("pcbnew", pcb.build_version(), storage.sha256(data))
        ctx.details = {"source": "upload", "board_key": key}
    else:
        out = u.dir / "unplaced.kicad_pcb"
        ctx.details = pcb.build_unplaced(nl, c, u.pcb, out)
        data = out.read_bytes()
        # SaveBoard wrote the project (net classes: widths, clearances, vias) next to the
        # board; later stages put it beside every board copy so pcbnew and the router see it.
        storage.put(
            common.run_key(ctx, "project.kicad_pro"), out.with_suffix(".kicad_pro").read_bytes()
        )
        storage.put(common.run_key(ctx, "unplaced.kicad_pcb"), data)
        source = u.pcb.read_bytes() if u.pcb else json.dumps(nl.to_json()).encode()
        ctx.provenance("pcbnew", pcb.build_version(), storage.sha256(source))
    ctx.output_hash = storage.sha256(data)
