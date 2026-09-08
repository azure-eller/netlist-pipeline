"""Validation of the 2D field solver against the Hammerstad-Jensen closed form."""

import math

import pytest

from pipeline.fields import Geometry, solve

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
