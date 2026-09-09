"""Draw docs/media/system.png and docs/media/model.png. Hand-placed on purpose: every arrow
and label sits where it reads best.  .venv/bin/python docs/media/diagrams.py"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

OUT = Path(__file__).parent
F = 1.25  # type scale: GitHub shows the image at ~900 px wide, so text has to be big
BG, BLUE, BLUE_FC, ORANGE, ORANGE_FC = "#0f1115", "#4f8fe6", "#16213a", "#d9822b", "#2a2320"
GREEN, GREEN_FC, GOLD, GOLD_FC, GREY, TXT, SUB = (
    "#4caf50", "#1f2a1f", "#e6d54a", "#2a2a1a", "#8fa3bf", "#ffffff", "#9fb3d1",
)  # fmt: skip


def box(ax, x1, y1, x2, y2, title, body="", fc=BLUE_FC, ec=BLUE, ts=9, bs=7.2, lw=1.3, pad=0.3):
    ax.add_patch(
        FancyBboxPatch((x1, y1), x2 - x1, y2 - y1, boxstyle=f"round,pad={pad}", fc=fc, ec=ec, lw=lw)
    )
    cx = (x1 + x2) / 2
    if body:
        ax.text(cx, y2 - 2.2, title, color=TXT, ha="center", va="top", fontsize=ts * F, weight="bold")
        ax.text(cx, y1 + (y2 - y1 - 3.2) / 2, body, color=SUB, ha="center", va="center", fontsize=bs * F)
    else:
        ax.text(cx, (y1 + y2) / 2, title, color=TXT, ha="center", va="center", fontsize=ts * F, weight="bold")


def frame(ax, x1, y1, x2, y2, title, ec, fc="none", ts=10):
    ax.add_patch(FancyBboxPatch((x1, y1), x2 - x1, y2 - y1, boxstyle="round,pad=0.4", fc=fc, ec=ec, lw=1.4))
    ax.text(x1 + 1.5, y2 - 1.2, title, color=TXT, ha="left", va="top", fontsize=ts * F, weight="bold")


def arrow(ax, p, q, color=GREY, rad=0.0, lw=1.3, ls="-"):
    ax.annotate(
        "", xy=q, xytext=p,
        arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, ls=ls, connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0),
    )  # fmt: skip


def poly(ax, pts, color=GREY, lw=1.3, ls="-"):
    xs, ys = zip(*pts, strict=True)
    ax.plot(xs[:-1], ys[:-1], color=color, lw=lw, ls=ls, solid_capstyle="round")
    arrow(ax, pts[-2], pts[-1], color, lw=lw, ls=ls)


def label(ax, x, y, s, color=SUB, size=7, ha="center", va="center", rot=0):
    ax.text(x, y, s, color=color, ha=ha, va=va, fontsize=size * F, rotation=rot, family="monospace" if "/" in s or "_" in s else None)


def canvas(w, h, xmax, ymax):
    fig, ax = plt.subplots(figsize=(w, h), facecolor=BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, xmax)
    ax.set_ylim(0, ymax)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def system() -> None:
    fig, ax = canvas(16, 9.6, 160, 96)
    ax.text(2, 94, "netlist-pipeline: one run through the system", color=TXT, fontsize=13, weight="bold", va="top")

    # row A: client, api, stores
    box(ax, 3, 78, 24, 90, "client", "uploads a schematic\n(or a finished board),\npolls, fetches artifacts", fc="#2b2b2b", ec="#cfd8dc", bs=6.6)
    box(ax, 30, 78, 66, 90, "api  (FastAPI)", "POST /designs → store, insert run + first job\nGET /runs/{id} → the rows\nGET …/artifacts/{name} → presigned redirect", bs=5.8)
    box(ax, 76, 78, 106, 90, "Postgres", "designs · runs · jobs · stages\ncandidates · verifications · artifacts\nmodels · judge_approvals", fc=ORANGE_FC, ec=ORANGE, bs=6.2)
    box(ax, 118, 78, 154, 90, "S3  (MinIO locally)", "designs/<sha>/…\nruns/<id>/…\nmodels/judge/<version>-<sha>.joblib", fc=ORANGE_FC, ec=ORANGE, bs=6.2)
    arrow(ax, (24, 84), (30, 84))
    arrow(ax, (66, 84), (76, 84))
    label(ax, 71, 86.2, "run + first job", size=5.4)
    arrow(ax, (47, 78), (136, 78), rad=0.22)
    label(ax, 91, 71.5, "upload bytes", size=6.5)

    # row B: worker
    frame(ax, 3, 38, 157, 66, "worker  — one job at a time, each in a child interpreter", BLUE, fc="#111827", ts=9)
    box(ax, 6, 44, 30, 58, "job loop", "claim a queued job\n(SKIP LOCKED)\nrun its stage\nenqueue the next stage")
    frame(ax, 33, 41, 154, 61, "the eight stages: one jobs row and one stages row each, chained by sha256", "#2f4f6f", fc="#0f1a2e", ts=7.5)
    stages = [
        ("1 extract_netlist", "kicad-cli"), ("2 constraints", "classes + patterns"),
        ("3  build_board", "pcbnew"), ("4  place", "simulated annealing"), ("5  route", "Freerouting"),
        ("6  judge", "the model slot"), ("7  verify", "DRC + netlist parity"), ("8 export", "gerbers, drill, png"),
    ]  # fmt: skip
    xs = []
    for i, (t, b) in enumerate(stages):
        x1 = 35 + i * 14.75
        xs.append((x1, x1 + 13.25))
        slot = i == 5
        box(ax, x1, 45, x1 + 13.25, 55, t, b, fc=GREEN_FC if slot else "#1b2d4f", ec=GREEN if slot else BLUE, ts=7, bs=5.8)
        if i:
            arrow(ax, (xs[i - 1][1], 50), (x1, 50))
    arrow(ax, (18, 58), (18, 78))
    label(ax, 19, 68, "claim", size=6.5, ha="left")
    arrow(ax, (95, 61), (95, 78))
    label(ax, 97, 69.5, "stage rows: tool, version,\ninput_hash, output_hash", size=6.3, ha="left")
    arrow(ax, (126, 61), (126, 78))
    label(ax, 128, 69.5, "netlist, boards,\nreport, gerbers", size=6.3, ha="left")

    # row C: registry + judge service
    box(ax, 4, 8, 34, 30, "registry  (rows in Postgres)", "models: name, version, dataset,\nartifact key + sha256, feature\nencoding, sklearn, commit\n\njudge_approvals: name, version,\nartifact sha, golden-set sha\n\nboth immutable", fc=GOLD_FC, ec=GOLD, bs=6, ts=8)
    frame(ax, 40, 3, 157, 33, "judge service  (JUDGE_URL, one versioned judge per process)", GREEN, fc="#0f1a14", ts=9)
    box(ax, 44, 10, 78, 26, "rule judge", "IPC-2141 impedance\nIPC-2221 current capacity\ndecoupling · crosstalk · vias\nscore = −Σ weighted excess", bs=6)
    frame(ax, 83, 6, 154, 29, "physics provider  (where the impedance numbers come from)", "#2e6b3a", fc="#0d1a12", ts=7.5)
    box(ax, 86, 10, 106, 23, "formula", "IPC-2141 closed form\nmicroseconds", ts=8, bs=6)
    box(ax, 109, 10, 129, 23, "oracle", "our 2D field solver\n0.36 s per geometry", ts=8, bs=6)
    box(ax, 132, 10, 152, 23, "learned-fd v5", "trained on the oracle\nµs, 0.9% error", fc=GOLD_FC, ec=GOLD, ts=8, bs=6)
    arrow(ax, (78, 18), (86, 18))
    label(ax, 82, 24.2, "z0 of every\nhigh-speed net", size=5.4)

    # the slot, the gate, the bytes
    sx = (xs[5][0] + xs[5][1]) / 2
    arrow(ax, (sx, 45), (sx, 33), color=GREEN, lw=1.6)
    label(ax, sx + 1.2, 39, "POST /v1/score (board, netlist, constraints)\n← score, violations, judge {name, version, sha}", color="#8fbf8f", size=5.8, ha="left")
    poly(ax, [(xs[5][0] + 1, 45), (xs[5][0] + 1, 36), (19, 36), (19, 30)], color=GOLD)
    label(ax, 20.5, 33.3, "gate: (name, version, sha) must be an approval row", color=GOLD, size=5.6, ha="left")
    poly(ax, [(34, 14), (38, 14), (38, 1.5), (142, 1.5), (142, 10)], color=GOLD)
    label(ax, 92, 0.2, "the models row names the artifact key; the service refuses bytes whose sha256 differs", color=GOLD, size=5.8, va="bottom")
    poly(ax, [(154, 81), (158.5, 81), (158.5, 16.5), (152, 16.5)], color=ORANGE)
    label(ax, 159.3, 47, "artifact bytes", color=ORANGE, size=6.3, rot=90)
    fig.savefig(OUT / "system.png", dpi=140, facecolor=BG, bbox_inches="tight")


def model() -> None:
    fig, ax = canvas(16, 7.4, 160, 74)
    ax.text(2, 72, "How the learned physics model is made, gated and served", color=TXT, fontsize=13, weight="bold", va="top")
    box(ax, 2, 46, 30, 60, "sampler", "cross-section-0.1\nw, h, t, εr, gap drawn log-uniform\nfrom ranges seen on real boards", bs=6.4)
    box(ax, 2, 27, 30, 41, "2D field solver", "fd2d-0.1, pipeline/fields.py\nour own; within 1% of\nHammerstad on a bare microstrip", bs=6.4)
    box(ax, 38, 36, 64, 50, "data:shard jobs", "the same Postgres queue\nthe worker runs boards through\nfour jobs, one per shard", bs=6.4)
    box(ax, 72, 36, 96, 50, "dataset 17", "5,000 solved geometries\nhashed JSONL shards + manifest\nsampler, solver, seed recorded", fc=ORANGE_FC, ec=ORANGE, bs=6.4)
    box(ax, 104, 36, 128, 50, "train", "scripts/train_surrogate.py\ngradient boosting, 600 trees\nz0 · z_odd · z_even", bs=6.4)
    box(ax, 134, 33, 159, 53, "models row · learned-fd v5", "dataset 17\nartifact key + sha256\nfeature encoding version\nsklearn version, commit\n(immutable)", fc=GOLD_FC, ec=GOLD, ts=8, bs=6.4)
    arrow(ax, (30, 53), (38, 46), rad=-0.15)
    arrow(ax, (30, 34), (38, 40), rad=0.15)
    label(ax, 33.5, 37.2, "labels", size=6)
    arrow(ax, (64, 43), (72, 43))
    arrow(ax, (96, 43), (104, 43))
    arrow(ax, (128, 43), (136, 43))
    label(ax, 132, 45, "register", size=6)

    box(ax, 38, 6, 64, 20, "fresh check", "200 geometries drawn after training,\nsolved by the oracle:\nz0 0.9% mean error, z_diff 1.9%", bs=6.4)
    box(ax, 72, 6, 96, 20, "golden set", "5 boards with expected scorecards,\n2 deliberately broken\nscripts/golden.py --judge-url …", fc=GREEN_FC, ec=GREEN, bs=6.4)
    box(ax, 104, 6, 128, 20, "judge_approvals row", "name, version, artifact sha,\ngolden-set sha\n(immutable)", fc=GOLD_FC, ec=GOLD, bs=6.4)
    box(ax, 136, 6, 158, 20, "served", "the judge service loads the\nbytes the row names; the worker's\njudge stage checks the approval", fc=GREEN_FC, ec=GREEN, bs=6.2)
    poly(ax, [(116, 36), (116, 27), (51, 27), (51, 20)])
    label(ax, 83, 28.6, "does it match the oracle on data it never saw?", size=6)
    poly(ax, [(142, 33), (142, 24), (84, 24), (84, 20)], color=GOLD)
    arrow(ax, (152, 33), (152, 20), color=GOLD)
    label(ax, 153, 26.5, "loads these\nbytes", color=GOLD, size=5.8, ha="left")
    label(ax, 113, 22.4, "does it agree with the reference on real boards?", color=GOLD, size=6)
    arrow(ax, (96, 13), (104, 13), color=GOLD)
    label(ax, 100, 15.2, "pass →\n--approve", color=GOLD, size=5.8)
    arrow(ax, (128, 13), (136, 13), color=GOLD)
    label(ax, 84, 3.2, "fail: judges v2, v3, v4 were refused here, on the broken-capacitor board", color="#e08a7a", size=6.2)
    fig.savefig(OUT / "model.png", dpi=140, facecolor=BG, bbox_inches="tight")


if __name__ == "__main__":
    system()
    model()
    print("ok")
