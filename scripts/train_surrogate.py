"""Train the physics surrogate from a dataset and register it as a judge version.

    scripts/train_surrogate.py --dataset ID --version v5 [--holdout 0.2] [--fresh 200]

Reads the dataset manifest (shards of geometry -> field-solver params), fits one model for
single traces (z0) and one for coupled pairs (z_odd, z_even), reports held-out error, then
solves `--fresh` new geometries with the oracle and reports error on those too (the number
that matters: unseen inputs against the truth). Saves models/judge-<version>.joblib, uploads
it to S3 `models/judge/<version>.joblib`, inserts a `models` row, and writes
docs/experiments/surrogate-<version>.md.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline import data, fields, storage, surrogate  # noqa: E402
from pipeline.db import connect  # noqa: E402


def mape(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean(np.abs(p - y) / np.abs(y)) * 100)


def split(
    samples: list[dict[str, Any]],
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    xs, ys, xp, yp = [], [], [], []
    for s in samples:
        g, p = s["geometry"], s["params"]
        if g.get("s") is None:
            xs.append(surrogate.single_features(g["w"], g["h"], g["t"], g["er"]))
            ys.append([p["z0"]])
        else:
            xp.append(surrogate.pair_features(g["w"], g["h"], g["t"], g["er"], g["s"]))
            yp.append([p["z_odd"], p["z_even"]])
    return (np.array(xs), np.array(ys)), (np.array(xp), np.array(yp))


def fit(x: np.ndarray, y: np.ndarray, seed: int) -> Any:
    base = GradientBoostingRegressor(
        n_estimators=600, max_depth=4, learning_rate=0.05, random_state=seed
    )
    return base if y.shape[1] == 1 else MultiOutputRegressor(base)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=int, required=True)
    ap.add_argument("--version", default="v5")
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument(
        "--fresh", type=int, default=200, help="new oracle-solved geometries to test on"
    )
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    t0 = time.perf_counter()

    samples = data.load(a.dataset)
    (xs, ys), (xp, yp) = split(samples)
    print(f"dataset {a.dataset}: {len(samples)} samples, {len(xs)} single, {len(xp)} pairs")
    metrics: dict[str, Any] = {"dataset_id": a.dataset, "n_samples": len(samples)}
    models: dict[str, Any] = {}
    for kind, x, y, targets in (
        ("single", xs, ys, surrogate.SINGLE_TARGETS),
        ("pair", xp, yp, surrogate.PAIR_TARGETS),
    ):
        xtr, xte, ytr, yte = train_test_split(x, y, test_size=a.holdout, random_state=a.seed)
        m = fit(xtr, ytr, a.seed).fit(xtr, ytr.ravel() if ytr.shape[1] == 1 else ytr)
        pred = np.array(m.predict(xte)).reshape(len(xte), -1)
        metrics[kind] = {
            "n_train": len(xtr),
            "n_test": len(xte),
            **{f"mape_{t}": mape(yte[:, i], pred[:, i]) for i, t in enumerate(targets)},
        }
        models[kind] = m
        print(f"{kind}: {metrics[kind]}")

    # fresh geometries against the oracle: the number that matters
    rng = random.Random(a.seed + 1)
    fresh_err: dict[str, list[float]] = {"z0": [], "z_odd": [], "z_even": []}
    learned = surrogate.Learned({"kind": "surrogate", "version": a.version, "models": models})
    for _ in range(a.fresh):
        g = data.sample(rng)
        truth = fields.solve(g)
        if g.s is None:
            fresh_err["z0"].append(
                abs(learned.z0(g.w, g.h, g.t, g.er, False) - truth.z0) / truth.z0
            )
        else:
            z_odd, z_even = models["pair"].predict(
                np.array([surrogate.pair_features(g.w, g.h, g.t, g.er, g.s)])
            )[0]
            fresh_err["z_odd"].append(abs(z_odd - truth.z_odd) / truth.z_odd)  # type: ignore[operator]
            fresh_err["z_even"].append(abs(z_even - truth.z_even) / truth.z_even)  # type: ignore[operator]
    metrics["fresh"] = {
        k: {"n": len(v), "mape": float(np.mean(v) * 100), "p95": float(np.percentile(v, 95) * 100)}
        for k, v in fresh_err.items()
        if v
    }
    print("fresh vs oracle:", metrics["fresh"])

    artifact = {
        "name": "learned-fd",
        "kind": "surrogate",
        "version": a.version,
        "dataset_id": a.dataset,
        "solver_version": fields.SOLVER_VERSION,
        "sampler_version": data.SAMPLER_VERSION,
        "feature_names": {"single": surrogate.SINGLE_FEATURES, "pair": surrogate.PAIR_FEATURES},
        "targets": {"single": surrogate.SINGLE_TARGETS, "pair": surrogate.PAIR_TARGETS},
        "models": models,
        "metrics": metrics,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "physics surrogate trained on the 2D field-solver oracle; predicts z0 and "
        "odd/even mode impedance from cross-section geometry",
    }
    out = ROOT / "models" / f"judge-{a.version}.joblib"
    out.parent.mkdir(exist_ok=True)
    joblib.dump(artifact, out)
    blob = out.read_bytes()
    key = storage.put(f"models/judge/{a.version}.joblib", blob)
    sha = storage.sha256(blob)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into models (name, version, dataset_id, artifact_key, sha256, metrics) "
            "values (%s, %s, %s, %s, %s, %s) on conflict (name, version) do update set "
            "dataset_id = excluded.dataset_id, artifact_key = excluded.artifact_key, "
            "sha256 = excluded.sha256, metrics = excluded.metrics, created_at = now()",
            ("learned-fd", a.version, a.dataset, key, sha, json.dumps(metrics)),
        )
        conn.commit()
    seconds = time.perf_counter() - t0
    doc = ROOT / "docs" / "experiments" / f"surrogate-{a.version}.md"
    doc.write_text(
        f"# Surrogate {a.version}: learned physics from the field-solver oracle\n\n"
        f"Trained {artifact['trained_at']} on dataset {a.dataset} ({len(samples)} samples; "
        f"sampler {data.SAMPLER_VERSION}, solver {fields.SOLVER_VERSION}). Gradient boosting, "
        f"600 trees, depth 4. Artifact `models/judge/{a.version}.joblib`, sha256 `{sha}`.\n\n"
        f"| model | train | test | held-out MAPE |\n|---|---|---|---|\n"
        f"| single (z0) | {metrics['single']['n_train']} | {metrics['single']['n_test']} | "
        f"{metrics['single']['mape_z0']:.2f}% |\n"
        f"| pair (z_odd, z_even) | {metrics['pair']['n_train']} | {metrics['pair']['n_test']} | "
        f"{metrics['pair']['mape_z_odd']:.2f}% / {metrics['pair']['mape_z_even']:.2f}% |\n\n"
        f"Fresh geometries solved by the oracle after training ({a.fresh} draws, unseen):\n\n"
        f"| target | n | MAPE | p95 |\n|---|---|---|---|\n"
        + "".join(
            f"| {k} | {v['n']} | {v['mape']:.2f}% | {v['p95']:.2f}% |\n"
            for k, v in metrics["fresh"].items()
        )
        + f"\nTraining plus fresh evaluation took {seconds:.0f} s. Command: "
        f"`scripts/train_surrogate.py --dataset {a.dataset} --version {a.version}`.\n"
    )
    print(f"wrote {out} ({len(blob)} bytes, sha256 {sha}) and s3://{key}")
    print(f"models row inserted; {doc} written; {seconds:.0f}s")


if __name__ == "__main__":
    main()
