"""The learned physics surrogate: predicts what the field solver would say, in microseconds.

Trained on `pipeline.data` shards (geometry -> solver LineParams) by scripts/train_surrogate.py.
Two models: single traces (w, h, t, er -> z0) and coupled pairs (w, h, t, er, s -> z_odd,
z_even). Inputs are log-scaled where the physics is log-like. Serves the judge through the same
two calls as `pipeline.physics.Formula`."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

SINGLE_FEATURES = ("log_w", "log_h", "log_t", "er", "log_w_over_h")
PAIR_FEATURES = ("log_w", "log_h", "log_t", "er", "log_w_over_h", "log_s", "log_s_over_h")
SINGLE_TARGETS = ("z0",)
PAIR_TARGETS = ("z_odd", "z_even")


def single_features(w: float, h: float, t: float, er: float) -> list[float]:
    return [math.log(w), math.log(h), math.log(t), er, math.log(w / h)]


def pair_features(w: float, h: float, t: float, er: float, s: float) -> list[float]:
    return [*single_features(w, h, t, er), math.log(s), math.log(s / h)]


class Learned:
    """Physics provider backed by a trained artifact (see scripts/train_surrogate.py)."""

    name = "learned-fd"

    def __init__(self, artifact: dict[str, Any]) -> None:
        if artifact.get("kind") != "surrogate":
            raise ValueError("artifact is not a physics surrogate")
        self.version = str(artifact["version"])
        self.single = artifact["models"]["single"]
        self.pair = artifact["models"]["pair"]

    def z0(self, w: float, h: float, t: float, er: float, inner: bool) -> float:
        # ponytail: the solver models one ground plane; stripline gets the microstrip answer
        return float(self.single.predict(np.array([single_features(w, h, t, er)]))[0])

    def zdiff(self, w: float, s: float, h: float, t: float, er: float, inner: bool) -> float:
        z_odd, _ = self.pair.predict(np.array([pair_features(w, h, t, er, s)]))[0]
        return float(2 * z_odd)
