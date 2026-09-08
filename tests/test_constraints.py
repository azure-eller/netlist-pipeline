import json
import shutil
from pathlib import Path

import pytest

from pipeline import kicad, netlist
from pipeline.constraints import derive
from pipeline.models import Component, Net, Netlist

FIXTURES = Path(__file__).parent / "fixtures"
NETS = ["GND", "+3V3", "CLK", "USB_D+", "USB_D-", "SIG_P", "SIG_N", "/LED", "Net-(R1-Pad1)"]


def _nl() -> Netlist:
    comps = tuple(Component(r, None, None) for r in ("U1", "R1", "C1"))
    return Netlist(comps, tuple(Net(i, n, ()) for i, n in enumerate(NETS)))


def test_patterns_classify_and_pair() -> None:
    c, src = derive(_nl(), None, None)
    assert c.classes == {
        "GND": "power",
        "+3V3": "power",
        "CLK": "high_speed",
        "USB_D+": "diff",
        "USB_D-": "diff",
        "SIG_P": "diff",
        "SIG_N": "diff",
    }
    assert sorted(c.diff_pairs) == [("SIG_P", "SIG_N"), ("USB_D+", "USB_D-")]
    assert c.rail_current_a == {"GND": 1.0, "+3V3": 1.0}
    assert src["GND"] == src["CLK"] == src["diff_pairs"] == "pattern"
    assert "/LED" not in src


def test_project_class_beats_pattern() -> None:
    project = {
        "net_settings": {
            "classes": [{"name": "Default"}, {"name": "HighSpeed", "nets": ["GND"]}],
            "netclass_patterns": [{"netclass": "POWER", "pattern": "LED"}],
        }
    }
    c, src = derive(_nl(), project, None)
    assert c.classes["GND"] == "high_speed" and src["GND"] == "project"
    assert c.classes["/LED"] == "power" and src["/LED"] == "project"


def test_user_overrides_everything() -> None:
    user = {
        "classes": {"CLK": "default", "GND": "high_speed"},
        "rail_current_a": {"+3V3": 0.5},
        "fixed": {"U1": [10, 20, 90]},
        "outline_mm": [60, 40],
        "stackup": {"layers": 4},
        "decoupling_max_mm": 5,
    }
    c, src = derive(_nl(), None, user)
    assert c.classes["CLK"] == "default" and src["CLK"] == "user"
    assert c.classes["GND"] == "high_speed"
    assert c.rail_current_a == {"+3V3": 0.5} and src["rail_current_a"] == "user"
    assert c.fixed == {"U1": (10.0, 20.0, 90.0)} and src["fixed"] == "user"
    assert c.outline_mm == (60.0, 40.0) and c.stackup.layers == 4 and c.decoupling_max_mm == 5


def test_unknown_net_raises() -> None:
    with pytest.raises(ValueError, match="NOPE"):
        derive(_nl(), None, {"classes": {"NOPE": "power"}})


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
def test_real_project(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES / "pic_programmer", tmp_path / "p")
    nl = netlist.parse(kicad.export_netlist(tmp_path / "p" / "pic_programmer.kicad_sch"))
    project = json.loads((tmp_path / "p" / "pic_programmer.kicad_pro").read_text())
    c, src = derive(nl, project, None)
    assert c.classes["GND"] == "power" and src["GND"] == "project"
    assert set(c.classes.values()) <= {"power", "high_speed", "diff"}
