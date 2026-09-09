"""Experimental GPU field solver. Explicit opt-in; the CPU oracle remains unchanged.

Uses the oracle's grid and finite-volume equations, FP64 GPU assembly, a Galerkin
multigrid preconditioner with conjugate gradients, and GPU charge extraction.
CuPy is imported only when this module is explicitly requested.
"""

from __future__ import annotations

import math
import time
from typing import Any

import cupy as cp
import cupyx.scipy.sparse as sp
import numpy as np
from cupyx.scipy.linalg import solve_triangular

from pipeline import fields
from pipeline.windows import Cut

_CSR = cp.RawKernel(
    r"""
extern "C" __global__ void csr_apply(
    const double* a, const int* indices, const int* offsets,
    const double* x, const double* rhs, const double* weight,
    double* out, int n, int columns, int mode) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n * columns) return;
    int row = i / columns, col = i % columns;
    double v = 0.;
    for (int j = offsets[row]; j < offsets[row + 1]; ++j)
        v += a[j] * x[indices[j] * columns + col];
    if (mode == 1) out[i] = x[i] + weight[row] * (rhs[i] - v);
    else if (mode == 2) out[i] = rhs[i] - v;
    else if (mode == 3) out[i] += v;
    else out[i] = v;
}
""",
    "csr_apply",
)

_DENSE = cp.RawKernel(
    r"""
extern "C" __global__ void dense_apply(
    const double* a, const double* x, double* out, int n, int columns) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n * columns) return;
    int row = i / columns, col = i % columns;
    double v = 0.;
    for (int j = 0; j < n; ++j) v += a[row*n+j] * x[j*columns+col];
    out[i] = v;
}
""",
    "dense_apply",
)

_FACTOR = cp.RawKernel(
    r"""
extern "C" __global__ void line_factor(const double* d, const double* lo,
 const double* hi, double* inv, double* upper, int lines, int length, int stride) {
 int line=blockIdx.x*blockDim.x+threadIdx.x;
 if(line>=lines) return;
 int base = stride==1 ? line*length : line;
 double prev=0.;
 for(int j=0;j<length;j++) {
   int i=base+j*stride;
   inv[i]=1./(d[i]-lo[i]*prev);
   prev=hi[i]*inv[i]; upper[i]=prev;
 }
}
""",
    "line_factor",
)

_LINE = cp.RawKernel(
    r"""
extern "C" __global__ void line_smooth(const double* rhs, const double* x,
 const double* lo, const double* inv, const double* upper, double* out,
 int lines, int length, int stride, int columns) {
 int thread=blockIdx.x*blockDim.x+threadIdx.x;
 if(thread>=lines*columns) return;
 int line=thread/columns, col=thread%columns;
 int base = stride==1 ? line*length : line;
 double prev=0.;
 for(int j=0;j<length;j++) {
   int row=base+j*stride, i=row*columns+col;
   prev=(rhs[i]-lo[row]*prev)*inv[row]; out[i]=prev;
 }
 prev=0.;
 for(int j=length-1;j>=0;j--) {
   int row=base+j*stride, i=row*columns+col;
   prev=out[i]-upper[row]*prev; out[i]=x[i]+0.7*prev;
 }
}
""",
    "line_smooth",
)


def _apply(
    matrix: Any, x: Any, out: Any, rhs: Any = None, weight: Any = None, mode: int = 0
) -> None:
    n, columns = out.shape
    _CSR(
        ((n * columns + 127) // 128,),
        (128,),
        (
            matrix.data,
            matrix.indices,
            matrix.indptr,
            x,
            x if rhs is None else rhs,
            x if weight is None else weight,
            out,
            np.int32(n),
            np.int32(columns),
            np.int32(mode),
        ),
    )


def _interpolation(axis: Any) -> tuple[Any, Any]:
    """Linear interpolation in physical coordinates, retaining both endpoints."""
    selected = np.unique(np.r_[np.arange(0, len(axis), 2), len(axis) - 1])
    coarse = axis[selected]
    left = np.clip(np.searchsorted(coarse, axis, side="right") - 1, 0, len(coarse) - 2)
    weight = (axis - coarse[left]) / (coarse[left + 1] - coarse[left])
    rows = np.repeat(np.arange(len(axis)), 2)
    cols = np.column_stack([left, left + 1]).ravel()
    values = np.column_stack([1 - weight, weight]).ravel()
    prolong = sp.coo_matrix(
        (cp.asarray(values), (cp.asarray(rows), cp.asarray(cols))),
        shape=(len(axis), len(coarse)),
    ).tocsr()
    return coarse, prolong


class Multigrid:
    """Symmetric V cycle used as a PCG preconditioner for all conductor columns."""

    def __init__(self, matrix: Any, x: Any, y: Any, free: Any) -> None:
        self.levels: list[Any] = []
        self.lines: list[Any] = []
        for level in range(20):
            if matrix.shape[0] <= 256:
                break
            # Semi-coarsen when grid cells are strongly anisotropic.
            dx, dy = np.median(np.diff(x)), np.median(np.diff(y))
            stride = 1 if dy <= dx else len(y)
            lines, length = (len(x), len(y)) if stride == 1 else (len(y), len(x))
            diagonal = matrix.diagonal()
            lo, hi = cp.zeros_like(diagonal), cp.zeros_like(diagonal)
            lo[stride:] = matrix.diagonal(-stride)
            hi[:-stride] = matrix.diagonal(stride)
            if stride == 1:
                lo[::length] = 0
                hi[length - 1 :: length] = 0
            inv, upper = cp.empty_like(diagonal), cp.empty_like(diagonal)
            _FACTOR(
                ((lines + 127) // 128,),
                (128,),
                (diagonal, lo, hi, inv, upper, np.int32(lines), np.int32(length), np.int32(stride)),
            )
            self.lines.append((lo, inv, upper, lines, length, stride))
            if len(y) <= 3 or (dx < dy / 2 and len(x) > 3):
                x, px = _interpolation(x)
                py = sp.eye(len(y), format="csr")
            elif len(x) <= 3 or dy < dx / 2:
                y, py = _interpolation(y)
                px = sp.eye(len(x), format="csr")
            else:
                x, px = _interpolation(x)
                y, py = _interpolation(y)
            prolong = sp.kron(px, py, format="csr")
            if level == 0:
                prolong = (sp.diags(free.astype(cp.float64)) @ prolong).tocsr()
            restrict = prolong.T.tocsr()
            self.levels.append((matrix, prolong, restrict))
            matrix = (restrict @ matrix @ prolong).tocsr()
            # A coarse basis function supported entirely inside fixed copper has no
            # free-node support. Give its unused degree of freedom an identity row.
            empty = matrix.diagonal() == 0
            matrix = (matrix + sp.diags(empty.astype(cp.float64))).tocsr()
        import cupyx

        with cupyx.errstate(linalg="raise"):
            self.coarse = cp.linalg.cholesky(matrix.toarray())
        identity = cp.eye(matrix.shape[0], dtype=cp.float64)
        inverse_l = solve_triangular(self.coarse, identity, lower=True, check_finite=False)
        self.inverse = cp.ascontiguousarray(inverse_l.T @ inverse_l)
        self.graph: Any = None

    def _cycle_into(self, level: int = 0) -> None:
        rhs, z, scratch, residual = self.work[level]
        if level == len(self.levels):
            n, columns = z.shape
            _DENSE(
                ((n * columns + 127) // 128,),
                (128,),
                (self.inverse, rhs, z, np.int32(n), np.int32(columns)),
            )
            return
        matrix, prolong, restrict = self.levels[level]
        z.fill(0)
        lo, inv, upper, lines, length, stride = self.lines[level]
        columns = z.shape[1]
        for _ in range(2):
            _apply(matrix, z, residual, rhs, mode=2)
            _LINE(
                ((lines * columns + 127) // 128,),
                (128,),
                (
                    residual,
                    z,
                    lo,
                    inv,
                    upper,
                    scratch,
                    np.int32(lines),
                    np.int32(length),
                    np.int32(stride),
                    np.int32(columns),
                ),
            )
            z, scratch = scratch, z
        _apply(matrix, z, residual, rhs, mode=2)
        _apply(restrict, residual, self.work[level + 1][0])
        self._cycle_into(level + 1)
        _apply(prolong, self.work[level + 1][1], z, mode=3)
        for _ in range(2):
            _apply(matrix, z, residual, rhs, mode=2)
            _LINE(
                ((lines * columns + 127) // 128,),
                (128,),
                (
                    residual,
                    z,
                    lo,
                    inv,
                    upper,
                    scratch,
                    np.int32(lines),
                    np.int32(length),
                    np.int32(stride),
                    np.int32(columns),
                ),
            )
            z, scratch = scratch, z
        # Four swaps return the result to the original solution buffer.

    def apply(self, rhs: Any) -> Any:
        if self.graph is None:
            sizes = [a.shape[0] for a, _, _ in self.levels] + [self.inverse.shape[0]]
            self.work = [
                tuple(cp.zeros((n, rhs.shape[1]), dtype=cp.float64) for _ in range(4))
                for n in sizes
            ]
            self.work[0][0][...] = rhs
            self._cycle_into()  # compile kernels and initialize BLAS before capture
            cp.cuda.get_current_stream().synchronize()
            self.stream = cp.cuda.Stream(non_blocking=True)
            with self.stream:
                self.stream.begin_capture()
                self._cycle_into()
                self.graph = self.stream.end_capture()
        self.work[0][0][...] = rhs
        self.graph.launch(cp.cuda.get_current_stream())
        return self.work[0][1]


def _pcg(matrix: Any, rhs: Any, mg: Multigrid, rtol: float) -> tuple[Any, int, float]:
    solution = cp.zeros_like(rhs)
    residual = rhs.copy()
    z = mg.apply(residual)
    direction = z.copy()
    rz = cp.sum(residual * z, axis=0)
    rhs_norm = cp.linalg.norm(rhs, axis=0)
    tiny = cp.finfo(cp.float64).tiny
    for iteration in range(1, 1501):
        product = matrix @ direction
        alpha = rz / cp.maximum(cp.sum(direction * product, axis=0), tiny)
        solution += direction * alpha
        residual -= product * alpha
        if iteration % 5 == 0:
            error = float(cp.max(cp.linalg.norm(residual, axis=0) / rhs_norm))
            if not math.isfinite(error):
                raise RuntimeError("GPU PCG produced a non-finite residual")
            if error < rtol:
                # Verify a fresh residual; recursive CG residuals can drift.
                actual = float(cp.max(cp.linalg.norm(rhs - matrix @ solution, axis=0) / rhs_norm))
                if actual < rtol * 2:
                    return solution, iteration, actual
        z = mg.apply(residual)
        next_rz = cp.sum(residual * z, axis=0)
        direction = z + direction * (next_rz / cp.maximum(rz, tiny))
        rz = next_rz
    raise RuntimeError(f"GPU PCG did not converge in {iteration} iterations (residual {error})")


def solve_cut(
    cut: Cut,
    nx: int = 500,
    ny: int = 200,
    *,
    rtol: float = 1e-11,
    diagnostics: dict[str, Any] | None = None,
) -> fields.CutParams | None:
    """Same grid and labels as fields.solve_cut; fail explicitly on nonconvergence."""
    plane = cut.plane_below or cut.plane_above
    cs, ti = fields.select_conductors(cut)
    if not plane and len(cs) == 1:
        return None
    h, t, er = cut.h, cut.t, cut.er
    edges = sorted(e for c in cs for e in (c.offset - c.width / 2, c.offset + c.width / 2))
    margin = max(8 * cs[ti].width, 6 * h)
    dx = min(min(c.width for c in cs), h) / 32 * 500 / nx
    fine = fields._fine([edges[0] - h, *edges, edges[-1] + h], dx)
    if len(fine) > 1.5 * nx:
        dx *= len(fine) / (1.5 * nx)
        fine = fields._fine([edges[0] - h, *edges, edges[-1] + h], dx)
    n_out = max(12, nx // 8)
    x = np.concatenate(
        [
            -fields._tail(-fine[0], fine[1] - fine[0], n_out, -(edges[0] - margin))[::-1],
            fine,
            fields._tail(fine[-1], fine[-1] - fine[-2], n_out, edges[-1] + margin),
        ]
    )
    up = fields._axis([0.0, h, h + t], ny // 2, ny - ny // 2, h + t + 8 * h)
    y = (
        up
        if plane
        else np.concatenate([-fields._tail(0.0, up[1] - up[0], ny // 4, 8 * h)[::-1], up])
    )
    xx, yy = cp.meshgrid(cp.asarray(x), cp.asarray(y), indexing="ij")
    on_layer = (yy >= h - fields.TOL) & (yy <= h + t + fields.TOL)
    masks = cp.stack(
        [
            on_layer
            & (xx >= c.offset - c.width / 2 - fields.TOL)
            & (xx <= c.offset + c.width / 2 + fields.TOL)
            for c in cs
        ]
    )
    fixed = cp.any(masks, axis=0)
    if plane:
        fixed |= yy == 0
    free = ~fixed.ravel()
    given = masks.reshape(len(cs), -1).T.astype(cp.float64)
    masks_flat = masks.reshape(len(cs), -1).astype(cp.float64)
    del xx, yy, masks
    gx_axis, gy_axis = cp.diff(cp.asarray(x)), cp.diff(cp.asarray(y))
    idx = cp.arange(len(x) * len(y)).reshape(len(x), len(y))
    a = cp.concatenate([idx[:-1, :].ravel(), idx[:, :-1].ravel()])
    b = cp.concatenate([idx[1:, :].ravel(), idx[:, 1:].ravel()])
    rows, cols = cp.concatenate([a, b, a, b]), cp.concatenate([a, b, b, a])
    yc = cp.asarray((y[:-1] + y[1:]) / 2)
    mats = []
    records = []
    for dielectric in (er, 1.0):
        if diagnostics is not None:
            cp.cuda.get_current_stream().synchronize()
        start = time.perf_counter()
        eps = cp.broadcast_to(
            cp.where((yc >= 0) & (yc < h), dielectric, 1.0), (len(x) - 1, len(y) - 1)
        )
        ex, ey = eps * gy_axis, eps * gx_axis[:, None]
        gx = (cp.pad(ex, ((0, 0), (1, 0))) + cp.pad(ex, ((0, 0), (0, 1)))) / 2 / gx_axis[:, None]
        gy = (cp.pad(ey, ((1, 0), (0, 0))) + cp.pad(ey, ((0, 1), (0, 0)))) / 2 / gy_axis
        conductance = cp.concatenate([gx.ravel(), gy.ravel()])
        values = cp.concatenate([conductance, conductance, -conductance, -conductance])
        n = len(x) * len(y)
        stiffness = sp.coo_matrix((values, (rows, cols)), shape=(n, n)).tocsr()
        constrained = stiffness.copy()
        csr_rows = cp.repeat(cp.arange(n), cp.diff(constrained.indptr))
        constrained.data *= free[csr_rows] & free[constrained.indices]
        constrained = constrained + sp.diags((~free).astype(cp.float64))
        constrained = constrained.tocsr()
        # Do not use CSR.eliminate_zeros here: cuSPARSE compression in the
        # tested CUDA 12.8 / CuPy 14.2 combination corrupts negative entries.
        # Explicit zeros are mathematically harmless and preserve the operator.
        rhs = -(stiffness @ given) * free[:, None]
        if diagnostics is not None:
            cp.cuda.get_current_stream().synchronize()
        assembled = time.perf_counter()
        mg = Multigrid(constrained, x, y, free)
        if diagnostics is not None:
            cp.cuda.get_current_stream().synchronize()
        prepared = time.perf_counter()
        solution, iterations, residual = _pcg(constrained, rhs, mg, rtol)
        if diagnostics is not None:
            cp.cuda.get_current_stream().synchronize()
        solved = time.perf_counter()
        charge = stiffness @ (solution + given)
        capacitance = cp.asnumpy((masks_flat @ charge) * fields.EPS0 * 1e12)
        mats.append(tuple(tuple(float(v) for v in row) for row in capacitance))
        records.append(
            {
                "iterations": iterations,
                "residual": residual,
                "levels": len(mg.levels),
                "assembly_seconds": assembled - start,
                "setup_seconds": prepared - assembled,
                "solve_seconds": solved - prepared,
                "charge_seconds": time.perf_counter() - solved,
            }
        )
    c, c0 = mats
    z0, eps_eff, c11, coupling = fields.derive(c, c0, ti, tuple(c.net for c in cs))
    if diagnostics is not None:
        diagnostics.update({"nodes": len(x) * len(y), "solves": records})
    return fields.CutParams(z0, eps_eff, c11, coupling, c, c0, ti, len(cs), plane)
