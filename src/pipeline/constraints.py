"""Stage 2 logic: classes, diff pairs and rails from .kicad_pro, name patterns, constraints.json."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Any

from pipeline import models

POWER = ("GND*", "*GND", "VCC*", "VDD*", "VSS*", "VBUS", "VBAT")
_PLUS_RAIL = re.compile(r"\+\d+(\.\d+)?V")  # +3V3, +3.3V, +5V, +12V, ...
HIGH_SPEED = ("*CLK*", "*SCK*", "*SCLK*", "USB*", "*MISO*", "*MOSI*", "*TX*", "*RX*", "D+", "D-")
PAIRS = (("_P", "_N"), ("+", "-"))
DEFAULT_RAIL_A = 1.0  # SPEC assumption for power nets without a rail_current_a entry


def derive(
    netlist: models.Netlist, project: dict[str, Any] | None, user: dict[str, Any] | None
) -> tuple[models.Constraints, dict[str, str]]:
    """Defaults, then .kicad_pro, then name patterns, then constraints.json.
    Returns the constraints and a source map: net name or top-level field -> where it came from."""
    c = models.Constraints()
    src: dict[str, str] = {}
    names = [n.name for n in netlist.nets]
    if project:
        _project(project, names, c, src)
    _patterns(names, c, src)
    if user:
        _user(user, netlist, c, src)
    for n in names:
        if c.net_class(n) == "power":
            c.rail_current_a.setdefault(n, DEFAULT_RAIL_A)
    return c, src


def _short(name: str) -> str:
    """Net name without its sheet path (/sheet/NAME -> NAME), for matching only."""
    return name.rsplit("/", 1)[-1]


def _class_of(project_class: str) -> str:
    n = project_class.lower()
    if "diff" in n or "usb" in n:
        return "diff"
    if "power" in n or "pwr" in n:
        return "power"
    if any(k in n for k in ("high", "hs", "clk", "fast")):
        return "high_speed"
    return "default"


def _project(
    project: dict[str, Any], names: list[str], c: models.Constraints, src: dict[str, str]
) -> None:
    ns = project.get("net_settings") or {}
    assigned: dict[str, str] = {}  # net -> project class name
    for cls in ns.get("classes") or []:
        for n in cls.get("nets") or []:  # older format lists members inline
            assigned[n] = cls["name"]
    for p in ns.get("netclass_patterns") or []:
        for n in names:
            if fnmatchcase(n, p["pattern"]) or fnmatchcase(_short(n), p["pattern"]):
                assigned.setdefault(n, p["netclass"])
    for n, cls in assigned.items():
        if n in names and _class_of(cls) != "default":
            c.classes[n] = _class_of(cls)
            src[n] = "project"


def _patterns(names: list[str], c: models.Constraints, src: dict[str, str]) -> None:
    upper = {n: _short(n).upper() for n in names}
    by_upper = {u: n for n, u in upper.items()}
    for n, u in upper.items():
        if n in c.classes:  # project wins over patterns
            continue
        if any(fnmatchcase(u, p) for p in POWER) or _PLUS_RAIL.match(u):
            c.classes[n], src[n] = "power", "pattern"
        elif any(fnmatchcase(u, p) for p in HIGH_SPEED):
            c.classes[n], src[n] = "high_speed", "pattern"
    for n, u in upper.items():
        for p, m in PAIRS:
            partner = by_upper.get(u[: -len(p)] + m) if u.endswith(p) else None
            if partner and "project" not in (src.get(n), src.get(partner)):
                c.diff_pairs.append((n, partner))
                c.classes[n] = c.classes[partner] = "diff"
                src[n] = src[partner] = src["diff_pairs"] = "pattern"


def _user(
    user: dict[str, Any], netlist: models.Netlist, c: models.Constraints, src: dict[str, str]
) -> None:
    names = {n.name for n in netlist.nets}
    refs = {x.ref for x in netlist.components}

    def check_nets(field: str, nets: Any) -> None:
        bad = sorted(set(nets) - names)
        if bad:
            raise ValueError(f"constraints.json {field}: unknown nets {bad}")

    classes = user.get("classes", {})
    check_nets("classes", classes)
    bad = {n: k for n, k in classes.items() if k not in models.NET_CLASSES}
    if bad:
        raise ValueError(f"constraints.json classes: not one of {models.NET_CLASSES}: {bad}")
    if any(len(p) != 2 for p in user.get("diff_pairs", [])):
        raise ValueError("constraints.json diff_pairs: each pair must name two nets")
    pairs = [(str(p[0]), str(p[1])) for p in user.get("diff_pairs", [])]
    check_nets("diff_pairs", [n for p in pairs for n in p])
    rails = user.get("rail_current_a", {})
    check_nets("rail_current_a", rails)
    fixed = user.get("fixed", {})
    if bad_refs := sorted(set(fixed) - refs):
        raise ValueError(f"constraints.json fixed: unknown refs {bad_refs}")
    if not all(len(v) == 3 and all(_num(x) for x in v) for v in fixed.values()):
        raise ValueError("constraints.json fixed: values must be [x, y, rot]")
    footprints = user.get("footprints", {})
    if bad_refs := sorted(set(footprints) - refs):
        raise ValueError(f"constraints.json footprints: unknown refs {bad_refs}")
    if not all(isinstance(v, str) and ":" in v for v in footprints.values()):
        raise ValueError('constraints.json footprints: values must be "Lib:Name"')
    outline = user.get("outline_mm")
    if outline is not None and not (len(outline) == 2 and all(_num(x) and x > 0 for x in outline)):
        raise ValueError("constraints.json outline_mm: must be two positive numbers")

    for n, k in classes.items():
        c.classes[n], src[n] = k, "user"
    for a, b in pairs:
        if (a, b) not in c.diff_pairs:
            c.diff_pairs.append((a, b))
        c.classes[a] = c.classes[b] = "diff"
        src[a] = src[b] = src["diff_pairs"] = "user"
    if rails:
        c.rail_current_a.update({n: float(a) for n, a in rails.items()})
        src["rail_current_a"] = "user"
    if imp := user.get("impedance_ohm"):
        c.impedance_ohm.update({k: float(v) for k, v in imp.items()})
        src["impedance_ohm"] = "user"
    if fixed:
        c.fixed = {r: (float(v[0]), float(v[1]), float(v[2])) for r, v in fixed.items()}
        src["fixed"] = "user"
    if footprints:
        c.footprints, src["footprints"] = dict(footprints), "user"
    if outline is not None:
        c.outline_mm, src["outline_mm"] = (float(outline[0]), float(outline[1])), "user"
    if stackup := user.get("stackup"):
        c.stackup, src["stackup"] = models.Stackup(**stackup), "user"
    if (d := user.get("decoupling_max_mm")) is not None:
        c.decoupling_max_mm, src["decoupling_max_mm"] = float(d), "user"


def _num(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)
