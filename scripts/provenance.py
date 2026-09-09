"""Draw one run's provenance as a hash graph: stage boxes, an arrow wherever a stage's
input_hash equals an earlier output_hash, and artifacts hanging under the stage whose output
they are.

    scripts/provenance.py RUN_ID [OUT.png]
"""

from __future__ import annotations

import sys

import matplotlib
from matplotlib.patches import FancyBboxPatch

from pipeline import db

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BG, STAGE, LINK, DEAD = "#0f1115", "#4f8fe6", "#4caf50", "#555"


def main() -> None:
    run_id = int(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else f"provenance_{run_id}.png"
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select d.filename, d.sha256, r.mode from runs r join designs d on d.id = r.design_id "
            "where r.id = %s",
            (run_id,),
        )
        fname, dsha, mode = cur.fetchone()  # type: ignore[misc]
        cur.execute(
            "select s.name, s.tool, s.tool_version, s.input_hash, s.output_hash, "
            "(select extract(epoch from j.finished_at - j.started_at) from jobs j "
            " where j.kind = s.name and j.payload->>'run_id' = %s order by j.id desc limit 1) "
            "from stages s where s.run_id = %s and s.status = 'done' order by s.id",
            (str(run_id), run_id),
        )
        stages = cur.fetchall()
        cur.execute(
            "select name, sha256, bytes from artifacts where run_id = %s order by name", (run_id,)
        )
        arts = cur.fetchall()
        cur.execute(
            "select seed, proxy_cost, score, chosen from candidates where run_id = %s "
            "order by seed",
            (run_id,),
        )
        cands = cur.fetchall()

    fig, ax = plt.subplots(figsize=(2.3 * len(stages) + 2, 7), facecolor=BG)
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.set_xlim(-1.2, 2.2 * len(stages) + 0.8)
    ax.set_ylim(-5.2, 2.6)

    def box(x: float, y: float, w: float, h: float, fc: str, ec: str) -> None:
        ax.add_patch(
            FancyBboxPatch(
                (x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.05", fc=fc, ec=ec, lw=1.2
            )
        )

    mono = {"family": "monospace", "ha": "center"}
    box(-0.6, 0, 1.4, 1.1, "#2a2320", "#d9822b")
    ax.text(-0.6, 0.15, fname, color="#fff", ha="center", fontsize=9)
    ax.text(-0.6, -0.2, dsha[:12] + "…", color="#d9822b", fontsize=7, **mono)
    xs: dict[str, float] = {}
    for i, (name, tool, ver, ih, _oh, secs) in enumerate(stages):
        x = 2.2 * i + 0.9
        xs[name] = x
        box(x, 0, 1.6, 1.1, "#16213a", STAGE)
        ax.text(x, 0.28, name, color="#fff", ha="center", fontsize=9, weight="bold")
        ax.text(x, 0.0, f"{tool} {ver[:10]}", color="#9fb3d1", ha="center", fontsize=6.5)
        if secs is not None:
            ax.text(x, -0.3, f"{secs:.1f}s", color="#6c7a8f", ha="center", fontsize=6.5)
        px = 2.2 * (i - 1) + 0.9 if i else -0.6
        linked = ih == (stages[i - 1][4] if i else dsha)
        color = LINK if linked else DEAD
        ax.annotate(
            "",
            xy=(x - 0.8, 0),
            xytext=(px + (0.8 if i else 0.7), 0),
            arrowprops=dict(
                arrowstyle="->", color=color, lw=1.6 if linked else 1, ls="-" if linked else "--"
            ),
        )
        label = (ih[:10] + "…") if linked else "≠"
        ax.text((x - 0.8 + px + 0.75) / 2, 0.42, label, color=color, fontsize=6.5, **mono)
        if not linked and i:
            src = next((s[0] for s in stages[:i] if s[4] == ih), None)
            if src:
                ax.annotate(
                    "",
                    xy=(x - 0.3, 0.55),
                    xytext=(xs[src] + 0.3, 0.55),
                    arrowprops=dict(
                        arrowstyle="->", color=LINK, lw=1.2, connectionstyle="arc3,rad=-0.35"
                    ),
                )
                ax.text(
                    (x + xs[src]) / 2,
                    1.35 + 0.35 * (i % 2),
                    f"reads {src} output {ih[:10]}…",
                    color=LINK,
                    fontsize=6.5,
                    **mono,
                )
    by_out = {s[4]: s[0] for s in stages}
    slot: dict[str, int] = {}
    for n, sha, by in arts:
        owner = by_out.get(sha, "export")
        k = slot.get(owner, 0)
        slot[owner] = k + 1
        x, y = xs[owner], -1.4 - 0.75 * k
        hit = sha in by_out
        box(x, y, 1.7, 0.6, "#1f2a1f" if hit else "#1a1a1a", LINK if hit else DEAD)
        ax.text(x, y + 0.1, n, color="#eee", ha="center", fontsize=7)
        ax.text(
            x,
            y - 0.17,
            f"{sha[:10]}…  {by:,} B",
            color="#8fbf8f" if hit else "#888",
            fontsize=6,
            **mono,
        )
        if k == 0:
            ax.plot(
                [x, x], [-0.55, y + 0.3], color=LINK if hit else DEAD, lw=1, ls="-" if hit else ":"
            )
    if cands and "place" in xs:
        for k, (seed, pc, sc, ch) in enumerate(cands):
            x, y = xs["place"], -1.4 - 0.75 * k
            box(x, y, 1.7, 0.6, "#2a2a1a", "#e6d54a" if ch else "#777")
            ax.text(
                x,
                y + 0.1,
                f"seed {seed}{'  ★ chosen' if ch else ''}",
                color="#eee",
                ha="center",
                fontsize=7,
            )
            ax.text(
                x, y - 0.17, f"proxy {pc:.0f}   score {sc:.2f}", color="#ccc", fontsize=6, **mono
            )
            if k == 0:
                ax.plot([x, x], [-0.55, y + 0.3], color="#e6d54a", lw=1)
    ax.text(
        -1.1,
        2.25,
        f"Provenance of run {run_id} ({mode} mode): every stage's input hash is an earlier "
        "stage's output hash",
        color="#fff",
        fontsize=12,
        weight="bold",
        ha="left",
    )
    ax.text(
        -1.1,
        1.9,
        "green arrow = sha256 recorded by the next stage equals the sha256 this stage wrote;  "
        "≠ = the stage hashed something else (shown by the arc);  "
        "blobs = artifacts the API serves, under the stage that produced them",
        color="#aaa",
        fontsize=8,
        ha="left",
    )
    fig.savefig(out, dpi=130, facecolor=BG, bbox_inches="tight")
    print(out)


if __name__ == "__main__":
    main()
