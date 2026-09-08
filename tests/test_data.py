"""Dataset sampler, shard job, manifest and loader. DB/MinIO tests skip when the compose
services are down; the solver is faked so the tests take under a second."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest

from pipeline import data, db, fields, jobs, storage


def fake_solve(g: Any) -> Any:
    pair = g.s is not None
    return fields.LineParams(
        z0=50.0 * g.h / g.w,
        eps_eff=(g.er + 1) / 2,
        c_pf_per_m=100.0,
        z_even=55.0 if pair else None,
        z_odd=45.0 if pair else None,
        z_diff=90.0 if pair else None,
        coupling=0.1 if pair else None,
    )


class SerialPool:
    """multiprocessing.Pool stand-in: the fake solver only exists in this process."""

    def __init__(self, n: int | None) -> None:
        pass

    def __enter__(self) -> SerialPool:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def map(self, fn: Callable[[Any], Any], xs: list[Any]) -> list[Any]:
        return [fn(x) for x in xs]


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[psycopg.Connection]:
    try:
        c = db.connect()
        storage.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("postgres/minio not running")
    monkeypatch.setattr(fields, "solve", fake_solve, raising=False)
    monkeypatch.setattr(data, "Pool", SerialPool)
    yield c
    with c.cursor() as cur:
        cur.execute("delete from datasets where name = 'test_data'")
    c.commit()
    c.close()


def new_dataset(conn: psycopg.Connection, shards: int = 2, per_shard: int = 5) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "insert into datasets (name, sampler_version, solver_version, seed, shards, "
            "samples_per_shard) values ('test_data', %s, %s, 7, %s, %s) returning id",
            (data.SAMPLER_VERSION, fields.SOLVER_VERSION, shards, per_shard),
        )
        dataset_id = int(cur.fetchone()[0])  # type: ignore[index]
    conn.commit()
    return dataset_id


def test_sample_is_deterministic_and_in_range() -> None:
    rng = random.Random(3)
    a = [data.sample(rng) for _ in range(500)]
    rng = random.Random(3)
    assert a == [data.sample(rng) for _ in range(500)]
    for g in a:
        assert 0.1 <= g.w <= 2.0 and 0.08 <= g.h <= 1.6 and 3.0 <= g.er <= 4.8
        assert g.t in data.COPPER_MM
        assert g.s is None or 0.1 <= g.s <= 3.0
    assert 200 <= sum(g.s is not None for g in a) <= 300


def test_shard_job_writes_object_and_row(conn: psycopg.Connection) -> None:
    dataset_id = new_dataset(conn)
    data.run_job(conn, jobs.Job(0, "data:shard", {"dataset_id": dataset_id, "shard": 1}, 1))
    with conn.cursor() as cur:
        cur.execute(
            "select object_key, sha256, n_samples from dataset_shards where dataset_id = %s",
            (dataset_id,),
        )
        key, sha, n = cur.fetchone()  # type: ignore[misc]
    assert key == f"datasets/{dataset_id}/shard-001.jsonl" and n == 5
    body = storage.get(key)
    assert storage.sha256(body) == sha
    rows = [json.loads(line) for line in body.decode().splitlines()]
    assert [r["i"] for r in rows] == list(range(5))
    assert set(rows[0]) == {"i", "geometry", "params", "solver", "seconds"}
    assert set(rows[0]["geometry"]) == {"w", "h", "t", "er", "s"}
    assert set(rows[0]["params"]) == {
        "z0", "eps_eff", "c_pf_per_m", "z_even", "z_odd", "z_diff", "coupling"
    }  # fmt: skip
    assert rows[0]["solver"] == fields.SOLVER_VERSION


def test_finalize_needs_every_shard_and_hashes_them(conn: psycopg.Connection) -> None:
    dataset_id = new_dataset(conn)
    data.run_shard(conn, dataset_id, 0)
    with pytest.raises(ValueError, match=r"shards not finished: \[1\]"):
        data.finalize(conn, dataset_id)
    data.run_shard(conn, dataset_id, 1)
    manifest = data.finalize(conn, dataset_id)
    assert manifest["n_samples"] == 10 and [s["shard"] for s in manifest["shards"]] == [0, 1]
    for s in manifest["shards"]:
        assert storage.sha256(storage.get(s["object_key"])) == s["sha256"]
    assert json.loads(storage.get(data.manifest_key(dataset_id))) == manifest
    assert len(data.load(dataset_id)) == 10


def test_load_rejects_sha256_mismatch(conn: psycopg.Connection) -> None:
    dataset_id = new_dataset(conn, shards=1)
    data.run_shard(conn, dataset_id, 0)
    manifest = data.finalize(conn, dataset_id)
    manifest["shards"][0]["sha256"] = "0" * 64
    key = storage.put(f"datasets/{dataset_id}/tampered.json", json.dumps(manifest).encode())
    with pytest.raises(ValueError, match="sha256 mismatch"):
        data.load(key)
