"""Stage 4: simulated-annealing placement, one candidate per seed. With `placer=claude` each
search layout is refined by one Claude call (placer_claude) and the refined board is the
candidate; the search board is kept alongside it for the record."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pipeline.board as board
import pipeline.pcb as pcb
from pipeline import placer, storage
from pipeline.config import settings
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
    in_hash = hashlib.sha256()
    out_hash = hashlib.sha256()
    pads_on = {f.ref: {p.number for p in f.pads} for f in b.footprints}
    nets = [
        (c.net_class(n.name), pins)
        for n in nl.nets
        if len(pins := [(x.ref, x.pin) for x in n.nodes if x.pin in pads_on.get(x.ref, ())]) > 1
    ]
    for seed in range(1, ctx.seeds + 1):

        def watch(
            i: int,
            n: int,
            t: float,
            cost: float,
            geo: dict[str, Any],
            s: int = seed,
            history: list[tuple[int, float]] = [],  # noqa: B006 - one list per seed, by design
        ) -> None:
            # live snapshot for the status board (GET /); overwritten every 0.1% of the anneal
            # (~3 ms each, under 4% of the stage)
            history.append((i, cost))
            frame = {
                "seed": s,
                "i": i,
                "n": n,
                "t": t,
                "cost": cost,
                "outline": b.outline,
                "fixed": sorted(c.fixed),
                "boxes": {r: g[1] for r, g in geo.items()},
                "pads": {r: g[0] for r, g in geo.items()},
                "nets": nets,
                "history": history,
            }
            storage.put(common.run_key(ctx, "anneal.json"), json.dumps(frame).encode())

        positions, cost = placer.place(b, nl, c, seed, watch=watch)
        out = d / f"placed-{seed}.kicad_pcb"
        pcb.set_positions(src, positions, out)
        data = out.read_bytes()
        if ctx.placer == "claude":
            from pipeline import placer_claude  # optional dependency: imported on this path only

            storage.put(common.run_key(ctx, f"candidates/{seed}/placed-search.kicad_pcb"), data)
            in_hash.update(data)
            positions, rec = placer_claude.refine(b, nl, c, positions, cost, seed)
            cost = rec["refined_cost"]
            out = d / f"placed-{seed}-claude.kicad_pcb"
            pcb.set_positions(src, positions, out)
            data = out.read_bytes()
            _record(ctx, seed, rec)
        else:
            ctx.details[str(seed)] = cost
        out_hash.update(data)
        key = storage.put(common.run_key(ctx, f"candidates/{seed}/placed.kicad_pcb"), data)
        with ctx.conn.cursor() as cur:
            cur.execute(
                "insert into candidates (run_id, seed, board_key, proxy_cost) "
                "values (%s, %s, %s, %s)",
                (ctx.run_id, seed, key, cost),
            )
    if ctx.placer == "claude":
        ctx.provenance("claude-agent-sdk", placer_claude.MODEL, in_hash.hexdigest())
    else:
        ctx.provenance("pipeline-placer", "0.1.0", storage.sha256(text.encode()))
    ctx.output_hash = out_hash.hexdigest()


def _record(ctx: Ctx, seed: int, rec: dict[str, Any]) -> None:
    usage = rec["usage"] or {}
    ctx.details[str(seed)] = {
        "search_cost": rec["search_cost"],
        "refined_cost": rec["refined_cost"],
        "moved": rec["moved"],
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "seconds": rec["seconds"],
    }
    if not settings.experiments_dir:
        return
    with ctx.conn.cursor() as cur:
        cur.execute("select filename from designs where id = %s", (ctx.design_id,))
        stem = Path(cur.fetchone()[0]).stem  # type: ignore[index]
    p = Path(settings.experiments_dir) / "claude-placer" / f"{stem}-run{ctx.run_id}-seed{seed}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1))
