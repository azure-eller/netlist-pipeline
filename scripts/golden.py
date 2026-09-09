"""Golden set: run a judge against golden/<case> and compare with expected.json.

    scripts/golden.py [--judge-url URL] [--capture] [--update] [--approve]

Expected answers come from the rule judge (SPEC.md, "Judge versions and approval"): they catch
regressions and disagreements, they are not ground truth. Every run appends one line to
docs/experiments/golden.jsonl and exits 1 on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from pipeline import board, db, judge
from pipeline.config import settings
from pipeline.models import Constraints, Netlist

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden"
LEDGER = ROOT / "docs" / "experiments" / "golden.jsonl"
CASES = (
    "pic_human",
    "pic_generated",
    "rpi_generated",
    "pic_cap_far",
    "rpi_thin",
    "rpi_fast",  # I2C nets declared high-speed at 50 ohm: impedance rule live, must violate
    "rpi_fast_tuned",  # same nets, target = the closed form's own answer: physics decides
)
ORDER = (("pic_cap_far", "pic_human"), ("rpi_thin", "rpi_generated"))  # worse < better
PIC_GEN_RUN, RPI_GEN_RUN = 34, 27  # local runs the generated boards were captured from
Case = dict[str, Any]  # {"score", "violations": [{"rule", "net", "ref"}], "totals"}


def load_case(name: str) -> tuple[str, Netlist, Constraints]:
    d = GOLDEN / name
    return (
        (d / "board.kicad_pcb").read_text(),
        Netlist.from_json(json.loads((d / "netlist.json").read_text())),
        Constraints.from_json(json.loads((d / "constraints.json").read_text())),
    )


def load_expected() -> dict[str, Case]:
    return {c: json.loads((GOLDEN / c / "expected.json").read_text()) for c in CASES}


def score_rules(name: str, physics: str = "rules") -> Case:
    """In-process judge: the closed-form reference ("rules") or the field solver ("oracle")."""
    from pipeline import physics as ph

    provider = ph.Oracle() if physics == "oracle" else ph.FORMULA
    text, nl, c = load_case(name)
    r = judge.score(board.parse(text), nl, c, physics=provider)
    return {
        "score": r.score,
        "violations": [{"rule": v.rule, "net": v.net, "ref": v.ref} for v in r.violations],
        "totals": r.metrics["totals"],
        "judge": r.judge,
    }


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.judge_token}"} if settings.judge_token else {}


def score_remote(url: str, name: str) -> Case:
    text, nl, c = load_case(name)
    body = {"run_id": 0, "netlist": nl.to_json(), "constraints": c.to_json(), "board_pcb": text}
    r = httpx.post(f"{url}/v1/score", json=body, headers=_headers(), timeout=120)
    r.raise_for_status()
    d = r.json()
    return {
        "score": d["score"],
        "violations": [
            {"rule": v["rule"], "net": v.get("net"), "ref": v.get("ref")} for v in d["violations"]
        ],
        "totals": d["metrics"].get("totals", {}),
        "judge": d.get("judge", {}),
    }


def info(url: str | None) -> dict[str, Any]:
    if not url:
        return dict(judge.JUDGE, capabilities=["score", "violations"])
    r = httpx.get(f"{url}/v1/info", headers=_headers(), timeout=30)
    if r.status_code == 404:
        print(
            f"note: {url}/v1/info is 404; assuming capabilities score+violations, version unknown"
        )
        return {"name": "unknown", "version": "unknown", "capabilities": ["score", "violations"]}
    r.raise_for_status()
    return r.json()  # type: ignore[no-any-return]


def _vset(case: Case) -> set[tuple[str, str | None, str | None]]:
    return {(v["rule"], v.get("net"), v.get("ref")) for v in case["violations"]}


def compare(
    expected: dict[str, Case], got: dict[str, Case], capabilities: list[str]
) -> tuple[bool, dict[str, Case]]:
    """Per case: {score, expected, ok, why}. Score within max(0.5, 10%); violation set equal on
    (rule, net, ref) when the judge reports violations; ordering constraints always."""
    out: dict[str, Case] = {}
    for name in CASES:
        e, g = expected[name], got[name]
        why = []
        tol = max(0.5, 0.1 * abs(e["score"]))
        if abs(g["score"] - e["score"]) > tol:
            why.append(f"score {g['score']:.2f} vs {e['score']:.2f} (tol {tol:.2f})")
        if "violations" in capabilities:
            for label, diff in (("missing", _vset(e) - _vset(g)), ("extra", _vset(g) - _vset(e))):
                if diff:
                    why.append(f"{label} violations {sorted(diff, key=str)}")
        out[name] = {"score": g["score"], "expected": e["score"], "ok": True, "why": ""}
        if why:
            out[name].update(ok=False, why="; ".join(why))
    for lo, hi in ORDER:
        if not got[lo]["score"] < got[hi]["score"]:
            out[lo].update(
                ok=False,
                why=f"{out[lo]['why']}; " * bool(out[lo]["why"])
                + f"{lo} {got[lo]['score']:.2f} not below {hi} {got[hi]['score']:.2f}",
            )
    return all(c["ok"] for c in out.values()), out


def golden_sha() -> str:
    h = hashlib.sha256()
    for p in sorted(GOLDEN.glob("*/expected.json")):
        h.update(p.read_bytes())
    return h.hexdigest()


def approve(name: str, version: str, artifact_sha256: str | None) -> None:
    """One immutable judge_approvals row: this name, version and these bytes passed this set."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into judge_approvals (name, version, artifact_sha256, golden_sha) "
            "values (%s, %s, %s, %s) on conflict do nothing",
            (name, version, artifact_sha256, golden_sha()),
        )
        conn.commit()
    print(
        f"approved {name} {version} artifact {artifact_sha256} against golden {golden_sha()[:12]}"
    )


# ---- capture: build the cases from local runs and fixtures ----


def _write_case(name: str, text: str, nl: dict[str, Any], c: dict[str, Any]) -> None:
    d = GOLDEN / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "board.kicad_pcb").write_text(text)
    (d / "netlist.json").write_text(json.dumps(nl))
    (d / "constraints.json").write_text(json.dumps(c))


def _run_inputs(run_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    import psycopg

    from pipeline import storage

    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "select body from constraints where run_id = %s order by id desc limit 1", (run_id,)
        )
        c = cur.fetchone()[0]  # type: ignore[index]
    return json.loads(storage.get(f"runs/{run_id}/netlist.json")), c


def _chosen_board(run_id: int) -> str:
    import psycopg

    from pipeline import storage

    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute("select board_key from candidates where run_id = %s and chosen", (run_id,))
        return storage.get(cur.fetchone()[0]).decode()  # type: ignore[index]


def _latest_pic_judge_run() -> int:
    import psycopg

    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "select r.id from runs r join designs d on d.id = r.design_id "
            "where r.mode = 'judge' and r.status = 'done' and d.filename like 'pic%' "
            "order by r.id desc limit 1"
        )
        return int(cur.fetchone()[0])  # type: ignore[index]


def cap_far(text: str, cap: str, ic: str, mm: float) -> str:
    """The board with `cap` moved `mm` to the right of `ic` (through pcbnew)."""
    from pipeline import pcb

    b = board.parse(text)
    u, c = b.footprint(ic), b.footprint(cap)
    assert u and c
    d = Path(tempfile.mkdtemp(prefix="golden-"))
    (d / "in.kicad_pcb").write_text(text)
    pcb.set_positions(d / "in.kicad_pcb", {cap: (u.x + mm, u.y, c.rot)}, d / "out.kicad_pcb")
    out = (d / "out.kicad_pcb").read_text()
    shutil.rmtree(d)
    return out


def thin(text: str, net: str, width: float) -> str:
    """Every top-level (segment ...) block on `net` rewritten to `width`. A segment block nests
    exactly one level of parens and has no parens inside its strings, so a regex is enough."""

    def sub(m: re.Match[str]) -> str:
        block = m.group(0)
        if f'(net "{net}")' not in block:
            return block
        return re.sub(r"\(width [\d.]+\)", f"(width {width})", block)

    return re.sub(r"\(segment\b[^()]*(?:\([^()]*\)[^()]*)*\)", sub, text)


def capture() -> None:
    pic_run = _latest_pic_judge_run()
    nl, c = _run_inputs(pic_run)
    human = (ROOT / "tests/fixtures/pic_programmer/pic_programmer.kicad_pcb").read_text()
    print(f"pic_human: fixture board, netlist/constraints from run {pic_run}")
    _write_case("pic_human", human, nl, c)
    _write_case("pic_generated", _chosen_board(PIC_GEN_RUN), nl, c)
    _write_case("pic_cap_far", cap_far(human, "C1", "U1", 30.0), nl, c)
    nl, c = _run_inputs(RPI_GEN_RUN)
    rpi = _chosen_board(RPI_GEN_RUN)
    _write_case("rpi_generated", rpi, nl, c)
    _write_case("rpi_thin", thin(rpi, "+5V", 0.15), nl, c)
    # impedance-live cases: the HAT's routed I2C nets as high-speed. Without these no golden
    # case exercises the physics provider at all (the fixtures' fast nets carry no copper).
    fast = dict(c, classes={**c["classes"], "/ID_SDA": "high_speed", "/ID_SCL": "high_speed"})
    _write_case("rpi_fast", rpi, nl, fast)
    from pipeline import physics

    z_formula = physics.FORMULA.z0(
        0.2,
        c["stackup"]["dielectric_mm"],
        c["stackup"]["copper_um"] / 1000,
        c["stackup"]["er"],
        inner=False,
    )
    tuned = dict(fast, impedance_ohm={**c["impedance_ohm"], "high_speed": round(z_formula, 1)})
    _write_case("rpi_fast_tuned", rpi, nl, tuned)
    update()


def update(physics: str = "rules") -> None:
    """Rewrite expected.json from the in-process judge. With --physics oracle the expected
    answers come from the field solver, which is the truth the surrogate is gated against."""
    for name in CASES:
        case = score_rules(name, physics)
        (GOLDEN / name / "expected.json").write_text(json.dumps(case, indent=1) + "\n")
        print(f"wrote {name}/expected.json ({case['judge']['name']} {case['judge']['version']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--judge-url", help="score via POST URL/v1/score instead of in-process")
    ap.add_argument("--capture", action="store_true", help="rebuild cases from local runs")
    ap.add_argument("--update", action="store_true", help="rewrite expected.json (rules judge)")
    ap.add_argument("--approve", action="store_true", help="record a passing version")
    ap.add_argument(
        "--physics",
        choices=("rules", "oracle"),
        default="rules",
        help="in-process judge to score (and, with --update, to capture expected) with",
    )
    a = ap.parse_args(argv)
    if a.capture:
        capture()
    elif a.update:
        update(a.physics)

    meta = info(a.judge_url)
    got = {
        c: score_remote(a.judge_url, c) if a.judge_url else score_rules(c, a.physics) for c in CASES
    }
    if meta["name"] == "unknown" or not a.judge_url:
        meta.update(got[CASES[0]].get("judge") or {})  # in-process: rules or oracle
    passed, cases = compare(load_expected(), got, meta["capabilities"])

    print(f"judge {meta['name']} {meta['version']} capabilities={meta['capabilities']}")
    print(f"{'case':<14} {'expected':>9} {'got':>9}  ok   why")
    for name, r in cases.items():
        print(f"{name:<14} {r['expected']:>9.2f} {r['score']:>9.2f}  {str(r['ok']):<5}{r['why']}")
    print("PASS" if passed else "FAIL")

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "judge": meta["name"],
                    "version": meta["version"],
                    "judge_url": a.judge_url,
                    "passed": passed,
                    "cases": cases,
                }
            )
            + "\n"
        )
    if passed and a.approve:
        approve(meta["name"], meta["version"], meta.get("artifact_sha256"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
