"""Windows: one net plus everything within a radius, with the stackup. The unit of the data
factory (docs/FACTORY.md): a solver labels it, a geometry model consumes it, edits make
children of it, datasets are lists of it.

`extract` cuts the window out of a board and re-origins it at the box corner so the same local
geometry anywhere on any board hashes the same. `cuts` takes perpendicular cross-sections along
the target net: the 2D input a cross-section solver understands. `label` solves the target
conductor alone with the existing microstrip solver; neighbours are recorded, not yet solved.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any

from pipeline import fields
from pipeline.models import BBox, Board, Pad, Point, Segment, Stackup, Via, Zone

WINDOW_VERSION = "window-0.1"
RADIUS_MM = 5.0
CUT_STEP_MM = 1.0
MIN_CUTS = 3
MICRON = 3  # decimals: coordinates rounded to 1 um before hashing


@dataclass(frozen=True)
class Window:
    net: str
    radius_mm: float
    origin: Point  # absolute board position of the box corner; provenance, not geometry
    copper_layers: tuple[str, ...]
    stackup: Stackup
    segments: tuple[Segment, ...]  # target and neighbours, clipped to the box, local coords
    vias: tuple[Via, ...]
    pads: tuple[Pad, ...]
    zones: tuple[Zone, ...]

    @property
    def box(self) -> BBox:
        return (0.0, 0.0, 2 * self.radius_mm, 2 * self.radius_mm)

    def canonical(self) -> dict[str, Any]:
        d = asdict(self)
        del d["origin"]
        return d

    @property
    def geometry_hash(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "version": WINDOW_VERSION, "geometry_hash": self.geometry_hash}

    @staticmethod
    def from_json(d: dict[str, Any]) -> Window:
        return Window(
            net=d["net"],
            radius_mm=d["radius_mm"],
            origin=tuple(d["origin"]),
            copper_layers=tuple(d["copper_layers"]),
            stackup=Stackup(**d["stackup"]),
            segments=tuple(Segment(**s) for s in d["segments"]),
            vias=tuple(Via(**v) for v in d["vias"]),
            pads=tuple(Pad(**{**p, "layers": tuple(p["layers"])}) for p in d["pads"]),
            zones=tuple(
                Zone(z["net"], z["layer"], tuple(tuple(p) for p in z["polygon"]))
                for z in d["zones"]
            ),
        )


@dataclass(frozen=True)
class Conductor:
    offset: float  # mm along the cut line; 0 is the target conductor
    width: float  # mm crossed by the cut line
    net: str | None


@dataclass(frozen=True)
class Cut:
    x: float  # local window coords of the cut point
    y: float
    layer: str
    conductors: tuple[Conductor, ...]  # sorted by offset
    plane_below: bool  # a copper pour on the next layer down contains the point
    plane_above: bool
    h: float  # dielectric height to the adjacent layer, mm (stackup; one value for all layers)
    t: float  # copper thickness, mm
    er: float

    @property
    def target(self) -> Conductor:
        return next(c for c in self.conductors if c.offset == 0.0)


def _r(v: float) -> float:
    return round(v, MICRON) + 0.0  # + 0.0 turns -0.0 into 0.0


def _clip(p: Point, q: Point, box: BBox) -> tuple[Point, Point] | None:
    """Liang-Barsky: the part of segment p-q inside the box, or None."""
    x1, y1, x2, y2 = box
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for num, den in ((p[0] - x1, -dx), (x2 - p[0], dx), (p[1] - y1, -dy), (y2 - p[1], dy)):
        if den == 0:
            if num < 0:
                return None
            continue
        t = num / den
        if den < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (p[0] + t0 * dx, p[1] + t0 * dy), (p[0] + t1 * dx, p[1] + t1 * dy)


def _clip_polygon(poly: tuple[Point, ...], box: BBox) -> tuple[Point, ...]:
    """Sutherland-Hodgman against the four box edges."""
    x1, y1, x2, y2 = box
    out = list(poly)
    for axis, bound, keep_greater in ((0, x1, True), (0, x2, False), (1, y1, True), (1, y2, False)):
        if not out:
            break
        out = _clip_edge(out, axis, bound, keep_greater)
    return tuple(out)


def _clip_edge(inp: list[Point], axis: int, bound: float, keep_greater: bool) -> list[Point]:
    def inside(p: Point) -> bool:
        return p[axis] >= bound if keep_greater else p[axis] <= bound

    def cross(a: Point, b: Point) -> Point:
        t = (bound - a[axis]) / (b[axis] - a[axis])
        return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])

    out: list[Point] = []
    for i, b in enumerate(inp):
        a = inp[i - 1]
        if inside(b):
            if not inside(a):
                out.append(cross(a, b))
            out.append(b)
        elif inside(a):
            out.append(cross(a, b))
    return out


def _contains(poly: tuple[Point, ...], p: Point) -> bool:
    """Ray casting; edges count as inside."""
    x, y = p
    hit = False
    for i, (bx, by) in enumerate(poly):
        ax, ay = poly[i - 1]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def extract(board: Board, net: str, radius_mm: float = RADIUS_MM) -> Window:
    target = [s for s in board.segments if s.net == net]
    if not target:
        raise ValueError(f"net {net!r} has no segments")
    # Centre on the net's longest run: a centroid can fall between two far-apart clusters of a
    # long net and leave no target copper in the box.
    longest = max(target, key=lambda s: s.length)
    cx, cy = (longest.x1 + longest.x2) / 2, (longest.y1 + longest.y2) / 2
    ox, oy = cx - radius_mm, cy - radius_mm
    box: BBox = (ox, oy, cx + radius_mm, cy + radius_mm)

    def local(p: Point) -> Point:
        return _r(p[0] - ox), _r(p[1] - oy)

    segments = []
    for s in board.segments:
        clipped = _clip((s.x1, s.y1), (s.x2, s.y2), box)
        if clipped is not None:
            (x1, y1), (x2, y2) = local(clipped[0]), local(clipped[1])
            segments.append(replace(s, x1=x1, y1=y1, x2=x2, y2=y2, width=_r(s.width)))
    vias = [
        replace(
            v, x=local((v.x, v.y))[0], y=local((v.x, v.y))[1], size=_r(v.size), drill=_r(v.drill)
        )
        for v in board.vias
        if box[0] <= v.x <= box[2] and box[1] <= v.y <= box[3]
    ]
    pads = [
        replace(p, x=local((p.x, p.y))[0], y=local((p.x, p.y))[1], w=_r(p.w), h=_r(p.h))
        for f in board.footprints
        for p in f.pads
        if p.x + p.w / 2 >= box[0] and p.x - p.w / 2 <= box[2]
        and p.y + p.h / 2 >= box[1] and p.y - p.h / 2 <= box[3]
        and any(ly.endswith(".Cu") for ly in p.layers)
    ]  # fmt: skip
    zones = [
        Zone(z.net, z.layer, tuple(local(p) for p in poly))
        for z in board.zones
        if (poly := _clip_polygon(z.polygon, box))
    ]
    return Window(
        net=net,
        radius_mm=radius_mm,
        origin=(_r(ox), _r(oy)),
        copper_layers=board.copper_layers,
        stackup=board.stackup,
        segments=tuple(segments),
        vias=tuple(vias),
        pads=tuple(pads),
        zones=tuple(zones),
    )


def _cut_points(
    target: list[Segment], step_mm: float, min_cuts: int
) -> list[tuple[Segment, float]]:
    """(segment, distance along it) for every cut: one per step_mm of each segment, then extra
    cuts on the longest segments until there are at least min_cuts."""
    counts = [max(1, round(s.length / step_mm)) for s in target]
    order = sorted(range(len(target)), key=lambda i: -target[i].length)
    i = 0
    while sum(counts) < min_cuts:
        counts[order[i % len(order)]] += 1
        i += 1
    return [
        (s, s.length * (j + 0.5) / n) for s, n in zip(target, counts, strict=True) for j in range(n)
    ]


def _crossing(seg: Segment, p: Point, n: Point) -> Conductor | None:
    """Where the line through p along unit normal n crosses `seg`: (offset, width crossed)."""
    dx, dy = seg.x2 - seg.x1, seg.y2 - seg.y1
    length = seg.length
    if length == 0:
        return None
    ux, uy = dx / length, dy / length
    det = n[0] * uy - n[1] * ux  # sin of the angle between the cut line and the segment
    if abs(det) < 1e-9:
        return None  # parallel: this segment is a neighbour along, not across, the cut
    rx, ry = seg.x1 - p[0], seg.y1 - p[1]
    s = (rx * uy - ry * ux) / det  # along the cut line
    u = (rx * n[1] - ry * n[0]) / det  # along the segment
    slack = seg.width / 2 * abs(det)
    if u < -slack or u > length + slack:
        return None
    return Conductor(_r(s), _r(min(seg.width / abs(det), length)), seg.net)


def _pad_crossing(pad: Pad, p: Point, n: Point, reach: float) -> Conductor | None:
    rect: BBox = (pad.x - pad.w / 2, pad.y - pad.h / 2, pad.x + pad.w / 2, pad.y + pad.h / 2)
    a, b = (p[0] - reach * n[0], p[1] - reach * n[1]), (p[0] + reach * n[0], p[1] + reach * n[1])
    clipped = _clip(a, b, rect)
    if clipped is None:
        return None
    (ax, ay), (bx, by) = clipped
    mid = ((ax + bx) / 2 - p[0]) * n[0] + ((ay + by) / 2 - p[1]) * n[1]
    return Conductor(_r(mid), _r(math.hypot(bx - ax, by - ay)), pad.net)


def cuts(window: Window, step_mm: float = CUT_STEP_MM, min_cuts: int = MIN_CUTS) -> list[Cut]:
    target = [s for s in window.segments if s.net == window.net and s.length > 0]
    layers = list(window.copper_layers)
    st = window.stackup
    out = []
    for seg, d in _cut_points(target, step_mm, min_cuts):
        ux, uy = (seg.x2 - seg.x1) / seg.length, (seg.y2 - seg.y1) / seg.length
        p: Point = (seg.x1 + ux * d, seg.y1 + uy * d)
        n: Point = (-uy, ux)
        found = [Conductor(0.0, _r(seg.width), seg.net)]
        for s in window.segments:
            if s is seg or s.layer != seg.layer:
                continue
            c = _crossing(s, p, n)
            if c is not None and c.offset != 0.0:
                found.append(c)
        for pad in window.pads:
            if seg.layer in pad.layers or "*.Cu" in pad.layers:
                c = _pad_crossing(pad, p, n, 2 * window.radius_mm)
                if c is not None and c.offset != 0.0:
                    found.append(c)
        i = layers.index(seg.layer)
        out.append(
            Cut(
                x=_r(p[0]),
                y=_r(p[1]),
                layer=seg.layer,
                conductors=_merge(found),
                plane_below=_plane(window, i + 1, p),
                plane_above=_plane(window, i - 1, p),
                h=st.dielectric_mm,
                t=st.copper_um / 1000,
                er=st.er,
            )
        )
    return out


def _merge(found: list[Conductor]) -> tuple[Conductor, ...]:
    """Same-net conductors whose spans overlap (a segment ending in its pad) become one; the
    target keeps offset 0."""
    out: list[Conductor] = []
    for c in sorted(found, key=lambda c: c.offset - c.width / 2):
        last = out[-1] if out else None
        if last and last.net == c.net and c.offset - c.width / 2 <= last.offset + last.width / 2:
            lo = min(last.offset - last.width / 2, c.offset - c.width / 2)
            hi = max(last.offset + last.width / 2, c.offset + c.width / 2)
            keep = 0.0 if 0.0 in (last.offset, c.offset) else _r((lo + hi) / 2)
            out[-1] = Conductor(keep, _r(hi - lo), c.net)
        else:
            out.append(c)
    return tuple(sorted(out, key=lambda c: c.offset))


def _plane(window: Window, layer_index: int, p: Point) -> bool:
    if not 0 <= layer_index < len(window.copper_layers):
        return False
    layer = window.copper_layers[layer_index]
    return any(z.layer == layer and _contains(z.polygon, p) for z in window.zones)


def label(cut: Cut) -> fields.LineParams | None:
    """Target conductor alone over its reference plane. None without a plane: no reference,
    no characteristic impedance. Neighbours wait for the general solver (FACTORY.md step 2)."""
    if not (cut.plane_below or cut.plane_above):
        return None
    return fields.solve(fields.Geometry(w=cut.target.width, h=cut.h, t=cut.t, er=cut.er))


def aggregate(labels: list[fields.LineParams | None]) -> dict[str, float | int | None]:
    z = [p.z0 for p in labels if p is not None]
    return {
        "n_cuts": len(labels),
        "n_with_plane": len(z),
        "z0_mean": sum(z) / len(z) if z else None,
        "z0_min": min(z) if z else None,
        "z0_max": max(z) if z else None,
    }
