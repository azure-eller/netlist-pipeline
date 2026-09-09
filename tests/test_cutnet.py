"""The cut model learns a planted rule and is indifferent to conductor order."""

import random

import torch

from pipeline import cutnet, fields, windows


def _planted(cut: windows.Cut) -> fields.CutParams:
    """A fake solver: C_ii = 100 / width, C_ij = -20 / (1 + gap); air = half of that."""
    cs, ti = fields.select_conductors(cut)
    n = len(cs)
    c = [[0.0] * n for _ in range(n)]
    for i, a in enumerate(cs):
        for j, b in enumerate(cs):
            if i == j:
                c[i][j] = 100.0 / a.width
            else:
                gap = abs(a.offset - b.offset) - (a.width + b.width) / 2
                c[i][j] = -20.0 / (1 + gap)
    c0 = [[v / 2 for v in row] for row in c]
    z0, eps_eff, c11, coupling = fields.derive(c, c0, ti, tuple(x.net for x in cs))
    return fields.CutParams(
        z0, eps_eff, c11, coupling, tuple(map(tuple, c)), tuple(map(tuple, c0)), ti, n, True
    )


def test_cutnet_learns_a_planted_rule_and_is_permutation_equivariant() -> None:
    torch.manual_seed(0)
    rng = random.Random(0)
    cuts = [
        windows.Cut(
            0.0,
            0.0,
            "F.Cu",
            tuple(
                sorted(
                    [windows.Conductor(0.0, rng.uniform(0.2, 1.0), "T")]
                    + [
                        windows.Conductor(s * rng.uniform(0.8, 3.0), rng.uniform(0.2, 1.0), f"N{s}")
                        for s in (-1, 1)
                        if rng.random() < 0.7
                    ],
                    key=lambda c: c.offset,
                )
            ),
            True,
            False,
            1.0,
            0.035,
            4.5,
        )
        for _ in range(400)
    ]
    params = [_planted(c) for c in cuts]
    batch = cutnet.batchify(cuts)
    y, valid = cutnet.targets(params)
    model = cutnet.CutNet(d=32, heads=2, layers=1, ff=64)
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    for _ in range(150):
        lo = cutnet.loss(model(batch), y, valid)
        opt.zero_grad()
        lo.backward()
        opt.step()
    assert float(lo) < 0.02, float(lo)
    model.eval()
    with torch.no_grad():
        pred = model(batch)
    err = (pred - y).abs()[valid[..., None].expand_as(pred)].mean()
    assert float(err) < 0.1, float(err)
    # the same cut with its conductors listed in another order gives the same matrix
    c = next(x for x in cuts if len(x.conductors) == 3)
    shuffled = windows.Cut(
        **{**c.__dict__, "conductors": (c.conductors[2], c.conductors[0], c.conductors[1])}
    )
    with torch.no_grad():
        a = model(cutnet.batchify([c]))[0, :3, :3]
        b = model(cutnet.batchify([shuffled]))[0, :3, :3]
    perm = [2, 0, 1]  # token order follows the cut's order, so the matrix is permuted with it
    assert torch.allclose(a, a.transpose(0, 1)), "pair head is not symmetric"
    assert torch.allclose(a[perm][:, perm], b, atol=1e-5)
