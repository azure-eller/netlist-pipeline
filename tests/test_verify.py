import shutil
from pathlib import Path
from typing import Any

import pytest

from pipeline import board, ipc356, kicad, netlist, verify
from pipeline.models import Board, Netlist

FIXTURE = Path(__file__).parent / "fixtures" / "pic_programmer"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("verify")
    shutil.copytree(FIXTURE, d, dirs_exist_ok=True)
    return d


@pytest.fixture(scope="module")
def parsed(project: Path) -> Board:
    return board.parse((project / "pic_programmer.kicad_pcb").read_text())


@pytest.fixture(scope="module")
def board_nets(project: Path) -> dict[str, frozenset[str]]:
    return ipc356.parse(kicad.export_ipcd356(project / "pic_programmer.kicad_pcb"))


@pytest.fixture(scope="module")
def schematic(project: Path) -> Netlist:
    return netlist.parse(kicad.export_netlist(project / "pic_programmer.kicad_sch"))


def test_routed_board_matches_schematic(
    board_nets: dict[str, frozenset[str]], schematic: Netlist, parsed: Board
) -> None:
    refs = {f.ref for f in parsed.footprints}
    assert verify.netlist_equivalent(board_nets, schematic, refs) == (True, [])


def test_moved_node_is_reported(
    board_nets: dict[str, frozenset[str]], schematic: Netlist, parsed: Board
) -> None:
    refs = {f.ref for f in parsed.footprints}
    bad = dict(board_nets)
    bad["GND"] = board_nets["GND"] - {"C1.2"}
    bad["VCC"] = board_nets["VCC"] | {"C1.2"}
    ok, diffs = verify.netlist_equivalent(bad, schematic, refs)
    assert not ok
    assert any(d.startswith("GND:") and "C1.2" in d for d in diffs)


def test_in_bounds(parsed: Board) -> None:
    assert verify.in_bounds(parsed) == (True, [])


def test_drc_summary_counts(project: Path) -> None:
    summary: dict[str, Any] = verify.drc_summary(
        kicad.drc(project / "pic_programmer.kicad_pcb", parity=True)
    )
    assert isinstance(summary["errors"], int)
    assert isinstance(summary["unrouted"], int)
    assert all(isinstance(n, int) for n in summary["violations"].values())
    assert len(summary["top"]) <= 10
    assert all({"type", "severity", "description", "x", "y"} <= set(v) for v in summary["top"])
