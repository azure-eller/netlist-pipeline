"""Synthetic cross-section datasets for the field-solver surrogates (docs/DATA.md).

A dataset is `shards` shard jobs (`data:shard`), each solving `samples_per_shard` sampled
geometries and writing one JSONL object per shard. Two samplers: `sample` draws one trace or a
pair for `fields.solve` (dataset 17, learned-fd v5); `sample_cut` draws a cut with neighbours
and an optional plane for `fields.solve_cut` (FACTORY.md step 6). The dataset row's
`sampler_version` says which. `finalize` writes the manifest once every shard is in; `load`
reads it back with the hashes checked."""

from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import asdict
from datetime import UTC, datetime
from multiprocessing import Pool
from typing import Any

import psycopg

from pipeline import board, fields, jobs, log, storage, windows

logger = log.get("data")

SAMPLER_VERSION = "cross-section-0.1"
CUT_SAMPLER_VERSION = "cut-0.1"
COPPER_MM = (0.018, 0.035, 0.070)  # 0.5, 1, 2 oz
SOLVER_FOR = {
    SAMPLER_VERSION: fields.SOLVER_VERSION,
    CUT_SAMPLER_VERSION: fields.CUT_SOLVER_VERSION,
}


def _log_uniform(rng: random.Random, lo: float, hi: float) -> float:
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def sample(rng: random.Random) -> fields.Geometry:
    """One cross-section from the ranges real boards use; half are coupled pairs."""
    w = _log_uniform(rng, 0.1, 2.0)
    h = _log_uniform(rng, 0.08, 1.6)
    t = rng.choice(COPPER_MM)
    er = rng.uniform(3.0, 4.8)
    s = _log_uniform(rng, 0.1, 3.0) if rng.random() < 0.5 else None
    return fields.Geometry(w=w, h=h, t=t, er=er, s=s)


def sample_cut(rng: random.Random) -> windows.Cut:
    """One cut: a target trace, a plane half the time, 0-4 neighbours (none 25 %, then
    halving) placed alternately right and left with log-uniform gaps so they never overlap.
    A cut with no plane and no neighbour has no reference and is redrawn."""
    while True:
        w = _log_uniform(rng, 0.1, 2.0)
        h = _log_uniform(rng, 0.08, 1.6)
        t = rng.choice(COPPER_MM)
        er = rng.uniform(3.0, 4.8)
        plane = rng.random() < 0.5
        n = 0
        while n < 4 and rng.random() < (0.75 if n == 0 else 0.5):
            n += 1
        if not plane and n == 0:
            continue
        found = [windows.Conductor(0.0, round(w, 3), "T")]
        edge = {+1: w / 2, -1: w / 2}
        for i in range(n):
            side = 1 if i % 2 == 0 else -1
            wn, gap = _log_uniform(rng, 0.1, 2.0), _log_uniform(rng, 0.1, 5.0)
            centre = side * (edge[side] + gap + wn / 2)
            edge[side] += gap + wn
            found.append(windows.Conductor(round(centre, 3), round(wn, 3), f"N{i + 1}"))
        return windows.Cut(
            x=0.0,
            y=0.0,
            layer="F.Cu",
            conductors=tuple(sorted(found, key=lambda c: c.offset)),
            plane_below=plane,
            plane_above=False,
            h=round(h, 4),
            t=t,
            er=round(er, 3),
        )


def shard_seed(seed: int, shard: int) -> int:
    return seed * 1000 + shard


def manifest_key(dataset_id: int) -> str:
    return f"datasets/{dataset_id}/manifest.json"


def _solve_one(g: fields.Geometry) -> tuple[dict[str, Any], float]:
    t0 = time.perf_counter()
    p = fields.solve(g)
    return asdict(p), time.perf_counter() - t0


def _solve_cut_one(c: windows.Cut) -> tuple[dict[str, Any] | None, float]:
    t0 = time.perf_counter()
    p = fields.solve_cut(c)
    return (asdict(p) if p else None), time.perf_counter() - t0


def run_job(conn: psycopg.Connection, job: jobs.Job) -> None:
    if job.kind == "data:windows":
        run_windows(conn, int(job.payload["board_id"]))
        return
    if job.kind != "data:shard":
        raise NotImplementedError(job.kind)
    dataset_id, shard = int(job.payload["dataset_id"]), int(job.payload["shard"])
    try:
        run_shard(conn, dataset_id, shard)
    except Exception:
        # The worker marks the job done regardless; the dataset row is the durable record.
        logger.exception("shard_failed", dataset_id=dataset_id, shard=shard)
        with conn.cursor() as cur:
            cur.execute("update datasets set status = 'failed' where id = %s", (dataset_id,))
        conn.commit()
        raise


def run_shard(conn: psycopg.Connection, dataset_id: int, shard: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select seed, samples_per_shard, sampler_version, solver_version from datasets "
            "where id = %s",
            (dataset_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"dataset {dataset_id} not found")
    seed, n, sampler_version, solver_version = row
    if solver_version != SOLVER_FOR.get(sampler_version):
        raise ValueError(
            f"dataset wants {sampler_version} solved by {solver_version}; "
            f"this code has {SOLVER_FOR}"
        )
    rng = random.Random(shard_seed(seed, shard))
    t0 = time.perf_counter()
    if sampler_version == SAMPLER_VERSION:
        geoms = [sample(rng) for _ in range(n)]
        with Pool(os.cpu_count()) as pool:
            results = pool.map(_solve_one, geoms)
        records = [
            {
                "i": i,
                "geometry": asdict(g),
                "params": params,
                "solver": solver_version,
                "seconds": s,
            }
            for i, (g, (params, s)) in enumerate(zip(geoms, results, strict=True))
        ]
    else:
        cuts = [sample_cut(rng) for _ in range(n)]
        with Pool(os.cpu_count()) as pool:
            cut_results = pool.map(_solve_cut_one, cuts)
        records = [
            {"i": i, "cut": asdict(c), "params": params, "solver": solver_version, "seconds": s}
            for i, (c, (params, s)) in enumerate(zip(cuts, cut_results, strict=True))
        ]
    seconds = time.perf_counter() - t0
    data = ("\n".join(json.dumps(r) for r in records) + "\n").encode()
    key = storage.put(f"datasets/{dataset_id}/shard-{shard:03d}.jsonl", data, "application/jsonl")
    with conn.cursor() as cur:
        cur.execute(
            "insert into dataset_shards (dataset_id, shard, object_key, sha256, n_samples, "
            "seconds, finished_at) values (%s, %s, %s, %s, %s, %s, now()) "
            "on conflict (dataset_id, shard) do update set object_key = excluded.object_key, "
            "sha256 = excluded.sha256, n_samples = excluded.n_samples, "
            "seconds = excluded.seconds, finished_at = now()",
            (dataset_id, shard, key, storage.sha256(data), n, seconds),
        )
    conn.commit()
    logger.info("shard_done", dataset_id=dataset_id, shard=shard, n=n, seconds=round(seconds, 1))


def finalize(conn: psycopg.Connection, dataset_id: int) -> dict[str, Any]:
    """Write the manifest and mark the dataset ready. Raises if any shard is missing."""
    with conn.cursor() as cur:
        cur.execute(
            "select name, sampler_version, solver_version, seed, shards, samples_per_shard, "
            "created_at from datasets where id = %s",
            (dataset_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"dataset {dataset_id} not found")
        name, sampler_version, solver_version, seed, shards, per_shard, created_at = row
        cur.execute(
            "select shard, object_key, sha256, n_samples, seconds from dataset_shards "
            "where dataset_id = %s and object_key is not null order by shard",
            (dataset_id,),
        )
        done = [
            {"shard": s, "object_key": k, "sha256": h, "n_samples": n, "seconds": sec}
            for s, k, h, n, sec in cur.fetchall()
        ]
    missing = sorted(set(range(shards)) - {d["shard"] for d in done})
    if missing:
        raise ValueError(f"dataset {dataset_id}: shards not finished: {missing}")
    n_samples = sum(d["n_samples"] for d in done)
    manifest = {
        "dataset_id": dataset_id,
        "name": name,
        "sampler_version": sampler_version,
        "solver_version": solver_version,
        "seed": seed,
        "n_shards": shards,
        "samples_per_shard": per_shard,
        "n_samples": n_samples,
        "shards": done,
        "created_at": created_at.isoformat(),
        "finalized_at": datetime.now(UTC).isoformat(),
    }
    key = storage.put(
        manifest_key(dataset_id), json.dumps(manifest, indent=1).encode(), "application/json"
    )
    with conn.cursor() as cur:
        cur.execute(
            "update datasets set status = 'ready', manifest_key = %s, n_samples = %s, "
            "finished_at = now() where id = %s",
            (key, n_samples, dataset_id),
        )
    conn.commit()
    return manifest


def load(dataset: int | str) -> list[dict[str, Any]]:
    """All samples of a dataset (id or manifest key), each shard's sha256 verified."""
    key = manifest_key(int(dataset)) if isinstance(dataset, int) or dataset.isdigit() else dataset
    manifest = json.loads(storage.get(key))
    samples: list[dict[str, Any]] = []
    for shard in manifest["shards"]:
        data = storage.get(shard["object_key"])
        if storage.sha256(data) != shard["sha256"]:
            raise ValueError(f"{shard['object_key']}: sha256 mismatch")
        samples.extend(json.loads(line) for line in data.decode().splitlines() if line)
    return samples


# ---- windows (docs/FACTORY.md step 1) ----


def _label_window(
    w: windows.Window,
) -> tuple[list[windows.Cut], list[dict[str, Any] | None], float]:
    t0 = time.perf_counter()
    cuts = windows.cuts(w)
    params = [windows.label(c) for c in cuts]
    return cuts, [asdict(p) if p else None for p in params], time.perf_counter() - t0


def run_windows(conn: psycopg.Connection, board_id: int) -> int:
    """One window per net with copper on the board; windows whose geometry is already stored
    are skipped, so rerunning is free. Returns the number inserted."""
    with conn.cursor() as cur:
        cur.execute("select object_key from boards where id = %s", (board_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"board {board_id} not found")
    b = board.parse(storage.get(row[0]).decode())
    nets = sorted({s.net for s in b.segments if s.net})
    ws = [windows.extract(b, n) for n in nets]
    with conn.cursor() as cur:  # a row labelled by an older solver is relabelled, not skipped
        cur.execute(
            "select geometry_hash from windows where geometry_hash = any(%s) "
            "and solver_version = %s",
            ([w.geometry_hash for w in ws], fields.CUT_SOLVER_VERSION),
        )
        seen = {r[0] for r in cur.fetchall()}
    fresh = [w for w in ws if w.geometry_hash not in seen]
    if not fresh:
        logger.info("windows_done", board_id=board_id, inserted=0, skipped=len(ws))
        return 0
    with Pool(min(os.cpu_count() or 1, len(fresh))) as pool:
        results = pool.map(_label_window, fresh)
    with conn.cursor() as cur:
        for w, (cuts, params, seconds) in zip(fresh, results, strict=True):
            lps = [fields.CutParams(**p) if p else None for p in params]
            labels = {"cuts": params, **windows.aggregate(lps)}
            record = {
                "window": w.to_json(),
                "cuts": [asdict(c) for c in cuts],
                "labels": labels,
                "board_id": board_id,
                "solver": fields.CUT_SOLVER_VERSION,
                "seconds": seconds,
            }
            key = storage.put(
                f"windows/{w.geometry_hash}.json", json.dumps(record).encode(), "application/json"
            )
            cur.execute(
                "insert into windows (board_id, net, radius_mm, window_version, geometry_hash, "
                "object_key, n_conductors, n_cuts, labels, solver_version, seconds) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (geometry_hash) do update set object_key = excluded.object_key, "
                "labels = excluded.labels, solver_version = excluded.solver_version, "
                "seconds = excluded.seconds "
                "where windows.solver_version is distinct from excluded.solver_version",
                (
                    board_id,
                    w.net,
                    w.radius_mm,
                    windows.WINDOW_VERSION,
                    w.geometry_hash,
                    key,
                    len(w.segments) + len(w.vias) + len(w.pads),
                    len(cuts),
                    json.dumps(labels),
                    fields.CUT_SOLVER_VERSION,
                    seconds,
                ),
            )
    conn.commit()
    logger.info("windows_done", board_id=board_id, inserted=len(fresh), skipped=len(seen))
    return len(fresh)


def load_windows(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Every labelled cut of every window: {cut, params (CutParams | None), family, source,
    board_id, net, geometry_hash}. The real-board test set for a cut model."""
    with conn.cursor() as cur:
        cur.execute(
            "select w.object_key, w.board_id, w.net, w.geometry_hash, b.family, b.source "
            "from windows w join boards b on b.id = w.board_id "
            "where w.solver_version = %s order by w.id",
            (fields.CUT_SOLVER_VERSION,),
        )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for key, board_id, net, ghash, family, source in rows:
        rec = json.loads(storage.get(key))
        for c, p in zip(rec["cuts"], rec["labels"]["cuts"], strict=True):
            out.append(
                {
                    "cut": windows.Cut.from_json(c),
                    "params": fields.CutParams(**p) if p else None,
                    "family": family,
                    "source": source,
                    "board_id": board_id,
                    "net": net,
                    "geometry_hash": ghash,
                }
            )
    return out
