<div align="center">

# Automated-Code-Refactoring_Recommendation-System

**Point it at a Python codebase. It tells you what's wrong, why, and exactly which lines to move where.**

[![PyPI](https://img.shields.io/badge/PyPI-refactor--scan%200.4.1-blue.svg)](https://pypi.org/project/refactor-scan/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](refactor-engine/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](refactor-engine/pyproject.toml)
[![Tests: 277 passing](https://img.shields.io/badge/tests-277%20passing-brightgreen.svg)](refactor-engine/tests/)
[![Built with click + rich](https://img.shields.io/badge/CLI-click%20%2B%20rich-8A2BE2.svg)](refactor-engine/pyproject.toml)

</div>

---

`refactor-scan` is a static-analysis pipeline that reads real Python source with the `ast`
module, computes structural metrics on every class it finds, feeds those metrics into a
trained classifier to name the code smell, and then — this is the part most linters don't
do — builds a graph of *which methods actually work together* and clusters it to propose a
concrete Extract Class split, complete with a suggested name for the new class.

No config files. No plugins to wire up. One command:

```bash
pip install refactor-scan
refactor-scan analyze your_project/
```

> **This file is the single authoritative README.** A byte-identical copy lives at
> `refactor-engine/README.md` purely because that is what `pyproject.toml` points its
> `readme` field at, and setuptools refuses to reference a path outside the project
> directory. **Edit this file, then re-sync the copy** — see
> [Keeping the packaging copy in sync](#keeping-the-packaging-copy-in-sync).

## Table of contents

- [What it actually does](#what-it-actually-does)
- [See it in action](#see-it-in-action)
- [Show me why](#show-me-why)
- [Quick start](#quick-start)
- [Two classifiers: which one to use](#two-classifiers-which-one-to-use)
- [Architecture](#architecture)
- [CLI reference](#cli-reference)
- [Is it getting better or worse?](#is-it-getting-better-or-worse)
- [Sanity check against real refactoring history](#sanity-check-against-real-refactoring-history)
- [How the classifier works](#how-the-classifier-works)
- [Grounded in the literature](#grounded-in-the-literature)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations-and-were-proud-of-documenting-them)
- [GitHub Action](#github-action)
- [Testing](#testing)
- [Keeping the packaging copy in sync](#keeping-the-packaging-copy-in-sync)
- [Changelog](#changelog)
- [Roadmap](#roadmap)

## What it actually does

Most "code smell detectors" stop at a red flag: *"this class is too big."* Thanks, very
helpful. `refactor-scan` goes four steps further:

| Stage | What happens |
|---|---|
| **1. Parse** | Every class in your codebase becomes structured data — methods, fields, who calls whom, who touches what, and which attributes it reaches for on *other* objects. |
| **2. Measure** | Structural metrics per class: LCOM (cohesion), CBO (coupling), cyclomatic complexity, class/method length, fan-in/out, depth of inheritance — plus real ATFD/FDP (foreign data access) for the real-corpus model. |
| **3. Classify** | A Random Forest names the smell — **God Class**, **Data Class**, **Feature Envy**, **Long Method**, or **Clean** — and gives a confidence score, not just a boolean. Two trained classifiers ship; see [Two classifiers](#two-classifiers-which-one-to-use). |
| **4. Suggest** | For cohesion-based smells, methods are modeled as a graph (edge = "these two methods touch the same field") and clustered with weighted modularity detection. Each resulting cluster becomes a named Extract Class suggestion. |
| **5. Explain** | `refactor-scan why` reopens all four stages and shows the working — which measurement actually drove the model's answer for *this* class, how the class scores against published literature thresholds, how the graph got grouped, and how that became the suggestion. In plain language, and in a standalone HTML report if you want the full picture. |

The suggester isn't guessing. If it tells you to extract `render_footer, set_footer` into
`ReportFooterRenderer`, it's because it *found* those two methods sharing the `footer` field
and touching nothing else — not because a line-count threshold tripped.

## See it in action

```
$ refactor-scan analyze sample_repo

┌───────────────────────────────── Scan Summary ──────────────────────────────────┐
│             Repo:  sample_repo                                                  │
│    Files scanned:  3                                                            │
│ Classes analyzed:  4                                                            │
│          Flagged:  3                                                            │
│            Clean:  1                                                            │
└─────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────── Order (coupled.py) ───────────────────────────────┐
│                                                                                 │
│  Feature Envy  89% confidence                                                   │
│                                                                                 │
│  Move method → Into class      Calls out / own data                             │
│  checkout      PaymentProcessor           3 / 1                                 │
│  cancel        PaymentProcessor           2 / 1                                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌───────────────────────── ReportManager (god_class.py) ──────────────────────────┐
│                                                                                 │
│  Data Class  73% confidence                                                     │
│                                                                                 │
│  Extract                                → New class            Shared fields    │
│  add_row, clear_rows, compute_total,    ReportRowsCollection   rows, total      │
│  reset_total                                                                    │
│  render_header, set_author, set_title   ReportAuthorRenderer   author, title    │
│  render_footer, set_footer              ReportFooterRenderer   footer           │
│  render_logo, set_logo                  ReportLogoRenderer     logo_path        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌───────────────────────── PaymentProcessor (coupled.py) ─────────────────────────┐
│                                                                                 │
│  Data Class  34% confidence                                                     │
│                                                                                 │
│  No clear extraction boundary found.                                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

In a real terminal, panel borders are color-coded by smell (red = God Class, yellow = Data
Class, magenta = Feature Envy, orange = Long Method, green = Clean), and the whole thing is
rendered through `rich` — no ANSI-escape spaghetti, no squinting at a wall of print statements.

<!-- TODO(presentation): record a terminal GIF / asciinema cast of `refactor-scan analyze
     sample_repo` and embed it here. Not recorded yet — deliberately left as a marked
     placeholder rather than a broken image link. -->

Don't want jargon? `--audience simple` renders the exact same findings in plain language for
someone who's never heard of LCOM. It is an `analyze` option only — `why` has its own
plain-language layer and does not accept `--audience`:

```
$ refactor-scan analyze sample_repo --audience simple

┌───────────────────────── ReportManager (god_class.py) ──────────────────────────┐
│                                                                                 │
│  Data Class (moderate confidence)                                               │
│                                                                                 │
│  A class that mostly just holds data -- getters and setters -- with little or   │
│  no real behavior of its own. Other classes reach in to read and write its      │
│  fields directly rather than asking it to do anything.                          │
│                                                                                 │
│  Suggested next step:                                                           │
│    • Move render_footer, set_footer into a new class (maybe called              │
│  ReportFooterRenderer) -- they work together on data the rest of the class      │
│  doesn't use.                                                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Show me why

A tool that says *"Feature Envy, 89% confidence"* and stops is asking you to take its word
for it. `refactor-scan why` shows the working instead — what was measured, what the
classifier made of that, how the class scores against the thresholds published in the
code-smell literature, how the methods were grouped, and how those add up to the suggestion.

**1. The short version.** Point it at a file and get the headline, the single factor that
mattered most, and the published-threshold scorecard, per class. Multi-class files are fine
— this stays compact no matter how many classes there are:

```
$ refactor-scan why sample_repo/coupled.py

PaymentProcessor — Data Class (34% confidence)
  Top reason: Its methods are an ordinary length. That is the pattern the model
              most associates with Data Class.
  Suggestion: no clean split found along the data its methods use -- worth a manual look

  Against published thresholds  rule: WOC < 1/3 AND ((NOPA+NOAM > 5 AND WMC < 31)
                                      OR (NOPA+NOAM > 8 AND WMC < 47))
    Metric                          Measured  Published  Meets rule?  Source
    Weight of class (WOC)                  1  < 0.33         no       Lanza & Marinescu (2006)
    Exposed attributes + accessors         0  > 5            no       Lanza & Marinescu (2006)
    Weighted method count (WMC)            3  < 31           yes      Lanza & Marinescu (2006)

Order — Feature Envy (89% confidence)
  Top reason: This class leans heavily on other classes -- a lot of what it does
              is really driving code that lives somewhere else.
  Suggestion: move checkout() into PaymentProcessor (+1 more)

  Against published thresholds  rule: ATFD > 5 AND LAA < 1/3 AND FDP <= 5
    Metric                            Measured  Published  Meets rule?  Source
    Foreign data reached (ATFD)              3  > 5            no       Lanza & Marinescu (2006)
    Attribute locality (LAA) *            0.57  < 0.33         no       Lanza & Marinescu (2006)
    Distinct foreign providers (FDP)         1  <= 5           yes      Lanza & Marinescu (2006)
    * proxy metric — our engine has no exact equivalent; see engine/thresholds.py

Thresholds are published values, not this tool's opinion — see engine/thresholds.py for
each one's source.
Run the same command with --output report.html for the full reasoning behind each of these.
```

Note that the model and the published rules can disagree — as they do for `Order` above,
where the forest is confident and the literal Lanza & Marinescu predicate is not. That
disagreement is shown rather than hidden: the ML label is a learned pattern match, the
threshold table is the textbook rule, and neither is silently presented as the other.

**2. One class at a time.** `--class` narrows it, and errors with the file's actual class
names if you typo one:

```bash
refactor-scan why sample_repo/coupled.py --class Order
```

**3. The full report.** `--output` writes a standalone, styled HTML page — no CDN, no
JavaScript, no network needed — and prints one line rather than duplicating the summary:

```
$ refactor-scan why sample_repo/god_class.py --output report.html
Full explanation for 1 class written to report.html
```

Each class opens with just its name, its label and one plain sentence. The reasoning steps
underneath — what was measured, what the model made of it, how the methods were grouped, and
what to do — are collapsed `<details>` sections, each showing a one-line takeaway so the page
can be skimmed shut. Open one and you get the full working: every metric as a sentence with
the raw number underneath, the model's per-factor ablation, and an SVG of the method graph
with each suggested group marked by a numbered arc. Native `<details>` does the collapsing;
there is still no JavaScript on the page. `--class` works here too, to narrow the report to
one class.

**4. Nothing wrong?** On a file where everything comes back Clean, it says so and stops:

```
$ refactor-scan why sample_repo/clean.py
No smells detected — nothing to explain.
```

(With `--output` it writes the report anyway, covering why each class was judged Clean — a
scripted step asked to produce a file shouldn't silently produce nothing.)

### How the model's own reasoning is worked out

Feature importances are the same numbers for every class in your codebase, so they can tell
you what the model cares about in general but never what tipped one particular decision.
`why` uses **single-feature ablation** instead: each measurement in turn is reset to the
value the model finds unremarkable (the training-set mean) and the class is re-predicted.
How far confidence falls is how much that measurement was really doing — which is why the
report can say *"if this one measurement were ordinary instead, confidence in Feature Envy
would fall by about 49 points"* and mean it literally.

It also reports the measurements that argued **against** the label the model picked. On a
56%-confidence call that's usually the most useful line on the page.

Two honest details it won't hide from you:

- **On the default synthetic classifier, `cbo`, `dit` and `fan_in` are marked "not used by
  the model"** wherever they appear. They are constant across that model's training data, so
  the forest never learned to use them. This is *not* true of the real-corpus model — see
  [Known limitations](#known-limitations-and-were-proud-of-documenting-them).
- **Long Parameter List and Duplicate Code aren't model findings at all.** They're a counting
  rule and a similarity check layered on top. When one of those is what flagged a class, the
  report says so outright rather than explaining a decision the classifier didn't make.

## Quick start

Install the published package:

```bash
pip install refactor-scan
refactor-scan analyze your_project/
```

Or work from a checkout:

```bash
git clone <this-repo>
cd refactor-engine
pip install -r requirements.txt
python run_scan.py analyze sample_repo
```

Either way, running the bare command gives you the startup banner:

```
┌──────────────────────────────────────────────────────────────── v1 ─┐
│                                                                     │
│    R E F A C T O R - E N G I N E                                    │
│                                                                     │
│    AST-powered code smell detection & refactoring suggestions       │
│                                                                     │
│    refactor-engine scans a Python codebase, flags classes with      │
│    structural code smells (God Class, Data Class, Feature Envy,     │
│    Long Method), and suggests concrete ways to split them up.       │
│    Built for developers who want a fast first pass over a           │
│    codebase before a deeper refactor -- point it at a repo and      │
│    get a prioritized list of what to look at first.                 │
│                                                                     │
│    Run refactor-scan --help to see all available commands.          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Two classifiers: which one to use

Two trained models ship inside the package. They are not two versions of the same thing —
they were trained on fundamentally different data and behave differently on real code.

| | `synthetic` (**default**) | `real` (**opt-in, recommended**) |
|---|---|---|
| Trained on | 400 generated example classes, 80 per smell | 6,264 classes mined from real-world Python (1,566 held out) |
| Class balance | 20% per label — 80% of examples smelly by construction | 89.7% Clean, mirroring real code |
| Features actually used | 5 of 8 (`cbo`, `dit`, `fan_in` are constant-zero) | 11 of 11, including real ATFD/FDP |
| Accuracy on held-out **real** code | 45.9% | **95.8%** |
| False alarms on clean code (of 1,405) | 759 (54.0%) | **16 (1.1%)** |
| Macro-F1 | 0.203 | **0.662** |

Select it per run, or once for a shell session:

```bash
refactor-scan analyze your_project/ --model real
export REFACTOR_SCAN_MODEL=real          # applies to analyze, why, evaluate and trend
```

When a non-default model is active, every command prints a line saying so, so a number in the
output can never be silently attributed to the wrong classifier.

### Where the real corpus came from

9,151 real Python files, recorded with provenance and licence in
[`data/real_world/corpus_manifest.json`](refactor-engine/data/real_world/corpus_manifest.json),
parsed down to 7,830 labeled classes:

| Source | Files |
|---|--:|
| ETH SRI Lab **Py150** (Raychev, Bielik & Vechev, OOPSLA 2016) | 3,211 |
| **44 open-source GitHub repositories** (20 initial, 24 in a later expansion) | 4,484 |
| **CodeSearchNet** Python corpus | 1,200 |
| Installed **site-packages** | 249 |

Labels were not hand-assigned. Each class was measured against published thresholds from the
code-smell literature (Lanza & Marinescu 2006; McCabe 1976), encoded in
[`engine/thresholds.py`](refactor-engine/engine/thresholds.py) and unit-tested against the
cited sources in `test_thresholds.py`. So the model's scores measure **agreement with those
published rules, not with human judgement** — a real limitation, recorded in the labelling
audit and applying equally to both models, so the comparison between them stays fair.

### Why the default wasn't just swapped

Three reasons, all of them about not breaking things silently:

1. **Backward compatibility.** Anyone on `refactor-scan<=0.4.1` who upgraded would get
   different labels and different confidences for the same unchanged code, with no diff to
   explain it. A classifier swap is a behaviour change and deserves a deliberate version
   decision, not a quiet patch release.
2. **The two models don't share an input space.** The synthetic model was fitted on 8
   features; the real model on 11. A model is meaningless under any column list but its own,
   so they cannot be hot-swapped behind a single code path — each ships as a self-contained
   bundle carrying its own scaler and label encoder (`engine/ml/bundle.py`). That machinery
   had to exist and be tested *before* a default could safely move.
3. **The real model is much more cautious.** It buys most of its accuracy by leaving ordinary
   code alone. That is the right trade for a CI check and arguably the wrong one for someone
   who installed the tool expecting to be shown something — a product decision to make
   deliberately, with the comparison on the table.

The full side-by-side, including per-class precision/recall, Wilson confidence intervals and
both confusion matrices, is in
[`RETRAIN_COMPARISON.md`](refactor-engine/data/real_world/RETRAIN_COMPARISON.md).

### A third, experimental model

`--model dataclass-experiment` selects an **experimental** classifier that trades overall
accuracy for Data Class recall. It is not a recommended production choice, and it is
documented under [Known limitations](#known-limitations-and-were-proud-of-documenting-them).

## Architecture

```
 your source files
        |
        v
  ┌───────────────┐
  │     PARSE     │   engine/parser.py — ast-based, zero regex heuristics
  └───────┬───────┘   -> ClassInfo / MethodInfo per class
          v
  ┌───────────────┐
  │    MEASURE    │   engine/metrics.py, engine/thresholds.py
  └───────┬───────┘   -> LCOM, CBO, complexity, length, fan-in/out, DIT, ATFD, FDP
          v
  ┌───────────────┐
  │ CLASSIFY (ML) │   engine/ml/ — RandomForestClassifier + StandardScaler
  └───────┬───────┘   -> smell label + confidence (synthetic or real bundle)
          v
  ┌───────────────┐
  │ CLUSTER (graph)│  engine/suggester/graph.py, cluster.py — networkx
  └───────┬───────┘   -> methods grouped by shared-field access
          v
  ┌───────────────┐
  │    SUGGEST    │   engine/suggester/suggest.py
  └───────┬───────┘   -> named Extract Class proposals
          v
  ┌───────────────┐
  │      CLI      │   engine/cli/ — click + rich, dev/simple audiences
  └───────┬───────┘   -> colorized terminal report or machine-readable JSON
          v
  ┌───────────────┐
  │    EXPLAIN    │   engine/reasoning.py, engine/ml/explain.py
  └───────────────┘   -> `why`: ablation + thresholds + graph, terminal or HTML
```

Every arrow above is a real function call, not aspirational — trace it yourself starting at
`engine/pipeline.py:analyze_path()`.

## CLI reference

```bash
refactor-scan                                   # banner + intro
refactor-scan --help                             # colorized command reference
refactor-scan --version                          # refactor-scan, version 0.4.1

refactor-scan analyze <path>                     # a directory OR a single .py file
refactor-scan analyze <path> --audience simple   # plain-language report (analyze only)
refactor-scan analyze <path> --top 20            # show N most severe results (default 10)
refactor-scan analyze <path> --top 0             # show everything, no cap
refactor-scan analyze <path> --exclude vendor    # skip an extra directory by name
refactor-scan analyze <path> --model real        # use the real-corpus classifier
refactor-scan analyze <path> --format json --output report.json

refactor-scan why <file.py>                      # the reasoning behind a file's results
refactor-scan why <file.py> --class Order        # narrow it to one class
refactor-scan why <file.py> --output report.html # full illustrated report instead
refactor-scan why <file.py> --model real         # reason with the real-corpus classifier
refactor-scan why <file.py> --class Order --output order.html

refactor-scan explain "God Class"                # what it is, why it matters, how to fix it
refactor-scan explain "Data Class"
refactor-scan explain "Feature Envy"
refactor-scan explain "Long Method"
refactor-scan explain --model                    # how the default classifier works
refactor-scan explain --model --classifier real  # same, with the real-corpus model's caveat

refactor-scan evaluate <path-to-git-repo>        # mines + checks against that repo's real history
refactor-scan evaluate <mined-repo-name>         # re-run on a repo already mined
refactor-scan evaluate                            # evaluate every repo already mined
refactor-scan evaluate <path> --max-commits 800  # scan further back into history
refactor-scan evaluate <path> --format json --output eval.json

refactor-scan trend <path-to-git-repo>           # smell counts across that repo's history
refactor-scan trend <path> --interval weekly     # commits | weekly | monthly (default monthly)
refactor-scan trend <path> --max-samples 12      # cap the historical points checked (default 24)
refactor-scan trend <path> --format json --output trend.json
```

A few things worth knowing:

- **`--model` is available on `analyze`, `why`, `evaluate` and `trend`.** It takes
  `synthetic` (default), `real`, `dataclass-experiment` or a path to a `.joblib` bundle, and
  is also settable via `$REFACTOR_SCAN_MODEL`. An unknown name is a hard error, never a
  silent fallback — a typo in a model name must not quietly evaluate the wrong model.
  `explain --model` takes `--classifier NAME` instead, because there `--model` is already the
  flag that switches the command into classifier-explanation mode.
- **Findings are ranked, not dumped.** On a large codebase, `--top` (default 10) shows the
  most actionable results first — classes with a real Extract Class suggestion outrank a bare
  "no clear boundary" note, and the summary panel always shows the *true* total count even
  when the list below it is capped.
- **venv/build noise is filtered automatically.** `venv/`, `.venv/`, `env/`, `__pycache__/`,
  `site-packages/`, `.git/`, `node_modules/`, `build/`, `dist/`, and `*.egg-info` are skipped
  by default — scanning a project root next to its own virtualenv won't recurse into
  thousands of vendored files. `--exclude` adds more.
- **JSON mode is not an afterthought.** `--format json` emits the exact same analysis —
  metrics, predicted smell, confidence, suggestions — as clean, sorted, pretty-printed JSON
  for CI pipelines or downstream tooling.

## Is it getting better or worse?

`analyze` answers "what is wrong with this code right now." `trend` answers the question a
team actually asks next. It samples a repository's own commit history, rebuilds the codebase
as it stood at each sampled commit, runs the ordinary analysis over it, and shows the
direction of travel.

Run fresh against a full clone of [`click`](https://github.com/pallets/click):

```
$ refactor-scan trend path/to/click --interval commits --max-samples 8

click — code smells over time (commits, 8 samples)

 date         commit    god   data   envy   long   smelly    rate
 ────────────────────────────────────────────────────────────────────────
 2014-04-24   4101de3     ·      ·     11      7    18/19   94.7%   —
 2014-08-11   c8d3300     7      ·     21     17    45/50   90.0%   ▼4.7%
 2017-10-09   a02cbb7     7      3     24     23    57/67   85.1%   ▼4.9%
 2020-03-05   792f03f     8      3     24     22    57/71   80.3%   ▼4.8%
 2021-06-01   09eebc0    13      2     24     24    63/85   74.1%   ▼6.2%
 2023-08-27   2d52254    15      2     23     24    64/88   72.7%   ▼1.4%
 2025-09-22   f6a87df    12      2     31     29   74/116   63.8%   ▼8.9%
 2026-09-05   6aabf09    16      3     46     32   97/150   64.7%   ▲0.9%

┌─────────────────── First → last ────────────────────┐
│ Span            2014-04-24 → 2026-09-05  (8 points) │
│ Smell rate                                 █▆▅▄▃▃▁▁ │
│ Direction                ▼ improving  (-30.1% rate) │
│                                                     │
│ Classes                                        +131 │
│ Smelly classes                                  +79 │
│   God Class                                     +16 │
│   Data Class                                     +3 │
│   Feature Envy                                  +35 │
│   Long Method                                   +25 │
└─────────────────────────────────────────────────────┘
```

Read those last two rows together, because they are the reason the command records a
denominator at all: click gained **79 smelly classes** over this stretch and got
**proportionally cleaner** — 94.7% of its classes were flagged at the start and 64.7% at the
end, because the codebase grew nearly eightfold in the same period. A tool reporting raw
counts alone would have called that a regression. The trend direction is therefore always
read from the *rate*, never from the count.

> The exact rows depend on your clone: sampling is computed over whatever history is
> present, so a shallower clone or a later HEAD picks different commits. The shape of the
> answer — rate-driven direction, oldest and newest points always kept — does not change.

Three things worth knowing:

- **Your working tree is never touched.** Historical states are read out with `git archive`
  — a read-only query — into a scratch directory outside the repository. Nothing here runs
  `git checkout`, moves `HEAD`, or writes into the work tree, and a `--workspace` pointed
  inside the repo is rejected before any scanning starts. Same guarantee, and the same
  guard, as the history mining in `evaluate`.
- **Sampling spans the range, it doesn't just take the recent end.** `--max-samples`
  (default 24) thins the available history by even spacing, always keeping the oldest and
  newest points — keeping only the newest N would quietly answer a different question.
- **Short histories say so.** A repo with one commit reports a snapshot rather than
  inventing a direction, and a shallow clone is called out as one, because a truncated
  history renders as an ordinary-looking table that means something different.

## Sanity check against real refactoring history

The `evaluate` command is a way to check the detector against something other than its own
test set: it mines a real git repository's history for commits that look like refactorings,
re-runs the analyzer on the code **as it existed the moment before** each one, and reports
whether the tool independently flagged the exact class the maintainers then went on to
restructure. Same detector, same thresholds, run blind against history it never saw.

A commit is only counted as *checkable* when a structural diff can name a specific class that
changed shape — a method moved to another class, a class renamed, methods extracted without
the class ballooning in size. A commit whose message merely contains the word "refactor" but
shows no such structural signature contributes nothing to the score; see the caveat below for
why that distinction matters.

Run against [`click`](https://github.com/pallets/click) — a widely used, professionally
maintained CLI framework, nothing staged or written for this demo:

```
$ refactor-scan evaluate path/to/click --max-commits 400

┌────────────────────────── Read this before the numbers ───────────────────────────┐
│ 'Was refactored' is NOT 'was smelly'. Developers refactor for many reasons        │
│ unrelated to detectable smells (renames, API changes, new features), so this      │
│ hit rate is a LOWER BOUND on what the tool could plausibly have flagged -- not    │
│ a rigorous precision/recall benchmark against verified ground truth.              │
└──────────────────────────────────────────────────────────────────────────────────┘

click — per-commit result
┌──────────────┬─────────────┬────────────────────────┬──────────────────────────────┬─────────────────────┬──────┐
│ commit       │ detected by │ refactor target        │ signal                       │ we flagged it as    │      │
├──────────────┼─────────────┼────────────────────────┼──────────────────────────────┼─────────────────────┼──────┤
│ 0585f456baa6 │ structural  │ ParamType (types.py)   │ method extracted             │ God Class (0.44)    │ HIT  │
│ 7a0a3447f6dd │ structural  │ KeepOpenFile (utils.py)│ method moved between classes │ God Class (0.707)   │ HIT  │
│              │             │ LazyFile (utils.py)    │ method moved between classes │ Feature Envy (0.39) │ HIT  │
│              │             │ PacifyFlushWrapper     │ method moved between classes │ not flagged         │ miss │
│ 8f300853dc5f │ structural  │ Parameter (core.py)    │ method extracted             │ Long Method (0.43)  │ HIT  │
│ ...          │             │ (4 more rows)          │                              │                     │      │
│ 2fdde68324c3 │ keyword     │ (no structural target — keyword only)                 │ —                   │ n/a  │
└──────────────┴─────────────┴────────────────────────┴──────────────────────────────┴─────────────────────┴──────┘

┌───────────────────── Aggregate ──────────────────────┐
│ Commits evaluated                                  8 │
│   with a structural target (checkable)             6 │
│   keyword-only (no verifiable target)              2 │
│                                                      │
│ TARGETED hit rate (commits)             6/6 = 100.0% │
│ TARGETED hit rate (individual targets)   8/9 = 88.9% │
│ FILE-LEVEL hit rate (much weaker)       8/8 = 100.0% │
└──────────────────────────────────────────────────────┘

Which smell flagged the target: Long Method (5), God Class (2), Feature Envy (1)
By structural signal: method extracted → 6/6 (100%) · method moved → 2/3 (66.7%)
By detector: structural → 6/6 checkable, 6 hits (100%) · keyword-only → 0 checkable, 2 file-level hits
```

**What this shows, in plain language:** every one of the six click commits that genuinely
restructured a class — moving a method, extracting one, renaming a class — had already been
independently flagged by this tool as smelly, *before* the commit that fixed it, using nothing
but the code as it stood at that point in history. The one miss (`PacifyFlushWrapper`) was a
tiny wrapper class renamed for a Python-2-cleanup pass, not a structural problem — exactly the
kind of refactor-that-wasn't-about-a-smell the caveat above warns about, and the tool correctly
left it alone rather than guessing.

**What it does not prove:** a 100% hit rate on six commits is not a statistical claim, and the
tool reports that plainly rather than dressing up six data points as a benchmark. It also does
not mean every refactor a developer makes is smell-driven — most aren't, which is exactly why
`evaluate` separates the *checkable* commits (where a structural signal names a real target)
from the much larger set of commits whose message says "refactor" but changed nothing a
detector could have flagged in the first place.

**Built for arbitrary real repositories, not just the ones in this demo.** Point `evaluate` at
any git repository and it: mines automatically if it hasn't been mined yet (no separate step);
shows a live progress bar while walking a large history, since structural diffing thousands of
commits is not instant; detects a shallow (`--depth`) clone specifically and tells you to
deepen it (`git fetch --depth=N`) rather than silently reporting "0 candidates" with no
explanation; and reports a clear, specific message — never a stack trace or a blank table —
for a repository with real history but nothing that structurally matches a refactor. Reproduce
the run above (or point it at your own project) with:

```bash
refactor-scan evaluate path/to/any/git/repo --max-commits 400
```

## How the classifier works

The short version — for the full breakdown, run `refactor-scan explain --model`:

A **Random Forest** (100 decision trees, `class_weight="balanced"`) looks at a class's
structural measurements and votes on a label. Confidence is literally the fraction of trees
that agreed — if 73 of 100 trees say "Data Class," you get 73% confidence. Low confidence
isn't noise; it's the model telling you the class sits in genuinely ambiguous territory.

Two models are trained with that same architecture and differ only in what they were shown:

- The **default (`synthetic`)** model is trained on a 400-row synthetic dataset, generated by
  writing real Python classes engineered to exhibit each smell and running them through this
  project's own Phase 1 pipeline (`parse_file` + `compute_all_metrics`) — no hand-fabricated
  metric values anywhere in the training data. It has 8 features and effectively uses 5.
- The **`real`** model is trained on 6,264 classes from the real-world corpus (1,566 held
  out, using the corpus's own fixed `split` column rather than a fresh re-split), on 11
  features including real ATFD and FDP.

See [`PHASE2_NOTES.md`](refactor-engine/PHASE2_NOTES.md) for the original methodology,
accuracy numbers and feature importances, and
[`RETRAIN_COMPARISON.md`](refactor-engine/data/real_world/RETRAIN_COMPARISON.md) for the
head-to-head on held-out real code.

## Grounded in the literature

Thresholds in this project are cited, not invented. Four published studies on Python code
smell detection were reviewed, our feature set compared against theirs, and the findings
written up in
[`docs/LITERATURE_REVIEW.md`](refactor-engine/docs/LITERATURE_REVIEW.md). Three things came
out of it:

- **Our relational metrics are ahead of that literature, not behind it.** None of the four
  papers uses a single coupling or cohesion metric; every feature in all four is a size,
  counting or Halstead-volume measure taken from one syntactic unit in isolation. `cbo`,
  `lcom`, `fan_in` and `fan_out` have no counterpart in any of them.
- **Real ATFD and FDP were implemented as a direct result.** Feature Envy detection used to
  approximate "foreign data reached" with a fan-out *proxy* built from call names, because
  attribute-level access wasn't tracked at all. That was the root cause of a specific
  misclassification — the ORCIDOAuth2 false positive — that the review set out to explain.
  ATFD is now computed properly, as distinct `(provider, attribute)` pairs read off other
  objects, which also unlocked FDP (distinct foreign providers), letting the rule separate
  real Feature Envy (fixated on one or two neighbours) from dispersed coupling that only
  looks similar.
- **A citation was corrected.** A paper circulated as "Rathee & Chhabra (ICSEC 2022)" is
  actually Vatanapakorn, Soomlek & Seresangtakul, *Python Code Smell Detection Using Machine
  Learning*, ICSEC 2022.

Separately, [`docs/DATASET_VALIDATION.md`](refactor-engine/docs/DATASET_VALIDATION.md) runs
this engine's metrics over the exact library versions behind two published studies and
compares what we compute against what they reported. The threshold table itself is unit-tested
against its cited sources in `tests/test_thresholds.py`, and proxy metrics are labeled as
proxies everywhere they surface.

## Project layout

```
refactor-engine/
  engine/
    parser.py, metrics.py, models.py, serializer.py, pipeline.py
    thresholds.py     # published literature thresholds, each with its citation
    duplication.py    # structural (name-stripped) method similarity
    reasoning.py       # assembles the `why` explanation: measurements + model + graph
    ml/                 # features.py, train.py, predict.py, explain.py, bundle.py
      models/            # opt-in classifier bundles (real, dataclass-experiment)
    suggester/           # graph.py, cluster.py, blocks.py, suggest.py
    evaluate/            # git_mining.py, refactor_eval.py, trend.py, compare_models.py
    ci/                   # pr_comment.py — the GitHub Action's scan step
    cli/                   # main.py, render.py, explanations.py, html_report.py
  data/
    generate_synthetic_dataset.py   # builds the 400-row training set from scratch
    labeled_dataset.csv
    real_world/                      # corpus collection, labeling, RETRAIN_COMPARISON.md
    refactor_history/                # mined before/after snapshots used by `evaluate`
  docs/
    LITERATURE_REVIEW.md, DATASET_VALIDATION.md, DATA_CLASS_EXPERIMENT.md
  sample_repo/, sample_repo_2/       # fixture repos used to validate every phase
  tests/                              # 277 tests across every module above
  run_scan.py                          # dev entrypoint (`pip install .` gives you `refactor-scan`)
  pyproject.toml                       # packaged as a real installable CLI, models included
  PHASE2_NOTES.md                       # classifier methodology & honest limitations
  README.md                              # SYNCED COPY of the root README — do not edit directly
```

Every pipeline stage above has its own dedicated test file.

## Known limitations (and we're proud of documenting them)

A tool that only tells you what it's good at isn't one you should trust. Some real ones,
audited against the current codebase:

- **The default classifier is the weaker one on real code.** `synthetic` scores 45.9% on
  held-out real classes and raises a false alarm on 54% of genuinely clean ones. That is why
  `--model real` exists and why it's recommended. The default stays as it is for backward
  compatibility, not because it's better — see
  [Two classifiers](#two-classifiers-which-one-to-use).
- **`cbo`, `dit` and `fan_in` carry zero weight in the *default* model only.** Measured
  importances in `smell_classifier.pkl` are exactly 0.0000 for all three, because they're
  constant across the synthetic training data — effectively a 5-feature model. The `real`
  model does not share this: real code varies on all of them, and their measured importances
  there are 0.005 / 0.016 / 0.025 — small, but genuinely nonzero.
- **Stateless/utility classes are out-of-distribution for the default model.** Every
  synthetic training example gives its class instance state, so a class with zero `self`
  access (LCOM = 1.0) sits outside anything that model has seen. The real corpus contains
  1,325 such classes (16.9% of it), so `--model real` does not share this blind spot either.
- **Feature Envy and Long Method have no Extract Class strategy.** The suggester proposes
  splits for cohesion-based smells; Feature Envy gets a Move Method suggestion instead, and
  Long Method gets no automated suggestion at all. Detecting a smell correctly is not the
  same as knowing how to fix it, and the CLI says so explicitly rather than pretending
  otherwise.
- **Data Class detection is weak, and the fix is a genuine trade-off rather than a bug.** The
  underlying parser bug *is* fixed: NOPA/WOC used to see only `self.x` assignments inside
  methods, so a class declaring attributes in its body (Django's `class Meta: ordering =
  [...]`, the commonest real Data Class shape) read NOPA = 0 and could never trip the rule.
  `engine/parser.py::_class_body_fields` now reads those, and that fix is shipped. What is
  **not** shipped as the default is the model retrained on the re-labeled corpus: it improves
  Data Class F1 (0.000 → 0.231) but costs overall accuracy (95.8% → 86.7%) and raises false
  alarms on clean code from 16 to 132, because the 11-feature space has nothing that measures
  "declarative attributes vs. behaviour" directly. It is available to anyone who wants it via
  `--model dataclass-experiment`, with the full analysis in
  [`docs/DATA_CLASS_EXPERIMENT.md`](refactor-engine/docs/DATA_CLASS_EXPERIMENT.md).
- **Data Class performance is genuinely unmeasured, not merely poor.** The held-out split
  contains 9 Data Class examples. Nine examples cannot measure anything; the 95% Wilson
  interval on that recall runs from 0.00 to 0.30. The honest statement is *"we do not know
  how this model handles Data Class."* Feature Envy (n=32) is measurable, but the error bars
  are wide.
- **Labels are threshold-derived, not human-verified.** Every accuracy number on this page
  measures agreement with published threshold rules, not with a developer's judgement about
  whether a class is actually bad. That is a ceiling on what any of these scores can mean,
  and it applies to both models equally.
What **has** now been validated, so that it isn't claimed as outstanding: the model has been
measured against 1,566 held-out real classes it never saw
([`RETRAIN_COMPARISON.md`](refactor-engine/data/real_world/RETRAIN_COMPARISON.md)), checked
blind against `click`'s own refactoring history (`evaluate`), tracked across twelve years of
that repository's commits (`trend`), and cross-checked against published academic tooling
([`docs/DATASET_VALIDATION.md`](refactor-engine/docs/DATASET_VALIDATION.md)). Earlier versions
of this README said real-world evaluation was "still ahead." It isn't; what remains genuinely
unvalidated is agreement with *human* judgement, and the two rare smells above.

Full detail on the original synthetic model, including exact numbers, lives in
[`PHASE2_NOTES.md`](refactor-engine/PHASE2_NOTES.md).

## GitHub Action

Run `refactor-scan` on every pull request, automatically. It scans only the Python files
the PR actually changed (never the whole repo), and posts one comment summarizing what it
found — smell, confidence, and a suggested fix per class. By default the check is purely
informational: it never fails CI or blocks a merge, so it's safe to drop into a repo without
a team conversation first.

Here is the comment it produces, generated by running the Action's own scan step over this
repo's fixtures:

> ### 🔍 refactor-scan
>
> ⚠️ **3 code smells** found in 2 of 2 changed Python files.
>
> | File | Class | Smell | Confidence | Suggested fix |
> | --- | --- | --- | --: | --- |
> | `sample_repo/coupled.py` | `Order` | Feature Envy | 89% | Move `checkout()` into `PaymentProcessor` (+1 more) |
> | `sample_repo/god_class.py` | `ReportManager` | Data Class | 73% | Extract `add_row`, `clear_rows`, `compute_total`, `reset_total` into `ReportRowsCollection` (+3 more) |
> | `sample_repo/coupled.py` | `PaymentProcessor` | Data Class | 34% | — |
>
> <sub>Want the full reasoning — what was measured, what drove the classifier, how the
> suggestion was reached? Run it locally:
> `refactor-scan why sample_repo/coupled.py --class Order`</sub>
>
> <sub>Only files changed in this PR were scanned — pre-existing code elsewhere is not
> reported. This check is informational and does not block merging.</sub>

**This workflow was exercised live on real pull requests, not just written.** Test branches
covered a PR containing real smells passing non-blocking, a clean PR producing the all-clear,
a docs-only PR silently doing nothing, and the comment being upserted rather than duplicated
when the branch was pushed again.

### Adding it to your own repo

1. Copy [`.github/workflows/refactor-scan.yml`](.github/workflows/refactor-scan.yml) into
   your repo at the same path.
2. In that file, replace the local install line with the published package:

   ```yaml
   - name: Install refactor-scan
     run: pip install "refactor-scan>=0.4.1"
   ```

That's it — no config file, no secrets, no repo settings to change. The workflow already
has the minimum permissions it needs (`pull-requests: write`, to post/update its own
comment) and triggers on `opened`, `synchronize`, and `reopened` pull request events. A PR
from a fork gets a read-only token and cannot comment; that is handled as expected rather
than as a failure — the same report still lands in the job summary.

### Stricter enforcement (optional)

By default a PR full of code smells still passes CI — the comment is informational only.
If your team wants the check to actually block merges once smells are found, add
`--fail-on-smell` to the scan step's arguments:

```yaml
run: |
  python -m engine.ci.pr_comment \
    --files-from changed_python_files.txt \
    --output refactor-scan-comment.md \
    --fail-on-smell
```

With that flag set, the job exits non-zero (and the check goes red) whenever the scan finds
at least one non-Clean class, on top of still posting the same comment.

## Testing

```bash
cd refactor-engine
pytest tests/ -v
```

277 tests, organized by pipeline stage:

| File | Covers |
|---|---|
| `test_parser.py` | AST extraction: methods, calls, field reads/writes, class-body attributes, foreign attribute access |
| `test_metrics.py` | Every structural metric, hand-verified against small examples |
| `test_graph.py` | Method-graph construction: shared-field edges vs. call edges |
| `test_cluster.py` | Community detection: the exact "two separate clusters split correctly" guarantee the suggester depends on |
| `test_blocks.py` | Statement-block splitting inside a long method |
| `test_duplication.py` | Name-stripped structural similarity between methods |
| `test_suggest.py` | Suggestion generation and the class-naming heuristic |
| `test_pipeline.py` | End-to-end integration on real fixture repos, including directory-exclusion and single-file scanning |
| `test_reasoning.py` | `why`: local ablation, per-strategy reasoning records, and the self-contained HTML report |
| `test_git_mining.py` | Read-only history mining: structural refactor detection, before/after extraction, repo-root and output-path safety checks |
| `test_refactor_eval.py` | `evaluate`'s hit-rate logic (both the flagged and the correctly-not-flagged branch), and the edge cases above: zero candidates, shallow clones, non-Python files |
| `test_ci_pr_comment.py` | PR comment formatting (`build_comment`), including the `--fail-on-smell` exit path — requires `pyyaml` (`pip install pyyaml`, or the `dev` extra) to also validate the workflow YAML itself |
| `test_trend.py` | `trend`: interval sampling and calendar bucketing as pure functions, the read-only historical checkout (work tree, `HEAD` and `git status` all asserted untouched), and rate-vs-count direction logic |
| `test_thresholds.py` | That the published threshold values match what the cited sources actually say, that proxy metrics are labeled as proxies, and that each labeling rule fires where it should |
| `test_model_bundles.py` | The silent failure modes of a two-model setup: a retrain overwriting the shipped model's scaler, predictions compared through disagreeing label encoders, held-out rows leaking into the scaler, and an unknown `--model` raising rather than falling back |
| `test_explain_cli.py` | `explain --model --classifier`: the caveat text is scoped to the selected classifier, unknown names are rejected, and `--classifier` without `--model` is a usage error |

## Keeping the packaging copy in sync

There is exactly one README a human should read: **this one, at the repository root.**

`refactor-engine/README.md` is a **byte-identical copy** that exists only so
`pyproject.toml`'s `readme = "README.md"` resolves — setuptools' sdist build refuses to
reference any path outside the project directory, so `readme = "../README.md"` fails the
build outright. Every release before v0.4.0 shipped to PyPI with an **empty** long
description because that path was wrong. Don't reintroduce that.

**After editing the root README, re-sync before building or releasing:**

```bash
cp README.md refactor-engine/README.md              # macOS / Linux
Copy-Item README.md refactor-engine/README.md       # PowerShell
```

Then verify the built artifact actually carries it:

```bash
cd refactor-engine
python -m build
python -m twine check dist/*
```

`twine check` only confirms a description exists and renders — not that it is current. The
byte comparison is the real check: `diff README.md refactor-engine/README.md` must be empty.

## Changelog

### 0.4.1 — current, published on [PyPI](https://pypi.org/project/refactor-scan/)

- **Fixed a hardcoded CLI version.** `--version` read a literal string rather than the
  installed package metadata, so it could drift from `pyproject.toml` silently. It now comes
  from `importlib.metadata`.
- **Fixed the empty PyPI long description.** Every release before 0.4.0 shipped with a blank
  project page because the `readme` path didn't resolve.
- **Data Class class-body attribute fix.** `engine/parser.py` now reads attributes declared
  in a class body, not just `self.x` assignments inside methods — the blind spot that made
  Django-style `class Meta:` data classes undetectable. Shipped, with a verified zero effect
  on default-model behaviour.
- **`--model dataclass-experiment`.** An opt-in experimental classifier retrained on the
  re-labeled corpus. Not the default, and documented as a trade-off rather than a fix.
- **`explain --model --classifier NAME`.** The classifier caveat printed by
  `explain --model` is now scoped to the classifier you ask about, instead of always
  describing the synthetic default.

### 0.4.0

Everything since 0.3.0 was aimed at one question: does this hold up outside the synthetic
dataset it was trained on?

- **Real-world dataset + retrained model.** A second classifier, trained on classes pulled
  from real open-source repositories rather than generated examples, ships alongside the
  original synthetic-trained one. It's opt-in — `--model real` (or `$REFACTOR_SCAN_MODEL`)
  — because the shipped default has to keep working the same way it always has; nothing
  about the default classifier's behavior changed in this release.
- **ATFD/FDP are now real metrics, not proxies.** Feature Envy detection previously
  approximated "foreign data reached" with a fan-out proxy because attribute-level access
  wasn't tracked. It's now computed directly — distinct (provider, attribute) pairs read off
  other objects — which also unlocked FDP (distinct foreign providers), letting the rule
  distinguish real Feature Envy (fixated on one or two neighbours) from dispersed coupling
  that only looks similar. Both are checked against Lanza & Marinescu's published thresholds
  in `test_thresholds.py`, not just eyeballed.
- **`evaluate`** — checks the detector against a repository's own git history rather than
  a synthetic test set: it mines commits that look like real refactors, and checks whether
  the class a developer went on to restructure had already been independently flagged.
- **`trend`** — samples a repository's commit history at regular intervals and tracks smell
  *rate* (not raw count) over time, so a growing codebase adding smelly classes while getting
  proportionally cleaner reads as improving, not worsening. Historical states are read with
  `git archive`, never `git checkout` — the working tree is never touched.
- **GitHub Action (`engine/ci`)** — a drop-in PR workflow that scans only the files a PR
  changed and posts the results as a comment. Informational by default; `--fail-on-smell`
  opts into blocking merges on it.

### 0.3.0 and earlier

Synthetic-only classifier, `analyze`/`why`/`explain` commands, Extract Class suggestions via
method-graph clustering, and the HTML explainability report. See git history for the
commit-by-commit path there.

## Roadmap

Genuinely outstanding, in rough order of value:

- **Make `real` the default classifier** in a release that treats it as the behaviour change
  it is — with a migration note and a `--model synthetic` escape hatch advertised in the
  release notes.
- **Collect more examples of the rare smells.** Data Class (n=9) and Feature Envy (n=32) in
  the held-out split are too few to measure. More corpus aimed at those two beats any further
  model tuning.
- **Give the model features that measure what the Data Class rule measures.** Expose WOC,
  NOPA and NOAM (or inference-time proxies) as model features, so Data Class is learned from
  direct signal instead of correlated ones — the concrete follow-up recommended by
  `docs/DATA_CLASS_EXPERIMENT.md`.
- **Sharper size metrics.** Replace raw physical line span with logical / source / comment /
  blank line counts, and add parameter count and message-chain length — the recommendations
  from `docs/LITERATURE_REVIEW.md` §6 that are not yet implemented.
- **A Long Method suggestion strategy.** Currently the one smell the suggester detects but
  offers nothing actionable for.
- **Record the terminal demo** (asciinema/GIF) flagged with a TODO in
  [See it in action](#see-it-in-action).

Two things deliberately *not* on this list: **wiring `cbo`/`dit`/`fan_in` into training data
that exercises them** (done — the real corpus varies on all three, and the real model uses
them) and a **Phase G web dashboard** (not being pursued: the `why` HTML report already
covers the standalone-artifact case without a server, and the GitHub Action covers team
visibility).

## License

[MIT](refactor-engine/LICENSE) — do what you want with it.
