"""Helpers shared by stages: unpack the upload, load/save run objects."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline import log, storage
from pipeline.models import Constraints, Netlist
from pipeline.stages import Ctx

logger = log.get("stages")


@dataclass
class Unpacked:
    dir: Path
    sch: Path  # root schematic
    pro: Path | None
    pcb: Path | None
    constraints_json: dict[str, Any] | None


def unpack(ctx: Ctx, into: Path | None = None) -> Unpacked:
    """Download the design upload and unpack it into a temp dir (or `into`)."""
    with ctx.conn.cursor() as cur:
        cur.execute("select filename, object_key from designs where id = %s", (ctx.design_id,))
        filename, key = cur.fetchone()  # type: ignore[misc]
    logger.info("unpack_fetch", run_id=ctx.run_id, key=key)
    data = storage.get(key)
    logger.info("unpack_fetched", run_id=ctx.run_id, bytes=len(data))
    d = into or Path(tempfile.mkdtemp(prefix=f"run{ctx.run_id}-"))
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                p = (d / m.filename).resolve()
                if not str(p).startswith(str(d.resolve())):
                    raise ValueError(f"zip entry escapes directory: {m.filename}")
            z.extractall(d)
    else:
        (d / filename).write_bytes(data)
    pro = next(iter(sorted(d.rglob("*.kicad_pro"))), None)
    pcb = next(iter(sorted(d.rglob("*.kicad_pcb"))), None)
    sch = _root_schematic(d, pro)
    cj = next(iter(d.rglob("constraints.json")), None)
    return Unpacked(d, sch, pro, pcb, json.loads(cj.read_text()) if cj else None)


def _root_schematic(d: Path, pro: Path | None) -> Path:
    schs = sorted(d.rglob("*.kicad_sch"))
    if not schs:
        raise ValueError("no .kicad_sch in upload")
    if pro:
        match = pro.with_suffix(".kicad_sch")
        if match in schs:
            return match
    if len(schs) == 1:
        return schs[0]
    # root sheet is the one no other sheet references
    referenced = set()
    for s in schs:
        for line in s.read_text(errors="replace").splitlines():
            if '(property "Sheetfile"' in line:
                referenced.add(line.split('"')[3])
    roots = [s for s in schs if s.name not in referenced]
    return roots[0] if roots else schs[0]


def run_key(ctx: Ctx, name: str) -> str:
    return f"runs/{ctx.run_id}/{name}"


def save_json(ctx: Ctx, name: str, obj: Any) -> str:
    return storage.put(run_key(ctx, name), json.dumps(obj).encode(), "application/json")


def load_json(ctx: Ctx, name: str) -> Any:
    return json.loads(storage.get(run_key(ctx, name)))


def load_netlist(ctx: Ctx) -> Netlist:
    return Netlist.from_json(load_json(ctx, "netlist.json"))


def load_constraints(ctx: Ctx) -> Constraints:
    with ctx.conn.cursor() as cur:
        cur.execute(
            "select body from constraints where run_id = %s order by id desc limit 1", (ctx.run_id,)
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("constraints stage has not run")
    return Constraints.from_json(row[0])


def add_artifact(
    ctx: Ctx, name: str, data: bytes, content_type: str = "application/octet-stream"
) -> None:
    key = storage.put(run_key(ctx, f"artifacts/{name}"), data, content_type)
    with ctx.conn.cursor() as cur:
        cur.execute(
            "insert into artifacts (run_id, name, object_key, sha256, bytes) "
            "values (%s, %s, %s, %s, %s) "
            "on conflict (run_id, name) do update set object_key = excluded.object_key, "
            "sha256 = excluded.sha256, bytes = excluded.bytes",
            (ctx.run_id, name, key, storage.sha256(data), len(data)),
        )


def chosen_candidate(ctx: Ctx) -> tuple[int, bytes]:
    """(candidate id, board bytes) of the chosen candidate, or the only one."""
    with ctx.conn.cursor() as cur:
        cur.execute(
            "select id, board_key from candidates where run_id = %s "
            "order by chosen desc, id limit 1",
            (ctx.run_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("no candidate board for this run")
    return int(row[0]), storage.get(row[1])
