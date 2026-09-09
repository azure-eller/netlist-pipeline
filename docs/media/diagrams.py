"""Draw docs/media/system.png and docs/media/model.png. Hand-placed on purpose: every arrow
and label sits where it reads best.  .venv/bin/python docs/media/diagrams.py"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Ellipse, FancyBboxPatch, Rectangle  # noqa: E402

OUT = Path(__file__).parent
F = 1.3  # type scale: GitHub shows the image at ~900 px wide, so text has to be big
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
        ax.text(
            cx, y2 - 2.2, title, color=TXT, ha="center", va="top", fontsize=ts * F, weight="bold"
        )
        ax.text(
            cx, y1 + (y2 - y1 - 3.2) / 2, body, color=SUB, ha="center", va="center", fontsize=bs * F
        )
    else:
        ax.text(
            cx,
            (y1 + y2) / 2,
            title,
            color=TXT,
            ha="center",
            va="center",
            fontsize=ts * F,
            weight="bold",
        )


def cyl(ax, x1, y1, x2, y2, title, body, fc, ec, ts=8.5, bs=6.6, e=3.2):
    """A database cylinder: body, bottom rim, top ellipse."""
    w, cx = x2 - x1, (x1 + x2) / 2
    ax.add_patch(Ellipse((cx, y1 + e / 2), w, e, fc=fc, ec=ec, lw=1.3))
    ax.add_patch(Rectangle((x1, y1 + e / 2), w, y2 - y1 - e, fc=fc, ec="none"))
    ax.plot([x1, x1], [y1 + e / 2, y2 - e / 2], color=ec, lw=1.3)
    ax.plot([x2, x2], [y1 + e / 2, y2 - e / 2], color=ec, lw=1.3)
    ax.add_patch(Ellipse((cx, y2 - e / 2), w, e, fc=fc, ec=ec, lw=1.3))
    ax.text(
        cx, y2 - e - 1.2, title, color=TXT, ha="center", va="top", fontsize=ts * F, weight="bold"
    )
    ax.text(
        cx,
        y1 + (y2 - y1 - e - 3) / 2 + 0.5,
        body,
        color=SUB,
        ha="center",
        va="center",
        fontsize=bs * F,
    )


def frame(ax, x1, y1, x2, y2, title, ec, fc="none", ts=10):
    ax.add_patch(
        FancyBboxPatch((x1, y1), x2 - x1, y2 - y1, boxstyle="round,pad=0.4", fc=fc, ec=ec, lw=1.4)
    )
    ax.text(
        x1 + 1.5, y2 - 1.2, title, color=TXT, ha="left", va="top", fontsize=ts * F, weight="bold"
    )


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
    ax.text(
        x,
        y,
        s,
        color=color,
        ha=ha,
        va=va,
        fontsize=size * F,
        rotation=rot,
        family="monospace" if "/" in s or "_" in s else None,
    )


def canvas(w, h, xmax, ymax):
    fig, ax = plt.subplots(figsize=(w, h), facecolor=BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, xmax)
    ax.set_ylim(0, ymax)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def system() -> None:
    # portrait: GitHub shows a README image at ~900 px wide, so a tall layout reads larger
    fig, ax = canvas(10, 15, 100, 150)
    ax.text(2, 148.5, "one run through the system", color=TXT, fontsize=13, weight="bold", va="top")

    box(
        ax,
        2,
        130,
        24,
        142,
        "client",
        "uploads a schematic\n(or a finished board),\npolls, fetches artifacts",
        fc="#2b2b2b",
        ec="#cfd8dc",
        ts=8.5,
        bs=6.6,
        pad=1.2,
    )
    box(
        ax,
        30,
        130,
        72,
        142,
        "api  (FastAPI)",
        "POST /designs → store, insert run + first job\nGET /runs/{id} → the rows\nGET …/artifacts/{name} → presigned redirect",
        ts=8.5,
        bs=6.4,
    )
    arrow(ax, (24, 136), (30, 136))
    cyl(
        ax,
        2,
        106,
        46,
        124,
        "Postgres",
        "designs · runs · jobs · stages\ncandidates · verifications · artifacts\nmodels · judge_approvals",
        fc=ORANGE_FC,
        ec=ORANGE,
    )
    cyl(
        ax,
        52,
        106,
        96,
        124,
        "S3  (MinIO locally)",
        "designs/<sha>/…\nruns/<id>/…\nmodels/judge/<version>-<sha>.joblib",
        fc=ORANGE_FC,
        ec=ORANGE,
    )
    arrow(ax, (40, 130), (30, 124), rad=0.1)
    label(ax, 27.5, 126.5, "run + first job", size=5.8, ha="right")
    arrow(ax, (62, 130), (72, 124), rad=-0.1)
    label(ax, 74, 126.5, "upload bytes", size=5.8, ha="left")

    frame(
        ax,
        2,
        50,
        96,
        100,
        "worker  — one job at a time, each in a child interpreter",
        BLUE,
        fc="#111827",
        ts=9,
    )
    box(
        ax,
        5,
        74,
        30,
        92,
        "job loop",
        "claim a queued job\n(SKIP LOCKED)\nrun its stage in a\nchild interpreter\nenqueue the next stage",
        ts=8.5,
        bs=6.4,
    )
    frame(
        ax,
        34,
        53,
        94,
        96,
        "the eight stages: one jobs row and\none stages row each, chained by sha256",
        "#2f4f6f",
        fc="#0f1a2e",
        ts=7.6,
    )
    stages = [
        ("1 extract_netlist", "kicad-cli"), ("2 constraints", "classes + patterns"),
        ("3 build_board", "pcbnew"), ("4 place", "annealing"), ("5 route", "Freerouting"),
        ("6 judge", "the model slot"), ("7 verify", "DRC + netlist parity"), ("8 export", "gerbers, drill, png"),
    ]  # fmt: skip
    pos = []
    for i, (t, b) in enumerate(stages):
        row, col = divmod(i, 4)
        x1 = 36.5 + col * 14.5
        y1 = 74 - row * 17
        pos.append((x1, y1, x1 + 13, y1 + 11))
        slot = i == 5
        box(
            ax,
            x1,
            y1,
            x1 + 13,
            y1 + 11,
            t,
            b,
            fc=GREEN_FC if slot else "#1b2d4f",
            ec=GREEN if slot else BLUE,
            ts=6.3,
            bs=5.1,
        )
        if i in (1, 2, 3, 5, 6, 7):
            arrow(ax, (pos[i - 1][2], y1 + 5.5), (x1, y1 + 5.5))
    poly(
        ax,
        [
            ((pos[3][0] + pos[3][2]) / 2, pos[3][1]),
            ((pos[3][0] + pos[3][2]) / 2, 71.5),
            ((pos[4][0] + pos[4][2]) / 2, 71.5),
            ((pos[4][0] + pos[4][2]) / 2, pos[4][3]),
        ],
    )
    arrow(ax, (17, 92), (17, 106))
    label(ax, 18, 103, "claim", size=5.8, ha="left")
    arrow(ax, (40, 96), (40, 106))
    label(ax, 41, 102, "stage rows: tool, version,\ninput_hash, output_hash", size=5.4, ha="left")
    arrow(ax, (74, 96), (74, 106))
    label(ax, 75, 102, "netlist, boards,\nreport, gerbers", size=5.4, ha="left")

    cyl(
        ax,
        2,
        4,
        30,
        42,
        "registry\n(rows in Postgres)",
        "models: name, version,\ndataset, artifact key + sha,\nfeature encoding,\nsklearn, commit\n\njudge_approvals: name,\nversion, artifact sha,\ngolden-set sha\n\nboth immutable",
        fc=GOLD_FC,
        ec=GOLD,
        ts=8,
        bs=6.2,
    )
    frame(
        ax,
        34,
        2,
        96,
        44,
        "judge service  (JUDGE_URL,\none versioned judge per process)",
        GREEN,
        fc="#0f1a14",
        ts=8.5,
    )
    box(
        ax,
        37,
        21,
        66,
        36,
        "rule judge",
        "IPC-2141 impedance\nIPC-2221 current capacity\ndecoupling · crosstalk · vias\nscore = −Σ weighted excess",
        ts=8,
        bs=5.6,
    )
    frame(
        ax,
        37,
        4,
        94,
        19,
        "physics provider  (where the impedance numbers come from)",
        "#2e6b3a",
        fc="#0d1a12",
        ts=6,
    )
    box(ax, 39, 5.5, 56, 14.5, "formula", "IPC-2141 closed form\nmicroseconds", ts=7, bs=5.6)
    box(ax, 58, 5.5, 75, 14.5, "oracle", "our 2D field solver\n0.36 s per geometry", ts=7, bs=5.6)
    box(
        ax,
        77,
        5.5,
        94,
        14.5,
        "learned-fd v5",
        "trained on the oracle\nµs, 0.9% error",
        fc=GOLD_FC,
        ec=GOLD,
        ts=7,
        bs=5.6,
    )
    arrow(ax, (51.5, 21), (51.5, 19))
    label(ax, 53, 20.5, "z0 of every high-speed net", size=5.4, ha="left")

    sx = (pos[5][0] + pos[5][2]) / 2
    arrow(ax, (sx, pos[5][1]), (sx, 44), color=GREEN, lw=1.6)
    label(
        ax,
        sx + 1.5,
        49.3,
        "POST /v1/score (board, netlist, constraints)\n← score, violations, judge {name, version, sha}",
        color="#8fbf8f",
        size=5,
        ha="left",
    )
    poly(ax, [(pos[5][0] + 1.5, pos[5][1]), (pos[5][0] + 1.5, 47), (16, 47), (16, 42)], color=GOLD)
    label(
        ax,
        3,
        44.5,
        "gate: (name, version, sha)\nmust be an approval row",
        color=GOLD,
        size=5.4,
        ha="left",
    )
    poly(ax, [(16, 4), (16, 0.8), (85.5, 0.8), (85.5, 5.5)], color=GOLD)
    label(
        ax,
        50,
        -0.4,
        "the models row names the artifact key; the service refuses bytes whose sha256 differs",
        color=GOLD,
        size=5.4,
        va="top",
    )
    poly(ax, [(96, 114), (98.8, 114), (98.8, 10), (94, 10)], color=ORANGE)
    label(ax, 99.6, 60, "artifact bytes", color=ORANGE, size=5.6, rot=90)
    fig.savefig(OUT / "system.png", dpi=150, facecolor=BG, bbox_inches="tight")


def model() -> None:
    fig, ax = canvas(16, 7.4, 160, 74)
    ax.text(
        2,
        72,
        "How the learned physics model is made, gated and served",
        color=TXT,
        fontsize=13,
        weight="bold",
        va="top",
    )
    box(
        ax,
        2,
        46,
        30,
        60,
        "sampler",
        "cross-section-0.1\nw, h, t, εr, gap drawn log-uniform\nfrom ranges seen on real boards",
        bs=6.4,
    )
    box(
        ax,
        2,
        27,
        30,
        41,
        "2D field solver",
        "fd2d-0.1, pipeline/fields.py\nour own; within 1% of\nHammerstad on a bare microstrip",
        bs=6.4,
    )
    box(
        ax,
        38,
        36,
        64,
        50,
        "data:shard jobs",
        "the same Postgres queue\nthe worker runs boards through\nfour jobs, one per shard",
        bs=6.4,
    )
    box(
        ax,
        72,
        36,
        96,
        50,
        "dataset 17",
        "5,000 solved geometries\nhashed JSONL shards + manifest\nsampler, solver, seed recorded",
        fc=ORANGE_FC,
        ec=ORANGE,
        bs=6.4,
    )
    box(
        ax,
        104,
        36,
        128,
        50,
        "train",
        "scripts/train_surrogate.py\ngradient boosting, 600 trees\nz0 · z_odd · z_even",
        bs=6.4,
    )
    box(
        ax,
        134,
        33,
        159,
        53,
        "models row · learned-fd v5",
        "dataset 17\nartifact key + sha256\nfeature encoding version\nsklearn version, commit\n(immutable)",
        fc=GOLD_FC,
        ec=GOLD,
        ts=8,
        bs=6.4,
    )
    arrow(ax, (30, 53), (38, 46), rad=-0.15)
    arrow(ax, (30, 34), (38, 40), rad=0.15)
    label(ax, 33.5, 37.2, "labels", size=6)
    arrow(ax, (64, 43), (72, 43))
    arrow(ax, (96, 43), (104, 43))
    arrow(ax, (128, 43), (136, 43))
    label(ax, 132, 45, "register", size=6)

    box(
        ax,
        38,
        6,
        64,
        20,
        "fresh check",
        "200 geometries drawn after training,\nsolved by the oracle:\nz0 0.9% mean error, z_diff 1.9%",
        bs=6.4,
    )
    box(
        ax,
        72,
        6,
        96,
        20,
        "golden set",
        "7 boards with expected scorecards,\n2 deliberately broken\nscripts/golden.py --judge-url …",
        fc=GREEN_FC,
        ec=GREEN,
        bs=6.4,
    )
    box(
        ax,
        104,
        6,
        128,
        20,
        "judge_approvals row",
        "name, version, artifact sha,\ngolden-set sha\n(immutable)",
        fc=GOLD_FC,
        ec=GOLD,
        bs=6.4,
    )
    box(
        ax,
        136,
        6,
        158,
        20,
        "served",
        "the judge service loads the\nbytes the row names; the worker's\njudge stage checks the approval",
        fc=GREEN_FC,
        ec=GREEN,
        bs=6.2,
    )
    poly(ax, [(116, 36), (116, 27), (51, 27), (51, 20)])
    label(ax, 83, 28.6, "does it match the oracle on data it never saw?", size=6)
    poly(ax, [(142, 33), (142, 24), (84, 24), (84, 20)], color=GOLD)
    arrow(ax, (152, 33), (152, 20), color=GOLD)
    label(ax, 153, 26.5, "loads these\nbytes", color=GOLD, size=5.8, ha="left")
    label(ax, 113, 22.4, "does it agree with the reference on real boards?", color=GOLD, size=6)
    arrow(ax, (96, 13), (104, 13), color=GOLD)
    label(ax, 100, 15.2, "pass →\n--approve", color=GOLD, size=5.8)
    arrow(ax, (128, 13), (136, 13), color=GOLD)
    label(
        ax,
        84,
        3.2,
        "fail: judges v2, v3, v4 were refused here, on the broken-capacitor board",
        color="#e08a7a",
        size=6.2,
    )
    fig.savefig(OUT / "model.png", dpi=140, facecolor=BG, bbox_inches="tight")


def field() -> None:
    """docs/media/field.png: the cross-section the solver sees, and the voltage it finds."""
    import numpy as np

    from pipeline import fields

    g = fields.Geometry(w=0.35, h=0.2, t=0.035, er=4.2)
    x, y = fields._grid(g, 400, 200)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    on = (yy >= g.h - fields.TOL) & (yy <= g.h + g.t + fields.TOL)
    trace = on & (np.abs(xx) <= g.w / 2 + fields.TOL)
    fixed = (yy == 0) | trace
    yc = (y[:-1] + y[1:]) / 2
    k = fields._stiffness(x, y, np.where(yc < g.h, g.er, 1.0))
    phi = fields._solve_fixed(k, fixed, trace.astype(float)).reshape(len(x), len(y))
    p = fields.solve(g)

    fig, (a1, a2) = plt.subplots(
        1, 2, figsize=(13, 5.0), facecolor=BG, gridspec_kw={"width_ratios": [1, 1.18]}
    )
    for ax in (a1, a2):
        ax.set_facecolor(BG)
        for sp_ in ax.spines.values():
            sp_.set_color(GREY)
        ax.tick_params(colors=SUB, labelsize=7 * F)
        ax.set_xlabel("mm", color=SUB, fontsize=7 * F)

    # left: what the solver is given
    W = 0.62
    a1.set_xlim(-W, W)
    a1.set_ylim(-0.06, 0.62)
    a1.set_aspect("equal")
    a1.add_patch(Rectangle((-W, -0.06), 2 * W, 0.06, fc="#c98a3a", ec="none"))
    a1.add_patch(Rectangle((-W, 0), 2 * W, g.h, fc="#25402a", ec="none"))
    a1.add_patch(Rectangle((-W, g.h), 2 * W, 0.62 - g.h, fc=BG, ec="none"))
    a1.add_patch(Rectangle((-g.w / 2, g.h), g.w, g.t, fc="#e6a24a", ec="none"))
    a1.text(
        0,
        g.h + g.t + 0.03,
        "trace  (1 volt)",
        color=TXT,
        ha="center",
        fontsize=8 * F,
        weight="bold",
    )
    a1.text(
        0,
        g.h / 2,
        f"board material, er = {g.er}",
        color="#bfe0c4",
        ha="center",
        va="center",
        fontsize=7.5 * F,
    )
    a1.text(
        0,
        -0.03,
        "ground plane  (0 volt)",
        color="#3a2a12",
        ha="center",
        va="center",
        fontsize=7.5 * F,
        weight="bold",
    )
    a1.text(0, 0.5, "air, er = 1", color=SUB, ha="center", fontsize=7.5 * F)
    a1.annotate(
        "",
        (-g.w / 2, g.h + g.t + 0.012),
        (g.w / 2, g.h + g.t + 0.012),
        arrowprops=dict(arrowstyle="<->", color=GOLD, lw=1.2),
    )
    a1.text(0, g.h + g.t + 0.09, f"w = {g.w} mm", color=GOLD, ha="center", fontsize=7.5 * F)
    a1.annotate("", (0.42, 0), (0.42, g.h), arrowprops=dict(arrowstyle="<->", color=GOLD, lw=1.2))
    a1.text(0.45, g.h / 2, f"h = {g.h} mm", color=GOLD, va="center", fontsize=7.5 * F)
    a1.annotate(
        f"t = {g.t} mm",
        (-g.w / 2, g.h + g.t / 2),
        (-0.5, 0.32),
        color=GOLD,
        fontsize=7.5 * F,
        ha="center",
        arrowprops=dict(arrowstyle="->", color=GOLD, lw=1.0),
    )
    a1.set_title(
        "what the solver is given: a slice across the trace", color=TXT, fontsize=9 * F, pad=10
    )

    # right: what it computes
    X, Y = np.meshgrid(x, y, indexing="ij")
    a2.set_xlim(-W, W)
    a2.set_ylim(0, 0.62)
    a2.set_aspect("equal")
    m = a2.pcolormesh(X, Y, phi, cmap="magma", vmin=0, vmax=1, shading="gouraud", rasterized=True)
    a2.contour(
        X, Y, phi, levels=np.linspace(0.1, 0.9, 9), colors="white", linewidths=0.5, alpha=0.6
    )
    a2.axhline(g.h, color="#bfe0c4", lw=0.8, ls="--", alpha=0.7)
    a2.text(-W + 0.03, g.h + 0.015, "board / air boundary", color="#bfe0c4", fontsize=6.5 * F)
    a2.add_patch(Rectangle((-g.w / 2, g.h), g.w, g.t, fc="none", ec="white", lw=0.8))
    cb = fig.colorbar(m, ax=a2, fraction=0.05, pad=0.03, shrink=0.75)
    cb.set_label("volts", color=SUB, fontsize=7 * F)
    cb.ax.tick_params(colors=SUB, labelsize=6.5 * F)
    a2.set_title(
        f"what it computes: the voltage at every grid point  →  z0 = {p.z0:.1f} ohm",
        color=TXT,
        fontsize=9 * F,
        pad=10,
    )
    fig.suptitle(
        "the 2D field solver: impedance from the electric field around one cross-section",
        color=TXT,
        fontsize=10.5 * F,
        weight="bold",
        y=0.99,
    )
    fig.tight_layout()
    fig.savefig(OUT / "field.png", dpi=140, facecolor=BG, bbox_inches="tight")


if __name__ == "__main__":
    system()
    model()
    field()
    print("ok")
