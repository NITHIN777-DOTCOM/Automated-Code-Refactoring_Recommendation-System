"""The `refactor-scan why --output` HTML report.

Takes the FileReasoning record built by engine/reasoning.py and renders it as
a single self-contained page: no external stylesheet, no CDN, no JavaScript,
no image files. A report that only renders correctly with a network
connection is not a report you can attach to a code review.

READING MODEL -- summary first, evidence on request. Each class shows its name,
its label and one plain-language sentence, and nothing else until asked. The
four reasoning steps underneath are collapsed <details> elements, each
carrying a one-line takeaway so the page can be skimmed without opening
anything. Native <details> does the whole job; there is no script on the page.
A twelve-class file should be readable in thirty seconds and still hold every
number behind it.

PALETTE -- four colours, no more:
    #112D4E  ink        headings, body text, the graph's darkest nodes
    #3F72AF  accent     links, group arcs, bars, anything active
    #DBE2EF  wash       rules, hairlines, the occasional filled panel
    #F9F7F7  paper      the page
Hierarchy comes from type, space and rules rather than from more hues, so
depth of ink (the same #112D4E at lower alpha) does the work a grey scale
would normally do.

The method graph is drawn as inline SVG with the node positions computed here
in Python. A layout library would be a heavier dependency than the drawing
warrants -- these graphs have a handful of nodes, and a ring is the layout
that makes "who is connected to whom" easiest to read at that size. Clusters
are marked by an arc drawn inside the ring rather than by colour, since four
colours cannot distinguish four groups.
"""

from __future__ import annotations

import html
import math
import os
from datetime import datetime

from engine.reasoning import ClassReasoning, FileReasoning, GraphReasoning
from engine.thresholds import rule_for

INK = "#112D4E"
ACCENT = "#3F72AF"
WASH = "#DBE2EF"
PAPER = "#F9F7F7"

_LEVEL_LABELS = {
    "high": "higher than usual",
    "low": "lower than usual",
    "typical": "about average",
    "unused": "unused by the model",
}

_STRATEGY_TITLES = {
    "clusters": "How its methods were grouped",
    "calls": "Where its methods actually reach",
    "blocks": "How the long method breaks up",
    "structure": "How its methods relate to each other",
}

# The picture is drawn for every class, but it is only the *deciding* evidence
# for the cohesion smells. Saying so up front keeps the section from reading
# like the graph produced a suggestion it had no part in.
_SHARED_LEDE = (
    "Before any suggestion is made, the class is turned into a picture of which methods "
    "belong with which -- based only on the data each one touches."
)
_ELSEWHERE_LEDE = (
    "Every class gets the same picture drawn: methods as dots, joined where they work on "
    "the same data. For this class the deciding evidence was somewhere else, and it is "
    "laid out underneath."
)
_STRATEGY_LEDES = {
    "clusters": _SHARED_LEDE,
    "structure": _SHARED_LEDE,
    "calls": _ELSEWHERE_LEDE,
    "blocks": _ELSEWHERE_LEDE,
}

_MODEL_CAVEAT = (
    "The classifier was trained on synthetic example classes written to demonstrate one "
    "smell each, not on real-world code. It is a fast first opinion worth checking, not a "
    "verdict -- which is exactly why this page shows its working."
)


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _and_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = f"""
:root {{
  --ink: {INK};
  --accent: {ACCENT};
  --wash: {WASH};
  --paper: {PAPER};
  /* Depth of the same ink, standing in for a grey scale the palette doesn't have. */
  --ink-70: rgba(17, 45, 78, .72);
  --ink-55: rgba(17, 45, 78, .56);
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Times New Roman", serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 0 24px 96px;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.7 var(--sans);
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 780px; margin: 0 auto; }}

/* ---------- page header ---------- */
.masthead {{ padding: 72px 0 0; }}
.eyebrow {{
  font-size: 11px; letter-spacing: .18em; text-transform: uppercase;
  color: var(--accent); margin: 0 0 20px; font-weight: 600;
}}
h1 {{
  font-family: var(--serif); font-weight: 400; font-size: 40px; line-height: 1.15;
  margin: 0 0 18px; letter-spacing: -.01em;
}}
.standfirst {{ font-size: 17px; color: var(--ink-70); margin: 0 0 26px; max-width: 60ch; }}
.dateline {{
  font-family: var(--mono); font-size: 12px; color: var(--ink-55);
  border-top: 1px solid var(--wash); padding-top: 14px; margin: 0;
  display: flex; flex-wrap: wrap; gap: 6px 18px;
}}
.dateline .path {{ word-break: break-all; }}

/* ---------- contents ---------- */
.contents {{ margin: 34px 0 0; padding: 0; list-style: none; }}
.contents li {{ border-bottom: 1px solid var(--wash); }}
.contents li:first-child {{ border-top: 1px solid var(--wash); }}
.contents a {{
  display: flex; justify-content: space-between; align-items: baseline; gap: 16px;
  padding: 11px 2px; text-decoration: none; color: var(--ink);
}}
.contents a:hover {{ color: var(--accent); }}
.contents .name {{ font-family: var(--mono); font-size: 14px; }}
.contents .label {{ font-size: 13px; color: var(--ink-55); text-align: right; }}

/* ---------- class ---------- */
.class {{ margin: 72px 0 0; border-top: 2px solid var(--ink); padding-top: 22px; }}
.class .file {{
  font-family: var(--mono); font-size: 11.5px; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ink-55); margin: 0 0 10px;
}}
.class h2 {{
  font-family: var(--serif); font-weight: 400; font-size: 30px; line-height: 1.2;
  margin: 0 0 8px; letter-spacing: -.01em;
}}
.verdict {{ font-size: 15px; margin: 0 0 16px; color: var(--ink); }}
.verdict .smell {{ font-weight: 600; }}
.verdict .sep {{ color: var(--accent); padding: 0 8px; }}
.verdict .score {{ color: var(--ink-70); }}
.summary-line {{ font-size: 17px; line-height: 1.6; margin: 0 0 30px; max-width: 58ch; }}

/* ---------- collapsible sections ---------- */
details {{ border-top: 1px solid var(--wash); }}
details:last-of-type {{ border-bottom: 1px solid var(--wash); }}
summary {{
  list-style: none; cursor: pointer; padding: 16px 0 16px 30px; position: relative;
  transition: background-color .12s ease;
}}
summary::-webkit-details-marker {{ display: none; }}
summary:hover {{ background: var(--wash); }}
summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
summary::before {{
  content: "+"; position: absolute; left: 6px; top: 15px;
  font-family: var(--mono); font-size: 15px; color: var(--accent); line-height: 1.6;
}}
details[open] > summary::before {{ content: "\\2212"; }}
details[open] > summary {{ background: none; }}
.s-title {{ display: block; font-size: 15.5px; font-weight: 600; }}
.s-take {{ display: block; font-size: 14px; color: var(--ink-70); margin-top: 2px; max-width: 62ch; }}
.body {{ padding: 4px 0 34px 30px; }}
.lede {{ font-size: 14.5px; color: var(--ink-70); margin: 0 0 26px; max-width: 62ch; }}

/* ---------- measurements ---------- */
.measure {{ padding: 16px 0; border-top: 1px solid var(--wash); }}
.measure:first-of-type {{ border-top: none; padding-top: 0; }}
.measure-head {{
  display: flex; justify-content: space-between; align-items: baseline; gap: 20px;
  margin-bottom: 5px;
}}
.measure-name {{ font-size: 12px; letter-spacing: .09em; text-transform: uppercase; font-weight: 600; }}
.measure-level {{ font-size: 12px; color: var(--ink-55); white-space: nowrap; }}
.measure-level.notable {{ color: var(--accent); }}
.measure p {{ margin: 0 0 7px; font-size: 15.5px; max-width: 60ch; }}
.figure {{ font-family: var(--mono); font-size: 12px; color: var(--ink-55); max-width: 68ch; line-height: 1.6; }}
.subhead {{
  font-size: 11.5px; letter-spacing: .12em; text-transform: uppercase; font-weight: 600;
  color: var(--ink-55); margin: 34px 0 4px; padding-top: 14px; border-top: 2px solid var(--wash);
}}

/* ---------- factors ---------- */
.factor {{ padding: 18px 0; border-top: 1px solid var(--wash); }}
.factor:first-of-type {{ border-top: none; padding-top: 0; }}
.factor p {{ margin: 0 0 12px; font-size: 15.5px; max-width: 62ch; }}
.gauge {{ display: flex; align-items: center; gap: 12px; margin-bottom: 9px; }}
.track {{ flex: 1; max-width: 300px; height: 8px; border: 1px solid var(--accent); }}
.track > span {{ display: block; height: 100%; background: var(--accent); }}
.track.hollow > span {{
  background: repeating-linear-gradient(
    135deg, var(--accent) 0 2px, transparent 2px 5px
  );
}}
.gauge .verdict-word {{
  font-size: 11.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--accent); white-space: nowrap; font-weight: 600;
}}

/* ---------- notes ---------- */
.note {{
  background: var(--wash); padding: 16px 18px; margin: 0 0 26px;
  font-size: 14.5px; max-width: 64ch;
}}
.note:last-child {{ margin-bottom: 0; }}
.note.plain {{ background: none; border-left: 2px solid var(--wash); padding: 2px 0 2px 18px; }}

/* ---------- graph ---------- */
.graph {{ margin: 0 0 22px; overflow-x: auto; }}
.graph svg {{ display: block; margin: 0 auto; max-width: 100%; height: auto; }}
.key {{
  display: flex; flex-wrap: wrap; gap: 8px 26px; margin: 0 0 26px;
  font-size: 12.5px; color: var(--ink-55);
}}
.key span {{ display: inline-flex; align-items: center; gap: 8px; }}
.key i {{ display: block; width: 20px; height: 0; border-top: 2px solid var(--accent); }}
.key i.faint {{ border-top-style: dashed; opacity: .5; }}
.key i.dot {{ width: 10px; height: 10px; border: 0; border-radius: 50%; background: var(--ink); }}
.key i.ring {{ width: 10px; height: 10px; border: 2px solid var(--accent); border-radius: 50%; background: none; }}

/* ---------- groups ---------- */
.group {{ padding: 16px 0; border-top: 1px solid var(--wash); }}
.group:first-of-type {{ border-top: none; padding-top: 0; }}
.group-head {{ display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }}
.group-num {{
  font-family: var(--mono); font-size: 12px; color: var(--accent); font-weight: 600;
}}
.group h4 {{ font-family: var(--mono); font-size: 15px; font-weight: 600; margin: 0; }}
.group .members {{ font-family: var(--mono); font-size: 13px; color: var(--ink-55); margin: 0 0 7px; }}
.group p {{ margin: 0; font-size: 15px; max-width: 62ch; }}

/* ---------- tables ---------- */
.scroll {{ overflow-x: auto; margin: 0 0 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; padding: 9px 14px 9px 0; border-bottom: 1px solid var(--wash); }}
th {{
  font-size: 11px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--ink-55); font-weight: 600; border-bottom: 1px solid var(--ink);
}}
td.mono {{ font-family: var(--mono); font-size: 13px; }}
td.num {{ font-variant-numeric: tabular-nums; }}

/* ---------- actions ---------- */
.action {{ padding: 15px 0; border-top: 1px solid var(--wash); font-size: 15.5px; max-width: 62ch; }}
.action:first-of-type {{ border-top: none; padding-top: 0; }}

/* ---------- footer ---------- */
footer {{
  margin-top: 80px; padding-top: 20px; border-top: 2px solid var(--ink);
  font-size: 13.5px; color: var(--ink-70); max-width: 68ch;
}}
footer code {{ font-family: var(--mono); font-size: 12.5px; color: var(--ink); }}
footer p {{ margin: 0 0 10px; }}

@media (max-width: 560px) {{
  body {{ padding: 0 18px 64px; }}
  h1 {{ font-size: 31px; }}
  .body {{ padding-left: 0; }}
}}
"""


# ---------------------------------------------------------------------------
# SVG method graph
# ---------------------------------------------------------------------------


def _ordered_nodes(graph: GraphReasoning) -> tuple[list[str], list[tuple[int, int]]]:
    """Node order around the ring, plus each cluster's contiguous span.

    Members of a cluster are placed next to each other so the group can be
    marked with a single arc. Without this, a "group" would be four dots
    scattered around the ring and the picture would argue against the very
    claim it exists to support.
    """
    order: list[str] = []
    spans: list[tuple[int, int]] = []

    for cluster in graph.clusters:
        start = len(order)
        order.extend(cluster["methods"])
        spans.append((start, len(order) - 1))

    placed = set(order)
    order.extend(n["name"] for n in graph.nodes if n["name"] not in placed)
    return order, spans


def _ring_positions(names: list[str], radius: float, cx: float, cy: float) -> dict[str, tuple[float, float]]:
    if len(names) == 1:
        return {names[0]: (cx, cy)}

    positions = {}
    for i, name in enumerate(names):
        positions[name] = (cx + radius * math.cos(_ring_angle(i, len(names))),
                           cy + radius * math.sin(_ring_angle(i, len(names))))
    return positions


def _ring_angle(index: float, count: int) -> float:
    # Start at the top and go clockwise, so the reader's eye lands on the
    # first member of the first group.
    return -math.pi / 2 + (2 * math.pi * index / count)


def _cluster_arc(span: tuple[int, int], count: int, radius: float, cx: float, cy: float, number: int) -> str:
    """An arc inside the ring spanning one cluster, tagged with its number.

    Colour cannot carry this: the palette has one accent, and a class can
    easily have four groups. Position and a label can.
    """
    start_index, end_index = span
    pad = 0.34  # a little short of the member on each end, so groups read as separate
    start_angle = _ring_angle(start_index - pad, count)
    end_angle = _ring_angle(end_index + pad, count)

    arc_radius = radius - 26
    x1 = cx + arc_radius * math.cos(start_angle)
    y1 = cy + arc_radius * math.sin(start_angle)
    x2 = cx + arc_radius * math.cos(end_angle)
    y2 = cy + arc_radius * math.sin(end_angle)
    large = 1 if (end_angle - start_angle) > math.pi else 0

    mid_angle = (start_angle + end_angle) / 2
    label_radius = arc_radius - 15
    lx = cx + label_radius * math.cos(mid_angle)
    ly = cy + label_radius * math.sin(mid_angle)

    return (
        f'<path d="M {x1:.1f} {y1:.1f} A {arc_radius:.1f} {arc_radius:.1f} 0 {large} 1 '
        f'{x2:.1f} {y2:.1f}" fill="none" stroke="{ACCENT}" stroke-width="2" '
        f'stroke-linecap="round" />'
        f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="middle" font-size="12" '
        f'font-weight="600" fill="{ACCENT}">{number}</text>'
    )


def _render_graph_svg(graph: GraphReasoning) -> str:
    order, spans = _ordered_nodes(graph)
    if not order:
        return ""

    # A lone method has no ring to draw, so sizing the canvas off a radius
    # would leave it stranded in a field of whitespace.
    if len(order) == 1:
        radius = 0.0
        width, height = 300.0, 100.0
    else:
        radius = max(100.0, 25.0 * len(order))
        # Labels sit outside the ring, so the canvas has to be wider than it
        # is tall -- method names are long and horizontal.
        width = radius * 2 + 290
        height = radius * 2 + 100
    cx, cy = width / 2, height / 2
    positions = _ring_positions(order, radius, cx, cy)

    parts = [
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" '
        f'role="img" aria-label="Diagram of which methods work on the same data" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="ui-monospace, Menlo, Consolas, monospace">'
    ]

    for number, span in enumerate(spans, start=1):
        parts.append(_cluster_arc(span, len(order), radius, cx, cy, number))

    excluded = set(graph.excluded)
    for edge in graph.edges:
        x1, y1 = positions[edge["source"]]
        x2, y2 = positions[edge["target"]]
        shares = edge["shares_fields"]
        stroke_width = min(1.0 + 1.2 * edge["weight"], 5.0) if shares else 1.2
        dash = "" if shares else ' stroke-dasharray="4 4"'

        # A constructor touches nearly every field, so its spokes reach every
        # node and drown out the grouping the picture exists to show. Fading
        # them keeps the real structure legible without hiding the fact that
        # the constructor does share that data.
        opacity = 0.55 if shares else 0.4
        if edge["source"] in excluded or edge["target"] in excluded:
            opacity = 0.16
            stroke_width = min(stroke_width, 1.2)

        tip = (
            f"{edge['source']} and {edge['target']} both use: {', '.join(edge['shared_fields'])}"
            if shares
            else f"{edge['source']} calls {edge['target']}"
        )
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{ACCENT}" stroke-width="{stroke_width:.1f}" stroke-opacity="{opacity}"{dash}>'
            f"<title>{_e(tip)}</title></line>"
        )

    clustered = {name for cluster in graph.clusters for name in cluster["methods"]}
    nodes_by_name = {n["name"]: n for n in graph.nodes}

    for name in order:
        node = nodes_by_name[name]
        x, y = positions[name]
        if name in excluded:
            fill, stroke, text_fill, node_radius = PAPER, WASH, "rgba(17,45,78,.5)", 7
        elif name in clustered:
            fill, stroke, text_fill, node_radius = INK, PAPER, INK, 9
        else:
            fill, stroke, text_fill, node_radius = PAPER, ACCENT, INK, 8

        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{node_radius}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2">'
            f'<title>{_e(name)}{_e(" - uses: " + ", ".join(node["fields"]) if node["fields"] else " - uses no class data")}</title>'
            f"</circle>"
        )

        # Push the label out along the same spoke, and flip its anchor on the
        # left-hand side so text always reads away from the ring.
        dx, dy = x - cx, y - cy
        length = math.hypot(dx, dy) or 1.0
        lx = x + (dx / length) * 20
        ly = y + (dy / length) * 20 + 4
        anchor = "start" if dx >= 0 else "end"
        if len(order) == 1:
            lx, ly, anchor = x, y + 28, "middle"
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-size="12.5" '
            f'fill="{text_fill}">{_e(name)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _graph_key(graph: GraphReasoning) -> str:
    items = ["<span><i></i> work on the same data</span>", '<span><i class="faint"></i> one calls the other</span>']
    if graph.clusters:
        items.append('<span><i class="dot"></i> in a suggested group</span>')
    if graph.unclustered:
        items.append('<span><i class="ring"></i> shares data with no group</span>')
    if graph.excluded:
        items.append('<span><i class="ring" style="border-color:' + WASH + '"></i> set aside (constructor)</span>')
    return f'<div class="key">{"".join(items)}</div>'


# ---------------------------------------------------------------------------
# One-line takeaways -- what a collapsed section says about itself
# ---------------------------------------------------------------------------


def _measured_takeaway(cls: ClassReasoning) -> str:
    notable = [m for m in cls.measurements if m.level in ("high", "low")]
    if not notable:
        return "Every measurement came back close to average."

    names = [m.plain_name[0].lower() + m.plain_name[1:] for m in notable[:2]]
    if len(notable) > 2:
        return f"Stands out for {names[0]}, {names[1]} and {len(notable) - 2} more."
    return f"Stands out for {_and_list(names)}."


def _model_takeaway(cls: ClassReasoning) -> str:
    model = cls.model
    if not model.decided_by_model:
        return f"Not the classifier's call -- it read this class as {model.model_label}."
    if not model.factors:
        return "No measurement stood out; the label came from the combination."
    top = model.factors[0]
    if not model.single_factor:
        return "No single measurement decided it -- the label came from the combination."
    points = abs(top.support) * 100
    name = top.plain_name[0].lower() + top.plain_name[1:]
    return f"Mostly {name} -- worth {points:.0f} points of confidence on its own."


def _graph_takeaway(cls: ClassReasoning) -> str:
    graph = cls.graph
    if graph is None:
        return "This class has no methods, so there is nothing to relate."

    if graph.strategy == "clusters" and graph.clusters:
        count = len(graph.clusters)
        return f"{count} groups of methods work on separate data from each other."

    if graph.strategy == "calls" and graph.call_attribution:
        targets = sorted({item["target"] for item in graph.call_attribution if item["target"]})
        if targets:
            return f"Its methods spend more time in {_and_list(targets)} than here."
        return "It reaches outside the class, but not consistently anywhere."

    if graph.strategy == "blocks" and graph.blocks:
        methods = sorted({b["method"] for b in graph.blocks})
        runs = sum(1 for b in graph.blocks if b["method"] == methods[0])
        return f"{methods[0]}() breaks into {runs} runs that barely share anything."

    return "Its methods don't separate cleanly along the data they use."


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _section(title: str, takeaway: str, lede: str, body: str) -> str:
    return (
        "<details><summary>"
        f'<span class="s-title">{_e(title)}</span>'
        f'<span class="s-take">{_e(takeaway)}</span>'
        "</summary>"
        f'<div class="body"><p class="lede">{_e(lede)}</p>{body}</div>'
        "</details>"
    )


def _measurements_section(cls: ClassReasoning) -> str:
    used = [m for m in cls.measurements if m.used_by_model]
    unused = [m for m in cls.measurements if not m.used_by_model]

    def row(measurement) -> str:
        notable = " notable" if measurement.level in ("high", "low") else ""
        return (
            f'<div class="measure">'
            f'<div class="measure-head"><span class="measure-name">{_e(measurement.plain_name)}</span>'
            f'<span class="measure-level{notable}">{_e(_LEVEL_LABELS[measurement.level])}</span></div>'
            f"<p>{_e(measurement.finding)}</p>"
            f'<div class="figure">{_e(measurement.detail)}</div>'
            f"</div>"
        )

    body = "".join(row(m) for m in used)

    if unused:
        body += (
            '<p class="subhead">Measured, but not used</p>'
            + "".join(row(m) for m in unused)
            + '<p class="note plain">Every example class the model learned from scored the same on '
            "these, so it never learned to use them and they played no part in the decision. "
            "They are here because they still describe the class.</p>"
        )

    return _section(
        "What was measured",
        _measured_takeaway(cls),
        "Nothing here reads what your code means. Eight structural facts are counted off the "
        "code's shape, and those numbers are the entire basis for everything below.",
        body,
    )


def _benchmarks_takeaway(cls: ClassReasoning) -> str:
    met = sum(1 for b in cls.benchmarks if b.exceeds)
    total = len(cls.benchmarks)
    if not total:
        return ""
    return f"Meets {met} of {total} published conditions."


def _benchmarks_section(cls: ClassReasoning) -> str:
    """Measured values against cited literature thresholds.

    Deliberately separate from "What was measured", which compares this
    class to the TRAINING data's distribution -- the model's own frame of
    reference. This section answers a different question, the one that has
    to survive review by someone who does not care what our classifier
    thinks: how does it read against published cutoffs?
    """
    if not cls.benchmarks:
        return ""

    rows = []
    for b in cls.benchmarks:
        meets = "yes" if b.exceeds else "no"
        meets_cell = f'<strong>{meets}</strong>' if b.exceeds else meets
        name = _e(b.plain_name) + (' <span class="measure-level">proxy</span>' if b.is_proxy else "")
        rows.append(
            "<tr>"
            f"<td>{name}</td>"
            f'<td class="num mono">{_e(_fmt_benchmark(b.value))}</td>'
            f'<td class="mono">{_e(b.operator)} {_e(_fmt_benchmark(b.threshold))}</td>'
            f"<td>{meets_cell}</td>"
            f"<td>{_e(b.published_metric)}</td>"
            f"<td>{_e(b.source_short)}</td>"
            "</tr>"
        )

    body = (
        '<div class="scroll"><table><thead><tr>'
        "<th>Metric</th><th>Measured</th><th>Published</th><th>Meets rule?</th>"
        "<th>Published rule</th><th>Source</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )

    rule = rule_for(cls.smell)
    if rule:
        body += (
            f'<p class="note plain">Detection rule: <code>{_e(rule.formula)}</code>. '
            f"{_e(rule.rationale)}</p>"
        )

    proxies = [b for b in cls.benchmarks if b.is_proxy]
    if proxies:
        notes = " ".join(f"{b.plain_name}: {b.mapping_note}" for b in proxies)
        body += (
            '<p class="note">Rows marked <em>proxy</em> are not the published metric itself — '
            f"our engine has no exact equivalent. {_e(notes)}</p>"
        )

    return _section(
        "Against published thresholds",
        _benchmarks_takeaway(cls),
        "How this class reads against cutoffs taken from the code-smell literature, independent "
        "of what the classifier concluded. Where the two disagree, both numbers are here to be "
        "checked.",
        body,
    )


def _fmt_benchmark(value) -> str:
    if isinstance(value, float) and not float(value).is_integer():
        return f"{value:.2f}"
    return str(int(value))


def _model_section(cls: ClassReasoning) -> str:
    model = cls.model
    body = []

    if cls.what_the_smell_is:
        body.append(f'<p class="note">{_e(cls.smell)}: {_e(cls.what_the_smell_is)}</p>')

    if not model.decided_by_model:
        body.append(f'<p class="note">{_e(model.non_model_reason)}</p>')
    elif not model.single_factor:
        body.append(
            '<p class="note">No single measurement carried this decision on its own. The label '
            "came out of the combination, so the factors below are ordered by how unusual they "
            "are for a class of this kind rather than by how much each one moved the answer.</p>"
        )

    largest = max((abs(f.support) for f in model.factors), default=0.0)
    for factor in model.factors:
        fraction = (abs(factor.support) / largest * 100) if largest else 0
        word = {"for": "pushed toward", "against": "pushed away", "neutral": "no effect"}[factor.direction]
        hollow = " hollow" if factor.direction != "for" else ""
        body.append(
            f'<div class="factor">'
            f'<div class="measure-head"><span class="measure-name">{_e(factor.plain_name)}</span></div>'
            f"<p>{_e(factor.sentence)}</p>"
            f'<div class="gauge"><span class="verdict-word">{_e(word)}</span>'
            f'<span class="track{hollow}"><span style="width:{min(fraction, 100):.0f}%"></span></span></div>'
            f'<div class="figure">{_e(factor.detail)} The model weights this metric '
            f"{factor.importance:.0%} overall, across every class it looks at.</div>"
            f"</div>"
        )

    if model.decided_by_model:
        body.append(
            f'<p class="note">The model settled on {_e(model.model_label)} with '
            f"{model.model_confidence:.0%} confidence, which is literally the share of its 100 "
            f"decision trees that voted that way. {_e(_MODEL_CAVEAT)}</p>"
        )

    return _section(
        "Why the classifier reached this conclusion",
        _model_takeaway(cls),
        "Each measurement was tested by asking a simple question: if this one number were "
        "ordinary instead of what it is, would the answer change? How far the model's "
        "confidence falls is how much that measurement was really doing.",
        "".join(body),
    )


def _groups_body(graph: GraphReasoning) -> str:
    parts = []
    for number, cluster in enumerate(graph.clusters, start=1):
        title = cluster["suggested_name"] or f"Group {number}"
        members = ", ".join(f"{m}()" for m in cluster["methods"])
        parts.append(
            f'<div class="group">'
            f'<div class="group-head"><span class="group-num">{number}</span>'
            f"<h4>{_e(title)}</h4></div>"
            f'<p class="members">{_e(members)}</p>'
            f"<p>{_e(cluster['why'])}</p>"
            f"</div>"
        )

    if graph.unclustered:
        listed = ", ".join(f"{m}()" for m in graph.unclustered)
        parts.append(
            f'<div class="group"><div class="group-head"><h4>Left on their own</h4></div>'
            f'<p class="members">{_e(listed)}</p>'
            f"<p>These methods share no data with any group, so nothing suggests where they "
            f"should go. They stay where they are.</p></div>"
        )
    if graph.excluded:
        listed = ", ".join(f"{m}()" for m in graph.excluded)
        parts.append(
            f'<div class="group"><div class="group-head"><h4>Deliberately set aside</h4></div>'
            f'<p class="members">{_e(listed)}</p>'
            f"<p>A constructor assigns nearly every field the class owns, which would make it "
            f"look related to everything and smear the real groups together. It is also not "
            f"something you can move out on its own, so it is excluded from the grouping.</p>"
            f"</div>"
        )
    return "".join(parts)


def _calls_body(graph: GraphReasoning) -> str:
    rows = []
    for item in graph.call_attribution:
        if item["target"]:
            rows.append(
                f'<tr><td class="mono">{_e(item["method"])}()</td>'
                f'<td class="mono">{_e(item["target"])}</td>'
                f'<td class="num">{_e(item["external_references"])}</td>'
                f'<td class="num">{_e(item["own_data_uses"])}</td></tr>'
            )
        else:
            rows.append(
                f'<tr><td class="mono">{_e(item["method"])}()</td>'
                f'<td colspan="3">{_e(item["note"])}</td></tr>'
            )
    if not rows:
        return ""
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Method</th><th>Spends its time in</th><th>Reaches out</th><th>Uses own data</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _blocks_body(graph: GraphReasoning) -> str:
    rows = [
        f'<tr><td class="mono">{_e(b["method"])}()</td>'
        f'<td class="mono num">{b["lines"][0]}&ndash;{b["lines"][1]}</td>'
        f"<td>{_e(b['description'])}</td>"
        f'<td class="mono">{_e(b["suggested_name"])}({_e(", ".join(b["params"]))})</td></tr>'
        for b in graph.blocks
    ]
    if not rows:
        return ""
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Inside</th><th>Lines</th><th>What that run does</th><th>Suggested helper</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _graph_section(cls: ClassReasoning) -> str:
    graph = cls.graph
    if graph is None:
        return _section(
            "How its methods relate to each other",
            _graph_takeaway(cls),
            "There are no methods in this class, so there is no picture to draw.",
            "",
        )

    svg = _render_graph_svg(graph)
    body = [f'<div class="graph">{svg}</div>{_graph_key(graph)}'] if svg else []
    body.append(f'<p class="note plain">{_e(graph.how_built)}</p>')
    if graph.note:
        body.append(f'<p class="note">{_e(graph.note)}</p>')

    if graph.strategy == "calls":
        body.append(_calls_body(graph))
    elif graph.strategy == "blocks":
        body.append(_blocks_body(graph))
    else:
        body.append(_groups_body(graph))

    return _section(
        _STRATEGY_TITLES.get(graph.strategy, "How its methods relate"),
        _graph_takeaway(cls),
        _STRATEGY_LEDES.get(graph.strategy, _SHARED_LEDE),
        "".join(body),
    )


def _actions_section(cls: ClassReasoning) -> str:
    body = "".join(f'<div class="action">{_e(s)}</div>' for s in cls.suggestion_sentences)
    return _section(
        "What to do about it",
        _sentence(cls.short_suggestion),
        "Putting the three steps above together: this is what the measurements, the model and "
        "the grouping add up to.",
        body,
    )


def _class_section(cls: ClassReasoning) -> str:
    score = cls.headline_metric

    return (
        f'<article class="class" id="class-{_e(cls.name)}">'
        f'<p class="file">{_e(os.path.basename(cls.file_path))}</p>'
        f"<h2>{_e(cls.name)}</h2>"
        f'<p class="verdict"><span class="smell">{_e(cls.smell)}</span>'
        f'<span class="sep">/</span><span class="score">{_e(score)}</span></p>'
        f'<p class="summary-line">{_e(_sentence(cls.top_reason))}</p>'
        f"{_measurements_section(cls)}"
        f"{_benchmarks_section(cls)}"
        f"{_model_section(cls)}"
        f"{_graph_section(cls)}"
        f"{_actions_section(cls)}"
        f"</article>"
    )


def _contents(reasoning: FileReasoning) -> str:
    if len(reasoning.classes) < 2:
        return ""
    items = "".join(
        f'<li><a href="#class-{_e(c.name)}"><span class="name">{_e(c.name)}</span>'
        f'<span class="label">{_e(c.smell)} &middot; {_e(c.headline_metric)}</span></a></li>'
        for c in reasoning.classes
    )
    return f'<ul class="contents">{items}</ul>'


def render_report(reasoning: FileReasoning) -> str:
    """The whole page, as one self-contained HTML string."""
    count = len(reasoning.classes)
    flagged = [c for c in reasoning.classes if c.is_flagged]

    if flagged:
        standfirst = (
            f"{len(flagged)} of the {reasoning.total_classes} class"
            f"{'' if reasoning.total_classes == 1 else 'es'} in this file "
            f"{'was' if len(flagged) == 1 else 'were'} flagged. Each one below opens with what "
            f"it found; the working behind it is folded away underneath."
        )
    else:
        standfirst = (
            "No smells were detected in this file. What follows is the reasoning behind that "
            "verdict -- the same measurements, read the other way."
        )

    generated = datetime.now().strftime("%d %B %Y, %H:%M")
    sections = "".join(_class_section(c) for c in reasoning.classes)

    return (
        "<style>" + _CSS + "</style>"
        '<div class="wrap">'
        '<header class="masthead">'
        '<p class="eyebrow">refactor-scan &middot; why</p>'
        "<h1>The reasoning behind these findings</h1>"
        f'<p class="standfirst">{_e(standfirst)}</p>'
        f'<p class="dateline"><span class="path">{_e(reasoning.path)}</span>'
        f'<span>{count} class{"" if count == 1 else "es"}</span>'
        f"<span>{_e(generated)}</span></p>"
        f"{_contents(reasoning)}"
        "</header>"
        f"{sections}"
        "<footer>"
        "<p>Every number on this page can be checked. <code>refactor-scan analyze &lt;path&gt; "
        "--format json</code> emits the same measurements and findings in machine-readable "
        "form; <code>refactor-scan explain &lt;smell&gt;</code> defines any label above in "
        "plain language; <code>refactor-scan explain --model</code> describes the classifier "
        "in full.</p>"
        "</footer>"
        "</div>"
    )


def write_report(reasoning: FileReasoning, output_path: str) -> str:
    """Render and write the report. Returns the path written."""
    document = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="color-scheme" content="light">'
        f"<title>Why these classes were flagged &middot; {_e(os.path.basename(reasoning.path))}</title>"
        "</head><body>" + render_report(reasoning) + "</body></html>"
    )

    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(document)
    return output_path
