"""Simulated-annealing placement over a parsed Board. Pure math; no I/O."""

from __future__ import annotations

import math
import random

from pipeline.models import BBox, Board, Constraints, Netlist

Pose = tuple[float, float, float]  # x_mm, y_mm, rot_deg
XY = tuple[float, float]
_Geo = tuple[dict[str, XY], BBox]  # absolute pad centers by pad number, absolute courtyard

GRID = 0.5
DECAP_FREE_MM = 2.0
WEIGHTS = {
    "default": 1.0,
    "power": 0.5,
    "high_speed": 2.0,
    "diff": 2.0,
    "decoupling": 3.0,
    "overlap": 50.0,
    "oob": 50.0,
}
CLEARANCE_MM = 0.5  # courtyard-to-courtyard gap so silkscreen and solder mask never touch


def _grow(b: BBox, d: float) -> BBox:
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def _rot(dx: float, dy: float, deg: float) -> XY:
    """KiCad convention: positive rotation is counter-clockwise on a y-down canvas."""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return dx * c + dy * s, -dx * s + dy * c


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def _area(b: BBox | None) -> float:
    return 0.0 if b is None else (b[2] - b[0]) * (b[3] - b[1])


def _clip(a: BBox, b: BBox) -> BBox | None:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return (x1, y1, x2, y2) if x1 < x2 and y1 < y2 else None


class _Model:
    """Footprint-local shapes plus the net/decoupling structure the cost needs."""

    def __init__(self, board: Board, netlist: Netlist, c: Constraints) -> None:
        self.board = board
        self.fixed = c.fixed
        self.local_pads: dict[str, dict[str, XY]] = {}
        self.local_court: dict[str, tuple[XY, ...]] = {}
        for f in board.footprints:
            self.local_pads[f.ref] = {p.number: _rot(p.x - f.x, p.y - f.y, -f.rot) for p in f.pads}
            x1, y1, x2, y2 = f.courtyard
            self.local_court[f.ref] = tuple(
                _rot(cx - f.x, cy - f.y, -f.rot) for cx in (x1, x2) for cy in (y1, y2)
            )
        # nets: (weight, [(ref, pin)]) restricted to pads present on the board
        self.nets: list[tuple[float, list[tuple[str, str]]]] = []
        pins_of: dict[str, list[tuple[str, str]]] = {}
        net_of: dict[tuple[str, str], str] = {}
        for n in netlist.nets:
            pins = [(x.ref, x.pin) for x in n.nodes if x.pin in self.local_pads.get(x.ref, {})]
            pins_of[n.name] = pins
            net_of.update({p: n.name for p in pins})
            if len(pins) > 1:
                self.nets.append((WEIGHTS[c.net_class(n.name)], pins))
        # decoupling: (cap ref, cap power pin, [(U ref, U pin) on the same power net])
        self.decap: list[tuple[str, str, list[tuple[str, str]]]] = []
        for ref, pads in self.local_pads.items():
            if not ref.startswith("C"):
                continue
            power = [
                (pin, net_of[(ref, pin)])
                for pin in pads
                if (ref, pin) in net_of and c.net_class(net_of[(ref, pin)]) == "power"
            ]
            if not any(net.startswith("GND") for _, net in power):
                continue
            for pin, net in power:
                targets = [(r, p) for r, p in pins_of[net] if r.startswith("U")]
                if not net.startswith("GND") and targets:
                    self.decap.append((ref, pin, targets))

    def initial(self) -> dict[str, Pose]:
        out: dict[str, Pose] = {}
        for f in self.board.footprints:
            if f.ref in self.fixed:
                out[f.ref] = self.fixed[f.ref]
            else:
                out[f.ref] = (_snap(f.x), _snap(f.y), float(f.rot % 360))
        return out

    def transform(self, ref: str, pose: Pose) -> _Geo:
        x, y, rot = pose
        pads = {}
        for k, (lx, ly) in self.local_pads[ref].items():
            rx, ry = _rot(lx, ly, rot)
            pads[k] = (x + rx, y + ry)
        corners = [_rot(lx, ly, rot) for lx, ly in self.local_court[ref]]
        xs, ys = [x + cx for cx, _ in corners], [y + cy for _, cy in corners]
        return pads, (min(xs), min(ys), max(xs), max(ys))

    def cost(self, geo: dict[str, _Geo]) -> float:
        total = 0.0
        for w, pins in self.nets:
            xs = [geo[r][0][p][0] for r, p in pins]
            ys = [geo[r][0][p][1] for r, p in pins]
            total += w * (max(xs) - min(xs) + max(ys) - min(ys))
        for cap, pin, targets in self.decap:
            at = geo[cap][0][pin]
            d = min(math.dist(at, geo[r][0][p]) for r, p in targets)
            total += WEIGHTS["decoupling"] * max(0.0, d - DECAP_FREE_MM)
        refs = list(geo)
        outline = self.board.outline
        # ponytail: O(n^2) courtyard pairs per evaluation; sweep-line if boards pass ~200 parts
        for i, a in enumerate(refs):
            ca = geo[a][1]
            total += WEIGHTS["oob"] * (_area(ca) - _area(_clip(ca, outline)))
            for b in refs[i + 1 :]:
                # ponytail: sides ignored, so through-hole parts (both sides) are always
                # respected; two-sided SMD placement would need per-side courtyards
                total += WEIGHTS["overlap"] * _area(
                    _clip(_grow(ca, CLEARANCE_MM / 2), _grow(geo[b][1], CLEARANCE_MM / 2))
                )
        return total


def cost(
    positions: dict[str, Pose], board: Board, netlist: Netlist, constraints: Constraints
) -> float:
    """Proxy cost of a layout; footprints missing from `positions` keep their board pose."""
    m = _Model(board, netlist, constraints)
    geo = {
        f.ref: m.transform(f.ref, positions.get(f.ref, (f.x, f.y, f.rot))) for f in board.footprints
    }
    return m.cost(geo)


def place(
    board: Board,
    netlist: Netlist,
    constraints: Constraints,
    seed: int,
    iterations: int = 20000,
) -> tuple[dict[str, Pose], float]:
    """Anneal from the board's current (grid) layout. Returns (ref -> pose, proxy cost)."""
    m = _Model(board, netlist, constraints)
    rng = random.Random(seed)
    pose = m.initial()
    netted = {n.ref for net in netlist.nets for n in net.nodes}
    # parts with no netted pads (mounting holes) are mechanical: never moved
    movable = [r for r in pose if r not in constraints.fixed and r in netted]
    geo = {r: m.transform(r, p) for r, p in pose.items()}
    cur = m.cost(geo)
    if not movable:
        return pose, cur
    ox1, oy1, ox2, oy2 = board.outline
    span = max(ox2 - ox1, oy2 - oy1)

    def propose(scale: float) -> dict[str, Pose]:
        ref = rng.choice(movable)
        x, y, rot = pose[ref]
        u = rng.random()
        if u < 0.7:
            s = max(GRID, 0.1 * span * scale)  # 10% of the board at T0, one grid step at the end
            dx, dy = _snap(rng.uniform(-s, s)), _snap(rng.uniform(-s, s))
            if dx == 0 and dy == 0:
                dx, dy = rng.choice(((GRID, 0.0), (-GRID, 0.0), (0.0, GRID), (0.0, -GRID)))
            return {ref: (x + dx, y + dy, rot)}
        if u < 0.9 or len(movable) < 2:
            return {ref: (x, y, (rot + 90) % 360)}
        other = rng.choice([r for r in movable if r != ref])
        ox, oy, orot = pose[other]
        return {ref: (ox, oy, rot), other: (x, y, orot)}

    def trial(new: dict[str, Pose]) -> float:
        g = dict(geo)
        for r, p in new.items():
            g[r] = m.transform(r, p)
        return m.cost(g)

    # initial temperature: ~50% acceptance of the uphill moves seen from the start state
    ups = [d for d in (trial(propose(1.0)) - cur for _ in range(50)) if d > 0]
    t0 = (sum(ups) / len(ups)) / math.log(2) if ups else 1.0

    best, best_cost = dict(pose), cur
    for i in range(iterations):
        t = t0 * 0.01 ** (i / iterations)
        new = propose(t / t0)
        old_pose = {r: pose[r] for r in new}
        old_geo = {r: geo[r] for r in new}
        for r, p in new.items():
            pose[r], geo[r] = p, m.transform(r, p)
        c = m.cost(geo)
        if c <= cur or rng.random() < math.exp((cur - c) / t):
            cur = c
            if c < best_cost:
                best, best_cost = dict(pose), c
        else:
            pose.update(old_pose)
            geo.update(old_geo)
    return best, best_cost
