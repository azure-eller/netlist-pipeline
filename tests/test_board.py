"""Oracle: the text parser must agree with pcbnew on the human-routed pic_programmer board."""

import shutil
from pathlib import Path

import pcbnew

from pipeline import board

PCB = Path(__file__).parent / "fixtures" / "pic_programmer" / "pic_programmer.kicad_pcb"


def _items(container: object) -> list:  # type: ignore[type-arg]
    # pcbnew's SWIG containers index fine but their iterators are broken on Python 3.14.
    return [container[i] for i in range(len(container))]  # type: ignore[index, arg-type]


def test_matches_pcbnew(tmp_path: Path) -> None:
    pcb = tmp_path / PCB.name
    shutil.copy(PCB, pcb)
    parsed = board.parse(pcb.read_text())
    ref = pcbnew.LoadBoard(str(pcb))

    footprints = _items(ref.Footprints())
    assert sorted(f.ref for f in parsed.footprints) == sorted(f.GetReference() for f in footprints)
    for fp in footprints:
        mine = parsed.footprint(fp.GetReference())
        assert mine is not None
        pads = _items(fp.Pads())
        assert len(mine.pads) == len(pads)
        for pad in pads:
            x, y = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
            assert any(
                p.number == pad.GetNumber()
                and abs(p.x - x) < 0.01
                and abs(p.y - y) < 0.01
                and (p.net or "") == pad.GetNetname()
                for p in mine.pads
            ), (fp.GetReference(), pad.GetNumber(), x, y, pad.GetNetname(), mine.pads)

    bb = ref.GetBoardEdgesBoundingBox()
    edges = (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom())
    for mine_v, ref_v in zip(parsed.outline, edges, strict=True):
        assert abs(mine_v - pcbnew.ToMM(ref_v)) < 0.5

    tracks = _items(ref.Tracks())
    assert len(parsed.segments) == sum(t.GetClass() == "PCB_TRACK" for t in tracks)
    assert len(parsed.vias) == sum(t.GetClass() == "PCB_VIA" for t in tracks)
