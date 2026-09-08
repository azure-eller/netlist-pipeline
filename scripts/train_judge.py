#!/usr/bin/env python
"""Distill the rule judge into models/judge-v2.joblib and upload it to S3.

Corpus: three real boards (the human pic_programmer fixture, the chosen generated
pic_programmer of run 34, the chosen generated rpi_hat of run 27) plus SAMPLES random
mutations of each, labeled with judge.score. See docs/experiments/judge-v2.md.
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import joblib
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from pipeline import board, judge, learned, storage
from pipeline.db import connect
from pipeline.models import Board, Constraints, Netlist, Via

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/pic_programmer/pic_programmer.kicad_pcb"
VERSION = sys.argv[1] if len(sys.argv) > 1 else "v2"  # scripts/train_judge.py v3 100
MAX_MOVE_MM = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
OUT = ROOT / f"models/judge-{VERSION}.joblib"
S3_KEY = f"models/judge/{VERSION}.joblib"
SAMPLES = 2000
SEED = 0

Sample = tuple[Board, Netlist, Constraints]
Mutation = Callable[[Board, Constraints, random.Random], Board]


def load_run(run_id: int) -> Sample:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select board_key from candidates where run_id = %s and chosen", (run_id,))
        key = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("select body from constraints where run_id = %s", (run_id,))
        constraints = Constraints.from_json(cur.fetchone()[0])  # type: ignore[index]
    netlist = Netlist.from_json(json.loads(storage.get(f"runs/{run_id}/netlist.json")))
    return board.parse(storage.get(key).decode()), netlist, constraints


def move_cap(b: Board, c: Constraints, rng: random.Random) -> Board:
    caps = [f for f in b.footprints if f.ref.startswith("C")]
    if not caps:
        return b
    f, d, a = rng.choice(caps), rng.uniform(0, MAX_MOVE_MM), rng.uniform(0, 2 * math.pi)
    dx, dy = d * math.cos(a), d * math.sin(a)
    x1, y1, x2, y2 = f.courtyard
    moved = replace(
        f,
        x=f.x + dx,
        y=f.y + dy,
        pads=tuple(replace(p, x=p.x + dx, y=p.y + dy) for p in f.pads),
        courtyard=(x1 + dx, y1 + dy, x2 + dx, y2 + dy),
    )
    return replace(b, footprints=tuple(moved if g is f else g for g in b.footprints))


def scale_rail(b: Board, c: Constraints, rng: random.Random) -> Board:
    rails = [n for n, cls in c.classes.items() if cls == "power"]
    if not rails:
        return b
    rail, k = rng.choice(rails), rng.uniform(0.3, 2.0)
    return replace(
        b, segments=tuple(replace(s, width=s.width * k) if s.net == rail else s for s in b.segments)
    )


def add_vias(b: Board, c: Constraints, rng: random.Random) -> Board:
    fast = [n for n, cls in c.classes.items() if cls in judge.FAST]
    if not fast:
        return b
    net, (x1, y1, x2, y2) = rng.choice(fast), b.outline
    new = [
        Via(net, rng.uniform(x1, x2), rng.uniform(y1, y2), 0.8, 0.4)
        for _ in range(rng.randint(1, 4))
    ]
    return replace(b, vias=b.vias + tuple(new))


def drop_net(b: Board, c: Constraints, rng: random.Random) -> Board:
    nets = sorted({s.net for s in b.segments if s.net})
    if not nets:
        return b
    net = rng.choice(nets)
    return replace(b, segments=tuple(s for s in b.segments if s.net != net))


MUTATIONS: tuple[Mutation, ...] = (move_cap, scale_rail, add_vias, drop_net)


def mutate(b: Board, c: Constraints, rng: random.Random) -> Board:
    for m in rng.sample(MUTATIONS, rng.randint(1, len(MUTATIONS))):
        b = m(b, c, rng)
    return b


def main() -> None:
    t0 = time.perf_counter()
    pic_run = load_run(34)
    corpus: dict[str, Sample] = {
        "pic_programmer human fixture": (board.parse(FIXTURE.read_text()), *pic_run[1:]),
        "pic_programmer generated (run 34)": pic_run,
        "rpi_hat generated (run 27)": load_run(27),
    }
    rng = random.Random(SEED)
    rows: list[list[float]] = []
    labels: list[float] = []
    for name, (b, nl, c) in corpus.items():
        boards = [b] + [mutate(b, c, rng) for _ in range(SAMPLES)]
        for mb in boards:
            rows.append(list(learned.features(mb, nl, c).values()))
            labels.append(judge.score(mb, nl, c).score)
        print(f"{name}: {len(boards)} samples, rule score of original {labels[-len(boards)]:.3f}")

    x_train, x_test, y_train, y_test = train_test_split(
        rows, labels, test_size=0.2, random_state=SEED
    )
    model = GradientBoostingRegressor(random_state=SEED).fit(x_train, y_train)
    pred = model.predict(x_test)
    r2, mae = float(r2_score(y_test, pred)), float(mean_absolute_error(y_test, pred))
    artifact = {
        "name": "learned-gbr",
        "version": VERSION,
        "max_move_mm": MAX_MOVE_MM,
        "model": model,
        "feature_names": list(learned.FEATURES),
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus_size": len(rows),
        "r2": r2,
        "mae": mae,
        "note": "distilled from the rule judge v0.1.0; exists to exercise serving, versioning "
        "and gating, not to be better physics",
    }
    OUT.parent.mkdir(exist_ok=True)
    joblib.dump(artifact, OUT)
    data = OUT.read_bytes()
    storage.put(S3_KEY, data)
    print(
        f"corpus {len(rows)} (held out {len(y_test)}): R2 {r2:.4f}, MAE {mae:.4f}\n"
        f"wrote {OUT} ({len(data)} bytes, sha256 {storage.sha256(data)}) and s3://{S3_KEY}\n"
        f"{time.perf_counter() - t0:.1f}s"
    )


if __name__ == "__main__":
    main()
