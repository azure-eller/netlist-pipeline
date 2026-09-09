#!/usr/bin/env python
"""Data factory (docs/FACTORY.md). Step 1: boards in, windows with labels out.

    scripts/factory.py add-board PATH [--source fixture] [--family NAME]
    scripts/factory.py add-runs            # every routed candidate in `candidates`
    scripts/factory.py windows (BOARD_ID | --all) [--wait]
    scripts/factory.py boards
    scripts/factory.py show GEOMETRY_HASH

`add-board` uploads a .kicad_pcb and inserts its row; `windows` enqueues one `data:windows`
job per board for a running worker; `show` prints a window's cuts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import psycopg

from pipeline import board, db, jobs, storage, windows


def add_board(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    path = Path(a.path)
    data = path.read_bytes()
    b = board.parse(data.decode())
    sha = storage.sha256(data)
    key = storage.put(f"boards/{sha}.kicad_pcb", data, "text/plain")
    with conn.cursor() as cur:
        cur.execute(
            "insert into boards (source, path, sha256, object_key, family, n_layers, n_nets, "
            "stackup) values (%s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (sha256) do update set path = excluded.path returning id",
            (
                a.source,
                str(path),
                sha,
                key,
                a.family or path.parent.name,
                len(b.copper_layers),
                len({s.net for s in b.segments if s.net}),
                json.dumps(asdict(b.stackup)),
            ),
        )
        board_id = int(cur.fetchone()[0])  # type: ignore[index]
    conn.commit()
    print(board_id)


FAMILY = {"pic": "pic_programmer", "rpi": "rpi_hat"}


def add_runs(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    """Every routed candidate of every run as a board, family = the design it came from
    (FACTORY.md step 3: our own placer and router as a board source)."""
    with conn.cursor() as cur:
        cur.execute(
            "select c.run_id, c.seed, c.board_key, d.filename from candidates c "
            "join runs r on r.id = c.run_id join designs d on d.id = r.design_id "
            "where c.board_key like '%%routed.kicad_pcb' order by c.id"
        )
        rows = cur.fetchall()
    added = 0
    for run_id, seed, key, filename in rows:
        family = next((f for k, f in FAMILY.items() if filename.lower().startswith(k)), None)
        if family is None:
            print(f"run {run_id}: design {filename!r} has no known family; skipped")
            continue
        data = storage.get(key)
        b = board.parse(data.decode())
        with conn.cursor() as cur:
            cur.execute(
                "insert into boards (source, path, sha256, object_key, family, n_layers, n_nets, "
                "stackup) values ('run', %s, %s, %s, %s, %s, %s, %s) on conflict (sha256) "
                "do nothing returning id",
                (
                    f"run {run_id} seed {seed}",
                    storage.sha256(data),
                    key,
                    family,
                    len(b.copper_layers),
                    len({s.net for s in b.segments if s.net}),
                    json.dumps(asdict(b.stackup)),
                ),
            )
            added += cur.fetchone() is not None
    conn.commit()
    print(f"{added} boards added from {len(rows)} routed candidates")


def enqueue_windows(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    with conn.cursor() as cur:
        if a.all:
            cur.execute("select id from boards order by id")
            ids = [r[0] for r in cur.fetchall()]
        else:
            ids = [a.board_id]
        job_ids = [jobs.enqueue(cur, "data:windows", {"board_id": i}) for i in ids]
    conn.commit()
    print("enqueued", job_ids)
    if not a.wait:
        return
    deadline = time.monotonic() + a.timeout
    while True:
        with conn.cursor() as cur:
            cur.execute(
                "select id, status, error from jobs where id = any(%s) order by id", (job_ids,)
            )
            rows = cur.fetchall()
        conn.commit()
        if all(r[1] in ("done", "failed") for r in rows):
            break
        if time.monotonic() > deadline:
            sys.exit(f"timeout; jobs: {rows}")
        time.sleep(2)
    for jid, st, err in rows:
        print(f"job {jid}: {st}{' ' + err if err else ''}")
    boards(conn, a)


def boards(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select b.id, b.source, b.family, b.n_layers, b.n_nets, count(w.id), "
            "coalesce(sum(w.n_cuts), 0), "
            "coalesce(sum((w.labels->>'n_with_plane')::int), 0), coalesce(sum(w.seconds), 0) "
            "from boards b left join windows w on w.board_id = b.id "
            "group by b.id order by b.id"
        )
        rows = cur.fetchall()
    print("id  source   family            layers nets windows cuts with_plane seconds")
    for r in rows:
        print(f"{r[0]:<3} {r[1]:<8} {r[2]:<17} {r[3]:>6} {r[4]:>4} {r[5]:>7} {r[6]:>4}", end="")
        print(f" {r[7]:>10} {r[8]:>7.1f}")


def show(conn: psycopg.Connection, a: argparse.Namespace) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select object_key, board_id, net from windows where geometry_hash like %s",
            (a.hash + "%",),
        )
        row = cur.fetchone()
    if row is None:
        sys.exit(f"no window with hash {a.hash}*")
    rec = json.loads(storage.get(row[0]))
    w = windows.Window.from_json(rec["window"])
    assert w.geometry_hash == rec["window"]["geometry_hash"], "stored window does not re-hash"
    nets = sorted({s.net for s in w.segments if s.net} | {p.net for p in w.pads if p.net})
    print(
        f"window {w.geometry_hash[:12]} board {row[1]} net {w.net!r} origin {w.origin} "
        f"radius {w.radius_mm} mm; {len(w.segments)} segments, {len(w.vias)} vias, "
        f"{len(w.pads)} pads, {len(w.zones)} zones; nets: {', '.join(nets)}"
    )
    print(f"stackup {w.stackup}; solver {rec['solver']} {rec['seconds']:.2f}s")
    print("   x      y   layer  target_w  plane  z0      neighbours (offset:width:net)")
    for c, p in zip(rec["cuts"], rec["labels"]["cuts"], strict=True):
        cond = [x for x in c["conductors"] if x["offset"] != 0.0]
        tw = next(x["width"] for x in c["conductors"] if x["offset"] == 0.0)
        plane = ("B" if c["plane_below"] else "") + ("A" if c["plane_above"] else "") or "-"
        z0 = f"{p['z0']:6.1f}" if p else "   -  "
        neigh = " ".join(f"{x['offset']:+.2f}:{x['width']:.2f}:{x['net']}" for x in cond)
        print(f"{c['x']:6.2f} {c['y']:6.2f}  {c['layer']:<6} {tw:8.3f}  {plane:<5}  {z0}  {neigh}")
    agg = {k: v for k, v in rec["labels"].items() if k != "cuts"}
    print(agg)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add-board")
    p.add_argument("path")
    p.add_argument("--source", default="fixture")
    p.add_argument("--family")
    p.set_defaults(fn=add_board)
    sub.add_parser("add-runs").set_defaults(fn=add_runs)
    p = sub.add_parser("windows")
    p.add_argument("board_id", type=int, nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=float, default=3600)
    p.set_defaults(fn=enqueue_windows)
    sub.add_parser("boards").set_defaults(fn=boards)
    p = sub.add_parser("show")
    p.add_argument("hash")
    p.set_defaults(fn=show)
    a = ap.parse_args()
    if a.cmd == "windows" and not a.all and a.board_id is None:
        ap.error("windows needs BOARD_ID or --all")
    with db.connect() as conn:
        a.fn(conn, a)


if __name__ == "__main__":
    main()
