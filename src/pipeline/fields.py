"""2D quasi-static finite-difference field solver for a microstrip cross-section: the slow oracle
a learned surrogate is trained on and gated against (docs/experiments/fields-oracle.md).

Solves div(eps grad phi) = 0 by finite volumes on a tensor grid (5-point stencil), takes the
capacitance per unit length from the discrete field energy, and converts to line parameters
from the solve with and without the dielectric. Lengths mm, impedance ohm, capacitance pF/m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import scipy.sparse as sp
from numpy.typing import NDArray
from scipy.optimize import brentq
from scipy.sparse.linalg import spsolve

SOLVER_VERSION = "fd2d-0.1"
C_LIGHT = 299_792_458.0  # m/s
EPS0 = 8.8541878188e-12  # F/m
TOL = 1e-9  # mm; node-on-edge test


@dataclass(frozen=True)
class Geometry:
    w: float  # trace width, mm
    h: float  # dielectric height between trace bottom and ground plane, mm
    t: float  # copper thickness, mm
    er: float  # dielectric relative permittivity
    s: float | None = None  # edge-to-edge spacing of a coupled pair, mm; None = single trace

    def __post_init__(self) -> None:
        if not (self.w > 0 and self.h > 0 and self.t > 0 and self.er >= 1):
            raise ValueError(f"need w, h, t > 0 and er >= 1: {self}")
        if self.s is not None and not self.s > 0:
            raise ValueError(f"need s > 0: {self}")


@dataclass(frozen=True)
class LineParams:
    z0: float  # single-ended impedance, ohm; for a pair: one line driven, the other grounded
    eps_eff: float
    c_pf_per_m: float  # capacitance per unit length with dielectric
    z_even: float | None
    z_odd: float | None
    z_diff: float | None  # 2 * z_odd
    coupling: float | None  # (z_even - z_odd) / (z_even + z_odd), backward crosstalk coefficient


def _axis(keys: list[float], n_fine: int, n_out: int, end: float) -> NDArray[np.float64]:
    """Nodes from keys[0] to end: uniform cells between successive keys (counts by length, every
    key on a node), then n_out geometrically growing cells from keys[-1] to end."""
    spans = np.diff(keys)
    counts = np.maximum(2, np.round(spans / spans.sum() * n_fine)).astype(int)
    pieces = zip(keys[:-1], keys[1:], counts, strict=True)
    fine = [np.linspace(a, b, n, endpoint=False) for a, b, n in pieces]
    d0 = spans[-1] / counts[-1]
    k = np.arange(n_out)
    r = brentq(lambda r: d0 * np.sum(r**k) - (end - keys[-1]), 1e-3, 100.0)
    return np.concatenate([*fine, [keys[-1]], keys[-1] + d0 * np.cumsum(r**k)])


def _grid(g: Geometry, nx: int, ny: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """x symmetric about 0, y from the ground plane up. Fine uniform cells over the conductors
    plus one h of margin, stretched cells out to >= max(8w, 6h) sideways and 8h above the trace."""
    edge = g.w / 2 if g.s is None else g.s / 2 + g.w
    xkeys = [0.0, edge] if g.s is None else [0.0, g.s / 2, edge]
    half = _axis(
        [*xkeys, edge + g.h],
        round(0.6 * nx / 2),
        nx // 2 - round(0.6 * nx / 2),
        edge + max(8 * g.w, 6 * g.h),
    )
    x = np.concatenate([-half[:0:-1], half])
    y = _axis([0.0, g.h, g.h + g.t], ny // 2, ny - ny // 2, g.h + g.t + 8 * g.h)
    return x, y


def _energy2(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    eps_col: NDArray[np.float64],
    fixed: NDArray[np.bool_],
    phi: NDArray[np.float64],
) -> NDArray[np.float64]:
    """2W = phi^T K phi (per unit length, in units of eps0) for each column of phi, whose values
    on `fixed` nodes are Dirichlet data. eps_col is the permittivity per cell row (y varies)."""
    dx, dy = np.diff(x), np.diff(y)
    eps = np.broadcast_to(eps_col, (len(dx), len(dy)))
    # face conductances: length-weighted mean of the two cells each face straddles, over distance
    ex, ey = eps * dy, eps * dx[:, None]
    gx = (np.pad(ex, ((0, 0), (1, 0))) + np.pad(ex, ((0, 0), (0, 1)))) / 2 / dx[:, None]
    gy = (np.pad(ey, ((1, 0), (0, 0))) + np.pad(ey, ((0, 1), (0, 0)))) / 2 / dy
    idx = np.arange(len(x) * len(y)).reshape(len(x), len(y))
    a = np.concatenate([idx[:-1, :].ravel(), idx[:, :-1].ravel()])
    b = np.concatenate([idx[1:, :].ravel(), idx[:, 1:].ravel()])
    gg = np.concatenate([gx.ravel(), gy.ravel()])
    n = idx.size
    rows, cols = np.concatenate([a, b, a, b]), np.concatenate([a, b, b, a])
    k = sp.coo_matrix((np.concatenate([gg, gg, -gg, -gg]), (rows, cols)), shape=(n, n)).tocsr()
    fix = fixed.ravel()
    free = ~fix
    phi = phi.reshape(n, -1).copy()
    rhs = -(k[free][:, fix] @ phi[fix])
    phi[free] = spsolve(k[free][:, free].tocsc(), rhs).reshape(rhs.shape)
    return np.asarray(np.sum(phi * (k @ phi), axis=0))


@lru_cache(maxsize=256)
def solve(g: Geometry, nx: int = 400, ny: int = 200) -> LineParams:
    """Line parameters of `g` on a nominally nx x ny grid (ground at y=0, Neumann elsewhere).
    For a pair, z0/eps_eff/c_pf_per_m are for one line driven with the other grounded, i.e.
    from C11 = (C_even + C_odd) / 2; z_even/z_odd from the even (+1,+1) and odd (+1,-1) modes."""
    x, y = _grid(g, nx, ny)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    lo, hi = (0.0, g.w / 2) if g.s is None else (g.s / 2, g.s / 2 + g.w)
    on_trace = (yy >= g.h - TOL) & (yy <= g.h + g.t + TOL)
    right = on_trace & (xx >= lo - TOL) & (xx <= hi + TOL)
    left = on_trace & (-xx >= lo - TOL) & (-xx <= hi + TOL)
    fixed = (yy == 0) | right | left
    even, odd = (right | left).astype(float), right.astype(float) - left.astype(float)
    phi = even[..., None] if g.s is None else np.stack([even, odd], axis=-1)
    lines = 1 if g.s is None else 2
    yc = (y[:-1] + y[1:]) / 2
    c, c0 = (
        _energy2(x, y, np.where(yc < g.h, er, 1.0), fixed, phi) * EPS0 / lines for er in (g.er, 1.0)
    )
    z = 1 / (C_LIGHT * np.sqrt(c * c0))
    if g.s is None:
        return LineParams(
            float(z[0]), float(c[0] / c0[0]), float(c[0] * 1e12), None, None, None, None
        )
    c11, c11_0 = float(c.mean()), float(c0.mean())
    ze, zo = float(z[0]), float(z[1])
    return LineParams(
        1 / (C_LIGHT * math.sqrt(c11 * c11_0)),
        c11 / c11_0,
        c11 * 1e12,
        ze,
        zo,
        2 * zo,
        (ze - zo) / (ze + zo),
    )
