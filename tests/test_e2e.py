"""End to end against the compose services: the API in-process, the worker driven inline.

`make e2e` runs this after `make up` and `make migrate`. Each test uploads a fixture through
the FastAPI app, then drains the queue by calling the worker's stage runner directly, so the
test sees exactly what a deployed worker would do, without a second process."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from pipeline import db, jobs, stages, storage

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client() -> TestClient:
    try:
        db.migrate()
    except psycopg.OperationalError:
        pytest.skip("postgres not running")
    storage.ensure_bucket()
    stages.load_all()
    from pipeline.api import app

    return TestClient(app)


def zip_dir(d: Path, drop: set[str] = frozenset()) -> bytes:  # type: ignore[assignment]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix not in drop and not p.name.startswith("."):
                z.write(p, p.relative_to(d))
    return buf.getvalue()


def drain(run_id: int) -> None:
    """Run queued jobs until the run reaches a terminal status."""
    with db.connect() as conn:
        for _ in range(50):
            job = jobs.claim(conn)
            if job is None:
                break
            try:
                stages.run(conn, job)
            except Exception as e:  # noqa: BLE001
                jobs.finish(conn, job, error=str(e))
                break
            jobs.finish(conn, job)
        with conn.cursor() as cur:
            cur.execute("select status from runs where id = %s", (run_id,))
            assert cur.fetchone()[0] in ("done", "failed", "failed_verification")  # type: ignore[index]


def upload(client: TestClient, name: str, data: bytes, **params: Any) -> dict[str, Any]:
    r = client.post("/designs", files={"file": (name, data)}, params=params)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def run_json(client: TestClient, run_id: int) -> dict[str, Any]:
    r = client.get(f"/runs/{run_id}")
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def test_judge_mode_on_human_routed_board(client: TestClient) -> None:
    up = upload(client, "pic.zip", zip_dir(FIX / "pic_programmer"))
    drain(up["run_id"])
    run = run_json(client, up["run_id"])
    assert run["mode"] == "judge"
    assert [s["status"] for s in run["stages"]] == ["done"] * 6, run
    assert run["status"] == "done", run
    v = run["verification"]
    assert v["passed"] and v["netlist_match"] and v["unrouted"] == 0 and v["in_bounds"]
    assert {"gerbers.zip", "report.json", "board.kicad_pcb"} <= {
        a["name"] for a in run["artifacts"]
    }
    # provenance on every stage
    assert all(s["tool"] and s["tool_version"] and s["input_hash"] for s in run["stages"])
    # same bytes again: same design, new run (SPEC invariant 3)
    again = upload(client, "pic.zip", zip_dir(FIX / "pic_programmer"))
    assert again["design_id"] == up["design_id"] and again["run_id"] != up["run_id"]


def test_generate_mode_from_schematic(client: TestClient) -> None:
    up = upload(client, "rpi_hat.zip", zip_dir(FIX / "rpi_hat"), mode="generate", seeds=1)
    drain(up["run_id"])
    run = run_json(client, up["run_id"])
    assert run["mode"] == "generate"
    assert [s["name"] for s in run["stages"]] == stages.ORDER["generate"], run
    assert run["status"] in ("done", "failed_verification"), run
    net = client.get(f"/designs/{up['design_id']}").json()
    assert net["has_board"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select nn.ref || '.' || nn.pin from net_nodes nn join nets n on n.id = nn.net_id "
            "where n.design_id = %s and n.name = '+3V3'",
            (up["design_id"],),
        )
        assert {r[0] for r in cur.fetchall()} == {
            "C1.1",
            "J1.1",
            "J1.17",
            "JP1.2",
            "R1.1",
            "R2.1",
            "U1.8",
        }
    assert len(run["candidates"]) == 1 and run["candidates"][0]["chosen"]
    assert run["verification"]["netlist_match"], run["verification"]


def test_verify_catches_a_broken_board(client: TestClient) -> None:
    """A pad moved to another net must fail netlist equivalence (SPEC invariant 5)."""
    d = FIX / "pic_programmer"
    pcb = (d / "pic_programmer.kicad_pcb").read_text()
    # swap the net of the first two pads that carry different nets
    import re

    nets = re.findall(r'\(net (\d+) "([^"]+)"\)', pcb)
    a, b = nets[1], nets[2]
    broken = (
        pcb.replace(f'(net {a[0]} "{a[1]}")', "\x00", 1)
        .replace(f'(net {b[0]} "{b[1]}")', f'(net {a[0]} "{a[1]}")', 1)
        .replace("\x00", f'(net {b[0]} "{b[1]}")', 1)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(d.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                z.write(p, p.relative_to(d)) if p.suffix != ".kicad_pcb" else z.writestr(
                    str(p.relative_to(d)), broken
                )
    up = upload(client, "broken.zip", buf.getvalue())
    drain(up["run_id"])
    run = run_json(client, up["run_id"])
    assert run["status"] == "failed_verification", run["status"]
    assert run["verification"]["netlist_match"] is False
    assert json.dumps(run["verification"]["drc"])  # summary is JSON
