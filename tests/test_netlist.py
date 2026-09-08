import shutil
from pathlib import Path

from pipeline import kicad, netlist

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLE = """
(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric"))
    (comp (ref "U1") (value "NE555")))
  (nets
    (net (code "1") (name "/Net-(U1-\\"A\\")")
      (node (ref "R1") (pin "1") (pintype "passive"))
      (node (ref "U1") (pin "3")))
    (net (code "2") (name "GND")
      (node (ref "U1") (pin "1")))))
"""


def test_parse_inline_sample() -> None:
    nl = netlist.parse(SAMPLE)
    assert [(c.ref, c.value, c.footprint) for c in nl.components] == [
        ("R1", "10k", "Resistor_SMD:R_0603_1608Metric"),
        ("U1", "NE555", None),
    ]
    assert [(n.code, n.name) for n in nl.nets] == [(1, '/Net-(U1-"A")'), (2, "GND")]
    assert nl.connectivity() == {'/Net-(U1-"A")': {"R1.1", "U1.3"}, "GND": {"U1.1"}}


def test_parse_rpi_hat_export(tmp_path: Path) -> None:
    sch = Path(shutil.copy(FIXTURES / "rpi_hat" / "RaspberryPi-HAT.kicad_sch", tmp_path))
    nl = netlist.parse(kicad.export_netlist(sch))
    assert nl.connectivity()["+3V3"] == {
        "C1.1",
        "J1.1",
        "J1.17",
        "JP1.2",
        "R1.1",
        "R2.1",
        "U1.8",
    }


def test_parse_pic_programmer_multi_sheet(tmp_path: Path) -> None:
    d = shutil.copytree(FIXTURES / "pic_programmer", tmp_path / "pic_programmer")
    text = kicad.export_netlist(d / "pic_programmer.kicad_sch")
    nl = netlist.parse(text)
    # 56 components on the root sheet + 7 on pic_sockets.kicad_sch
    assert text.count('(value "pic_sockets.kicad_sch")') == 7
    assert len(nl.components) == 63
    refs = {c.ref for c in nl.components}
    assert {x.ref for n in nl.nets for x in n.nodes} <= refs
