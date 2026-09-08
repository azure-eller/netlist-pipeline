# Fields oracle: 2D quasi-static FD solver for microstrip

**What it is.** `pipeline.fields.solve(Geometry) -> LineParams` (`SOLVER_VERSION = "fd2d-0.1"`):
a deterministic electrostatic finite-difference solver for a microstrip cross-section, single
trace or edge-coupled pair. It is the slow oracle a learned surrogate is trained on and gated
against; it is not called from the pipeline stages. stdlib + numpy + scipy only.

**Equations.** `div(eps grad phi) = 0` on the cross-section. Capacitance per unit length from the
discrete field energy, `C = 2W / V^2` with `2W = phi^T K phi` (K is the assembled stencil, so
the energy is exactly consistent with the solve; no contour integration). Each geometry is
solved twice, with the dielectric (`C`) and with `er = 1` everywhere (`C0`), then

    eps_eff = C / C0        z0 = 1 / (c * sqrt(C * C0))

For a pair the even mode (+1 V, +1 V) and odd mode (+1 V, -1 V) each give a per-line
capacitance (total energy over two lines) and hence `z_even`, `z_odd`; `z_diff = 2 z_odd`;
`coupling = (z_even - z_odd) / (z_even + z_odd)`. The pair's `z0` / `eps_eff` / `c_pf_per_m` are
for one line driven with the other grounded, from `C11 = (C_even + C_odd) / 2` (the cross term
of the two modes vanishes by symmetry, so no third solve is needed).

**Boundaries and grid.** Ground plane at `y = 0` (phi = 0); dielectric slab `0 < y < h` with
`er`, air above; conductor(s) of width `w` and thickness `t` on top of the slab as Dirichlet
node regions. Far-field boundaries are Neumann (zero normal flux, the natural finite-volume
boundary): `max(8w, 6h)` beyond the outer trace edge on each side, `8h` above the trace top.
Tensor grid, node-centred potentials, cell-centred permittivity; node lines land exactly on
the ground plane, the dielectric interface, and every conductor edge, so no interface is
smeared. Face conductance = length-weighted mean of the permittivity of the two cells the face
straddles, divided by node distance (this is the exact finite-volume flux when interfaces lie
on cell boundaries, which they do here by construction; a harmonic mean would be needed only
if a face cut through an interface). Grid: uniform cells over the conductors plus one `h` of
margin (60% of the nodes on each axis), geometrically stretched cells out to the box edge.
`nx, ny` are nominal (default 400 x 200, about 80k unknowns). Linear system: `scipy.sparse`
COO -> CSC, direct solve with `scipy.sparse.linalg.spsolve` (SuperLU); both modes of a pair are
solved as a two-column right-hand side with one factorisation. `solve` is `lru_cache`d.

**Validation** (`tests/test_fields.py`; closed form is Hammerstad-Jensen 1980 with the
finite-thickness correction, implemented in the test). `t = 0.035`, `h = 1.0`, `er = 4.5`:

| w/h | z0 solver | z0 H-J | dz | eps_eff solver | eps_eff H-J | de |
|---|---|---|---|---|---|---|
| 0.5 | 92.26 | 91.74 | +0.6% | 3.087 | 3.031 | +1.9% |
| 1.0 | 69.24 | 68.96 | +0.4% | 3.218 | 3.178 | +1.3% |
| 2.0 | 47.72 | 47.59 | +0.3% | 3.403 | 3.376 | +0.8% |
| 3.0 | 36.86 | 36.74 | +0.3% | 3.545 | 3.511 | +1.0% |

Gates: z0 within 6%, eps_eff within 5%. Coupled pair, `w = h = 1`, same t and er:

| s | z_even | z_odd | z_diff | coupling | z0 (other grounded) |
|---|---|---|---|---|---|
| 0.5 | 85.11 | 50.44 | 100.88 | 0.256 | 63.25 |
| 1.0 | 79.25 | 58.10 | 116.21 | 0.154 | 66.97 |
| 2.0 | 73.88 | 64.02 | 128.03 | 0.072 | 68.55 |
| 4.0 | 70.77 | 67.04 | 134.07 | 0.027 | 68.84 |

`z_even > z_odd` throughout, coupling strictly decreasing, 0.027 at `s = 4`, `z_diff(s=4)` is
3% below `2 z0` of the single line (138.5). `er = 1` gives `eps_eff = 1` exactly (identical
matrices) and z0 falling with width. Refining from (200, 100) to (400, 200) moves z0 for
`w = h = 1` from 69.06 to 69.24 (0.3%). Invalid geometry raises `ValueError` from `Geometry`.

**Timing.** Default resolution, one Geometry (two spsolve calls), cache cleared each run,
median of 5: 0.36 s single trace, 0.39 s pair. The test file (12 distinct solves) runs in
about 4.3 s.

**Known limits.** Quasi-static (TEM approximation; no dispersion, valid where `h` is well
below a wavelength). Lossless: no conductor or dielectric loss, so no attenuation or
frequency-dependent R and G. Finite box with Neumann walls; the 8h / max(8w, 6h) margins keep
the box effect below the grid error at the tested w/h; w/h far outside 0.5 to 3 is untested.
Rectangular
conductor cross-section (no etch taper), no solder mask, single dielectric layer, ground plane
assumed infinite and perfect. Grid resolution near the conductor corners is fixed by the
default nx, ny; the field singularity there converges slowly, which is where the remaining
+0.3 to +0.6% on z0 comes from.
