from pipeline import placer
from pipeline.models import (
    BBox,
    Board,
    Constraints,
    Footprint,
    Net,
    Netlist,
    Node,
    Pad,
    Stackup,
)


def _fp(
    ref: str,
    x: float,
    y: float,
    pads: list[tuple[str, str, float, float]],
    half: tuple[float, float],
) -> Footprint:
    return Footprint(
        ref=ref,
        value=None,
        name=f"Test:{ref}",
        x=x,
        y=y,
        rot=0.0,
        layer="F.Cu",
        pads=tuple(Pad(n, net, x + dx, y + dy, 1.0, 1.0, ("F.Cu",)) for n, net, dx, dy in pads),
        courtyard=(x - half[0], y - half[1], x + half[0], y + half[1]),
    )


HALF = {"U1": (3.0, 3.0), "C1": (1.25, 0.75), "R1": (1.25, 0.75), "J1": (2.0, 2.0)}


def _board(u1: tuple[float, float], c1: tuple[float, float], r1: tuple[float, float]) -> Board:
    return Board(
        outline=(0.0, 0.0, 40.0, 30.0),
        copper_layers=("F.Cu", "B.Cu"),
        stackup=Stackup(),
        footprints=(
            _fp(
                "U1",
                *u1,
                [
                    ("1", "+3V3", -2, -2),
                    ("2", "GND", 2, -2),
                    ("3", "SIG_A", 2, 2),
                    ("4", "SIG_B", -2, 2),
                ],
                HALF["U1"],
            ),
            _fp("C1", *c1, [("1", "+3V3", -0.75, 0), ("2", "GND", 0.75, 0)], HALF["C1"]),
            _fp("R1", *r1, [("1", "SIG_A", -0.75, 0), ("2", "SIG_B", 0.75, 0)], HALF["R1"]),
            _fp("J1", 35.0, 25.0, [("1", "+3V3", -1, 0), ("2", "GND", 1, 0)], HALF["J1"]),
        ),
        segments=(),
        vias=(),
    )


def _net(code: int, name: str, *pins: str) -> Net:
    return Net(code, name, tuple(Node(*p.split(".")) for p in pins))


NETLIST = Netlist(
    components=(),
    nets=(
        _net(1, "+3V3", "U1.1", "C1.1", "J1.1"),
        _net(2, "GND", "U1.2", "C1.2", "J1.2"),
        _net(3, "SIG_A", "U1.3", "R1.1"),
        _net(4, "SIG_B", "U1.4", "R1.2"),
    ),
)
CONSTRAINTS = Constraints(
    classes={"+3V3": "power", "GND": "power"}, fixed={"J1": (35.0, 25.0, 0.0)}
)
SPREAD = _board((20, 15), (5, 5), (5, 25))


def _courtyard(ref: str, pose: tuple[float, float, float]) -> BBox:
    x, y, rot = pose
    hw, hh = HALF[ref]
    if rot % 180 == 90:
        hw, hh = hh, hw
    return (x - hw, y - hh, x + hw, y + hh)


def test_overlap_costs_more_than_spread() -> None:
    stacked = _board((20, 15), (20, 15), (20, 15))
    poses = {f.ref: (f.x, f.y, f.rot) for f in stacked.footprints}
    assert placer.cost(poses, stacked, NETLIST, CONSTRAINTS) > placer.cost(
        {}, SPREAD, NETLIST, CONSTRAINTS
    )


def test_place_is_legal_and_keeps_fixed() -> None:
    positions, _ = placer.place(SPREAD, NETLIST, CONSTRAINTS, seed=1)
    assert set(positions) == {"U1", "C1", "R1", "J1"}
    assert positions["J1"] == (35.0, 25.0, 0.0)
    boxes = {r: _courtyard(r, p) for r, p in positions.items()}
    for x1, y1, x2, y2 in boxes.values():
        assert 0 <= x1 < x2 <= 40 and 0 <= y1 < y2 <= 30
    refs = list(boxes)
    for i, a in enumerate(refs):
        for b in refs[i + 1 :]:
            assert placer._clip(boxes[a], boxes[b]) is None, (a, b)


def test_watch_sees_every_part_and_the_end() -> None:
    seen: list[tuple[int, int, set[str]]] = []
    placer.place(
        SPREAD,
        NETLIST,
        CONSTRAINTS,
        seed=1,
        watch=lambda i, n, _t, _c, g: seen.append((i, n, set(g))),
    )
    assert len(seen) >= 1000 and seen[-1][0] == seen[-1][1] - 1
    assert all(refs == {"U1", "C1", "R1", "J1"} for _, _, refs in seen)


def test_same_seed_is_deterministic() -> None:
    assert placer.place(SPREAD, NETLIST, CONSTRAINTS, seed=7) == placer.place(
        SPREAD, NETLIST, CONSTRAINTS, seed=7
    )


def test_final_cost_not_above_initial() -> None:
    _, final = placer.place(SPREAD, NETLIST, CONSTRAINTS, seed=1)
    assert final <= placer.cost({}, SPREAD, NETLIST, CONSTRAINTS)
