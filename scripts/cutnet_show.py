#!/usr/bin/env python
"""Show the cut model on one slice: what goes in, what it attends to, what comes out.

    scripts/cutnet_show.py --version v8 --board 19 --net /ID_SDA [--cut 5] [--out PNG]
    scripts/cutnet_show.py --version v8 --synthetic 3

Prints the token table (nine numbers per conductor), the pairwise gaps, the predicted and
solved capacitance matrices and the derived z0 and coupling; draws the slice, the attention
of the last layer, and both matrices side by side.
"""

from __future__ import annotations

import argparse
import io
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv" / "bin" / "python"
if VENV.exists() and Path(sys.prefix) != VENV.parent.parent:
    os.execv(str(VENV), [str(VENV), *sys.argv])  # run under the repo's venv, whatever `python` is
sys.path.insert(0, str(ROOT / "src"))

import joblib  # noqa: E402
import matplotlib  # noqa: E402
import numpy as np  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from pipeline import board, cutnet, data, db, fields, storage, windows  # noqa: E402

NAMES = ["log w", "offset", "log|off|", "target", "same net", "log h", "log t", "er", "plane"]


def load_model(version: str) -> cutnet.Learned:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select artifact_key, sha256 from models where version = %s", (version,))
        key, sha = cur.fetchone()  # type: ignore[misc]
    blob = storage.get(key)
    assert storage.sha256(blob) == sha, "artifact bytes do not match the models row"
    return cutnet.Learned(joblib.load(io.BytesIO(blob)))


def pick_cut(a: argparse.Namespace) -> windows.Cut:
    if a.synthetic is not None:
        rng = random.Random(a.synthetic)
        return data.sample_cut(rng)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select object_key from boards where id = %s", (a.board,))
        key = cur.fetchone()[0]  # type: ignore[index]
    b = board.parse(storage.get(key).decode())
    cs = windows.cuts(windows.extract(b, a.net))
    return cs[a.cut]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v8")
    ap.add_argument("--board", type=int)
    ap.add_argument("--net")
    ap.add_argument("--cut", type=int, default=0)
    ap.add_argument("--synthetic", type=int, help="seed for a synthetic cut instead")
    ap.add_argument("--out", default=str(ROOT / "docs" / "media" / "cutnet_example.png"))
    a = ap.parse_args()
    model = load_model(a.version)
    cut = pick_cut(a)
    cs, ti = fields.select_conductors(cut)
    tokens, pairs, _, nets = cutnet.featurise(cut)

    plane = "yes" if cut.plane_below or cut.plane_above else "no"
    print(f"slice: {len(cs)} conductors, plane {plane}, h {cut.h} mm, t {cut.t} mm, er {cut.er}\n")
    print("INPUT: one row per conductor, nine numbers each (raw, before standardising)")
    print(f"{'net':<18}" + "".join(f"{n:>10}" for n in NAMES))
    for c, row in zip(cs, tokens, strict=True):
        print(f"{str(c.net):<18}" + "".join(f"{v:10.3f}" for v in row))
    print("\nPAIRWISE: log(edge gap + 0.01) fed into every attention score")
    print(f"{'':<18}" + "".join(f"{str(c.net)[:9]:>10}" for c in cs))
    for c, row in zip(cs, pairs, strict=True):
        print(f"{str(c.net):<18}" + "".join(f"{v[0]:10.2f}" for v in row))

    pred = model.predict(cut)
    truth = fields.solve_cut(cut)
    assert pred is not None and truth is not None
    att = model.model.att[-1].last[0].mean(0).numpy()  # last layer, heads averaged
    print("\nATTENTION (last layer, heads averaged): row = who is looking, column = at whom")
    print(f"{'':<18}" + "".join(f"{str(c.net)[:9]:>10}" for c in cs))
    for c, row in zip(cs, att[: len(cs), : len(cs)], strict=True):
        print(f"{str(c.net):<18}" + "".join(f"{v:10.2f}" for v in row))

    def show(title: str, m: object) -> None:
        print(f"\n{title} (pF per metre)")
        print(f"{'':<18}" + "".join(f"{str(c.net)[:9]:>10}" for c in cs))
        for c, row in zip(cs, m, strict=True):  # type: ignore[call-overload]
            print(f"{str(c.net):<18}" + "".join(f"{v:10.1f}" for v in row))

    show("OUTPUT: predicted capacitance matrix, board material", pred.cmatrix)
    show("SOLVER: the same matrix from the field solver", truth.cmatrix)
    print(
        f"\nDERIVED  z0: model {pred.z0:.1f} ohm, solver {truth.z0:.1f} ohm "
        f"({abs(pred.z0 / truth.z0 - 1) * 100:.1f}% off)"
    )
    for (n, kp), (_, kt) in zip(pred.coupling, truth.coupling, strict=True):
        print(f"         coupling to {n}: model {kp:.3f}, solver {kt:.3f}")

    # ---- picture ----
    BG, TXT = "#0f1115", "#ffffff"
    fig, axes = plt.subplots(
        1, 4, figsize=(18, 4.6), facecolor=BG, gridspec_kw={"width_ratios": [1.6, 1, 1, 1]}
    )
    ax = axes[0]
    ax.set_facecolor(BG)
    lo = min(c.offset - c.width / 2 for c in cs) - 0.5
    hi = max(c.offset + c.width / 2 for c in cs) + 0.5
    ax.set_xlim(lo, hi)
    ax.set_ylim(-cut.h - 0.3, 0.6)
    ax.add_patch(Rectangle((lo, -cut.h), hi - lo, cut.h, fc="#25402a", ec="none"))
    if cut.plane_below or cut.plane_above:
        ax.add_patch(Rectangle((lo, -cut.h - 0.12), hi - lo, 0.12, fc="#c98a3a", ec="none"))
    for i, c in enumerate(cs):
        ax.add_patch(
            Rectangle(
                (c.offset - c.width / 2, 0),
                c.width,
                cut.t * 4,
                fc="#e6d54a" if i == ti else "#8fa3bf",
                ec="none",
            )
        )
        ax.text(c.offset, 0.25, str(c.net)[:10], color=TXT, ha="center", fontsize=8, rotation=45)
    ax.set_yticks([])
    ax.tick_params(colors="#9fb3d1")
    ax.set_xlabel("mm along the cut", color="#9fb3d1")
    ax.set_title("the slice (target in yellow)", color=TXT, fontsize=10)
    labels = [str(c.net)[:8] for c in cs]
    for ax, m, title in (
        (axes[1], att[: len(cs), : len(cs)], "attention: who looks at whom"),
        (axes[2], np.log10(np.abs(np.array(pred.cmatrix))), "model: log10 |C|"),
        (axes[3], np.log10(np.abs(np.array(truth.cmatrix))), "solver: log10 |C|"),
    ):
        ax.set_facecolor(BG)
        im = ax.imshow(m, cmap="magma")
        ax.set_xticks(range(len(cs)), labels, rotation=60, color="#9fb3d1", fontsize=7)
        ax.set_yticks(range(len(cs)), labels, color="#9fb3d1", fontsize=7)
        ax.set_title(title, color=TXT, fontsize=10)
        fig.colorbar(im, ax=ax, fraction=0.046).ax.tick_params(colors="#9fb3d1", labelsize=7)
    fig.suptitle(
        f"learned-cut {a.version} on one slice: z0 model {pred.z0:.1f} ohm, "
        f"solver {truth.z0:.1f} ohm",
        color=TXT,
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(a.out, dpi=130, facecolor=BG, bbox_inches="tight")
    print(f"\nfigure: {a.out}")


if __name__ == "__main__":
    main()
