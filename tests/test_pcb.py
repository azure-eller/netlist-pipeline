import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pcbnew
import pytest

from pipeline import kicad, netlist, pcb
from pipeline.config import settings
from pipeline.models import Constraints, Netlist

FIXTURES = Path(__file__).parent / "fixtures"
# The HAT template leaves these footprints unassigned in the schematic.
FOOTPRINTS = {
    "C1": "Capacitor_SMD:C_0603_1608Metric",
    "R1": "Resistor_SMD:R_0603_1608Metric",
    "R2": "Resistor_SMD:R_0603_1608Metric",
    "JP1": "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
    "U1": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
}


@pytest.fixture
def hat(tmp_path: Path) -> tuple[Netlist, Path]:
    d = Path(shutil.copytree(FIXTURES / "rpi_hat", tmp_path / "rpi_hat"))
    nl = netlist.parse(kicad.export_netlist(d / "RaspberryPi-HAT.kicad_sch"))
    nl = Netlist(
        tuple(replace(c, footprint=c.footprint or FOOTPRINTS[c.ref]) for c in nl.components),
        nl.nets,
    )
    return nl, d / "RaspberryPi-HAT.kicad_pcb"


def pad_nets(path: Path) -> dict[str, str]:
    board = pcbnew.LoadBoard(str(path))
    return {
        f"{fp.GetReference()}.{p.GetNumber()}": p.GetNetname()
        for fp in board.GetFootprints()
        for p in fp.Pads()
    }


def test_build_unplaced_from_template(hat: tuple[Netlist, Path], tmp_path: Path) -> None:
    nl, template = hat
    out = tmp_path / "unplaced.kicad_pcb"
    summary = pcb.build_unplaced(nl, Constraints(), template, out)
    board = pcbnew.LoadBoard(str(out))
    refs = {fp.GetReference() for fp in board.GetFootprints()}
    assert {c.ref for c in nl.components} <= refs
    assert len(board.Tracks()) == 0
    nets = pad_nets(out)
    for net in nl.nets:
        for node in net.nodes:
            assert nets[node.key] == net.name
    assert summary["source"] == "template"


def test_build_unplaced_from_library(hat: tuple[Netlist, Path], tmp_path: Path) -> None:
    nl, _ = hat
    out = tmp_path / "unplaced.kicad_pcb"
    summary = pcb.build_unplaced(nl, Constraints(), None, out)
    board = pcbnew.LoadBoard(str(out))
    assert {fp.GetReference() for fp in board.GetFootprints()} == {c.ref for c in nl.components}
    bb = board.GetBoardEdgesBoundingBox()
    assert pcbnew.ToMM(bb.GetWidth()) > 0 and pcbnew.ToMM(bb.GetHeight()) > 0
    assert summary["source"] == "library"


def test_set_positions(hat: tuple[Netlist, Path], tmp_path: Path) -> None:
    nl, template = hat
    src, out = tmp_path / "a.kicad_pcb", tmp_path / "b.kicad_pcb"
    pcb.build_unplaced(nl, Constraints(), template, src)
    pcb.set_positions(src, {"U1": (120.0, 70.0, 90.0)}, out)
    fp = pcbnew.LoadBoard(str(out)).FindFootprintByReference("U1")
    assert (pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)) == (120.0, 70.0)
    assert fp.GetOrientationDegrees() == 90.0


@pytest.mark.slow
def test_route(hat: tuple[Netlist, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "freerouting_passes", 5)
    nl, template = hat
    unplaced, placed, routed = (
        tmp_path / f"{n}.kicad_pcb" for n in ("unplaced", "placed", "routed")
    )
    pcb.build_unplaced(nl, Constraints(), template, unplaced)
    grid = {
        "C1": (120.0, 70.0, 0.0),
        "R1": (130.0, 70.0, 0.0),
        "R2": (140.0, 70.0, 0.0),
        "JP1": (150.0, 70.0, 0.0),
        "U1": (130.0, 85.0, 0.0),
    }  # inside the HAT outline
    pcb.set_positions(unplaced, grid, placed)
    r = pcb.route(placed, routed)
    assert r["version"] == "2.4.1"
    assert r["passes"] == 5
    assert r["unrouted"] == 0 == pcb.unrouted_count(routed)
    assert len(pcbnew.LoadBoard(str(routed)).Tracks()) > 0


def test_strip_copper_removes_tracks_vias_zones_and_keeps_footprints() -> None:
    from pipeline.pcb import strip_copper

    src = (FIXTURES / "pic_programmer" / "pic_programmer.kicad_pcb").read_text()
    out = strip_copper(src)
    for block in ("(segment", "(via", "(zone\n", "(arc"):
        assert block not in out.replace("(zone_connect", "")
    assert out.count("(footprint") == src.count("(footprint")
    path = Path(tempfile.mkdtemp()) / "stripped.kicad_pcb"
    path.write_text(out)
    b = pcbnew.LoadBoard(str(path))
    assert len(b.Tracks()) == 0 and len(b.Zones()) == 0 and len(b.GetFootprints()) == 63
