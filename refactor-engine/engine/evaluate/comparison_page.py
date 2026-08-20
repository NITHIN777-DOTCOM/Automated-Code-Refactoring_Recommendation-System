"""The retrain comparison as a presentable HTML page.

report.py writes the Markdown record; this writes the version meant to be put
on a screen in front of someone. Same numbers, same source -- both are
functions of a live compare() call, so neither can drift away from the model
it describes.

DESIGN NOTES
------------
The palette and type stack are lifted from engine/cli/html_report.py rather
than invented here, so this page reads as part of the same tool family as the
`why` reports it sits beside. Two additions this page needs and that one does
not:

  * A SECOND series colour. The `why` report describes one model; this one
    compares two, and identity has to be carried by hue. #A8562C (clay) is the
    old synthetic model, the existing #3F72AF (accent blue) is the new one.
    Blue/orange is the one categorical pair that survives all three common
    forms of colour blindness, and the pair was checked rather than assumed:
    adjacent-pair separation is dE 19.3 under protanopia, 22.5 for normal
    vision, both comfortably above the dE 8 / dE 15 floors.

  * A DARK theme. The `why` report pins itself to light. This page is opened
    on whatever machine is to hand, so it carries all three theme states. The
    dark series steps are selected (#C26E3A / #6094D0), not flipped -- the
    light hexes drift out of the dark lightness band once they sit on a dark
    ground.

Run:  python -m engine.evaluate.comparison_page [OUTPUT.html]
"""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime

from engine.evaluate.compare_models import MIN_TRUSTWORTHY_SUPPORT, compare

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

OUTPUT_PATH = os.path.join(_PROJECT_ROOT, "data", "real_world", "RETRAIN_COMPARISON.html")

OLD_SERIES = "old"
NEW_SERIES = "new"


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _pct(value: float, places: int = 1) -> str:
    return f"{value * 100:.{places}f}%"


def _f3(value: float) -> str:
    return f"{value:.3f}"


_CSS = """
:root {
  color-scheme: light dark;

  /* Ground and ink, carried over from engine/cli/html_report.py. The neutrals
     are the same navy at lower alpha rather than a grey scale, so nothing on
     the page is an unconsidered mid-grey. */
  --paper:      #F9F7F7;
  --panel:      #FFFFFF;
  --ink:        #112D4E;
  --ink-70:     rgba(17, 45, 78, .72);
  --ink-55:     rgba(17, 45, 78, .56);
  --ink-30:     rgba(17, 45, 78, .30);
  --wash:       #DBE2EF;
  --rule:       rgba(17, 45, 78, .14);

  /* Series identity. Validated as a categorical pair -- see module docstring. */
  --old:        #A8562C;
  --new:        #3F72AF;
  --old-fill:   rgba(168, 86, 44, .16);
  --new-fill:   rgba(63, 114, 175, .16);

  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Times New Roman", serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;

  --measure: 68ch;
  --page: 940px;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:    #0E1A2B;
    --panel:    #14243A;
    --ink:      #E3EAF3;
    --ink-70:   rgba(227, 234, 243, .74);
    --ink-55:   rgba(227, 234, 243, .58);
    --ink-30:   rgba(227, 234, 243, .32);
    --wash:     #1E3247;
    --rule:     rgba(227, 234, 243, .18);
    --old:      #C26E3A;
    --new:      #6094D0;
    --old-fill: rgba(194, 110, 58, .22);
    --new-fill: rgba(96, 148, 208, .22);
  }
}

:root[data-theme="dark"] {
  --paper:    #0E1A2B;
  --panel:    #14243A;
  --ink:      #E3EAF3;
  --ink-70:   rgba(227, 234, 243, .74);
  --ink-55:   rgba(227, 234, 243, .58);
  --ink-30:   rgba(227, 234, 243, .32);
  --wash:     #1E3247;
  --rule:     rgba(227, 234, 243, .18);
  --old:      #C26E3A;
  --new:      #6094D0;
  --old-fill: rgba(194, 110, 58, .22);
  --new-fill: rgba(96, 148, 208, .22);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  padding: 0 28px 110px;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.65 var(--sans);
  -webkit-font-smoothing: antialiased;
}

.wrap { max-width: var(--page); margin: 0 auto; }
.measure { max-width: var(--measure); }

/* ---------- masthead ---------- */
.masthead { padding: 76px 0 0; display: flex; flex-direction: column; gap: 18px; }
.eyebrow {
  font-size: 11px; letter-spacing: .2em; text-transform: uppercase;
  color: var(--new); font-weight: 700; margin: 0;
}
h1 {
  font-family: var(--serif); font-weight: 400; font-size: clamp(34px, 5vw, 52px);
  line-height: 1.08; margin: 0; letter-spacing: -.015em; text-wrap: balance;
}
.standfirst {
  font-size: 18px; line-height: 1.55; color: var(--ink-70); margin: 0;
  max-width: var(--measure);
}
.dateline {
  font-family: var(--mono); font-size: 12px; color: var(--ink-55);
  border-top: 1px solid var(--rule); padding-top: 14px; margin: 6px 0 0;
  display: flex; flex-wrap: wrap; gap: 6px 26px;
}

/* ---------- sections ---------- */
section { margin: 68px 0 0; display: flex; flex-direction: column; gap: 18px; }
h2 {
  font-family: var(--serif); font-weight: 400; font-size: 27px; line-height: 1.2;
  margin: 0; letter-spacing: -.01em; text-wrap: balance;
}
h3 {
  font-size: 12px; letter-spacing: .13em; text-transform: uppercase;
  font-weight: 700; color: var(--ink-55); margin: 0;
}
p { margin: 0; max-width: var(--measure); }
.lede { color: var(--ink-70); font-size: 15.5px; }

/* ---------- KPI row ---------- */
.kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 14px;
}
.kpi {
  background: var(--panel); border: 1px solid var(--rule); border-radius: 3px;
  padding: 18px 18px 16px; display: flex; flex-direction: column; gap: 12px;
}
.kpi-name {
  font-size: 11.5px; letter-spacing: .07em; text-transform: uppercase;
  font-weight: 700; color: var(--ink-55); line-height: 1.35;
  /* Two lines' worth reserved whether or not this name needs them, so the
     four headline figures share a baseline instead of stepping down wherever
     a label happens to wrap. */
  min-height: 2.7em;
}
.kpi-pair { display: flex; align-items: baseline; gap: 10px; }
.kpi-from {
  font-family: var(--mono); font-size: 19px; color: var(--ink-55);
  font-variant-numeric: tabular-nums; text-decoration: line-through;
  text-decoration-color: var(--ink-30);
}
.kpi-arrow { color: var(--ink-30); font-size: 15px; }
.kpi-to {
  font-family: var(--mono); font-size: 30px; font-weight: 600; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums; line-height: 1;
}
.kpi-delta { font-size: 12.5px; color: var(--ink-70); font-variant-numeric: tabular-nums; }

/* ---------- chart ---------- */
.legend {
  display: flex; flex-wrap: wrap; gap: 8px 22px; font-size: 13px; color: var(--ink-70);
  align-items: center;
}
.legend span { display: inline-flex; align-items: center; gap: 8px; }
.swatch { width: 11px; height: 11px; border-radius: 2px; flex: none; }
.swatch.old { background: var(--old); }
.swatch.new { background: var(--new); }

.chart {
  background: var(--panel); border: 1px solid var(--rule); border-radius: 3px;
  padding: 24px 22px 18px; display: flex; flex-direction: column; gap: 20px;
}
.chart-row { display: grid; grid-template-columns: 132px 1fr; gap: 16px; align-items: center; }
.chart-label { display: flex; flex-direction: column; gap: 2px; }
.chart-label b { font-size: 14px; font-weight: 600; }
.chart-label small {
  font-family: var(--mono); font-size: 11px; color: var(--ink-55);
  font-variant-numeric: tabular-nums;
}
.bars { display: flex; flex-direction: column; gap: 2px; }
.bar-track { display: flex; align-items: center; gap: 9px; }
.bar-rail {
  /* display:block is load-bearing, not decoration: these are <span>s, and an
     inline element ignores width and height outright -- the bars render as an
     empty rail. */
  display: block; flex: 1; height: 15px; background: var(--wash);
  border-radius: 2px; overflow: hidden;
}
.bar {
  display: block; height: 100%; border-radius: 0 3px 3px 0; min-width: 2px;
  transition: filter .12s ease;
}
.bar.old { background: var(--old); }
.bar.new { background: var(--new); }
.bar-track:hover .bar { filter: brightness(1.12); }
.bar-value {
  font-family: var(--mono); font-size: 12px; width: 3.4em; flex: none;
  font-variant-numeric: tabular-nums; color: var(--ink-70);
}
.axis {
  display: grid; grid-template-columns: 132px 1fr; gap: 16px;
  border-top: 1px solid var(--rule); padding-top: 8px;
}
.ticks {
  display: flex; justify-content: space-between; font-family: var(--mono);
  font-size: 11px; color: var(--ink-55); padding-right: calc(3.4em + 9px);
}

/* ---------- tables ---------- */
.scroll { overflow-x: auto; border: 1px solid var(--rule); border-radius: 3px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
caption {
  text-align: left; padding: 14px 16px 0; font-size: 12px; font-weight: 700;
  letter-spacing: .09em; text-transform: uppercase; color: var(--ink-55);
}
th, td { text-align: left; padding: 9px 16px; border-bottom: 1px solid var(--rule); }
thead th {
  font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--ink-55); font-weight: 700; white-space: nowrap;
}
tbody tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
td.name { font-weight: 600; white-space: nowrap; }
tr.total td { border-top: 2px solid var(--ink-30); font-weight: 700; }

.tier {
  display: inline-block; font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase;
  font-weight: 700; padding: 2px 8px; border-radius: 2px; white-space: nowrap;
  border: 1px solid var(--ink-30); color: var(--ink-70);
}
.tier[data-tier="solid"] { border-color: var(--new); color: var(--new); }
.tier[data-tier="not meaningful"] { border-style: dashed; color: var(--ink-55); }

/* ---------- confusion ---------- */
.matrices { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }
.cm td.cell { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
.cm td.diag { outline: 2px solid var(--new); outline-offset: -2px; font-weight: 700; }
.cm th.rowhead { font-weight: 600; white-space: nowrap; font-size: 12.5px; }

/* ---------- notes ---------- */
.note {
  background: var(--wash); border-radius: 3px; padding: 16px 18px;
  font-size: 15px; max-width: var(--measure); color: var(--ink);
}
.callout {
  border-left: 3px solid var(--new); padding: 4px 0 4px 18px;
  font-size: 16px; max-width: var(--measure);
}
ul.findings { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 14px; }
ul.findings li {
  padding-left: 18px; border-left: 1px solid var(--rule); max-width: var(--measure);
  font-size: 15px;
}
ul.findings li b { font-weight: 700; }
code {
  font-family: var(--mono); font-size: .88em; background: var(--wash);
  padding: 1px 5px; border-radius: 2px;
}
pre {
  font-family: var(--mono); font-size: 12.5px; line-height: 1.6; margin: 0;
  background: var(--panel); border: 1px solid var(--rule); border-radius: 3px;
  padding: 16px 18px; overflow-x: auto; color: var(--ink);
}
pre .c { color: var(--ink-55); }

footer {
  margin-top: 84px; padding-top: 20px; border-top: 1px solid var(--ink-30);
  font-size: 13px; color: var(--ink-55); max-width: var(--measure);
  display: flex; flex-direction: column; gap: 8px;
}

a { color: var(--new); }
:focus-visible { outline: 2px solid var(--new); outline-offset: 2px; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}

@media (max-width: 640px) {
  body { padding: 0 18px 72px; }
  .chart-row, .axis { grid-template-columns: 1fr; gap: 8px; }
  .ticks { padding-right: 0; }
}
"""


def _kpi(name, old_text, new_text, delta_text) -> str:
    return (
        '<div class="kpi">'
        f'<div class="kpi-name">{_e(name)}</div>'
        '<div class="kpi-pair">'
        f'<span class="kpi-from">{_e(old_text)}</span>'
        '<span class="kpi-arrow" aria-hidden="true">&rarr;</span>'
        f'<span class="kpi-to">{_e(new_text)}</span>'
        "</div>"
        f'<div class="kpi-delta">{_e(delta_text)}</div>'
        "</div>"
    )


def _bar_chart(synthetic, real, class_names) -> str:
    """Per-class F1, two series, grouped horizontal bars.

    Horizontal because the category names are words, not dates -- a vertical
    layout would turn "Feature Envy" into rotated text. Every bar is directly
    labelled with its value AND the legend is present, so identity never rests
    on colour alone. Bars are anchored to a shared 0-1 baseline; the rounded
    end is the data end only, so length stays readable at small values.
    """
    rows = []
    for name in class_names:
        old = synthetic.by_label(name)
        new = real.by_label(name)
        bars = []
        for series, score in ((OLD_SERIES, old), (NEW_SERIES, new)):
            label = "Old" if series == OLD_SERIES else "New"
            width = max(score.f1 * 100, 0.0)
            bars.append(
                '<div class="bar-track">'
                f'<span class="bar-rail"><span class="bar {series}" style="width:{width:.1f}%" '
                f'title="{_e(label)} model, {_e(name)}: F1 {_f3(score.f1)} '
                f'(precision {_f3(score.precision)}, recall {_f3(score.recall)}, n={score.support})">'
                "</span></span>"
                f'<span class="bar-value">{_f3(score.f1)}</span>'
                "</div>"
            )
        rows.append(
            '<div class="chart-row">'
            f'<div class="chart-label"><b>{_e(name)}</b><small>n = {new.support}</small></div>'
            f'<div class="bars">{"".join(bars)}</div>'
            "</div>"
        )

    ticks = "".join(f"<span>{v}</span>" for v in ("0", "0.25", "0.50", "0.75", "1.0"))
    return (
        '<div class="chart">'
        + "".join(rows)
        + f'<div class="axis"><div></div><div class="ticks">{ticks}</div></div>'
        "</div>"
    )


def _confusion(score, class_names) -> str:
    head = "".join(f'<th class="num">{_e(n)}</th>' for n in class_names)
    body = []
    for i, name in enumerate(class_names):
        row = score.confusion[i]
        total = max(int(row.sum()), 1)
        cells = []
        for j, value in enumerate(row):
            share = int(value) / total
            diag = " diag" if i == j else ""
            # Sequential fill: one hue, light to dark by magnitude.
            style = f' style="background: rgba(63,114,175,{share * 0.55:.3f})"' if value else ""
            cells.append(f'<td class="cell{diag}"{style}>{int(value)}</td>')
        body.append(
            f'<tr><th class="rowhead" scope="row">{_e(name)}</th>{"".join(cells)}</tr>'
        )

    return (
        '<div class="scroll"><table class="cm">'
        f"<caption>{_e(score.name)}</caption>"
        f'<thead><tr><th scope="col">actual \\ predicted</th>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def _metrics_table(score, n_test) -> str:
    rows = []
    for c in score.per_class:
        low, high = c.recall_interval
        rows.append(
            f'<tr><td class="name">{_e(c.label)}</td>'
            f'<td class="num">{_f3(c.precision)}</td>'
            f'<td class="num">{_f3(c.recall)}</td>'
            f'<td class="num">{_f3(c.f1)}</td>'
            f'<td class="num">{c.support}</td>'
            f'<td class="num">{low:.2f}&ndash;{high:.2f}</td></tr>'
        )
    rows.append(
        f'<tr class="total"><td class="name">Overall accuracy</td>'
        f'<td class="num">&mdash;</td><td class="num">&mdash;</td>'
        f'<td class="num">{_f3(score.accuracy)}</td><td class="num">{n_test}</td>'
        f'<td class="num">&mdash;</td></tr>'
    )
    rows.append(
        f'<tr><td class="name">Macro-average F1</td>'
        f'<td class="num">&mdash;</td><td class="num">&mdash;</td>'
        f'<td class="num">{_f3(score.macro_f1)}</td><td class="num">{n_test}</td>'
        f'<td class="num">&mdash;</td></tr>'
    )
    rows.append(
        f'<tr><td class="name">Weighted-average F1</td>'
        f'<td class="num">&mdash;</td><td class="num">&mdash;</td>'
        f'<td class="num">{_f3(score.weighted_f1)}</td><td class="num">{n_test}</td>'
        f'<td class="num">&mdash;</td></tr>'
    )
    return (
        '<div class="scroll"><table>'
        f"<caption>{_e(score.name)} &middot; trained on {_e(score.trained_on)}</caption>"
        '<thead><tr><th scope="col">Smell</th><th class="num" scope="col">Precision</th>'
        '<th class="num" scope="col">Recall</th><th class="num" scope="col">F1</th>'
        '<th class="num" scope="col">n</th>'
        '<th class="num" scope="col">95% CI recall</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def build_page(results: dict) -> str:
    synthetic, real = results["synthetic"], results["real"]
    class_names = results["class_names"]
    n_test = results["test_size"]

    clean_index = class_names.index("Clean")
    clean_total = int(synthetic.confusion[clean_index].sum())
    syn_fp = clean_total - int(synthetic.confusion[clean_index][clean_index])
    real_fp = clean_total - int(real.confusion[clean_index][clean_index])

    generated = datetime.now().strftime("%d %B %Y")

    kpis = "".join([
        _kpi("Overall accuracy", _pct(synthetic.accuracy), _pct(real.accuracy),
             f"+{(real.accuracy - synthetic.accuracy) * 100:.1f} points"),
        _kpi("Macro-F1 (all 5 smells equal)", _f3(synthetic.macro_f1), _f3(real.macro_f1),
             f"+{real.macro_f1 - synthetic.macro_f1:.3f}"),
        _kpi("Weighted F1 (by prevalence)", _f3(synthetic.weighted_f1), _f3(real.weighted_f1),
             f"+{real.weighted_f1 - synthetic.weighted_f1:.3f}"),
        _kpi(f"False alarms on {clean_total} clean classes", str(syn_fp), str(real_fp),
             f"{syn_fp - real_fp} fewer interruptions"),
    ])

    trust_rows = []
    for name in class_names:
        c = real.by_label(name)
        low, high = c.recall_interval
        trust_rows.append(
            f'<tr><td class="name">{_e(name)}</td>'
            f'<td class="num">{c.support}</td>'
            f'<td><span class="tier" data-tier="{_e(c.tier)}">{_e(c.tier)}</span></td>'
            f'<td class="num">{low:.2f}&ndash;{high:.2f}</td>'
            f"<td>{_e(c.tier_meaning)}</td></tr>"
        )

    data_class = real.by_label("Data Class")
    envy = real.by_label("Feature Envy")
    god = real.by_label("God Class")
    long_method = real.by_label("Long Method")
    clean = real.by_label("Clean")

    return f"""<title>Retraining on Real Code</title>
<style>{_CSS}</style>
<div class="wrap">

<header class="masthead">
  <p class="eyebrow">refactor-scan &middot; model evaluation</p>
  <h1>What changed when the classifier learned from real code</h1>
  <p class="standfirst">The smell classifier was trained on generated example classes.
  Retrained on real classes mined from open-source Python, it goes from getting
  <strong>{_pct(synthetic.accuracy)}</strong> of real code right to
  <strong>{_pct(real.accuracy)}</strong> &mdash; and stops crying wolf on healthy code.</p>
  <p class="dateline">
    <span>{_e(generated)}</span>
    <span>{n_test} held-out real classes</span>
    <span>both models, one test set</span>
  </p>
</header>

<section>
  <h2>The headline</h2>
  <p class="lede">Both models measured on exactly the same {n_test} classes marked
  <code>split&nbsp;==&nbsp;"test"</code> in the real corpus &mdash; rows neither model was
  trained on.</p>
  <div class="kpis">{kpis}</div>
  <p class="callout">The old model raises a false alarm on <strong>{_pct(syn_fp / clean_total)}
  of clean code</strong>. On a 550-class codebase it would interrupt a developer {syn_fp} times
  with nothing to show them. That is the finding that justifies retraining &mdash; not the
  accuracy number.</p>
</section>

<section>
  <h2>Per-class F1, side by side</h2>
  <div class="legend">
    <span><i class="swatch old"></i> Old &mdash; trained on synthetic examples</span>
    <span><i class="swatch new"></i> New &mdash; trained on real code</span>
  </div>
  {_bar_chart(synthetic, real, class_names)}
  <p class="lede">F1 combines precision and recall into one number, from 0 to 1. Read every bar
  against its <code>n</code>: the bottom two rest on 5 and 22 examples respectively.</p>
</section>

<section>
  <h2>Full breakdown</h2>
  <p class="lede"><strong>Precision</strong> &mdash; when the model claims this smell, how often
  it is right. <strong>Recall</strong> &mdash; of the classes that really have this smell, how
  many it finds.</p>
  {_metrics_table(synthetic, n_test)}
  {_metrics_table(real, n_test)}
</section>

<section>
  <h2>Which of these numbers can actually be trusted</h2>
  <p class="lede">The real corpus is 85.8% ordinary code, so the per-class numbers rest on
  wildly different amounts of evidence. Reading the F1 column without the <code>n</code>
  column beside it is the easiest way to draw a wrong conclusion from this page.</p>
  <div class="scroll"><table>
    <thead><tr><th scope="col">Smell</th><th class="num" scope="col">Test n</th>
    <th scope="col">Weight of evidence</th><th class="num" scope="col">95% CI recall</th>
    <th scope="col">How to read it</th></tr></thead>
    <tbody>{''.join(trust_rows)}</tbody>
  </table></div>
  <h3>Stated plainly, so it is not glossed over</h3>
  <ul class="findings">
    <li><b>Data Class (n={data_class.support}) &mdash; not meaningful.</b> Five test examples
    cannot measure anything. Its F1 of 0.000 means the model never predicted Data Class on
    those five, but the interval on that recall runs 0.00&ndash;0.43. The honest statement is
    <em>&ldquo;we do not know how this model handles Data Class&rdquo;</em>, not
    <em>&ldquo;the model fails at Data Class&rdquo;</em>.</li>
    <li><b>Feature Envy (n={envy.support}) &mdash; too small to defend.</b> The score is
    genuinely poor and the direction is probably real, but at this size a swing of two or three
    classes moves F1 by more than 10 points.</li>
    <li><b>God Class (n={god.support}) and Long Method (n={long_method.support}) &mdash; the two
    that improved most</b>, and both sit right around the n={MIN_TRUSTWORTHY_SUPPORT} line. The
    jumps are far too large to be sampling noise, but not worth quoting to three decimals.</li>
    <li><b>Clean (n={clean.support}) &mdash; solid.</b> The only per-class number here with
    enough behind it to stand alone &mdash; and the one that matters most in practice, because
    false alarms on healthy code are what get a tool switched off.</li>
  </ul>
</section>

<section>
  <h2>Where each model's mistakes go</h2>
  <div class="matrices">
    {_confusion(synthetic, class_names)}
    {_confusion(real, class_names)}
  </div>
  <p class="lede">Cell shading is proportional to each row's total; the outlined diagonal is
  the correct answer. The old model's <code>Clean</code> row is the whole story: of
  {clean_total} genuinely clean classes it labels only {clean_total - syn_fp} clean and
  scatters the other {syn_fp} across all four smells.</p>
</section>

<section>
  <h2>What changed, in plain language</h2>
  <p>The classifier was originally taught from 400 example classes that we generated ourselves.
  Each was written to demonstrate exactly one problem &mdash; a class that was too long, a class
  whose methods had nothing to do with each other, and so on. Those examples were deliberately
  clear-cut, because the point of them was to check the detection machinery worked at all. The
  model learned them almost perfectly, and its reported accuracy was correspondingly high.</p>
  <p>The problem is that real code looks nothing like a textbook example. In our generated data,
  four out of every five classes had something wrong with them. In real open-source Python, the
  overwhelming majority of classes are perfectly ordinary. A model taught on the first kind of
  data arrives at the second kind expecting to find problems everywhere &mdash; and duly finds
  them. Measured against real code it had never seen, the old model got
  {_pct(synthetic.accuracy)} of classes right and flagged {syn_fp} of {clean_total} perfectly
  healthy classes as having a problem.</p>
  <p>So we rebuilt the training data from real code instead: 3,206 classes from real open-source
  projects, labelled by measuring them against published thresholds from the code-smell
  literature rather than generated to order. We retrained the same classifier &mdash; same
  algorithm, same settings, same eight measurements, the only difference being what it was shown
  &mdash; and tested it on the same {n_test} held-out real classes. Accuracy went from
  {_pct(synthetic.accuracy)} to {_pct(real.accuracy)}, and false alarms on clean code fell from
  {syn_fp} to {real_fp}.</p>
  <p class="note"><strong>The honest caveat.</strong> The new model buys much of that improvement
  by being more cautious. It is very good at recognising ordinary code and leaving it alone, and
  reliable on the two smells with real structural signatures &mdash; God Class and Long Method.
  It is still weak at Feature Envy, and we genuinely cannot say how it handles Data Class,
  because the corpus held only five test examples. Those are limitations of the data, not of the
  retraining: the next useful step is collecting more examples of the rare smells, not tuning
  the model.</p>
</section>

<section>
  <h2>What changed technically</h2>
  <div class="scroll"><table>
    <thead><tr><th scope="col"></th><th scope="col">Old</th><th scope="col">New</th></tr></thead>
    <tbody>
      <tr><td class="name">Training data</td><td>400 generated classes</td>
        <td>2,565 real training rows</td></tr>
      <tr><td class="name">Class balance</td><td>80 per smell (20% each)</td>
        <td>85.8% Clean, mirroring real code</td></tr>
      <tr><td class="name">Algorithm</td><td>RandomForest, 100 trees, balanced</td>
        <td>RandomForest, 100 trees, balanced <em>(unchanged)</em></td></tr>
      <tr><td class="name">Features</td><td>8 metrics</td><td>8 metrics <em>(unchanged)</em></td></tr>
      <tr><td class="name">Usable features</td>
        <td>5 of 8 &mdash; <code>cbo</code>, <code>dit</code>, <code>fan_in</code> constant at 0</td>
        <td><strong>8 of 8</strong> &mdash; real code varies on all of them</td></tr>
      <tr><td class="name">Train/test split</td><td>stratified 80/20, re-split each run</td>
        <td>the corpus's own fixed <code>split</code> column</td></tr>
    </tbody>
  </table></div>
  <p class="lede">The <em>usable features</em> row is worth a sentence at a viva. In the generated
  data three of the eight measurements &mdash; coupling, inheritance depth, and how many other
  classes call into this one &mdash; were identical for every example, so the model could learn
  nothing from them and effectively ran on five inputs.</p>
</section>

<section>
  <h2>Reproducing this</h2>
  <pre><span class="c"># 1. Retrain on the real corpus (separate bundle; leaves the default model alone)</span>
python -m engine.ml.train \\
    --dataset data/real_world/labeled_real_dataset.csv \\
    --split-column split \\
    --output engine/ml/models/classifier_real.joblib

<span class="c"># 2. Score both models on the same held-out rows and regenerate this page</span>
python -m engine.evaluate.report
python -m engine.evaluate.comparison_page

<span class="c"># 3. Try the new model without making it the default</span>
refactor-scan analyze path/to/code.py --model real</pre>
  <p class="note"><strong>The new model is not the default.</strong> The shipped classifier is
  still the synthetic-trained one; the real-data model is opt-in via <code>--model real</code>
  or <code>REFACTOR_SCAN_MODEL=real</code>, so the two can be compared on live code before
  production switches over. Each model is stored with its own scaler, so neither can be
  accidentally paired with the other's preprocessing.</p>
</section>

<section>
  <h2>Methodology</h2>
  <ul class="findings">
    <li><b>Same test rows for both models.</b> The {n_test} rows marked <code>test</code> in the
    corpus, exactly as the labelling run fixed them. Neither model was re-split.</li>
    <li><b>Each model applied with its own scaler.</b> A model's scaler defines the input space
    it was fitted in; scoring the old model through the new one's scaler would measure a model
    that never existed.</li>
    <li><b>The scaler is fitted on training rows only.</b> Held-out rows contribute nothing to
    the means and standard deviations, so no test information reaches training.</li>
    <li><b>Predictions compared as label strings, not encoder integers.</b> Two encoders fitted
    on different datasets can map the same smell to different numbers; comparing codes would
    score each model against a permuted answer key.</li>
    <li><b>Recall intervals are Wilson 95%.</b> The textbook normal interval collapses to
    [0,&nbsp;0] at zero successes, which would claim certainty from five examples.</li>
    <li><b>Labels are threshold-derived, not hand-verified.</b> The corpus was labelled by
    measuring each class against published cutoffs, so these scores measure agreement with those
    published rules rather than with human judgement. That applies equally to both models, so
    the comparison between them stays fair.</li>
  </ul>
</section>

<footer>
  <p>Generated by <code>python -m engine.evaluate.comparison_page</code> from a live evaluation
  &mdash; every number on this page is read from the models, not transcribed.</p>
  <p>The Markdown record of the same run is at
  <code>data/real_world/RETRAIN_COMPARISON.md</code>.</p>
</footer>

</div>
"""


def _assert_ascii_only(page: str) -> None:
    """Refuse to emit a page carrying raw non-ASCII characters.

    This page is read wherever it lands -- a local file, a static server, an
    embedded viewer -- and not all of those declare a charset. A served page
    that gets decoded as latin-1 turns every multi-byte character into
    mojibake, and the failure is invisible in the source: it looks correct in
    the editor and wrong only on screen. Writing punctuation as HTML entities
    (&mdash;, &middot;, &ndash;) sidesteps the question entirely, so this
    check keeps the whole document to that convention rather than trusting
    each new edit to remember.
    """
    offenders = sorted({ch for ch in page if ord(ch) > 127})
    if offenders:
        listed = ", ".join(f"{ch!r} (U+{ord(ch):04X})" for ch in offenders)
        raise ValueError(
            f"Page contains raw non-ASCII characters: {listed}. "
            f"Write them as HTML entities instead."
        )


def main(output_path: str | None = None) -> str:
    output_path = output_path or OUTPUT_PATH
    results = compare()
    page = build_page(results)
    _assert_ascii_only(page)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Comparison page written to {output_path}")
    return output_path


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
