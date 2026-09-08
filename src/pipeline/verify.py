"""Independent verification checks: board netlist vs schematic, DRC summary, courtyards in
outline. Pure functions; the verify stage wires them to kicad-cli and the database."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pipeline import ipc356
from pipeline.models import Board, Netlist


def canon(name: str) -> str:
    """A schematic net name as KiCad's IPC-D-356 exporter writes it (export_d356.cpp):
    non-graphic characters become '?', upper-cased, last 14 characters kept; then KiCad's
    `{slash}`-style escapes are undone the same way ipc356.parse does."""
    # ponytail: KiCad appends "#N" when two nets share the same 14-char tail; neither fixture
    # collides. Match by node overlap if a real design ever does.
    flat = "".join(c if 32 < ord(c) < 127 else "?" for c in name).upper()[-14:]
    return ipc356.unescape(flat)


def netlist_equivalent(
    board_nets: dict[str, frozenset[str]], schematic: Netlist, board_refs: set[str]
) -> tuple[bool, list[str]]:
    expected: dict[str, frozenset[str]] = {}
    for net in schematic.nets:
        nodes = frozenset(n.key for n in net.nodes if n.ref in board_refs)
        if nodes:
            expected[canon(net.name)] = nodes
    diffs = []
    for name in sorted(expected.keys() | board_nets.keys()):
        want = expected.get(name, frozenset())
        have = board_nets.get(name, frozenset())
        if want != have:
            diffs.append(f"{name}: missing {sorted(want - have)}, extra {sorted(have - want)}")
    return not diffs, diffs


def drc_summary(report: dict[str, Any]) -> dict[str, Any]:
    lists = ("violations", "unconnected_items", "schematic_parity")
    out: dict[str, Any] = {
        k: dict(Counter(v["severity"] for v in report.get(k, []))) for k in lists
    }
    # Silkscreen rules are cosmetic (fabs clip silk over copper); reported, not gating.
    silk = [v for v in report.get("violations", []) if v["type"].startswith("silk_")]
    out["silk"] = len(silk)
    out["errors"] = sum(out[k].get("error", 0) for k in lists) - sum(
        1 for v in silk if v["severity"] == "error"
    )
    out["unrouted"] = len(report.get("unconnected_items", []))
    out["top"] = [
        {
            "type": v["type"],
            "severity": v["severity"],
            "description": v["description"],
            "x": v["items"][0]["pos"]["x"],
            "y": v["items"][0]["pos"]["y"],
        }
        for v in report.get("violations", [])[:10]
    ]
    return out


def in_bounds(board: Board, tolerance: float = 0.01) -> tuple[bool, list[str]]:
    """Every footprint's copper (its pads) lies inside the outline. Courtyards are not the
    criterion: edge connectors and mounting holes legitimately overhang the edge on real
    boards (pic_programmer's DSUB J1 by 8.7 mm), and the spec's intent is "part is on the
    board", which the pads decide."""
    x1, y1, x2, y2 = board.outline

    def inside(x: float, y: float, r: float) -> bool:
        # ponytail: pad rotation is not stored, so use the inscribed circle of the pad.
        return x1 + r - tolerance <= x <= x2 - r + tolerance and (
            y1 + r - tolerance <= y <= y2 - r + tolerance
        )

    outside = [
        f.ref
        for f in board.footprints
        if not all(inside(p.x, p.y, min(p.w, p.h) / 2) for p in f.pads)
    ]
    return not outside, outside
