import math

from sklearn.ensemble import GradientBoostingRegressor

from pipeline import learned
from pipeline.models import Constraints
from tests.test_judge import board, fp, netlist, seg

C = Constraints(classes={"+3V3": "power", "GND": "power", "CLK": "high_speed"})
NL = netlist({"+3V3": ["U1.1", "C1.1"], "GND": ["U1.2", "C1.2"], "CLK": ["U1.3", "R1.1"]})
B = board(
    [
        fp("U1", 0, 0, ["+3V3", "GND", "CLK"]),
        fp("C1", 15, 0, ["+3V3", "GND"]),
        fp("R1", 2, 10, ["CLK"]),
    ],
    [seg("+3V3", 0, 0, 15, 0), seg("CLK", 2, 0, 2, 10, 0.2)],
)


def test_features_have_every_key_and_are_finite() -> None:
    f = learned.features(B, NL, C)
    assert tuple(f) == learned.FEATURES
    assert all(math.isfinite(v) for v in f.values())
    assert f["decoupling_max_mm"] == 15.0
    assert f["unrouted_fraction"] == 1 / 3  # GND has 2 pads and no copper


def test_predict_returns_float() -> None:
    rows = [list(learned.features(B, NL, C).values())] * 2
    model = GradientBoostingRegressor(n_estimators=2).fit(rows, [-1.0, -2.0])
    score, feats = learned.predict({"model": model, "feature_names": learned.FEATURES}, B, NL, C)
    assert isinstance(score, float) and math.isfinite(score)
    assert feats == learned.features(B, NL, C)
