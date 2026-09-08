import shutil
from pathlib import Path

import pytest

from pipeline import ipc356, kicad

FIXTURE = Path(__file__).parent / "fixtures" / "pic_programmer"


@pytest.fixture(scope="module")
def nets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, frozenset[str]]:
    d = tmp_path_factory.mktemp("ipc")
    shutil.copytree(FIXTURE, d, dirs_exist_ok=True)
    return ipc356.parse(kicad.export_ipcd356(d / "pic_programmer.kicad_pcb"))


def test_nodes_are_ref_dot_pin(nets: dict[str, frozenset[str]]) -> None:
    assert {"C1.2", "C2.2"} <= nets["GND"]
    assert all("." in node and not node.endswith(".") for n in nets.values() for node in n)


def test_unescapes_kicad_tokens(nets: dict[str, frozenset[str]]) -> None:
    assert "VPP/MCLR" in nets
    assert not any("{" in n for n in nets)


def test_skips_unconnected(nets: dict[str, frozenset[str]]) -> None:
    assert "N/C" not in nets
