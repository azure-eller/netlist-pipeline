"""Parser for .kicad_pcb text (KiCad 8/9/10) into models.Board. Geometry only; nets are
read as KiCad wrote them, never derived."""

from __future__ import annotations

import math
from collections.abc import Callable

from pipeline import sexpr
from pipeline.models import BBox, Board, Footprint, Pad, Segment, Stackup, Via
from pipeline.sexpr import SExpr, child, children, value

Point = tuple[float, float]


def parse(text: str) -> Board:
    root = sexpr.parse(text)
    # KiCad 8/9 declare (net N "name") at top level and reference nets by number;
    # KiCad 10 references nets by name only.
    names = {n[1]: n[2] for n in children(root, "net") if len(n) > 2 and isinstance(n[2], str)}

    def net(node: SExpr) -> str | None:
        c = child(node, "net")
        if c is None:
            return None
        name = c[2] if len(c) > 2 else names.get(str(c[1]), c[1])
        return str(name) or None

    layers = child(root, "layers") or []
    copper = tuple(
        str(entry[1])
        for entry in layers
        if isinstance(entry, list) and str(entry[1]).endswith(".Cu")
    )
    edge = [
        p
        for g in root
        if isinstance(g, list) and str(g[0]).startswith("gr_") and value(g, "layer") == "Edge.Cuts"
        for p in _points(g)
    ]
    return Board(
        outline=_bbox(edge),
        copper_layers=copper,
        stackup=_stackup(child(child(root, "setup") or [], "stackup")),
        footprints=tuple(_footprint(f, net) for f in children(root, "footprint")),
        segments=tuple(
            Segment(
                net(s),
                value(s, "layer") or "",
                *_xy(s, "start"),
                *_xy(s, "end"),
                _num(s, "width"),
            )
            for tag in ("segment", "arc")
            for s in children(root, tag)
        ),
        vias=tuple(
            Via(net(v), *_xy(v, "at"), _num(v, "size"), _num(v, "drill"))
            for v in children(root, "via")
        ),
        zone_nets=tuple(sorted({n for z in children(root, "zone") if (n := net(z))})),
    )


def _stackup(node: list[SExpr] | None) -> Stackup:
    if node is None:
        return Stackup()
    layers = children(node, "layer")
    copper = [i for i, ly in enumerate(layers) if value(ly, "type") == "copper"]
    if not copper:
        return Stackup()
    below = layers[copper[0] + 1]
    return Stackup(
        layers=len(copper),
        board_thickness_mm=sum(_num(ly, "thickness", 0.0) for ly in layers),
        copper_um=_num(layers[copper[0]], "thickness") * 1000,
        dielectric_mm=_num(below, "thickness"),
        er=_num(below, "epsilon_r"),
    )


def _footprint(f: list[SExpr], net: Callable[[SExpr], str | None]) -> Footprint:
    at = child(f, "at") or []
    x, y = float(str(at[1])), float(str(at[2]))
    rot = float(str(at[3])) if len(at) > 3 else 0.0
    props = {p[1]: p[2] for p in children(f, "property") if len(p) > 2 and isinstance(p[2], str)}

    def absolute(p: Point) -> Point:
        # Same rule for F.Cu and B.Cu: KiCad stores back-side offsets already mirrored.
        # y is down; positive rotation is counter-clockwise on screen.
        r = math.radians(rot)
        dx, dy = p
        return x + dx * math.cos(r) + dy * math.sin(r), y - dx * math.sin(r) + dy * math.cos(r)

    pads = []
    for pad in children(f, "pad"):
        px, py = absolute(_xy(pad, "at"))
        size = child(pad, "size") or []
        pads.append(
            Pad(
                number=str(pad[1]),
                net=net(pad),
                x=px,
                y=py,
                w=float(str(size[1])),
                h=float(str(size[2])),
                layers=tuple(str(ly) for ly in (child(pad, "layers") or [])[1:]),
            )
        )
    courtyard = [
        absolute(pt)
        for g in f
        if isinstance(g, list) and str(g[0]).startswith("fp_")
        if (value(g, "layer") or "").endswith(".CrtYd")
        for pt in _points(g)
    ]
    if not courtyard:
        for p in pads:
            courtyard += [(p.x - p.w / 2, p.y - p.h / 2), (p.x + p.w / 2, p.y + p.h / 2)]
    return Footprint(
        ref=str(props.get("Reference", "")),
        value=str(props["Value"]) if "Value" in props else None,
        name=str(f[1]),
        x=x,
        y=y,
        rot=rot,
        layer=value(f, "layer") or "F.Cu",
        pads=tuple(pads),
        courtyard=_bbox(courtyard),
    )


def _points(g: list[SExpr]) -> list[Point]:
    """Corner/end points of a graphic item, enough for a bounding box."""
    if str(g[0]).endswith("_circle"):
        cx, cy = _xy(g, "center")
        ex, ey = _xy(g, "end")
        r = math.hypot(ex - cx, ey - cy)
        return [(cx - r, cy - r), (cx + r, cy + r)]
    pts = child(g, "pts")
    if pts is not None:
        return [(float(str(p[1])), float(str(p[2]))) for p in children(pts, "xy")]
    return [_xy(g, tag) for tag in ("start", "mid", "end") if child(g, tag) is not None]


def _bbox(points: list[Point]) -> BBox:
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _xy(node: SExpr, tag: str) -> Point:
    c = child(node, tag)
    if c is None:
        raise ValueError(f"missing ({tag} ...) in {node!r:.120}")
    return float(str(c[1])), float(str(c[2]))


def _num(node: SExpr, tag: str, default: float | None = None) -> float:
    v = value(node, tag)
    if v is None:
        if default is None:
            raise ValueError(f"missing ({tag} ...) in {node!r:.120}")
        return default
    return float(v)
