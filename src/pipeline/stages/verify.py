"""Stage 7: DRC with schematic parity, IPC-D-356 vs schematic netlist, courtyards in outline."""

from __future__ import annotations

import json

from pipeline import board, ipc356, kicad, storage
from pipeline.stages import Ctx, common, stage
from pipeline.verify import drc_summary, in_bounds, netlist_equivalent


@stage("verify")
def verify(ctx: Ctx) -> None:
    candidate_id, data = common.chosen_candidate(ctx)
    ctx.provenance("kicad-cli", kicad.version(), storage.sha256(data))
    # The project and schematics must sit next to the board for --schematic-parity.
    up = common.unpack(ctx)
    pcb = up.pro.with_suffix(".kicad_pcb") if up.pro else up.dir / "board.kicad_pcb"
    pcb.write_bytes(data)

    drc = drc_summary(kicad.drc(pcb, parity=True))
    parsed = board.parse(data.decode())
    match, diffs = netlist_equivalent(
        ipc356.parse(kicad.export_ipcd356(pcb)),
        common.load_netlist(ctx),
        {f.ref for f in parsed.footprints},
    )
    bounded, outside = in_bounds(parsed)
    passed = drc["errors"] == 0 and match and drc["unrouted"] == 0 and bounded

    with ctx.conn.cursor() as cur:
        cur.execute(
            "insert into verifications "
            "(run_id, candidate_id, passed, drc, netlist_match, unrouted, in_bounds) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (ctx.run_id, candidate_id, passed, json.dumps(drc), match, drc["unrouted"], bounded),
        )
    ctx.details = {
        "passed": passed,
        "errors": drc["errors"],
        "unrouted": drc["unrouted"],
        "netlist_match": match,
        "netlist_diffs": diffs[:5],
        "in_bounds": bounded,
        "out_of_bounds": outside,
    }
    ctx.output_hash = storage.sha256(json.dumps(ctx.details, sort_keys=True).encode())
    if not passed:
        ctx.run_status = "failed_verification"
