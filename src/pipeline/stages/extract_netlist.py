"""Stage 1: ERC (recorded, not a gate), then kicad-cli netlist -> netlist.json + rows."""

from __future__ import annotations

import json
from collections import Counter

from pipeline import kicad, netlist, storage
from pipeline.stages import Ctx, common, stage


@stage("extract_netlist")
def extract_netlist(ctx: Ctx) -> None:
    u = common.unpack(ctx)
    report = kicad.erc(u.sch)
    violations = [v for s in report["sheets"] for v in s["violations"]]
    severity = Counter(v["severity"] for v in violations)
    # ERC is recorded, not a gate: templates without PWR_FLAGs report "errors" that do not
    # change connectivity. DRC and verification are the gates.
    ctx.details["erc"] = {
        "errors": severity["error"],
        "warnings": severity["warning"],
        "top": [
            {"type": v["type"], "severity": v["severity"], "description": v["description"]}
            for v in violations
            if v["severity"] == "error"
        ][:10],
    }

    text = kicad.export_netlist(u.sch)
    nl = netlist.parse(text)

    # ctx.conn is not autocommit: everything below is one transaction, committed by the runner.
    with ctx.conn.cursor() as cur:
        cur.execute("delete from components where design_id = %s", (ctx.design_id,))
        cur.execute("delete from nets where design_id = %s", (ctx.design_id,))  # cascades nodes
        cur.executemany(
            "insert into components (design_id, ref, value, footprint) values (%s, %s, %s, %s)",
            [(ctx.design_id, c.ref, c.value, c.footprint) for c in nl.components],
        )
        for net in nl.nets:
            cur.execute(
                "insert into nets (design_id, code, name) values (%s, %s, %s) returning id",
                (ctx.design_id, net.code, net.name),
            )
            net_id = cur.fetchone()[0]  # type: ignore[index]
            cur.executemany(
                "insert into net_nodes (net_id, ref, pin) values (%s, %s, %s)",
                [(net_id, x.ref, x.pin) for x in net.nodes],
            )

    common.save_json(ctx, "netlist.json", nl.to_json())
    storage.put(common.run_key(ctx, "netlist.net"), text.encode())
    ctx.provenance("kicad-cli", kicad.version(), storage.sha256(u.sch.read_bytes()))
    ctx.output_hash = storage.sha256(json.dumps(nl.to_json()).encode())
    ctx.details["components"] = len(nl.components)
    ctx.details["nets"] = len(nl.nets)
