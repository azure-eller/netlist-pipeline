"""Reference physics judge: rule-based, every formula named. This is the slot a learned model
fills behind the same contract (SPEC.md, "Judge contract").

All lengths are mm unless the name says otherwise. The stackup of record is
`constraints.stackup` (constraints.json / .kicad_pro win over whatever the board file says).
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from typing import Any

from pipeline import windows
from pipeline.models import Board, Constraints, JudgeResult, Netlist, Segment, Violation
from pipeline.physics import FORMULA, Physics

JUDGE = {"name": "rules", "version": "0.1.0"}

WEIGHTS = {
    "impedance": 5.0,
    "diff_pair_length": 3.0,
    "decoupling": 2.0,
    "current": 5.0,
    "crosstalk": 2.0,
    "vias": 1.0,
    "unrouted": 10.0,
}
IMPEDANCE_TOL = 0.10  # relative
DIFF_MISMATCH_MM = 1.0
CROSSTALK_MAX = 10.0  # sum of parallel run / gap (mm/mm) per victim net
VIAS_MAX = 2
PARALLEL_DEG = 5.0
DELTA_T_C = 10.0  # IPC-2221 temperature rise
MIL_PER_MM = 39.37
OUTER = ("F.Cu", "B.Cu")
FAST = ("high_speed", "diff")


# ---- formulas ----


def z0_microstrip(w: float, h: float, t: float, er: float) -> float:
    """IPC-2141 surface microstrip: Z0 = 87/sqrt(er+1.41) * ln(5.98h / (0.8w + t))."""
    return 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))


def z0_stripline(w: float, h: float, t: float, er: float) -> float:
    """IPC-2141 symmetric stripline: Z0 = 60/sqrt(er) * ln(4h / (0.67 pi (0.8w + t)))."""
    return 60 / math.sqrt(er) * math.log(4 * h / (0.67 * math.pi * (0.8 * w + t)))


def zdiff_microstrip(z0: float, s: float, h: float) -> float:
    """IPC-2141 edge-coupled microstrip: Zdiff = 2 Z0 (1 - 0.48 exp(-0.96 s/h))."""
    return 2 * z0 * (1 - 0.48 * math.exp(-0.96 * s / h))


def current_capacity(w_mm: float, t_mm: float, dt: float = DELTA_T_C) -> float:
    """IPC-2221 external layer: I = 0.048 dT^0.44 A^0.725, A in mil^2."""
    area_mil2 = (w_mm * MIL_PER_MM) * (t_mm * MIL_PER_MM)
    return float(0.048 * dt**0.44 * area_mil2**0.725)


def parallel_run(a: Segment, b: Segment) -> tuple[float, float] | None:
    """(overlapping parallel length, edge-to-edge gap) when a and b are parallel within
    PARALLEL_DEG and overlap along a's direction; else None."""
    ax, ay = a.x2 - a.x1, a.y2 - a.y1
    bx, by = b.x2 - b.x1, b.y2 - b.y1
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    if not la or not lb:
        return None
    if abs(ax * bx + ay * by) / (la * lb) < math.cos(math.radians(PARALLEL_DEG)):
        return None
    ux, uy = ax / la, ay / la
    mx, my = (b.x1 + b.x2) / 2 - a.x1, (b.y1 + b.y2) / 2 - a.y1
    centre_dist = abs(mx * uy - my * ux)
    p1 = (b.x1 - a.x1) * ux + (b.y1 - a.y1) * uy
    p2 = (b.x2 - a.x1) * ux + (b.y2 - a.y1) * uy
    overlap = min(max(p1, p2), la) - max(min(p1, p2), 0.0)
    if overlap <= 0:
        return None
    return overlap, max(centre_dist - (a.width + b.width) / 2, 0.01)


# ---- scoring ----


def _excess(v: Violation) -> float:
    """Relative excess |measured - threshold| / threshold, clipped to [0, 5]."""
    assert v.measured is not None and v.threshold is not None
    return min(abs(v.measured - v.threshold) / v.threshold, 5.0)


def score(
    board: Board, netlist: Netlist, constraints: Constraints, physics: Physics | None = None
) -> JudgeResult:
    ph: Physics = physics or FORMULA
    c = constraints
    st = c.stackup
    h, t = st.dielectric_mm, st.copper_um / 1000
    segs: dict[str, list[Segment]] = defaultdict(list)
    for s in board.segments:
        if s.net:
            segs[s.net].append(s)
    vias = Counter(v.net for v in board.vias if v.net)
    partner = {a: b for a, b in c.diff_pairs} | {b: a for a, b in c.diff_pairs}
    fast = [n for n, cls in c.classes.items() if cls in FAST]

    nets: dict[str, dict[str, Any]] = defaultdict(dict)
    rails: dict[str, dict[str, Any]] = {}
    caps: dict[str, dict[str, Any]] = {}
    violations: list[Violation] = []

    for net, ss in segs.items():
        nets[net]["length_mm"] = sum(s.length for s in ss)
        nets[net]["via_count"] = vias[net]

    # impedance: the provider sees the real cross-sections along the net (neighbours, plane or
    # not) and the net's z0 is their median; a net with no reference anywhere falls back to
    # the ideal-trace call on its narrowest segment, and says so.
    for net in fast:
        if not segs[net]:
            continue
        s = min(segs[net], key=lambda x: x.width)
        cuts = windows.cuts(windows.extract(board, net))
        zs = [zc for cut in cuts if (zc := ph.cut(cut)) is not None]
        if zs:
            z, ref = float(statistics.median(zs)), "cut"
        else:
            z, ref = ph.z0(s.width, h, t, st.er, inner=s.layer not in OUTER), "assumed"
        nets[net].update(
            z0_ohm=z,
            width_mm=s.width,
            layer=s.layer,
            reference=ref,
            n_cuts=len(cuts),
            n_with_reference=len(zs),
        )
        cls = c.net_class(net)
        if cls == "diff":
            gaps = [
                r[1]
                for a in segs[net]
                for b in segs.get(partner.get(net, ""), [])
                if a.layer == b.layer and (r := parallel_run(a, b))
            ]
            gap = min(gaps) if gaps else 2 * s.width
            z = ph.zdiff(s.width, gap, h, t, st.er, inner=s.layer not in OUTER)
            nets[net].update(zdiff_ohm=z, spacing_mm=gap)
        target = c.impedance_ohm.get(cls)
        if target and abs(z - target) / target > IMPEDANCE_TOL:
            violations.append(
                Violation(
                    "impedance",
                    f"{net}: {z:.1f} ohm on {s.layer} at {s.width} mm, target {target} ohm",
                    net=net,
                    measured=z,
                    threshold=target,
                )
            )

    # diff pair length
    for a, b in c.diff_pairs:
        la, lb = nets[a].get("length_mm", 0.0), nets[b].get("length_mm", 0.0)
        mm = abs(la - lb)
        nets[a]["mismatch_mm"] = nets[b]["mismatch_mm"] = mm
        if mm > DIFF_MISMATCH_MM:
            violations.append(
                Violation(
                    "diff_pair_length",
                    f"{a}/{b}: {la:.2f} vs {lb:.2f} mm",
                    net=f"{a}/{b}",
                    measured=mm,
                    threshold=DIFF_MISMATCH_MM,
                )
            )

    # decoupling
    u_pads = defaultdict(list)
    for f in board.footprints:
        if f.ref.startswith("U"):
            for p in f.pads:
                if p.net:
                    u_pads[p.net].append(p)
    for f in board.footprints:
        if not f.ref.startswith("C"):
            continue
        gnd = [p for p in f.pads if p.net and p.net.upper().startswith("GND")]
        power = [p for p in f.pads if p.net and p not in gnd and c.net_class(p.net) == "power"]
        if not gnd or not power:
            continue
        p, rail = power[0], str(power[0].net)
        if not u_pads[rail]:
            continue
        d = min(math.hypot(p.x - q.x, p.y - q.y) for q in u_pads[rail])
        caps[f.ref] = {"distance_mm": d, "rail": rail}
        if d > c.decoupling_max_mm:
            violations.append(
                Violation(
                    "decoupling",
                    f"{f.ref} is {d:.2f} mm from the nearest IC pin on {rail}",
                    net=rail,
                    ref=f.ref,
                    measured=d,
                    threshold=c.decoupling_max_mm,
                )
            )

    # current
    for rail, amps in c.rail_current_a.items():
        if not segs[rail]:
            continue
        w = min(s.width for s in segs[rail])
        cap = current_capacity(w, t)
        rails[rail] = {"min_width_mm": w, "capacity_a": cap, "required_a": amps}
        if amps > cap:
            violations.append(
                Violation(
                    "current",
                    f"{rail}: {w} mm carries {cap:.2f} A, needs {amps} A",
                    net=rail,
                    measured=amps,
                    threshold=cap,
                )
            )

    # crosstalk
    # ponytail: O(fast segments x all segments); add a per-layer grid if boards get large.
    xt: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for sa in (s for n in fast for s in segs[n]):
        victim = str(sa.net)
        for sb in board.segments:
            if not sb.net or sb.net in (victim, partner.get(victim)) or sb.layer != sa.layer:
                continue
            r = parallel_run(sa, sb)
            if r and r[1] < 3 * max(sa.width, sb.width):
                xt[victim][sb.net] += r[0] / r[1]
    for net, aggs in xt.items():
        total = sum(aggs.values())
        nets[net]["crosstalk"] = dict(aggs)
        if total > CROSSTALK_MAX:
            violations.append(
                Violation(
                    "crosstalk",
                    f"{net}: parallel run/gap sum {total:.1f} from {sorted(aggs)}",
                    net=net,
                    measured=total,
                    threshold=CROSSTALK_MAX,
                )
            )

    # vias
    for net in fast:
        if vias[net] > VIAS_MAX:
            violations.append(
                Violation(
                    "vias",
                    f"{net}: {vias[net]} vias",
                    net=net,
                    measured=float(vias[net]),
                    threshold=float(VIAS_MAX),
                )
            )

    # unrouted
    pad_count = Counter(p.net for f in board.footprints for p in f.pads if p.net)
    unrouted = sorted(
        n.name
        for n in netlist.nets
        if pad_count[n.name] >= 2
        and not segs[n.name]
        and not vias[n.name]
        and n.name not in board.zone_nets
    )
    for net in unrouted[:20]:
        violations.append(Violation("unrouted", f"{net}: no copper", net=net))

    penalty = sum(WEIGHTS[v.rule] * _excess(v) for v in violations if v.rule != "unrouted")
    penalty += WEIGHTS["unrouted"] * len(unrouted)
    metrics = {
        "nets": dict(nets),
        "rails": rails,
        "caps": caps,
        "totals": {
            "track_length_mm": sum(s.length for s in board.segments),
            "via_count": len(board.vias),
            "unrouted": len(unrouted),
            "violations": len(violations),
        },
    }
    return JudgeResult(
        -penalty if penalty else 0.0,
        metrics,
        violations,
        {"name": ph.name, "version": ph.version},
    )
