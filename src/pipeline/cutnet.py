"""The cut model: a small transformer over the conductors of one cut that predicts the two
capacitance matrices (with the dielectric, with air) the field solver would compute, and
answers the judge through `fields.derive` exactly as the solver does (docs/FACTORY.md step 6).

Each conductor is a token of TOKEN_FEATURES numbers. Attention between tokens carries a bias
from the pair's edge gap, so every layer sees relative geometry directly. The diagonal C_ii
comes from a head on token i; the off-diagonal C_ij from a head on (h_i + h_j, |h_i - h_j|),
symmetric in i and j by construction. Trained by scripts/train_cutnet.py; served as a
physics provider through `Learned`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from torch import Tensor, nn

from pipeline import fields, windows

FEATURE_VERSION = "cut-features-0.1"  # bump when featurise() changes
MAX_TOKENS = fields.MAX_NEIGHBOURS + 1
TOKEN_FEATURES = 9
PAIR_FEATURES = 2
EPS_MM = 0.01


def featurise(cut: windows.Cut) -> tuple[np.ndarray, np.ndarray, int, tuple[str | None, ...]]:
    """(tokens [K, 9], pairs [K, K, 2], target index, nets) for the conductors the solver keeps,
    in the solver's order, so predicted and solved matrices line up."""
    cs, ti = fields.select_conductors(cut)
    plane = float(cut.plane_below or cut.plane_above)
    glob = [math.log(cut.h), math.log(cut.t), cut.er, plane]
    tnet = cs[ti].net
    tokens = np.array(
        [
            [
                math.log(c.width),
                c.offset,
                math.log(abs(c.offset) + EPS_MM),
                float(i == ti),
                float(c.net == tnet and c.net is not None),
                *glob,
            ]
            for i, c in enumerate(cs)
        ]
    )
    pairs = np.zeros((len(cs), len(cs), PAIR_FEATURES))
    for i, a in enumerate(cs):
        for j, b in enumerate(cs):
            gap = max(0.0, abs(a.offset - b.offset) - (a.width + b.width) / 2)
            pairs[i, j] = (math.log(gap + EPS_MM), float(np.sign(b.offset - a.offset)))
    return tokens, pairs, ti, tuple(c.net for c in cs)


@dataclass
class Batch:
    tokens: Tensor  # [B, K, 9]
    pairs: Tensor  # [B, K, K, 2]
    mask: Tensor  # [B, K] True where a token is real
    target: Tensor  # [B] target index


def batchify(cuts: list[windows.Cut], device: torch.device | str = "cpu") -> Batch:
    n = len(cuts)
    tokens = np.zeros((n, MAX_TOKENS, TOKEN_FEATURES))
    pairs = np.zeros((n, MAX_TOKENS, MAX_TOKENS, PAIR_FEATURES))
    mask = np.zeros((n, MAX_TOKENS), dtype=bool)
    target = np.zeros(n, dtype=np.int64)
    for b, cut in enumerate(cuts):
        t, p, ti, _ = featurise(cut)
        k = len(t)
        tokens[b, :k], pairs[b, :k, :k], mask[b, :k], target[b] = t, p, True, ti
    return Batch(
        torch.tensor(tokens, dtype=torch.float32, device=device),
        torch.tensor(pairs, dtype=torch.float32, device=device),
        torch.tensor(mask, device=device),
        torch.tensor(target, device=device),
    )


def targets(params: list[fields.CutParams]) -> tuple[Tensor, Tensor]:
    """log|C| [B, K, K, 2] (dielectric, air) and the valid-entry mask [B, K, K]."""
    n = len(params)
    y = np.zeros((n, MAX_TOKENS, MAX_TOKENS, 2))
    valid = np.zeros((n, MAX_TOKENS, MAX_TOKENS), dtype=bool)
    for b, p in enumerate(params):
        k = p.n_conductors
        y[b, :k, :k, 0] = np.log(np.abs(np.array(p.cmatrix)))
        y[b, :k, :k, 1] = np.log(np.abs(np.array(p.cmatrix_air)))
        valid[b, :k, :k] = True
    return torch.tensor(y, dtype=torch.float32), torch.tensor(valid)


class _Attention(nn.Module):
    """Multi-head self-attention with an additive per-pair bias and a padding mask."""

    def __init__(self, d: int, heads: int) -> None:
        super().__init__()
        self.h, self.dh = heads, d // heads
        self.qkv = nn.Linear(d, 3 * d)
        self.out = nn.Linear(d, d)

    def forward(self, x: Tensor, bias: Tensor, mask: Tensor) -> Tensor:
        b, k, _ = x.shape
        q, kk, v = self.qkv(x).view(b, k, 3, self.h, self.dh).unbind(2)
        scores = torch.einsum("bihd,bjhd->bhij", q, kk) / math.sqrt(self.dh) + bias
        scores = scores.masked_fill(~mask[:, None, None, :], float("-inf"))
        att = torch.softmax(scores, dim=-1)
        y = torch.einsum("bhij,bjhd->bihd", att, v).reshape(b, k, self.h * self.dh)
        out: Tensor = self.out(y)
        return out


class CutNet(nn.Module):
    def __init__(self, d: int = 64, heads: int = 4, layers: int = 2, ff: int = 128) -> None:
        super().__init__()
        self.embed = nn.Sequential(nn.Linear(TOKEN_FEATURES, d), nn.GELU(), nn.Linear(d, d))
        self.pair_bias = nn.Linear(PAIR_FEATURES, heads)
        self.norm1 = nn.ModuleList(nn.LayerNorm(d) for _ in range(layers))
        self.att = nn.ModuleList(_Attention(d, heads) for _ in range(layers))
        self.norm2 = nn.ModuleList(nn.LayerNorm(d) for _ in range(layers))
        self.ff = nn.ModuleList(
            nn.Sequential(nn.Linear(d, ff), nn.GELU(), nn.Linear(ff, d)) for _ in range(layers)
        )
        self.diag = nn.Sequential(nn.Linear(d, 32), nn.GELU(), nn.Linear(32, 2))
        self.pair = nn.Sequential(nn.Linear(2 * d, 64), nn.GELU(), nn.Linear(64, 2))

    def forward(self, batch: Batch) -> Tensor:
        """log|C| [B, K, K, 2]: diagonal from `diag`, off-diagonal symmetric from `pair`."""
        x = self.embed(batch.tokens)
        bias = self.pair_bias(batch.pairs).permute(0, 3, 1, 2)  # [B, heads, K, K]
        for n1, att, n2, ff in zip(self.norm1, self.att, self.norm2, self.ff, strict=True):
            x = x + att(n1(x), bias, batch.mask)
            x = x + ff(n2(x))
        hi, hj = x[:, :, None, :], x[:, None, :, :]
        off = self.pair(torch.cat([hi + hj, (hi - hj).abs()], dim=-1))  # [B, K, K, 2]
        eye = torch.eye(x.shape[1], device=x.device, dtype=torch.bool)[None, :, :, None]
        return torch.where(eye, self.diag(x)[:, :, None, :].expand_as(off), off)


def loss(
    pred: Tensor,
    y: Tensor,
    valid: Tensor,
    y_mean: Tensor | None = None,
    y_std: Tensor | None = None,
    conservation: float = 0.1,
) -> Tensor:
    """MSE on standardised log|C| over valid entries, plus relu(sum_j |C_ij| - C_ii) / C_ii per
    real row on the de-standardised matrices: a Maxwell capacitance matrix is diagonally
    dominant, the plane (or the far field) taking the rest of each conductor's charge."""
    v = valid[..., None].expand_as(pred)
    mse = ((pred - y) ** 2)[v].mean()
    if conservation == 0:
        return mse
    logc = pred if y_mean is None or y_std is None else pred * y_std + y_mean
    c = logc.exp()
    k = pred.shape[1]
    eye = torch.eye(k, device=pred.device, dtype=torch.bool)[None, :, :, None]
    offsum = torch.where(valid[..., None] & ~eye, c, torch.zeros_like(c)).sum(dim=2)  # [B,K,2]
    diag = c[:, torch.arange(k), torch.arange(k)]  # [B, K, 2]
    rows = valid[:, torch.arange(k), torch.arange(k)][..., None].expand_as(diag)
    pen = (torch.relu(offsum - diag) / diag)[rows].mean()
    return mse + conservation * pen


def matrices(pred: Tensor, n: int) -> tuple[list[list[float]], list[list[float]]]:
    """The two matrices (pF/m) of one prediction, signs restored: diagonal +, off-diagonal -."""
    c = pred[:n, :n].exp().detach().cpu().numpy()
    sign = -np.ones((n, n)) + 2 * np.eye(n)
    return (c[..., 0] * sign).tolist(), (c[..., 1] * sign).tolist()


class Learned:
    """Physics provider backed by a trained CutNet artifact (scripts/train_cutnet.py)."""

    name = "learned-cut"

    def __init__(self, artifact: dict[str, Any]) -> None:
        if artifact.get("kind") != "cutnet":
            raise ValueError("artifact is not a cut model")
        if artifact["feature_version"] != FEATURE_VERSION:
            raise ValueError(
                f"artifact encodes {artifact['feature_version']!r}, this code computes "
                f"{FEATURE_VERSION!r}"
            )
        self.version = str(artifact["version"])
        self.model = CutNet(**artifact["config"])
        self.model.load_state_dict(artifact["state_dict"])
        self.model.eval()
        norm = artifact["norm"]
        self.x_mean, self.x_std = torch.tensor(norm["x_mean"]), torch.tensor(norm["x_std"])
        self.y_mean, self.y_std = torch.tensor(norm["y_mean"]), torch.tensor(norm["y_std"])

    def predict(self, cut: windows.Cut) -> fields.CutParams | None:
        plane = cut.plane_below or cut.plane_above
        _, _, ti, nets = featurise(cut)
        if not plane and len(nets) == 1:  # same rule as the solver: nothing to measure against
            return None
        batch = batchify([cut])
        batch.tokens = (batch.tokens - self.x_mean) / self.x_std
        with torch.no_grad():
            pred = self.model(batch)[0] * self.y_std + self.y_mean
        c, c0 = matrices(pred, len(nets))
        z0, eps_eff, c11, coupling = fields.derive(c, c0, ti, nets)
        return fields.CutParams(
            z0,
            eps_eff,
            c11,
            coupling,
            tuple(tuple(r) for r in c),
            tuple(tuple(r) for r in c0),
            ti,
            len(nets),
            plane,
        )

    def cut(self, c: windows.Cut) -> float | None:
        p = self.predict(c)
        return p.z0 if p else None

    def z0(self, w: float, h: float, t: float, er: float, inner: bool) -> float:
        # ponytail: one plane, like the solver; stripline gets the microstrip answer
        return float(self.cut(_single(w, h, t, er)) or 0.0)

    def zdiff(self, w: float, s: float, h: float, t: float, er: float, inner: bool) -> float:
        """Symmetric pair over a plane: z_diff ~ 2 z0 / (1 + k) with k the predicted coupling
        (exact when the air-line coupling equals the dielectric one; a few percent otherwise)."""
        p = self.predict(_pair(w, s, h, t, er))
        assert p is not None
        return 2 * p.z0 / (1 + p.coupling[0][1])


def _single(w: float, h: float, t: float, er: float) -> windows.Cut:
    return windows.Cut(0.0, 0.0, "F.Cu", (windows.Conductor(0.0, w, "T"),), True, False, h, t, er)


def _pair(w: float, s: float, h: float, t: float, er: float) -> windows.Cut:
    cs = (windows.Conductor(0.0, w, "T"), windows.Conductor(w + s, w, "P"))
    return windows.Cut(0.0, 0.0, "F.Cu", cs, True, False, h, t, er)
