"""Feature vector and prediction for a learned judge served behind the judge contract.

Features are the rule judge's own metrics flattened to fixed numbers, so both judges agree on
what a decoupling distance, an impedance error, or an unrouted net is (SPEC.md "Judge contract").
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from pipeline import judge
from pipeline.models import Board, Constraints, Netlist

FEATURES: tuple[str, ...] = (
    "track_length_mm",
    "via_count",
    "segment_count",
    "footprint_count",
    "board_area_mm2",
    "track_length_default_mm",
    "track_length_power_mm",
    "track_length_high_speed_mm",
    "track_length_diff_mm",
    "power_min_width_mm",
    "decoupling_max_mm",
    "decoupling_mean_mm",
    "fast_via_count",
    "fast_nets_over_via_max",
    "impedance_err_min",
    "impedance_err_mean",
    "diff_mismatch_max_mm",
    "unrouted_fraction",
    "zone_net_count",
)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def features(board: Board, netlist: Netlist, constraints: Constraints) -> dict[str, float]:
    """One finite number per FEATURES key; anything missing on this board is 0."""
    c = constraints
    m = judge.score(board, netlist, c).metrics
    nets: dict[str, dict[str, Any]] = m["nets"]
    fast = [n for n, cls in c.classes.items() if cls in judge.FAST]

    length_by_class: dict[str, float] = defaultdict(float)
    for s in board.segments:
        length_by_class[c.net_class(s.net or "")] += s.length
    power_widths = [s.width for s in board.segments if s.net and c.net_class(s.net) == "power"]
    fast_vias = [nets.get(n, {}).get("via_count", 0) for n in fast]
    errors = []
    for n in fast:
        z = nets.get(n, {}).get("zdiff_ohm") or nets.get(n, {}).get("z0_ohm")
        target = c.impedance_ohm.get(c.net_class(n))
        if z and target:
            errors.append(abs(z - target) / target)
    distances = [v["distance_mm"] for v in m["caps"].values()]
    pads = Counter(p.net for f in board.footprints for p in f.pads if p.net)
    routable = [n for n in netlist.nets if pads[n.name] >= 2 and n.name not in board.zone_nets]
    x1, y1, x2, y2 = board.outline

    raw = {
        "track_length_mm": m["totals"]["track_length_mm"],
        "via_count": m["totals"]["via_count"],
        "segment_count": len(board.segments),
        "footprint_count": len(board.footprints),
        "board_area_mm2": (x2 - x1) * (y2 - y1),
        "track_length_default_mm": length_by_class["default"],
        "track_length_power_mm": length_by_class["power"],
        "track_length_high_speed_mm": length_by_class["high_speed"],
        "track_length_diff_mm": length_by_class["diff"],
        "power_min_width_mm": min(power_widths, default=0.0),
        "decoupling_max_mm": max(distances, default=0.0),
        "decoupling_mean_mm": _mean(distances),
        "fast_via_count": sum(fast_vias),
        "fast_nets_over_via_max": sum(v > judge.VIAS_MAX for v in fast_vias),
        "impedance_err_min": min(errors, default=0.0),
        "impedance_err_mean": _mean(errors),
        "diff_mismatch_max_mm": max(
            (v.get("mismatch_mm", 0.0) for v in nets.values()), default=0.0
        ),
        "unrouted_fraction": m["totals"]["unrouted"] / len(routable) if routable else 0.0,
        "zone_net_count": len(board.zone_nets),
    }
    return {k: float(raw[k]) if math.isfinite(raw[k]) else 0.0 for k in FEATURES}


def predict(
    artifact: dict[str, Any], board: Board, netlist: Netlist, constraints: Constraints
) -> tuple[float, dict[str, float]]:
    """(predicted score, features) from an artifact holding "model" and "feature_names"."""
    f = features(board, netlist, constraints)
    row = [[f.get(k, 0.0) for k in artifact["feature_names"]]]
    return float(artifact["model"].predict(row)[0]), f
