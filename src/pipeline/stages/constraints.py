import json

from pipeline import storage
from pipeline.constraints import derive
from pipeline.models import NET_CLASSES
from pipeline.stages import Ctx, common, stage


@stage("constraints")
def constraints(ctx: Ctx) -> None:
    u = common.unpack(ctx)
    nl = common.load_netlist(ctx)
    project = json.loads(u.pro.read_text()) if u.pro else None
    c, sources = derive(nl, project, u.constraints_json)
    body = json.dumps(c.to_json())
    with ctx.conn.cursor() as cur:
        cur.execute(
            "insert into constraints (run_id, source, body) values (%s, %s, %s)",
            (ctx.run_id, json.dumps(sources), body),
        )
    ctx.provenance("pipeline", "0.1.0", None)
    ctx.output_hash = storage.sha256(body.encode())
    ctx.details = {
        "classes": {k: sum(1 for n in nl.nets if c.net_class(n.name) == k) for k in NET_CLASSES},
        "diff_pairs": len(c.diff_pairs),
        "fixed": len(c.fixed),
        "user_constraints": u.constraints_json is not None,
    }
