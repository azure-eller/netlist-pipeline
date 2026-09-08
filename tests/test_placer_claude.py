import re

from pipeline import placer, placer_claude
from tests.test_placer import CONSTRAINTS, NETLIST, SPREAD

SEARCH, COST = placer.place(SPREAD, NETLIST, CONSTRAINTS, seed=1)


def _validate(*positions: dict[str, float | str]) -> dict[str, placer.Pose]:
    return placer_claude.validate(
        SPREAD, NETLIST, CONSTRAINTS, SEARCH, {"positions": list(positions), "rationale": ""}
    )


def test_validate_restores_fixed_part() -> None:
    out = _validate({"ref": "J1", "x": 5.0, "y": 5.0, "rot": 0})
    assert out["J1"] == SEARCH["J1"]


def test_validate_ignores_unknown_ref() -> None:
    assert _validate({"ref": "X9", "x": 5.0, "y": 5.0, "rot": 0}) == SEARCH


def test_validate_snaps_rotation() -> None:
    out = _validate({"ref": "R1", "x": 10.0, "y": 10.0, "rot": 47})
    assert out["R1"] == (10.0, 10.0, 90.0)


def test_validate_clamps_inside_outline() -> None:
    out = _validate({"ref": "C1", "x": 100.0, "y": -50.0, "rot": 0})
    x, y, _ = out["C1"]
    hw, hh = 1.25, 0.75
    assert (x - hw, y - hh, x + hw, y + hh) == (37.5, 0.0, 40.0, 1.5)


def test_prompt_lists_each_movable_ref_once() -> None:
    prompt = placer_claude.build_prompt(SPREAD, NETLIST, CONSTRAINTS, SEARCH, COST)
    for ref in ("U1", "C1", "R1"):
        assert len(re.findall(rf"^- {ref} .* fixed: no$", prompt, re.M)) == 1, ref
    assert len(re.findall(r"^- J1 .* fixed: yes$", prompt, re.M)) == 1
    assert not re.findall(r"^- J1 .* fixed: no$", prompt, re.M)
