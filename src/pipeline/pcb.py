"""pcbnew wrapper: unplaced board from netlist (+ optional template), moves, Freerouting."""

from __future__ import annotations

import math
import re
import shlex
import subprocess
import time
from functools import cache
from pathlib import Path
from typing import Any

import pcbnew

from pipeline.config import settings
from pipeline.models import Constraints, Netlist

LIB_DIR = Path("/usr/share/kicad/footprints")


def _items(container: Any) -> list[Any]:
    # ponytail: SWIG iterators (GetTracks/GetDrawings) are broken under Python 3.14; index.
    return [container[i] for i in range(len(container))]


def _mm(v: float) -> int:
    return int(pcbnew.FromMM(v))


COPPER_BLOCKS = ("segment", "arc", "via", "zone")


def strip_copper(text: str) -> str:
    """Drop top-level (segment|arc|via|zone ...) blocks from .kicad_pcb text, byte-exact
    otherwise. Walks parens, skipping quoted strings."""
    out: list[str] = []
    i, n, depth = 0, len(text), 0
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if c == "(" and depth == 1:
            head = text[i + 1 : i + 12].split()[0] if text[i + 1 : i + 12].split() else ""
            if head in COPPER_BLOCKS:
                d, j = 0, i
                while True:  # find the matching close paren
                    ch = text[j]
                    if ch == '"':
                        j += 1
                        while text[j] != '"':
                            j += 2 if text[j] == "\\" else 1
                    elif ch == "(":
                        d += 1
                    elif ch == ")":
                        d -= 1
                        if d == 0:
                            break
                    j += 1
                i = j + 1
                while i < n and text[i] in " \t\n":
                    i += 1
                continue
        depth += (c == "(") - (c == ")")
        out.append(c)
        i += 1
    return "".join(out)


def load_footprint(fpid: str | None, ref: str) -> Any:
    lib, _, name = (fpid or "").partition(":")
    if not name:
        raise ValueError(f"{ref}: no footprint assigned in the schematic or constraints.json")
    lib_dir = LIB_DIR / f"{lib}.pretty"
    if not lib_dir.is_dir():
        raise ValueError(f"{ref}: footprint library {lib!r} not found in {LIB_DIR}")
    fp = pcbnew.FootprintLoad(str(lib_dir), name)
    if fp is None:
        raise ValueError(f"{ref}: footprint {fpid!r} not found in {lib_dir}")
    fp.SetReference(ref)
    return fp


def footprint_area_mm2(fp: Any) -> float:
    fp.BuildCourtyardCaches()
    bb = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
    if bb.GetWidth() == 0:  # no courtyard drawn (old footprints)
        bb = fp.GetBoundingBox(False)
    return float(pcbnew.ToMM(bb.GetWidth()) * pcbnew.ToMM(bb.GetHeight()))


def build_unplaced(
    netlist: Netlist, constraints: Constraints, template: Path | None, out: Path
) -> dict[str, Any]:
    if template:
        # Strip copper in the text, not through pcbnew: removing hundreds of tracks via
        # board.Remove() segfaults the SWIG bindings.
        stripped = out.with_name("template_stripped.kicad_pcb")
        stripped.write_text(strip_copper(template.read_text()))
        board = pcbnew.LoadBoard(str(stripped))
    else:
        board = pcbnew.BOARD()
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    for c in netlist.components:
        if c.ref not in fps:
            fp = load_footprint(constraints.footprints.get(c.ref) or c.footprint, c.ref)
            fp.SetValue(c.value or "")
            board.Add(fp)
            fps[c.ref] = fp

    if template:
        bb = board.GetBoardEdgesBoundingBox()
        x0, y0 = pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY())
        w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
    else:
        x0 = y0 = 0.0
        if constraints.outline_mm:
            w, h = constraints.outline_mm
        else:
            w = h = math.ceil(math.sqrt(3 * sum(footprint_area_mm2(f) for f in fps.values())))
        rect = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
        rect.SetStart(pcbnew.VECTOR2I(0, 0))
        rect.SetEnd(pcbnew.VECTOR2I(_mm(w), _mm(h)))
        rect.SetLayer(pcbnew.Edge_Cuts)
        rect.SetWidth(_mm(0.1))
        board.Add(rect)

    # Seed the search: every movable part on a grid inside the outline. Parts the template
    # already placed that carry no nets (mounting holes) and constraints.fixed stay put.
    netted = {n.ref for net in netlist.nets for n in net.nodes}
    movable = [
        fp
        for ref, fp in fps.items()
        if ref not in constraints.fixed and (ref in netted or not template)
    ]
    cols = math.ceil(math.sqrt(len(movable))) or 1
    rows = math.ceil(len(movable) / cols) or 1
    for i, fp in enumerate(movable):
        x = x0 + (i % cols + 0.5) * w / cols
        y = y0 + (i // cols + 0.5) * h / rows
        fp.SetPosition(pcbnew.VECTOR2I(_mm(x), _mm(y)))

    for ref, (x, y, rot) in constraints.fixed.items():
        fps[ref].SetPosition(pcbnew.VECTOR2I(_mm(x), _mm(y)))
        fps[ref].SetOrientationDegrees(rot)

    nets = {}
    for net in netlist.nets:
        item = board.FindNet(net.name)
        if item is None:
            item = pcbnew.NETINFO_ITEM(board, net.name)
            board.Add(item)
        for node in net.nodes:
            nets[node.key] = item
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            item = nets.get(f"{fp.GetReference()}.{pad.GetNumber()}")
            if item is None:
                pad.SetNetCode(0)
            else:
                pad.SetNet(item)
    # ponytail: constraints.classes not written; pcbnew's NET_SETTINGS API is not a few lines.
    pcbnew.SaveBoard(str(out), board)
    return {
        "footprints": len(board.GetFootprints()),
        "nets": len(netlist.nets),
        "outline_mm": [w, h],
        "source": "template" if template else "library",
    }


def set_positions(
    path_in: Path, positions: dict[str, tuple[float, float, float]], path_out: Path
) -> None:
    board = pcbnew.LoadBoard(str(path_in))
    for fp in board.GetFootprints():
        pos = positions.get(fp.GetReference())
        if pos is not None:
            fp.SetPosition(pcbnew.VECTOR2I(_mm(pos[0]), _mm(pos[1])))
            fp.SetOrientationDegrees(pos[2])
    pcbnew.SaveBoard(str(path_out), board)


def unrouted_count(path: Path) -> int:
    board = pcbnew.LoadBoard(str(path))
    board.BuildConnectivity()
    return int(board.GetConnectivity().GetUnconnectedCount(True))


@cache
def freerouting_version() -> str:
    p = subprocess.run(
        [*shlex.split(settings.freerouting_bin), "-help"], capture_output=True, text=True
    )
    m = re.search(r"Freerouting v?(\S+)", p.stdout + p.stderr)
    return m.group(1) if m else (p.stdout + p.stderr).strip().splitlines()[0]


def route(path_in: Path, path_out: Path) -> dict[str, Any]:
    dsn, ses = path_in.with_suffix(".dsn"), path_in.with_suffix(".ses")
    board = pcbnew.LoadBoard(str(path_in))
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        raise RuntimeError(f"DSN export failed for {path_in}")
    argv = [
        *shlex.split(settings.freerouting_bin),
        "-de",
        str(dsn),
        "-do",
        str(ses),
        "-mp",
        str(settings.freerouting_passes),
    ]
    started = time.monotonic()
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=settings.freerouting_timeout_seconds
        )
        output = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        out = [x.decode() if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr)]
        output = f"{out[0]}{out[1]}\n(timed out after {e.timeout}s)"
    seconds = time.monotonic() - started
    if not ses.exists():
        raise RuntimeError(f"Freerouting wrote no SES: {output[-2000:]}")
    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        raise RuntimeError(f"SES import failed for {ses}")
    # Freerouting necks down below the board minimum in tight spots; the fab rule wins.
    min_w = board.GetDesignSettings().m_TrackMinWidth
    for t in _items(board.Tracks()):
        if t.GetClass() == "PCB_TRACK" and t.GetWidth() < min_w:
            t.SetWidth(min_w)
    pcbnew.SaveBoard(str(path_out), board)
    return {
        "version": freerouting_version(),
        "seconds": round(seconds, 1),
        "unrouted": unrouted_count(path_out),
        "passes": settings.freerouting_passes,
    }
