import math
import sys
import types

import pytest

from pipeline import judge
from pipeline.models import (
    Board,
    Component,
    Constraints,
    Footprint,
    JudgeResult,
    Net,
    Netlist,
    Node,
    Pad,
    Segment,
    Stackup,
)

H, T, ER = 1.51, 0.035, 4.5  # default 2-layer stackup, mm / mm / -


def seg(net: str, x1: float, y1: float, x2: float, y2: float, w: float = 0.3) -> Segment:
    return Segment(net, "F.Cu", x1, y1, x2, y2, w)


def fp(ref: str, x: float, y: float, nets: list[str]) -> Footprint:
    """Footprint at (x, y) with pads 1..n spaced 1 mm apart along x."""
    pads = tuple(Pad(str(i + 1), n, x + i, y, 1, 1, ("F.Cu",)) for i, n in enumerate(nets))
    return Footprint(ref, None, "Test:Test", x, y, 0, "F.Cu", pads, (x, y, x + len(nets), y + 1))


def board(fps: list[Footprint], segs: list[Segment] = []) -> Board:  # noqa: B006
    return Board((0, 0, 50, 50), ("F.Cu", "B.Cu"), Stackup(), tuple(fps), tuple(segs), ())


def netlist(nets: dict[str, list[str]]) -> Netlist:
    refs = sorted({k.split(".")[0] for ks in nets.values() for k in ks})
    return Netlist(
        tuple(Component(r, None, None) for r in refs),
        tuple(
            Net(i + 1, name, tuple(Node(*k.split(".")) for k in keys))
            for i, (name, keys) in enumerate(nets.items())
        ),
    )


def rules(result: JudgeResult) -> set[str]:
    return {v.rule for v in result.violations}


# one net U1.1 -> R1.1 routed by one straight trace of the given width
def clk_board(width: float) -> Board:
    return board(
        [fp("U1", 0, 0, ["CLK"]), fp("R1", 10, 0, ["CLK"])], [seg("CLK", 0, 0, 10, 0, width)]
    )


def test_impedance_microstrip_formula() -> None:
    z = judge.z0_microstrip(0.3, H, T, ER)
    expected = 87 / math.sqrt(ER + 1.41) * math.log(5.98 * H / (0.8 * 0.3 + T))
    assert round(z, 3) == round(expected, 3)
    assert 100 < z < 140  # 0.3 mm over 1.51 mm FR-4 is a ~125 ohm line by IPC-2141


def test_impedance_violation_and_pass() -> None:
    c = Constraints(classes={"CLK": "high_speed"})
    nl = netlist({"CLK": ["U1.1", "R1.1"]})
    assert "impedance" in rules(judge.score(clk_board(0.3), nl, c))
    lo, hi = 0.1, 10.0  # bisect z0_microstrip(w) = 50 (Z0 falls as w grows)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if judge.z0_microstrip(mid, H, T, ER) > 50 else (lo, mid)
    assert "impedance" not in rules(judge.score(clk_board(lo), nl, c))


@pytest.mark.parametrize(("cap_x", "violates"), [(3.0, False), (15.0, True)])
def test_decoupling_distance(cap_x: float, violates: bool) -> None:
    c = Constraints(classes={"+3V3": "power", "GND": "power"})
    nl = netlist({"+3V3": ["U1.1", "C1.1"], "GND": ["U1.2", "C1.2"]})
    b = board(
        [fp("U1", 0, 0, ["+3V3", "GND"]), fp("C1", cap_x, 0, ["+3V3", "GND"])],
        [seg("+3V3", 0, 0, cap_x, 0), seg("GND", 1, 0, cap_x + 1, 0)],
    )
    r = judge.score(b, nl, c)
    assert ("decoupling" in rules(r)) is violates
    assert r.metrics["caps"]["C1"] == {"distance_mm": cap_x, "rail": "+3V3"}


@pytest.mark.parametrize(("width", "violates"), [(0.2, True), (2.0, False)])
def test_current_capacity(width: float, violates: bool) -> None:
    c = Constraints(classes={"+5V": "power"}, rail_current_a={"+5V": 2.0})
    nl = netlist({"+5V": ["U1.1", "J1.1"]})
    b = board([fp("U1", 0, 0, ["+5V"]), fp("J1", 10, 0, ["+5V"])], [seg("+5V", 0, 0, 10, 0, width)])
    r = judge.score(b, nl, c)
    assert ("current" in rules(r)) is violates
    assert r.metrics["rails"]["+5V"]["capacity_a"] == judge.current_capacity(width, T)


def test_unrouted_net_is_violation_with_negative_score() -> None:
    r = judge.score(
        board([fp("U1", 0, 0, ["N1"]), fp("R1", 10, 0, ["N1"])]),
        netlist({"N1": ["U1.1", "R1.1"]}),
        Constraints(),
    )
    assert rules(r) == {"unrouted"}
    assert r.score < 0
    assert r.metrics["totals"]["unrouted"] == 1


def test_diff_pair_length_mismatch() -> None:
    c = Constraints(classes={"D_P": "diff", "D_N": "diff"}, diff_pairs=[("D_P", "D_N")])
    nl = netlist({"D_P": ["U1.1", "J1.1"], "D_N": ["U1.2", "J1.2"]})
    b = board(
        [fp("U1", 0, 0, ["D_P", "D_N"]), fp("J1", 10, 0, ["D_P", "D_N"])],
        [seg("D_P", 0, 0, 10, 0), seg("D_N", 1, 0, 13, 0)],
    )
    v = next(v for v in judge.score(b, nl, c).violations if v.rule == "diff_pair_length")
    assert v.measured == pytest.approx(2.0)
    assert v.threshold == 1.0


def test_clean_board_scores_zero() -> None:
    r = judge.score(clk_board(0.3), netlist({"CLK": ["U1.1", "R1.1"]}), Constraints())
    assert r.violations == []
    assert r.score == 0.0
    assert r.judge == {"name": "rules", "version": "0.1.0"}


def test_judge_api_auth_and_score(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from pipeline.config import settings

    try:
        import pipeline.board as bm
    except ImportError:  # board.py is being written by another agent
        bm = types.ModuleType("pipeline.board")
        monkeypatch.setitem(sys.modules, "pipeline.board", bm)
    monkeypatch.setattr(bm, "parse", lambda text: clk_board(0.3), raising=False)
    monkeypatch.setattr(settings, "judge_token", "secret")
    from pipeline import judge_api

    client = TestClient(judge_api.app)
    body = {
        "run_id": 1,
        "netlist": netlist({"CLK": ["U1.1", "R1.1"]}).to_json(),
        "constraints": Constraints().to_json(),
        "board_pcb": "(kicad_pcb)",
    }
    assert client.post("/v1/score", json=body).status_code == 401
    ok = client.post("/v1/score", json=body, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert ok.json()["score"] == 0.0
    assert client.get("/healthz").json() == {"status": "ok"}
