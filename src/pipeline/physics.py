"""Physics providers for the judge: where impedance numbers come from.

`Formula` is the closed form (IPC-2141), the reference judge. `Oracle` is the 2D field solver
(`pipeline.fields`), slow and closest to the truth we have. Learned providers implement the
same calls (`pipeline.surrogate.Learned`, `pipeline.cutnet.Learned`). The judge's rules never
change; only the physics behind them does.

Three calls: `z0` and `zdiff` take the four (five) numbers of an ideal trace over a plane;
`cut` takes a real cross-section (`windows.Cut`: neighbours, plane or not) and answers None
when there is nothing to measure against. The judge's impedance rule asks `cut` first."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pipeline.windows import Cut

OUTER = ("F.Cu", "B.Cu")


class Physics(Protocol):
    name: str
    version: str

    def z0(self, w: float, h: float, t: float, er: float, inner: bool) -> float: ...

    def zdiff(self, w: float, s: float, h: float, t: float, er: float, inner: bool) -> float: ...

    def cut(self, c: Cut) -> float | None: ...


class Formula:
    """IPC-2141 closed forms. The reference judge 'rules 0.1.0'."""

    name = "rules"
    version = "0.1.0"

    def z0(self, w: float, h: float, t: float, er: float, inner: bool) -> float:
        if inner:  # symmetric stripline
            return 60 / math.sqrt(er) * math.log(4 * h / (0.67 * math.pi * (0.8 * w + t)))
        return 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))

    def zdiff(self, w: float, s: float, h: float, t: float, er: float, inner: bool) -> float:
        return 2 * self.z0(w, h, t, er, inner) * (1 - 0.48 * math.exp(-0.96 * s / h))

    def cut(self, c: Cut) -> float | None:
        """The closed form on the target alone: it assumes a plane whether or not one is there."""
        return self.z0(c.target.width, c.h, c.t, c.er, inner=c.layer not in OUTER)


class Oracle:
    """The 2D quasi-static field solver (cached per geometry inside `fields.solve`). Stripline
    is approximated by the microstrip solve (the solver models one ground plane); noted, not
    hidden."""

    name = "oracle-fd"

    def __init__(self) -> None:
        from pipeline import fields

        self.version = f"{fields.SOLVER_VERSION}+{fields.CUT_SOLVER_VERSION}"
        self._fields = fields

    def z0(self, w: float, h: float, t: float, er: float, inner: bool) -> float:
        return float(self._fields.solve(self._fields.Geometry(w, h, t, er)).z0)

    def zdiff(self, w: float, s: float, h: float, t: float, er: float, inner: bool) -> float:
        p = self._fields.solve(self._fields.Geometry(w, h, t, er, s))
        return float(p.z_diff if p.z_diff is not None else 2 * p.z0)

    def cut(self, c: Cut) -> float | None:
        """The general solver on the real cross-section: neighbours, plane or not."""
        p = self._fields.solve_cut(c)
        return p.z0 if p else None


FORMULA = Formula()
