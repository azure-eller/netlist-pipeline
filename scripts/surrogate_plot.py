"""Draw a learned physics surrogate against the field-solver oracle on fresh geometries.

    scripts/surrogate_plot.py VERSION [OUT.png] [--n 200]

Loads the artifact the models row names, draws geometries the sampler has never produced for
training (seed 999), solves each with the oracle, and plots prediction against truth.
"""

from __future__ import annotations

import argparse
import io
import random
from multiprocessing import Pool

import joblib
import matplotlib
import matplotlib.ticker
import numpy as np

from pipeline import data, db, fields, storage, surrogate

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BG = "#0f1115"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select artifact_key, sha256, dataset_id from models where version = %s", (a.version,)
        )
        key, sha, dataset_id = cur.fetchone()  # type: ignore[misc]
    blob = storage.get(key)
    assert storage.sha256(blob) == sha, "artifact bytes differ from the models row"
    learned = surrogate.Learned(joblib.load(io.BytesIO(blob)))
    rng = random.Random(999)
    geoms = [data.sample(rng) for _ in range(a.n)]
    with Pool() as pool:
        truth = pool.map(fields.solve, geoms)
    single = [(g, t) for g, t in zip(geoms, truth, strict=True) if g.s is None]
    pair = [(g, t) for g, t in zip(geoms, truth, strict=True) if g.s is not None]
    z0_true = np.array([t.z0 for _, t in single])
    z0_pred = np.array([learned.z0(g.w, g.h, g.t, g.er, False) for g, _ in single])
    zd_true = np.array([t.z_diff for _, t in pair])
    zd_pred = np.array([learned.zdiff(g.w, g.s, g.h, g.t, g.er, False) for g, _ in pair])

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.2), facecolor=BG)
    for ax, t, p, label in (
        (axes[0], z0_true, z0_pred, "single trace  z0"),
        (axes[1], zd_true, zd_pred, "coupled pair  z_diff"),
    ):
        err = np.abs(p - t) / t * 100
        ax.set_facecolor("#171a21")
        lo, hi = min(t.min(), p.min()) * 0.9, max(t.max(), p.max()) * 1.1
        ax.plot([lo, hi], [lo, hi], color="#555", lw=1, ls="--", label="perfect")
        ax.scatter(t, p, s=14, color="#e6d54a", alpha=0.85, edgecolors="none")
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("field solver (oracle), Ω", color="#aaa")
        ax.set_ylabel(f"learned-fd {a.version}, Ω", color="#aaa")
        ax.set_title(
            f"{label}   n={len(t)}   mean error {err.mean():.2f}%   "
            f"p95 {np.percentile(err, 95):.2f}%",
            color="#ddd",
            fontsize=10,
        )
        ax.tick_params(colors="#aaa", which="both")
        for axis in (ax.xaxis, ax.yaxis):
            axis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
            axis.set_minor_formatter(matplotlib.ticker.NullFormatter())
            axis.set_major_locator(matplotlib.ticker.FixedLocator([10, 20, 30, 50, 100, 200, 300]))
        for s in ax.spines.values():
            s.set_color("#444")
    fig.suptitle(
        f"learned-fd {a.version} (trained on dataset {dataset_id}) against the field solver "
        f"on {a.n} geometries it never saw",
        color="#fff",
        fontsize=12,
        weight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out = a.out or f"surrogate_{a.version}.png"
    fig.savefig(out, dpi=130, facecolor=BG)
    print(out)


if __name__ == "__main__":
    main()
