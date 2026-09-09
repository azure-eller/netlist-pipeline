"""Validation of the 2D field solver against the Hammerstad-Jensen closed form."""

import math

import pytest
from scipy.special import ellipk

from pipeline.fields import C_LIGHT, Geometry, select_conductors, solve, solve_cut
from pipeline.windows import Conductor, Cut

ETA0 = 376.730313668
T, ER = 0.035, 4.5


def _z01(u: float) -> float:
    """Air-filled microstrip impedance, Hammerstad-Jensen 1980."""
    f = 6 + (2 * math.pi - 6) * math.exp(-((30.666 / u) ** 0.7528))
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))


def _eps_eff(u: float, er: float) -> float:
    a = 1 + math.log((u**4 + (u / 52) ** 2) / (u**4 + 0.432)) / 49
    a += math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return float((er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b))


def hammerstad_jensen(w: float, h: float, t: float, er: float) -> tuple[float, float]:
    """(z0, eps_eff) with the Hammerstad-Jensen finite-thickness correction."""
    u, th = w / h, t / h
    du1 = th / math.pi * math.log(1 + 4 * math.e / (th / math.tanh(math.sqrt(6.517 * u)) ** 2))
    dur = (1 + 1 / math.cosh(math.sqrt(er - 1))) / 2 * du1
    u1, ur = u + du1, u + dur
    return _z01(ur) / math.sqrt(_eps_eff(ur, er)), _eps_eff(ur, er) * (_z01(u1) / _z01(ur)) ** 2


def test_single_microstrip_matches_closed_form() -> None:
    print("\n  w/h   z0 solver  z0 H-J   dz%   eps_eff solver  eps_eff H-J   de%")
    for u in (0.5, 1.0, 2.0, 3.0):
        p = solve(Geometry(u, 1.0, T, ER))
        z, e = hammerstad_jensen(u, 1.0, T, ER)
        dz, de = p.z0 / z - 1, p.eps_eff / e - 1
        print(f"  {u:<5} {p.z0:8.2f} {z:8.2f} {dz:+6.1%} {p.eps_eff:12.3f} {e:12.3f} {de:+8.1%}")
        assert abs(dz) < 0.06, (u, p.z0, z)
        assert abs(de) < 0.05, (u, p.eps_eff, e)


def test_air_line_has_unit_eps_eff_and_z0_falls_with_width() -> None:
    ps = [solve(Geometry(w, 1.0, T, 1.0)) for w in (0.5, 1.0, 2.0)]
    assert all(abs(p.eps_eff - 1) < 0.02 for p in ps)
    assert ps[0].z0 > ps[1].z0 > ps[2].z0


def test_coupled_pair_modes() -> None:
    ps = {s: solve(Geometry(1.0, 1.0, T, ER, s)) for s in (0.5, 1.0, 2.0, 4.0)}
    single = solve(Geometry(1.0, 1.0, T, ER))
    ks = [p.coupling for p in ps.values() if p.coupling is not None]
    assert len(ks) == 4
    assert all(p.z_even > p.z_odd for p in ps.values())  # type: ignore[operator]
    assert ks == sorted(ks, reverse=True) and len(set(ks)) == len(ks)
    assert ps[4.0].coupling < 0.1  # type: ignore[operator]
    assert abs(ps[4.0].z_diff / (2 * single.z0) - 1) < 0.15  # type: ignore[operator]


def test_grid_refinement_is_converged() -> None:
    coarse, fine = solve(Geometry(1.0, 1.0, T, ER), 200, 100), solve(Geometry(1.0, 1.0, T, ER))
    assert abs(coarse.z0 / fine.z0 - 1) < 0.03


@pytest.mark.parametrize("bad", [(0, 1, T, ER), (1, -1, T, ER), (1, 1, 0, ER), (1, 1, T, 0.5)])
def test_invalid_geometry_raises(bad: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValueError):
        solve(Geometry(*bad))
    with pytest.raises(ValueError):
        solve(Geometry(1, 1, T, ER, s=0))


# ---- the general cut solver (docs/FACTORY.md step 2) ----


def _cut(conductors: list[tuple[float, float, str]], plane: bool, h: float = 1.51) -> Cut:
    cs = tuple(Conductor(o, w, n) for o, w, n in sorted(conductors))
    return Cut(0.0, 0.0, "F.Cu", cs, plane, False, h, T, ER)


def test_cut_with_one_conductor_matches_solve() -> None:
    for w in (0.3, 1.51, 3.0):
        p = solve_cut(_cut([(0.0, w, "SIG")], plane=True))
        assert p is not None and p.n_conductors == 1 and p.plane and p.coupling == ()
        ref = solve(Geometry(w, 1.51, T, ER))
        assert abs(p.z0 / ref.z0 - 1) < 0.005, (w, p.z0, ref.z0)
        assert abs(p.eps_eff / ref.eps_eff - 1) < 0.01


def test_cut_pair_matches_solve_modes() -> None:
    w, s = 0.3, 0.3
    p = solve_cut(_cut([(0.0, w, "SIG"), (w + s, w, "B")], plane=True))
    assert p is not None
    ref = solve(Geometry(w, 1.51, T, ER, s))
    (c, c0), i = (p.cmatrix, p.cmatrix_air), p.target
    # even mode: both at +1 V -> C11 + C12; odd: +1/-1 -> C11 - C12 (C12 < 0)
    for sign, z_ref in ((+1, ref.z_even), (-1, ref.z_odd)):
        ce, ce0 = c[i][i] + sign * c[i][1 - i], c0[i][i] + sign * c0[i][1 - i]
        z = 1 / (C_LIGHT * math.sqrt(ce * ce0) * 1e-12)
        assert z_ref is not None and abs(z / z_ref - 1) < 0.01, (sign, z, z_ref)


def test_cut_without_plane_matches_coplanar_waveguide() -> None:
    """Centre strip w, gap s to a wide ground each side, no plane, thick substrate: the
    conformal-map closed form Z = 30 pi / sqrt(eps_eff) K(k') / K(k), k = w / (w + 2s),
    eps_eff = (er + 1) / 2, with Gupta's finite-thickness correction (the field solver has
    real copper thickness; at t/s = 7 % it is worth 8 %)."""
    w, s, wg = 0.5, 0.5, 15.0
    h = 10 * w  # thick enough that the substrate reads as infinite
    p = solve_cut(
        _cut(
            [(0.0, w, "SIG"), (w / 2 + s + wg / 2, wg, "GND"), (-(w / 2 + s + wg / 2), wg, "GND")],
            plane=False,
            h=h,
        )
    )
    assert p is not None and not p.plane

    def ratio(k: float) -> float:  # K(k) / K(k')
        return float(ellipk(k * k) / ellipk(1 - k * k))

    d = 1.25 * T / math.pi * (1 + math.log(4 * math.pi * w / T))
    k0, k = w / (w + 2 * s), (w + d) / (w + d + 2 * (s - d))
    ee = (ER + 1) / 2
    ee -= 0.7 * (ee - 1) * (T / s) / (ratio(k0) + 0.7 * T / s)
    z = 30 * math.pi / math.sqrt(ee) / ratio(k)
    assert abs(p.z0 / z - 1) < 0.03, (p.z0, z)
    assert abs(p.eps_eff / ee - 1) < 0.03, (p.eps_eff, ee)


def test_cut_capacitance_matrix_is_symmetric_and_charge_agrees_with_energy() -> None:
    p = solve_cut(_cut([(0.0, 0.3, "SIG"), (0.7, 0.5, "A"), (-1.2, 0.2, "B")], plane=True))
    assert p is not None
    c = p.cmatrix
    for i in range(3):
        for j in range(3):
            assert abs(c[i][j] - c[j][i]) < 1e-3 * abs(c[i][i]), (i, j)
        assert c[i][i] > 0 and all(c[i][j] < 0 for j in range(3) if j != i)
    # 2W = phi^T K phi for the target driven alone must equal C11 from its charge
    g = Geometry(0.3, 1.51, T, ER)
    assert (
        abs(solve_cut(_cut([(0.0, 0.3, "SIG")], plane=True)).c11_pf_per_m / solve(g).c_pf_per_m - 1)
        < 0.005
    )  # type: ignore[union-attr]


def test_cut_far_neighbour_does_not_matter_and_coupling_falls_with_gap() -> None:
    alone = solve_cut(_cut([(0.0, 0.3, "SIG")], plane=True))
    far = solve_cut(_cut([(0.0, 0.3, "SIG"), (0.15 + 3.0 + 0.15, 0.3, "A")], plane=True))
    assert alone is not None and far is not None
    assert abs(far.z0 / alone.z0 - 1) < 0.01
    ks = [
        solve_cut(_cut([(0.0, 0.3, "SIG"), (0.3 + gap, 0.3, "A")], plane=True)).coupling[0][1]  # type: ignore[union-attr]
        for gap in (0.1, 0.3, 1.0, 3.0)
    ]
    assert ks == sorted(ks, reverse=True) and 0 < ks[-1] < ks[0] < 1, ks


def test_cut_grid_is_converged() -> None:
    cut = _cut([(0.0, 0.3, "SIG"), (0.6, 0.3, "A")], plane=True)
    a, b = solve_cut(cut), solve_cut(cut, nx=1000, ny=400)
    assert a is not None and b is not None
    assert abs(a.z0 / b.z0 - 1) < 0.005, (a.z0, b.z0)
    assert abs(a.coupling[0][1] - b.coupling[0][1]) < 0.01


def test_cut_drops_an_overlapping_neighbour_and_survives_touching_edges() -> None:
    # GND at 3.716 (span 3.575-3.858) and JP1 at 4.002 (3.807-4.197) overlap: real cut, board 19
    cut = _cut(
        [(0.0, 0.2, "SIG"), (3.716, 0.283, "GND"), (4.002, 0.389, "JP1"), (4.232, 0.849, "V")],
        plane=False,
    )
    cs, ti = select_conductors(cut)
    assert [c.net for c in cs] == ["SIG", "GND"] and ti == 0
    p = solve_cut(cut)
    assert p is not None and math.isfinite(p.z0) and p.n_conductors == 2
    touching = _cut([(0.0, 0.3, "SIG"), (0.4, 0.5, "A")], plane=True)  # edges meet at 0.15
    q = solve_cut(touching)
    assert q is not None and math.isfinite(q.z0) and q.n_conductors == 2
