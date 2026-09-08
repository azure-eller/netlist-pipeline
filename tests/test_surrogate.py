"""Physics providers: the closed form is the old judge exactly; the surrogate serves a fitted
model through the same two calls."""

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor

from pipeline import judge, physics, surrogate


def test_formula_provider_matches_the_reference_formulas() -> None:
    f = physics.FORMULA
    assert f.z0(0.3, 1.51, 0.035, 4.5, inner=False) == judge.z0_microstrip(0.3, 1.51, 0.035, 4.5)
    assert f.z0(0.3, 1.51, 0.035, 4.5, inner=True) == judge.z0_stripline(0.3, 1.51, 0.035, 4.5)
    z = judge.z0_microstrip(0.3, 1.51, 0.035, 4.5)
    assert f.zdiff(0.3, 0.6, 1.51, 0.035, 4.5, inner=False) == judge.zdiff_microstrip(z, 0.6, 1.51)
    assert (f.name, f.version) == ("rules", "0.1.0")


def test_learned_provider_serves_a_fitted_artifact() -> None:
    rng = np.random.default_rng(0)
    xs = rng.uniform([-2, -2, -4, 3, -2], [1, 0.5, -2.5, 5, 2], size=(200, 5))
    single = GradientBoostingRegressor(n_estimators=20, random_state=0).fit(xs, 50 - 10 * xs[:, 4])
    xp = rng.uniform([-2, -2, -4, 3, -2, -2, -2], [1, 0.5, -2.5, 5, 2, 1, 2], size=(200, 7))
    pair = MultiOutputRegressor(GradientBoostingRegressor(n_estimators=20, random_state=0)).fit(
        xp, np.stack([40 + 5 * xp[:, 5], 60 - 5 * xp[:, 5]], axis=1)
    )
    p = surrogate.Learned(
        {"kind": "surrogate", "version": "vtest", "models": {"single": single, "pair": pair}}
    )
    z = p.z0(0.3, 1.0, 0.035, 4.5, inner=False)
    zd = p.zdiff(0.3, 0.3, 1.0, 0.035, 4.5, inner=False)
    assert isinstance(z, float) and 20 < z < 90
    assert isinstance(zd, float) and zd > 0
    assert (p.name, p.version) == ("learned-fd", "vtest")
