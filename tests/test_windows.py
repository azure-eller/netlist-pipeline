"""Windows: the data factory's unit (docs/FACTORY.md step 1). Extraction, hashing, cuts and
labels on the human-routed pic_programmer board and on a synthetic trace; the job test needs
the compose services and skips without them."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from pipeline import board, data, db, fields, jobs, storage, windows
from pipeline.models import Board, Segment, Stackup, Zone

PCB = Path(__file__).parent / "fixtures" / "pic_programmer" / "pic_programmer.kicad_pcb"


@pytest.fixture(scope="module")
def pic() -> Board:
    return board.parse(PCB.read_text())


def test_window_holds_the_net_and_its_neighbours_inside_the_box(pic: Board) -> None:
    w = windows.extract(pic, "/DATA-RB7")
    x1, y1, x2, y2 = w.box
    ox, oy = w.origin
    inside = [
        s for s in pic.segments if ox <= (s.x1 + s.x2) / 2 <= ox + 2 * w.radius_mm
        and oy <= (s.y1 + s.y2) / 2 <= oy + 2 * w.radius_mm
    ]  # fmt: skip
    neighbours = {s.net for s in inside if s.net != w.net}
    assert neighbours and neighbours <= {s.net for s in w.segments}
    assert any(s.net == w.net for s in w.segments)
    for s in w.segments:
        assert x1 - 1e-6 <= s.x1 <= x2 + 1e-6 and y1 - 1e-6 <= s.y2 <= y2 + 1e-6
    for p in w.pads:
        assert x1 - p.w <= p.x <= x2 + p.w and y1 - p.h <= p.y <= y2 + p.h
    assert [z.net for z in w.zones] == ["GND"]


def test_hash_is_stable_and_sees_a_moved_neighbour(pic: Board) -> None:
    w = windows.extract(pic, "/DATA-RB7")
    assert windows.extract(pic, "/DATA-RB7").geometry_hash == w.geometry_hash
    assert windows.Window.from_json(w.to_json()).geometry_hash == w.geometry_hash
    ox, oy = w.origin
    other = next(
        s for s in pic.segments if s.net != w.net
        and ox <= (s.x1 + s.x2) / 2 <= ox + 2 * w.radius_mm
        and oy <= (s.y1 + s.y2) / 2 <= oy + 2 * w.radius_mm
    )  # fmt: skip
    moved = tuple(
        replace(s, y1=s.y1 + 0.1, y2=s.y2 + 0.1) if s is other else s for s in pic.segments
    )
    assert (
        windows.extract(replace(pic, segments=moved), "/DATA-RB7").geometry_hash != w.geometry_hash
    )


def test_cuts_see_plane_width_neighbour_and_label_matches_solver() -> None:
    sig = Segment("SIG", "F.Cu", 10.0, 10.0, 20.0, 10.0, 0.3)
    near = Segment("N2", "F.Cu", 10.0, 11.0, 20.0, 11.0, 0.5)
    gnd = Zone("GND", "B.Cu", ((0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0)))
    b = Board((0, 0, 30, 30), ("F.Cu", "B.Cu"), Stackup(), (), (sig, near), (), (gnd,))
    w = windows.extract(b, "SIG")
    cs = windows.cuts(w)
    assert len(cs) == 10
    for c in cs:
        assert c.plane_below and not c.plane_above and c.layer == "F.Cu"
        assert c.target.width == 0.3
        assert [(x.offset, x.width, x.net) for x in c.conductors if x.offset] == [(1.0, 0.5, "N2")]
    assert windows.label(cs[0]) == fields.solve_cut(cs[0])
    alone = replace(cs[0], conductors=(cs[0].target,), plane_below=False)
    assert windows.label(alone) is None  # no plane, no neighbour: no reference
    assert windows.cuts(windows.extract(replace(b, segments=(replace(sig, x2=10.5), near)), "SIG"))
    assert (
        len(windows.cuts(windows.extract(replace(b, segments=(replace(sig, x2=10.5),)), "SIG")))
        == 3
    )


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[psycopg.Connection]:
    try:
        c = db.connect()
        storage.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("postgres/minio not running")
    monkeypatch.setattr(
        data, "Pool", __import__("tests.test_data", fromlist=["SerialPool"]).SerialPool
    )
    stub = fields.CutParams(50.0, 3.0, 100.0, (), ((100.0,),), ((33.0,),), 0, 1, True)
    monkeypatch.setattr(fields, "solve_cut", lambda c: stub)
    yield c
    with c.cursor() as cur:
        cur.execute("delete from boards where source = 'test_windows'")
    c.commit()
    c.close()


def test_windows_job_inserts_once(conn: psycopg.Connection) -> None:
    raw = PCB.read_bytes() + b"\n"  # a distinct sha256: the fixture itself may be registered
    key = storage.put(f"boards/{storage.sha256(raw)}.kicad_pcb", raw)
    with conn.cursor() as cur:
        cur.execute(
            "insert into boards (source, path, sha256, object_key, family, n_layers, n_nets, "
            "stackup) values ('test_windows', %s, %s, %s, 'pic', 2, 33, '{}') returning id",
            (str(PCB), storage.sha256(raw), key),
        )
        board_id = int(cur.fetchone()[0])  # type: ignore[index]
    conn.commit()
    data.run_job(conn, jobs.Job(0, "data:windows", {"board_id": board_id}, 1))
    with conn.cursor() as cur:
        cur.execute(
            "select net, geometry_hash, object_key, n_cuts, labels->>'n_with_plane', "
            "solver_version from windows where board_id = %s order by net",
            (board_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 33 and rows[0][5] == fields.CUT_SOLVER_VERSION
    rec = json.loads(storage.get(rows[0][2]))
    assert windows.Window.from_json(rec["window"]).geometry_hash == rows[0][1]
    assert len(rec["cuts"]) == rows[0][3] == len(rec["labels"]["cuts"])
    assert data.run_windows(conn, board_id) == 0
