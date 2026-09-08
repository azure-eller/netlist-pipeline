"""Parser for `kicad-cli sch export netlist --format kicadsexpr` output."""

from __future__ import annotations

from pipeline import sexpr
from pipeline.models import Component, Net, Netlist, Node


def parse(text: str) -> Netlist:
    root = sexpr.parse(text)
    components = tuple(
        Component(_req(c, "ref"), sexpr.value(c, "value"), sexpr.value(c, "footprint"))
        for c in sexpr.children(sexpr.child(root, "components") or [], "comp")
    )
    nets = tuple(
        Net(
            int(_req(n, "code")),
            _req(n, "name"),
            tuple(Node(_req(x, "ref"), _req(x, "pin")) for x in sexpr.children(n, "node")),
        )
        for n in sexpr.children(sexpr.child(root, "nets") or [], "net")
    )
    return Netlist(components, nets)


def _req(node: sexpr.SExpr, tag: str) -> str:
    v = sexpr.value(node, tag)
    if v is None:
        raise ValueError(f"netlist entry missing ({tag} ...): {node!r:.200}")
    return v
