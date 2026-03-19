"""Diagram builder for Foster and Cauer thermal RC networks.

Foster topology: N parallel branches of series R-C between input node and ground.
Cauer topology: N-stage ladder of series R with shunt C to ground at each node.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import schemdraw
import schemdraw.elements as elm


@dataclass
class DiagramOptions:
    labels: bool = False
    label_names: Optional[List[str]] = None


def _default_labels(n_nodes: int) -> List[str]:
    base = ["Junction", "Case", "Sink", "Ambient", "Air", "Board", "Heatsink"]
    labels = []
    for i in range(n_nodes):
        if i < len(base):
            labels.append(base[i])
        else:
            labels.append(f"Node {i}")
    return labels


def _svg_from_drawing(d: schemdraw.Drawing) -> str:
    # schemdraw returns bytes for svg, decode to str
    data = d.get_imagedata("svg")
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return str(data)


def foster_svg(order: int, opts: DiagramOptions | None = None) -> str:
    """Render Foster network as shown in reference: series R rail and series C rail."""
    opts = opts or DiagramOptions()
    d = schemdraw.Drawing(unit=2.0)

    stage_len = 2.6
    vert_drop = 1.8

    # Input line and power source marker
    power_x = -2.1
    d += elm.SourceI().at((power_x, 0)).right().length(1.0).label("Power", loc="top")
    d += elm.Line().at((power_x + 1.0, 0)).right().length(1.1)
    d += elm.Dot().at((0, 0))
    labels = opts.label_names or _default_labels(order + 1)
    if opts.labels and str(labels[0]).strip():
        d += elm.Label().at((0, 0)).label(labels[0], loc="top")

    # Build series R rail on top
    node_x = 0.0
    top_nodes = [0.0]
    for i in range(order):
        d += elm.Line().right().length(0.5)
        d += elm.Resistor().right().length(1.2).label(f"R{i+1}")
        d += elm.Line().right().length(0.9)
        node_x += stage_len
        top_nodes.append(node_x)
        node_label = labels[i + 1] if i + 1 < len(labels) else ""
        if opts.labels and str(node_label).strip():
            d += elm.Dot().at((node_x, 0)).label(node_label, loc="top")
        else:
            d += elm.Dot().at((node_x, 0))

    # Bottom series C rail (same stage length as top rail)
    cursor_x = 0.0
    for i in range(order):
        d += elm.Line().at((cursor_x, -vert_drop)).right().length(0.5)
        cursor_x += 0.5
        d += elm.Capacitor().at((cursor_x, -vert_drop)).right().length(1.2).label(f"C{i+1}")
        cursor_x += 1.2
        d += elm.Line().at((cursor_x, -vert_drop)).right().length(0.9)
        cursor_x += 0.9

    # Connect verticals between top and bottom rails
    for x in top_nodes:
        d += elm.Line().at((x, 0)).down().length(vert_drop)

    # Ground at end of bottom rail
    d += elm.Ground().at((top_nodes[-1], -vert_drop))

    return _svg_from_drawing(d)


def cauer_svg(order: int, opts: DiagramOptions | None = None) -> str:
    """Render Cauer ladder: shunt C at each node, series R between nodes."""
    opts = opts or DiagramOptions()
    d = schemdraw.Drawing(unit=2.0)

    stage_len = 2.6
    shunt_len = 2.0

    labels = opts.label_names or _default_labels(order + 1)

    # Start node with power source marker
    power_x = -2.1
    d += elm.SourceI().at((power_x, 0)).right().length(1.0).label("Power", loc="top")
    d += elm.Line().at((power_x + 1.0, 0)).right().length(1.1)
    d += elm.Dot().at((0, 0))
    if opts.labels and str(labels[0]).strip():
        d += elm.Label().at((0, 0)).label(labels[0], loc="top")

    # C1 is at the input node
    d += elm.Capacitor().down().length(shunt_len).at((0, 0)).label("C1")
    d += elm.Ground().at((0, -shunt_len))

    for i in range(order):
        # Series resistor to next node (explicitly on top rail)
        start_x = i * stage_len
        d += elm.Line().at((start_x, 0)).right().length(0.5)
        d += elm.Resistor().at((start_x + 0.5, 0)).right().length(1.2).label(f"R{i+1}")
        d += elm.Line().at((start_x + 1.7, 0)).right().length(0.9)

        node_x = (i + 1) * stage_len
        node_label = labels[i + 1] if i + 1 < len(labels) else ""
        if opts.labels and str(node_label).strip():
            d += elm.Dot().at((node_x, 0)).label(node_label, loc="top")
        else:
            d += elm.Dot().at((node_x, 0))

        # Shunt capacitor at node, except after the last resistor
        if i < order - 1:
            cap_index = i + 2
            d += elm.Capacitor().down().length(shunt_len).at((node_x, 0)).label(f"C{cap_index}")
            d += elm.Ground().at((node_x, -shunt_len))

    # Ground at end of top rail (as in reference image)
    d += elm.Ground().at((stage_len * order, 0))

    return _svg_from_drawing(d)
