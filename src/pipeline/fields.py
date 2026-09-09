"""2D quasi-static finite-difference field solver for a microstrip cross-section: the slow oracle
a learned surrogate is trained on and gated against (docs/experiments/fields-oracle.md).

Solves div(eps grad phi) = 0 by finite volumes on a tensor grid (5-point stencil), takes the
capacitance per unit length from the discrete field energy, and converts to line parameters
from the solve with and without the dielectric. Lengths mm, impedance ohm, capacitance pF/m.

`solve` is the one-trace (or pair) oracle behind dataset 17 and learned-fd v5; it is frozen.
`solve_cut` is the general form (docs/FACTORY.md step 2): any number of conductors on one
layer, a reference plane or not, the full capacitance matrix by the charge on each conductor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import scipy.sparse as sp
from numpy.typing import NDArray
from scipy.optimize import brentq
from scipy.sparse.linalg import spsolve

if TYPE_CHECKING:
    from pipeline.windows import Conductor, Cut

SOLVER_VERSION = "fd2d-0.1"
CUT_SOLVER_VERSION = "fd2d-cut-0.1"
MAX_NEIGHBOURS = 6  # nearest by |offset|; keeps a busy cut a ~1 s solve
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


@dataclass(frozen=True)
class CutParams:
    """Line parameters of the target conductor of a cut, among its neighbours. The matrices are
    Maxwell capacitance matrices in pF/m, conductor order as in the cut (sorted by offset);
    `target` is the target's index. z0, eps_eff and coupling are `derive`d from them."""

    z0: float  # target driven, every other conductor and the plane at 0 V
    eps_eff: float
    c11_pf_per_m: float
    coupling: tuple[tuple[str | None, float], ...]  # (net, k) per neighbour, cut order
    cmatrix: tuple[tuple[float, ...], ...]
    cmatrix_air: tuple[tuple[float, ...], ...]
    target: int
    n_conductors: int
    plane: bool


def derive(
    c: tuple[tuple[float, ...], ...] | list[list[float]],
    c0: tuple[tuple[float, ...], ...] | list[list[float]],
    target: int,
    nets: tuple[str | None, ...],
) -> tuple[float, float, float, tuple[tuple[str | None, float], ...]]:
    """(z0, eps_eff, c11_pf_per_m, coupling) from the two capacitance matrices (pF/m). Shared by
    the solver and the learned model so both answer the judge through one formula."""
    c11, c11_0 = c[target][target] * 1e-12, c0[target][target] * 1e-12
    z0 = 1 / (C_LIGHT * math.sqrt(c11 * c11_0))
    coupling = tuple(
        (nets[j], -c[target][j] / math.sqrt(c[target][target] * c[j][j]))
        for j in range(len(nets))
        if j != target
    )
    return z0, c11 / c11_0, c[target][target], coupling


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
    k = _stiffness(x, y, eps_col)
    phi = _solve_fixed(k, fixed, phi)
    return np.asarray(np.sum(phi * (k @ phi), axis=0))


def _stiffness(
    x: NDArray[np.float64], y: NDArray[np.float64], eps_col: NDArray[np.float64]
) -> sp.csr_matrix:
    """Finite-volume stiffness matrix of div(eps grad) on the tensor grid, in units of eps0."""
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
    return sp.coo_matrix((np.concatenate([gg, gg, -gg, -gg]), (rows, cols)), shape=(n, n)).tocsr()


def _solve_fixed(
    k: sp.csr_matrix, fixed: NDArray[np.bool_], phi: NDArray[np.float64]
) -> NDArray[np.float64]:
    """phi (n, m) with the free nodes solved for each column's Dirichlet data."""
    fix = fixed.ravel()
    free = ~fix
    phi = phi.reshape(k.shape[0], -1).copy()
    rhs = -(k[free][:, fix] @ phi[fix])
    phi[free] = spsolve(k[free][:, free].tocsc(), rhs).reshape(rhs.shape)
    return phi


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


def _fine(keys: list[float], dx: float) -> NDArray[np.float64]:
    """Nodes from keys[0] to keys[-1], cells no larger than dx, every key on a node."""
    out = []
    for a, b in zip(keys[:-1], keys[1:], strict=True):
        n = max(2, math.ceil((b - a) / dx))
        out.append(np.linspace(a, b, n, endpoint=False))
    return np.concatenate([*out, [keys[-1]]])


def _tail(start: float, d0: float, n: int, end: float) -> NDArray[np.float64]:
    """n geometrically growing cells from start to end; nodes after start."""
    k = np.arange(n)
    with np.errstate(
        over="ignore"
    ):  # brentq probes r = 100; the overflow is a bracket, not an answer
        r = brentq(lambda r: d0 * np.sum(r**k) - (end - start), 1e-3, 100.0)
    return start + d0 * np.cumsum(r**k)


def select_conductors(cut: Cut) -> tuple[list[Conductor], int]:
    """The conductors a solve (and a model) sees: the target and its MAX_NEIGHBOURS nearest
    neighbours by |offset|, in cut order; and the target's index among them."""
    ti = next(i for i, c in enumerate(cut.conductors) if c.offset == 0.0)
    keep = sorted(range(len(cut.conductors)), key=lambda i: abs(cut.conductors[i].offset))
    keep = sorted(keep[: MAX_NEIGHBOURS + 1])
    return [cut.conductors[i] for i in keep], keep.index(ti)


@lru_cache(maxsize=256)
def solve_cut(cut: Cut, nx: int = 500, ny: int = 200) -> CutParams | None:
    """Capacitance matrix of every conductor in `cut` (nearest MAX_NEIGHBOURS kept), by the
    charge on each conductor with one driven at a time, with the dielectric and with air.
    Conductors sit on y in [h, h+t] over a slab of er; the plane, when either flag is set, is
    the ground at y = 0 (a plane above a bottom-layer trace is the mirror image). Without a
    plane the slab is over air out to a Neumann edge, and the neighbours are the only
    reference: with no neighbour either there is nothing to measure against, so None."""
    plane = cut.plane_below or cut.plane_above
    if not plane and len(cut.conductors) == 1:
        return None
    cs, ti = select_conductors(cut)
    h, t, er = cut.h, cut.t, cut.er
    w0 = cs[ti].width
    edges = sorted(e for c in cs for e in (c.offset - c.width / 2, c.offset + c.width / 2))
    margin = max(8 * w0, 6 * h)
    dx = min(min(c.width for c in cs), h) / 32 * 500 / nx  # the field crowds the edges
    fine = _fine([edges[0] - h, *edges, edges[-1] + h], dx)
    if len(fine) > 1.5 * nx:  # a busy cut: coarsen until the fine region fits the budget
        dx *= len(fine) / (1.5 * nx)
        fine = _fine([edges[0] - h, *edges, edges[-1] + h], dx)
    n_out = max(12, nx // 8)
    x = np.concatenate(
        [
            -_tail(-fine[0], fine[1] - fine[0], n_out, -(edges[0] - margin))[::-1],
            fine,
            _tail(fine[-1], fine[-1] - fine[-2], n_out, edges[-1] + margin),
        ]
    )
    up = _axis([0.0, h, h + t], ny // 2, ny - ny // 2, h + t + 8 * h)
    y = up if plane else np.concatenate([-_tail(0.0, up[1] - up[0], ny // 4, 8 * h)[::-1], up])
    xx, yy = np.meshgrid(x, y, indexing="ij")
    on_layer = (yy >= h - TOL) & (yy <= h + t + TOL)
    masks = [
        on_layer & (xx >= c.offset - c.width / 2 - TOL) & (xx <= c.offset + c.width / 2 + TOL)
        for c in cs
    ]
    fixed = np.any(masks, axis=0)
    if plane:
        fixed |= yy == 0
    phi = np.stack([m.astype(float) for m in masks], axis=-1)  # column j: conductor j at 1 V
    yc = (y[:-1] + y[1:]) / 2
    mats = []
    for e in (er, 1.0):
        k = _stiffness(x, y, np.where((yc >= 0) & (yc < h), e, 1.0))
        sol = _solve_fixed(k, fixed, phi)
        q = k @ sol  # charge per node, units of eps0
        mats.append(
            tuple(
                tuple(float(np.sum(q[m.ravel(), j]) * EPS0 * 1e12) for j in range(len(cs)))
                for m in masks
            )
        )
    c, c0 = mats
    nets = tuple(x.net for x in cs)
    z0, eps_eff, c11, coupling = derive(c, c0, ti, nets)
    return CutParams(z0, eps_eff, c11, coupling, c, c0, ti, len(cs), plane)
