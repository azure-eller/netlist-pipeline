"""Shared value types. Everything here is plain data; parsing and math live elsewhere."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---- netlist (from kicad-cli sch export netlist) ----


@dataclass(frozen=True)
class Component:
    ref: str
    value: str | None
    footprint: str | None


@dataclass(frozen=True)
class Node:
    ref: str
    pin: str

    @property
    def key(self) -> str:
        return f"{self.ref}.{self.pin}"


@dataclass(frozen=True)
class Net:
    code: int
    name: str
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Netlist:
    components: tuple[Component, ...]
    nets: tuple[Net, ...]

    def connectivity(self) -> dict[str, frozenset[str]]:
        """net name -> {"R1.1", "C3.2", ...}. The thing verification compares."""
        return {n.name: frozenset(x.key for x in n.nodes) for n in self.nets}

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> Netlist:
        return Netlist(
            components=tuple(Component(**c) for c in d["components"]),
            nets=tuple(
                Net(n["code"], n["name"], tuple(Node(**x) for x in n["nodes"])) for n in d["nets"]
            ),
        )


# ---- constraints (stage 2 output; judge and placer input) ----

NET_CLASSES = ("default", "power", "high_speed", "diff")


@dataclass
class Stackup:
    layers: int = 2
    board_thickness_mm: float = 1.6
    copper_um: float = 35.0
    dielectric_mm: float = 1.51  # outer copper to nearest plane; 2-layer default
    er: float = 4.5


@dataclass
class Constraints:
    classes: dict[str, str] = field(default_factory=dict)  # net -> NET_CLASSES
    diff_pairs: list[tuple[str, str]] = field(default_factory=list)
    rail_current_a: dict[str, float] = field(default_factory=dict)  # power net -> amps
    impedance_ohm: dict[str, float] = field(
        default_factory=lambda: {"high_speed": 50.0, "diff": 100.0}
    )
    fixed: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # ref -> x, y, rot
    footprints: dict[str, str] = field(
        default_factory=dict
    )  # ref -> "Lib:Name" when the schematic has none
    outline_mm: tuple[float, float] | None = None  # (w, h) when generating without a board
    stackup: Stackup = field(default_factory=Stackup)
    decoupling_max_mm: float = 10.0

    def net_class(self, net: str) -> str:
        return self.classes.get(net, "default")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> Constraints:
        d = dict(d)
        d["diff_pairs"] = [tuple(p) for p in d.get("diff_pairs", [])]
        d["fixed"] = {k: tuple(v) for k, v in d.get("fixed", {}).items()}
        if d.get("outline_mm"):
            d["outline_mm"] = tuple(d["outline_mm"])
        d["stackup"] = Stackup(**d.get("stackup", {}))
        return Constraints(**d)


# ---- board geometry (parsed from .kicad_pcb text by board.py) ----

BBox = tuple[float, float, float, float]  # x1, y1, x2, y2 in mm, absolute


@dataclass(frozen=True)
class Pad:
    number: str
    net: str | None
    x: float
    y: float
    w: float
    h: float
    layers: tuple[str, ...]


@dataclass(frozen=True)
class Footprint:
    ref: str
    value: str | None
    name: str  # "Library:Footprint"
    x: float
    y: float
    rot: float
    layer: str  # "F.Cu" | "B.Cu"
    pads: tuple[Pad, ...]
    courtyard: BBox  # absolute; falls back to pad bbox when no courtyard drawn


@dataclass(frozen=True)
class Segment:
    net: str | None
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float
    width: float

    @property
    def length(self) -> float:
        return float(((self.x2 - self.x1) ** 2 + (self.y2 - self.y1) ** 2) ** 0.5)


@dataclass(frozen=True)
class Via:
    net: str | None
    x: float
    y: float
    size: float
    drill: float


@dataclass(frozen=True)
class Board:
    outline: BBox  # bbox of Edge.Cuts
    copper_layers: tuple[str, ...]
    stackup: Stackup
    footprints: tuple[Footprint, ...]
    segments: tuple[Segment, ...]
    vias: tuple[Via, ...]

    def footprint(self, ref: str) -> Footprint | None:
        return next((f for f in self.footprints if f.ref == ref), None)


# ---- judge output ----


@dataclass
class Violation:
    rule: str
    message: str
    net: str | None = None
    ref: str | None = None
    measured: float | None = None
    threshold: float | None = None


@dataclass
class JudgeResult:
    score: float  # higher is better; 0 is "no violations"
    metrics: dict[str, Any]
    violations: list[Violation]
    judge: dict[str, str]  # {"name": ..., "version": ...}

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
