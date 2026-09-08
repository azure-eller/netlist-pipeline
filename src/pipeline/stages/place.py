"""Stage 4: simulated-annealing placement, one candidate per seed."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pipeline.board as board
import pipeline.pcb as pcb
from pipeline import placer, storage
from pipeline.stages import Ctx, common, stage


@stage("place")
def place(ctx: Ctx) -> None:
    text = storage.get(common.run_key(ctx, "unplaced.kicad_pcb")).decode()
    b = board.parse(text)
    nl = common.load_netlist(ctx)
    c = common.load_constraints(ctx)
    d = Path(tempfile.mkdtemp(prefix=f"place{ctx.run_id}-"))
    src = d / "unplaced.kicad_pcb"
    src.write_text(text)
    out_hash = hashlib.sha256()
    for seed in range(1, ctx.seeds + 1):
        positions, cost = placer.place(b, nl, c, seed)
        out = d / f"placed-{seed}.kicad_pcb"
        pcb.set_positions(src, positions, out)
        data = out.read_bytes()
        out_hash.update(data)
        key = storage.put(common.run_key(ctx, f"candidates/{seed}/placed.kicad_pcb"), data)
        with ctx.conn.cursor() as cur:
            cur.execute(
                "insert into candidates (run_id, seed, board_key, proxy_cost) "
                "values (%s, %s, %s, %s)",
                (ctx.run_id, seed, key, cost),
            )
        ctx.details[str(seed)] = cost
    ctx.provenance("pipeline-placer", "0.1.0", storage.sha256(text.encode()))
    ctx.output_hash = out_hash.hexdigest()
