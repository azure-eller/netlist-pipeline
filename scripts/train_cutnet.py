#!/usr/bin/env python
"""Train the cut model (pipeline.cutnet) on a cut dataset and register it as a judge version.

    scripts/train_cutnet.py --dataset ID --version v8 [--real none|FAMILY,...] [--model cutnet|gbr]
                            [--epochs 800] [--holdout 0.2] [--fresh 200] [--seed 0]

Training rows: the dataset's synthetic cuts minus a held-out fraction, plus the real cuts of
the families named by --real. Reported: held-out synthetic, fresh synthetic cuts solved after
training, and the real cuts of every family, each marked trained-on or not. Saves
models/judge-<version>.joblib, uploads it, inserts a `models` row, writes
docs/experiments/cutnet-<version>.md. --model gbr trains the gradient-boosting baseline on
hand features (z0 only) and writes only the doc table.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import math
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pipeline import cutnet, data, db, fields, storage, windows  # noqa: E402

Row = tuple[windows.Cut, fields.CutParams]


def load_synthetic(dataset: int) -> list[Row]:
    out = []
    for s in data.load(dataset):
        if s["params"] is not None:
            out.append((windows.Cut.from_json(s["cut"]), fields.CutParams(**s["params"])))
    return out


def load_real() -> dict[str, list[Row]]:
    with db.connect() as conn:
        rows = data.load_windows(conn)
    by_family: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r["params"] is not None:
            by_family[family(r["family"])].append((r["cut"], r["params"]))
    return dict(by_family)


def family(name: str) -> str:
    """Boards registered by hand carry their directory name; fold `pic_generated` and
    `rpi_generated` into the design families they came from."""
    for prefix, fam in (("pic", "pic_programmer"), ("rpi", "rpi_hat")):
        if name.startswith(prefix):
            return fam
    return name


def fresh_rows(seed: int, n: int) -> list[Row]:
    rng = random.Random(seed)
    cuts = [data.sample_cut(rng) for _ in range(n)]
    with Pool(os.cpu_count()) as pool:
        params = pool.map(fields.solve_cut, cuts)
    return [(c, p) for c, p in zip(cuts, params, strict=True) if p is not None]


def gbr_features(cut: windows.Cut) -> list[float]:
    cs, ti = fields.select_conductors(cut)
    t = cs[ti]
    left = [c for c in cs if c.offset < 0]
    right = [c for c in cs if c.offset > 0]
    gl = (-left[-1].offset - left[-1].width / 2 - t.width / 2) if left else 10.0
    gr = (right[0].offset - right[0].width / 2 - t.width / 2) if right else 10.0
    wl, wr = (left[-1].width if left else 0.01), (right[0].width if right else 0.01)
    return [
        math.log(t.width),
        math.log(cut.h),
        math.log(cut.t),
        cut.er,
        float(cut.plane_below or cut.plane_above),
        math.log(max(gl, 0.01)),
        math.log(max(gr, 0.01)),
        math.log(wl),
        math.log(wr),
        len(cs),
    ]


def errors(pred: list[fields.CutParams | None], truth: list[fields.CutParams]) -> dict[str, Any]:
    z = np.array([abs(p.z0 - t.z0) / t.z0 for p, t in zip(pred, truth, strict=True) if p])
    ks = [
        abs(pk - tk)
        for p, t in zip(pred, truth, strict=True)
        if p
        for (_, pk), (_, tk) in zip(p.coupling, t.coupling, strict=True)
    ]
    logc = [
        abs(math.log(abs(a)) - math.log(abs(b)))
        for p, t in zip(pred, truth, strict=True)
        if p
        for ra, rb in zip(p.cmatrix, t.cmatrix, strict=True)
        for a, b in zip(ra, rb, strict=True)
    ]
    return {
        "n": int(len(z)),
        "z0_mape": float(z.mean() * 100),
        "z0_p95": float(np.percentile(z, 95) * 100),
        "k_mae": float(np.mean(ks)) if ks else None,
        "logc_mae": float(np.mean(logc)) if logc else None,
    }


def train_cutnet(
    train: list[Row],
    val: list[Row],
    epochs: int,
    seed: int,
    device: torch.device,
    conservation: float = 0.1,
    config: dict[str, int] | None = None,
) -> tuple[cutnet.CutNet, dict[str, Any], dict[str, Any]]:
    torch.manual_seed(seed)
    xb = cutnet.batchify([c for c, _ in train])
    y, valid = cutnet.targets([p for _, p in train])
    x_mean = xb.tokens[xb.mask].mean(0)
    x_std = xb.tokens[xb.mask].std(0).clamp_min(1e-3)
    y_mean = y[valid].mean(0)
    y_std = y[valid].std(0).clamp_min(1e-3)
    norm = {
        "x_mean": x_mean.tolist(),
        "x_std": x_std.tolist(),
        "y_mean": y_mean.tolist(),
        "y_std": y_std.tolist(),
    }

    def prep(rows: list[Row]) -> tuple[cutnet.Batch, torch.Tensor, torch.Tensor]:
        b = cutnet.batchify([c for c, _ in rows], device)
        b.tokens = (b.tokens - x_mean.to(device)) / x_std.to(device)
        yy, vv = cutnet.targets([p for _, p in rows])
        return b, ((yy - y_mean) / y_std).to(device), vv.to(device)

    tb, ty, tv = prep(train)
    vb, vy, vv = prep(val)
    ym, ys = y_mean.to(device), y_std.to(device)
    config = config or {"d": 128, "heads": 8, "layers": 3, "ff": 256}
    model = cutnet.CutNet(**config).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    n, bs = len(train), 256
    best, best_state = float("inf"), copy.deepcopy(model.state_dict())
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(n, device=device)
        for i in range(0, n, bs):
            idx = perm[i : i + bs]
            sub = cutnet.Batch(tb.tokens[idx], tb.pairs[idx], tb.mask[idx], tb.target[idx])
            lo = cutnet.loss(model(sub), ty[idx], tv[idx], ym, ys, conservation)
            opt.zero_grad()
            lo.backward()
            opt.step()
        sched.step()
        if epoch % 10 == 9 or epoch == epochs - 1:
            model.eval()
            with torch.no_grad():
                vl = float(cutnet.loss(model(vb), vy, vv, ym, ys, conservation))
            if vl < best:
                best, best_state = vl, copy.deepcopy(model.state_dict())
            print(f"epoch {epoch + 1:4d}  train {lo.item():.4f}  val {vl:.4f}  best {best:.4f}")
    model.load_state_dict(best_state)
    model.cpu().eval()
    return model, config, norm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=int, required=True)
    ap.add_argument("--version", default="v6")
    ap.add_argument("--real", default="none", help="none, or families to train on, comma-separated")
    ap.add_argument("--model", choices=("cutnet", "gbr"), default="cutnet")
    ap.add_argument("--epochs", type=int, default=800)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--fresh", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--conservation", type=float, default=0.1)
    a = ap.parse_args()
    t0 = time.perf_counter()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    synthetic = load_synthetic(a.dataset)
    rng = random.Random(a.seed)
    rng.shuffle(synthetic)
    n_val = int(len(synthetic) * a.holdout)
    val, train = synthetic[:n_val], synthetic[n_val:]
    real = load_real()
    train_families = [] if a.real == "none" else a.real.split(",")
    for fam in train_families:
        train += real[fam]
    n_real = sum(len(real[f]) for f in train_families)
    print(
        f"dataset {a.dataset}: {len(synthetic)} synthetic ({len(train) - n_real} train, "
        f"{n_val} held out); real: "
        f"{', '.join(f'{k} {len(v)}' for k, v in sorted(real.items()))}; "
        f"training on real families {train_families or 'none'}; device {device}"
    )
    fresh = fresh_rows(a.seed + 1, a.fresh)
    sets: dict[str, list[Row]] = {"synthetic held-out": val, "fresh synthetic": fresh}
    for fam, rows in sorted(real.items()):
        sets[f"real {fam}" + (" (trained on)" if fam in train_families else " (never seen)")] = rows

    metrics: dict[str, Any] = {
        "dataset_id": a.dataset,
        "n_train": len(train),
        "real_families": train_families,
        "model": a.model,
    }
    if a.model == "gbr":
        from sklearn.ensemble import GradientBoostingRegressor

        gbr = GradientBoostingRegressor(
            n_estimators=600, max_depth=4, learning_rate=0.05, random_state=a.seed
        )
        gbr.fit([gbr_features(c) for c, _ in train], [math.log(p.z0) for _, p in train])
        for name, rows in sets.items():
            z = np.exp(gbr.predict([gbr_features(c) for c, _ in rows]))
            err = np.array([abs(zi - p.z0) / p.z0 for zi, (_, p) in zip(z, rows, strict=True)])
            metrics[name] = {
                "n": len(rows),
                "z0_mape": float(err.mean() * 100),
                "z0_p95": float(np.percentile(err, 95) * 100),
            }
            print(f"{name}: {metrics[name]}")
        write_doc(a, metrics, time.perf_counter() - t0, None)
        return

    model, config, norm = train_cutnet(train, val, a.epochs, a.seed, device, a.conservation)
    bundle = {
        "kind": "cutnet",
        "version": a.version,
        "feature_version": cutnet.FEATURE_VERSION,
        "config": config,
        "state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
        "norm": norm,
    }
    learned = cutnet.Learned(bundle)
    for name, rows in sets.items():
        metrics[name] = errors([learned.predict(c) for c, _ in rows], [p for _, p in rows])
        print(f"{name}: {metrics[name]}")

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()
    artifact = {
        **bundle,
        "name": cutnet.Learned.name,
        "dataset_id": a.dataset,
        "real_families": train_families,
        "sampler_version": data.CUT_SAMPLER_VERSION,
        "solver_version": fields.CUT_SOLVER_VERSION,
        "torch": torch.__version__,
        "commit": commit,
        "seed": a.seed,
        "epochs": a.epochs,
        "n_parameters": sum(p.numel() for p in model.parameters()),
        "metrics": metrics,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "cut model: transformer over conductors predicting the capacitance matrices of "
        "the 2D field solver; z0 and coupling derived by fields.derive",
    }
    buf = io.BytesIO()
    joblib.dump(artifact, buf)
    blob = buf.getvalue()
    sha = storage.sha256(blob)
    out = ROOT / "models" / f"judge-{a.version}.joblib"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(blob)
    key = storage.put(f"models/judge/{a.version}-{sha[:12]}.joblib", blob)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into models (name, version, dataset_id, artifact_key, sha256, metrics) "
            "values (%s, %s, %s, %s, %s, %s)",
            (
                cutnet.Learned.name,
                a.version,
                a.dataset,
                key,
                sha,
                json.dumps(
                    {
                        **metrics,
                        "feature_version": cutnet.FEATURE_VERSION,
                        "torch": torch.__version__,
                        "commit": commit,
                        "solver_version": fields.CUT_SOLVER_VERSION,
                        "n_parameters": artifact["n_parameters"],
                    }
                ),
            ),
        )
        conn.commit()
    write_doc(a, metrics, time.perf_counter() - t0, (key, sha, artifact["n_parameters"]))
    print(f"models row inserted: {key} {sha[:12]}; {time.perf_counter() - t0:.0f}s")


def write_doc(
    a: argparse.Namespace, m: dict[str, Any], seconds: float, art: tuple[str, str, int] | None
) -> None:
    doc = ROOT / "docs" / "experiments" / f"cutnet-{a.version}.md"
    sets = [k for k in m if isinstance(m[k], dict) and "z0_mape" in m[k]]
    if a.model == "gbr":
        lines = [
            f"### baseline: gradient boosting on hand features (real families {a.real})",
            "",
            "| set | n | z0 MAPE | z0 p95 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {k} | {m[k]['n']} | {m[k]['z0_mape']:.2f}% | {m[k]['z0_p95']:.2f}% |" for k in sets
        ]
    else:
        assert art is not None
        lines = [
            f"### cutnet {a.version} (real families {a.real}): {art[2]} parameters, "
            f"{a.epochs} epochs, seed {a.seed}",
            "",
            f"Artifact `{art[0]}`, sha256 `{art[1]}`. Trained {time.strftime('%Y-%m-%d %H:%M')} "
            f"on dataset {a.dataset} ({m['n_train']} training cuts).",
            "",
            "| set | n | z0 MAPE | z0 p95 | coupling MAE | log C MAE |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {k} | {m[k]['n']} | {m[k]['z0_mape']:.2f}% | {m[k]['z0_p95']:.2f}% | "
            f"{m[k]['k_mae']:.4f} | {m[k]['logc_mae']:.4f} |"
            for k in sets
        ]
    cmd = (
        f"scripts/train_cutnet.py --dataset {a.dataset} --version {a.version} "
        f"--real {a.real} --model {a.model}"
    )
    lines += [
        "",
        f"{seconds:.0f} s. Command: `{cmd}`.",
        "",
    ]
    with doc.open("a") as f:
        f.write("\n".join(lines) + "\n")
    print(f"appended to {doc}")


if __name__ == "__main__":
    main()
