"""Claude refinement of a search placement: one structured-output call, validated in code.

The experiment is explicit (`placer=claude` on the run). A failed call fails the stage; it
never silently falls back to the search layout."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, EffortLevel, ResultMessage, query

from pipeline import placer
from pipeline.models import NET_CLASSES, Board, Constraints, Netlist
from pipeline.placer import CLEARANCE_MM, Pose

MODEL = "claude-fable-5-1"
SYSTEM_PROMPT = (
    "You are a PCB layout engineer. You are given a board's parts, nets, constraints and a "
    "search-placed starting layout, and you return improved part positions as the requested "
    "JSON. Reason about circuit intent: decoupling next to the pins it serves, connectors and "
    "mechanical parts fixed, short high-speed nets, wide short power paths, no overlaps."
)
NET_CAP = 40  # default-class nets shown in the prompt (largest by pin count)
SCHEMA = {
    "type": "object",
    "properties": {
        "positions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "rot": {"type": "number"},
                },
                "required": ["ref", "x", "y", "rot"],
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["positions", "rationale"],
}


def _movable(board: Board, netlist: Netlist, constraints: Constraints) -> set[str]:
    """Same rule as placer.place: not fixed and has at least one netted pad."""
    netted = {n.ref for net in netlist.nets for n in net.nodes}
    return {f.ref for f in board.footprints if f.ref not in constraints.fixed and f.ref in netted}


def build_prompt(
    board: Board,
    netlist: Netlist,
    constraints: Constraints,
    search_positions: dict[str, Pose],
    search_cost: float,
) -> str:
    m = placer._Model(board, netlist, constraints)
    movable = _movable(board, netlist, constraints)
    geo = {f.ref: m.transform(f.ref, search_positions[f.ref]) for f in board.footprints}
    x1, y1, x2, y2 = board.outline
    lines = [
        "You are placing components on a 2-layer PCB. Goal: a board that routes fully, passes "
        "DRC, and scores well on physics: decoupling capacitors within "
        f"{constraints.decoupling_max_mm:g} mm of the IC pin they serve, short high-speed nets, "
        "power rails short and wide.",
        "",
        f"Board outline (mm): x {x1:g}..{x2:g}, y {y1:g}..{y2:g}. Y grows downward.",
        "",
        "Footprints (ref, value, footprint, courtyard w x h mm at the current rotation, "
        "current x/y/rot):",
    ]
    for f in board.footprints:
        x, y, rot = search_positions[f.ref]
        cx1, cy1, cx2, cy2 = geo[f.ref][1]
        lines.append(
            f"- {f.ref} {f.value or '-'} {f.name} {cx2 - cx1:.2f}x{cy2 - cy1:.2f} "
            f"at ({x:.2f}, {y:.2f}) rot {rot:g} fixed: {'no' if f.ref in movable else 'yes'}"
        )
    lines.append("")
    lines.append("Nets by class (member pins as ref.pin):")
    for cls in NET_CLASSES:
        nets = [
            n for n in netlist.nets if constraints.net_class(n.name) == cls and len(n.nodes) > 1
        ]
        if not nets:
            continue
        nets.sort(key=lambda n: -len(n.nodes))
        shown = nets[:NET_CAP] if cls == "default" else nets
        lines.append(f"[{cls}]")
        for n in shown:
            lines.append(f"- {n.name}: {' '.join(x.key for x in n.nodes)}")
        if len(shown) < len(nets):
            lines.append(f"({len(nets) - len(shown)} smaller default nets omitted)")
    lines.append("")
    lines.append("Decoupling pairs (cap pin -> nearest IC pin on the same rail, current distance):")
    for cap, pin, targets in m.decap:
        at = geo[cap][0][pin]
        r, p = min(targets, key=lambda t: math.dist(at, geo[t[0]][0][t[1]]))
        lines.append(f"- {cap}.{pin} -> {r}.{p}: {math.dist(at, geo[r][0][p]):.1f} mm")
    if not m.decap:
        lines.append("- none")
    lines += [
        "",
        f"The current layout came from simulated annealing; its proxy cost is {search_cost:.1f} "
        "(weighted half-perimeter wire length + decoupling distance + overlap penalties; "
        "lower is better).",
        "",
        "Constraints:",
        "- Fixed parts stay exactly where they are.",
        "- Rotations are 0, 90, 180 or 270 only.",
        f"- No courtyard may overlap another; keep {CLEARANCE_MM:g} mm between courtyards.",
        "- Every courtyard lies fully inside the outline.",
        "- A layout with overlaps or unrouted nets scores zero.",
        "",
        "Return a position (x, y, rot in mm and degrees) for every movable part, plus a short "
        "rationale naming what you moved and why.",
    ]
    return "\n".join(lines)


def validate(
    board: Board,
    netlist: Netlist,
    constraints: Constraints,
    search_positions: dict[str, Pose],
    proposal: dict[str, Any],
) -> dict[str, Pose]:
    """Search poses overridden by legal proposed poses for movable parts only."""
    m = placer._Model(board, netlist, constraints)
    movable = _movable(board, netlist, constraints)
    ox1, oy1, ox2, oy2 = board.outline
    out = dict(search_positions)
    for p in proposal.get("positions", []):
        ref = p.get("ref")
        if ref not in movable:
            continue
        rot = float(round(float(p["rot"]) / 90) * 90 % 360)
        x, y = float(p["x"]), float(p["y"])
        c = m.transform(ref, (x, y, rot))[1]
        dx = max(0.0, ox1 - c[0]) + min(0.0, ox2 - c[2])
        dy = max(0.0, oy1 - c[1]) + min(0.0, oy2 - c[3])
        out[ref] = (x + dx, y + dy, rot)
    return out


async def _ask(prompt: str, effort: EffortLevel) -> ResultMessage:
    options = ClaudeAgentOptions(
        model=MODEL,
        effort=effort,
        tools=[],
        max_turns=1,
        # no built-in tools: a short system prompt instead of Claude Code's default (~45k tokens)
        system_prompt=SYSTEM_PROMPT,
        output_format={"type": "json_schema", "schema": SCHEMA},
    )
    result = None
    async for message in query(prompt=prompt, options=options):  # drain: no early return
        if isinstance(message, ResultMessage):
            result = message
    if result is None:
        raise RuntimeError("claude placer: query ended without a result message")
    return result


def refine(
    board: Board,
    netlist: Netlist,
    constraints: Constraints,
    search_positions: dict[str, Pose],
    search_cost: float,
    seed: int,
    effort: EffortLevel = "high",
) -> tuple[dict[str, Pose], dict[str, Any]]:
    """(refined positions, JSON record). Raises RuntimeError when Claude gives no layout."""
    prompt = build_prompt(board, netlist, constraints, search_positions, search_cost)
    t0 = time.monotonic()
    result = asyncio.run(_ask(prompt, effort))
    seconds = time.monotonic() - t0
    if result.subtype != "success" or result.structured_output is None:
        raise RuntimeError(
            f"claude placer: {result.subtype}: {result.errors or result.result or ''}"
        )
    proposal: dict[str, Any] = result.structured_output
    positions = validate(board, netlist, constraints, search_positions, proposal)
    movable = _movable(board, netlist, constraints)
    proposed = [str(p.get("ref")) for p in proposal.get("positions", [])]
    record = {
        "model": MODEL,
        "effort": effort,
        "seed": seed,
        "prompt_chars": len(prompt),
        "usage": result.usage,
        "num_turns": result.num_turns,
        "total_cost_usd": result.total_cost_usd,
        "seconds": seconds,
        "output": proposal,
        "moved": sorted(r for r, p in positions.items() if p != search_positions[r]),
        "rejected": sorted(r for r in proposed if r not in movable),
        "search_cost": search_cost,
        "refined_cost": placer.cost(positions, board, netlist, constraints),
    }
    return positions, record
