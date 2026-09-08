"""Stage 8: fabrication outputs for the chosen candidate."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

from botocore.exceptions import ClientError

from pipeline import kicad, storage
from pipeline.stages import Ctx, common, stage


def _zip(files: list[Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    return buf.getvalue()


@stage("export")
def export(ctx: Ctx) -> None:
    _, data = common.chosen_candidate(ctx)
    ctx.provenance("kicad-cli", kicad.version(), storage.sha256(data))
    d = Path(tempfile.mkdtemp(prefix=f"export{ctx.run_id}-"))
    pcb = d / "board.kicad_pcb"
    pcb.write_bytes(data)

    gerbers = _zip(kicad.export_gerbers(pcb, d / "gerbers"))
    common.add_artifact(ctx, "gerbers.zip", gerbers, "application/zip")
    common.add_artifact(
        ctx, "drill.zip", _zip(kicad.export_drill(pcb, d / "drill")), "application/zip"
    )
    common.add_artifact(
        ctx, "positions.csv", kicad.export_pos(pcb, d / "positions.csv").read_bytes(), "text/csv"
    )
    common.add_artifact(
        ctx, "stats.json", json.dumps(kicad.export_stats(pcb)).encode(), "application/json"
    )
    common.add_artifact(ctx, "board.kicad_pcb", data, "text/plain")
    try:
        report = storage.get(common.run_key(ctx, "report.json"))
    except ClientError:
        pass  # judge did not write a report (e.g. external judge)
    else:
        common.add_artifact(ctx, "report.json", report, "application/json")
    try:
        png = kicad.render(pcb, d / "board.png").read_bytes()
    except Exception as e:  # rendering needs a GL stack; its absence must not fail export
        ctx.details["render_error"] = str(e)[-500:]
    else:
        common.add_artifact(ctx, "board.png", png, "image/png")
    ctx.output_hash = storage.sha256(gerbers)
