#!/usr/bin/env python
"""Synthetic cross-section datasets through the job queue (docs/DATA.md).

    scripts/dataset.py create [--kind cross-section|cut] [--shards N] [--per-shard M]
                              [--seed S] [--name NAME] [--only-shard K] [--dataset ID]
    scripts/dataset.py status ID | wait ID [--timeout S] | finalize ID | show ID

`create` inserts the dataset row and enqueues one `data:shard` job per shard; a running worker
solves them. `--only-shard K --dataset ID` re-enqueues one shard of an existing dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import psycopg

from pipeline import data, db, jobs, storage


def create(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    sampler = data.CUT_SAMPLER_VERSION if a.kind == "cut" else data.SAMPLER_VERSION
    with conn.cursor() as cur:
        if a.dataset is None:
            cur.execute(
                "insert into datasets (name, sampler_version, solver_version, seed, shards, "
                "samples_per_shard) values (%s, %s, %s, %s, %s, %s) returning id",
                (
                    a.name or f"{a.kind}-seed{a.seed}",
                    sampler,
                    data.SOLVER_FOR[sampler],
                    a.seed,
                    a.shards,
                    a.per_shard,
                ),
            )
            dataset_id = int(cur.fetchone()[0])  # type: ignore[index]
        else:
            dataset_id = a.dataset
            cur.execute("update datasets set status = 'generating' where id = %s", (dataset_id,))
            cur.execute("select shards from datasets where id = %s", (dataset_id,))
            a.shards = cur.fetchone()[0]  # type: ignore[index]
        shards = [a.only_shard] if a.only_shard is not None else range(a.shards)
        for shard in shards:
            jobs.enqueue(cur, "data:shard", {"dataset_id": dataset_id, "shard": shard})
    conn.commit()
    print(dataset_id)


def rows(conn: psycopg.Connection, dataset_id: int) -> tuple[Any, list[Any], list[Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "select name, status, seed, shards, samples_per_shard, manifest_key, n_samples "
            "from datasets where id = %s",
            (dataset_id,),
        )
        ds = cur.fetchone()
        if ds is None:
            sys.exit(f"dataset {dataset_id} not found")
        cur.execute(
            "select shard, object_key, sha256, n_samples, seconds, finished_at from dataset_shards "
            "where dataset_id = %s order by shard",
            (dataset_id,),
        )
        shards = cur.fetchall()
        cur.execute(
            "select id, payload->>'shard', status, attempts, error from jobs "
            "where kind = 'data:shard' and (payload->>'dataset_id')::bigint = %s order by id",
            (dataset_id,),
        )
        queue = cur.fetchall()
    return ds, shards, queue


def status(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    ds, shards, queue = rows(conn, a.id)
    name, st, seed, n_shards, per_shard, manifest, n = ds
    print(
        f"dataset {a.id} {name!r}: {st}, seed {seed}, {n_shards} x {per_shard}, manifest {manifest}"
    )
    for shard, key, sha, n, secs, fin in shards:
        print(f"  shard {shard}: {key} sha256 {sha[:12]} n={n} {secs:.1f}s finished {fin:%H:%M:%S}")
    for jid, shard, st, attempts, err in queue:
        print(f"  job {jid} shard {shard}: {st} (attempt {attempts}){' ' + err if err else ''}")


def summary(m: dict[str, Any]) -> None:
    print(
        f"dataset {m['dataset_id']} {m['name']!r}: {m['n_samples']} samples in "
        f"{m['n_shards']} shards, sampler {m['sampler_version']}, solver {m['solver_version']}, "
        f"seed {m['seed']}"
    )
    for s in m["shards"]:
        print(f"  shard {s['shard']}: n={s['n_samples']} {s['seconds']:.1f}s {s['sha256'][:12]}")


def finalize(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    summary(data.finalize(conn, a.id))


def wait(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    deadline = time.monotonic() + a.timeout
    while True:
        ds, shards, queue = rows(conn, a.id)
        pending = [q for q in queue if q[2] in ("queued", "running")]
        done = {s[0] for s in shards if s[1]}
        if len(done) >= ds[3]:
            return finalize(conn, a)
        if ds[1] == "failed" or not pending:
            status(conn, a)
            sys.exit(f"dataset {a.id}: shards missing {sorted(set(range(ds[3])) - done)}")
        if time.monotonic() > deadline:
            sys.exit(f"dataset {a.id}: timeout, {len(done)}/{ds[3]} shards done")
        time.sleep(2)


def show(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    ds, _, _ = rows(conn, a.id)
    if ds[5] is None:
        sys.exit(f"dataset {a.id} has no manifest yet (status {ds[1]})")
    summary(json.loads(storage.get(ds[5])))
    for s in data.load(a.id)[:3]:
        print(json.dumps(s))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--kind", choices=("cross-section", "cut"), default="cross-section")
    c.add_argument("--shards", type=int, default=4)
    c.add_argument("--per-shard", type=int, default=1250)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--name")
    c.add_argument("--only-shard", type=int)
    c.add_argument("--dataset", type=int, help="existing dataset id to re-enqueue shards of")
    c.set_defaults(fn=create)
    for name, fn in (("status", status), ("finalize", finalize), ("wait", wait), ("show", show)):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.set_defaults(fn=fn)
        if name == "wait":
            p.add_argument("--timeout", type=float, default=3600)
    a = ap.parse_args()
    with db.connect() as conn:
        a.fn(conn, a)


if __name__ == "__main__":
    main()
