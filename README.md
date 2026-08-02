<div align="center">

# refactor-scan

**Point it at a Python codebase. It tells you what's wrong, why, and exactly which lines to move where.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](refactor-engine/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](refactor-engine/pyproject.toml)
[![Tests: 36 passing](https://img.shields.io/badge/tests-36%20passing-brightgreen.svg)](refactor-engine/tests/)
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
refactor-scan analyze your_project/
```

## Table of contents

- [What it actually does](#what-it-actually-does)
- [See it in action](#see-it-in-action)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [CLI reference](#cli-reference)
- [How the classifier works](#how-the-classifier-works)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations-and-were-proud-of-documenting-them)
- [Testing](#testing)
- [Roadmap](#roadmap)

## What it actually does

Most "code smell detectors" stop at a red flag: *"this class is too big."* Thanks, very
helpful. `refactor-scan` goes four steps further:

| Stage | What happens |
|---|---|
| **1. Parse** | Every class in your codebase becomes structured data — methods, fields, who calls whom, who touches what. |
| **2. Measure** | 8 structural metrics per class: LCOM (cohesion), CBO (coupling), cyclomatic complexity, class/method length, fan-in/out, depth of inheritance. |
| **3. Classify** | A Random Forest trained specifically for this pipeline names the smell — **God Class**, **Data Class**, **Feature Envy**, **Long Method**, or **Clean** — and gives a confidence score, not just a boolean. |
| **4. Suggest** | For cohesion-based smells, methods are modeled as a graph (edge = "these two methods touch the same field") and clustered with weighted modularity detection. Each resulting cluster becomes a named Extract Class suggestion. |

The suggester isn't guessing. If it tells you to extract `render_footer, set_footer` into
`ReportFooterRenderer`, it's because it *found* those two methods sharing the `footer` field
and touching nothing else — not because a line-count threshold tripped.

## See it in action

```
$ refactor-scan analyze sample_repo

┌─────────────────────────────── Scan Summary ────────────────────────────────┐
│             Repo:  sample_repo                                              │
│    Files scanned:  3                                                        │
│ Classes analyzed:  4                                                        │
│          Flagged:  3                                                        │
│            Clean:  1                                                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────── ReportManager (god_class.py) ────────────────────────┐
│  Data Class  60% confidence                                                 │
│                                                                              │
│  Extract                               → New class          Shared fields   │
│  add_row, clear_rows, compute_total,   ReportRowsCollection rows, total     │
│  reset_total                                                                │
│  render_header, set_author, set_title  ReportAuthorRenderer author, title   │
│  render_footer, set_footer             ReportFooterRenderer footer          │
│  render_logo, set_logo                 ReportLogoRenderer   logo_path       │
└─────────────────────────────────────────────────────────────────────────────┘
```

In a real terminal, panel borders are color-coded by smell (red = God Class, yellow = Data
Class, magenta = Feature Envy, orange = Long Method, green = Clean), and the whole thing is
rendered through `rich` — no ANSI-escape spaghetti, no squinting at a wall of print statements.

Don't want jargon? `--audience simple` renders the exact same findings in plain language for
someone who's never heard of LCOM:

```
┌─────────────────────── ReportManager (god_class.py) ────────────────────────┐
│  Data Class (low confidence — worth a second look)                          │
│                                                                              │
│  A class that mostly just holds data -- getters and setters -- with little  │
│  or no real behavior of its own.                                            │
│                                                                              │
│  Suggested next step:                                                       │
│    • Move render_footer, set_footer into a new class (maybe called          │
│      ReportFooterRenderer) -- they work together on data the rest of        │
│      the class doesn't use.                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
git clone <this-repo>
cd refactor-engine
pip install -r requirements.txt
python run_scan.py analyze sample_repo
```

Or install it as a real CLI tool:

```bash
pip install .
refactor-scan analyze your_project/
```

Either way, on first run you'll see the startup banner:

```
┌──────────────────────────────────────────────────────────────────── v1 ─┐
│                                                                          │
│    R E F A C T O R - E N G I N E        (purple → cyan gradient title)  │
│                                                                          │
│    AST-powered code smell detection & refactoring suggestions           │
│                                                                          │
│    refactor-engine scans a Python codebase, flags classes with          │
│    structural code smells, and suggests concrete ways to split them     │
│    up. Point it at a repo and get a prioritized list of what to look    │
│    at first.                                                            │
│                                                                          │
│    Run refactor-scan --help to see all available commands.              │
│                                                                          │
└───────────────────────────────────────────────────────────────────────┘
```

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
  │    MEASURE    │   engine/metrics.py
  └───────┬───────┘   -> LCOM, CBO, cyclomatic complexity, length, fan-in/out, DIT
          v
  ┌───────────────┐
  │ CLASSIFY (ML) │   engine/ml/ — RandomForestClassifier + StandardScaler
  └───────┬───────┘   -> smell label + confidence
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
  └───────────────┘   -> colorized terminal report or machine-readable JSON
```

Every arrow above is a real function call, not aspirational — trace it yourself starting at
`engine/pipeline.py:analyze_path()`.

## CLI reference

```bash
refactor-scan                                   # banner + intro
refactor-scan --help                             # colorized command reference
refactor-scan --version

refactor-scan analyze <path>                     # a directory OR a single .py file
refactor-scan analyze <path> --audience simple   # plain-language report
refactor-scan analyze <path> --top 20            # show N most severe results (default 10)
refactor-scan analyze <path> --top 0             # show everything, no cap
refactor-scan analyze <path> --exclude vendor    # skip an extra directory by name
refactor-scan analyze <path> --format json --output report.json

refactor-scan explain "God Class"                # what it is, why it matters, how to fix it
refactor-scan explain "Data Class"
refactor-scan explain "Feature Envy"
refactor-scan explain "Long Method"
refactor-scan explain --model                    # how the classifier itself works
```

A few things worth knowing:

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

## How the classifier works

The short version — for the full breakdown, run `refactor-scan explain --model`:

A **Random Forest** (100 decision trees) looks at 8 structural measurements per class and
votes on a label. Confidence is literally the fraction of trees that agreed — if 56 of 100
trees say "God Class," you get 56% confidence. Low confidence isn't noise; it's the model
telling you the class sits in genuinely ambiguous territory.

The model is trained on a **400-row synthetic dataset**, generated by writing real Python
classes engineered to exhibit each smell and running them through this project's own Phase 1
pipeline (`parse_file` + `compute_all_metrics`) — no hand-fabricated metric values anywhere in
the training data. See [`PHASE2_NOTES.md`](refactor-engine/PHASE2_NOTES.md) for the full methodology,
accuracy numbers, feature importances, and — because a tool that hides its weaknesses isn't
trustworthy — the known failure modes.

## Project layout

```
refactor-engine/
  engine/
    parser.py, metrics.py, models.py, serializer.py, pipeline.py
    ml/            # features.py, train.py, predict.py + the trained .pkl artifacts
    suggester/      # graph.py, cluster.py, suggest.py
    cli/             # main.py, render.py, explanations.py
  data/
    generate_synthetic_dataset.py   # builds the 400-row training set from scratch
    labeled_dataset.csv
  sample_repo/, sample_repo_2/       # fixture repos used to validate every phase
  tests/                              # 36 tests across parser/metrics/graph/cluster/suggest/pipeline
  run_scan.py                          # dev entrypoint (`pip install .` gives you `refactor-scan`)
  pyproject.toml                       # packaged as a real installable CLI, model included
  PHASE2_NOTES.md                       # classifier methodology & honest limitations
```

~2,900 lines of Python across 26 source files. Every one of the 5 pipeline stages above has
its own dedicated test file.

## Known limitations (and we're proud of documenting them)

A tool that only tells you what it's good at isn't one you should trust. Some real ones:

- **`cbo`, `dit`, and `fan_in` currently carry zero weight** in the trained model — they're
  constant-zero across the synthetic training data (no inheritance, no cross-class calls
  generated), so the classifier has never learned to use them. Effectively a 5-feature model
  today.
- **Stateless/utility classes are out-of-distribution.** Every synthetic training example
  gives its class instance state, so a class with zero `self` access (LCOM = 1.0) sits outside
  anything the model has seen and gets a low-confidence, often-wrong prediction.
- **Feature Envy and Long Method have no suggestion strategy yet** — the suggester only knows
  how to propose Extract Class splits for cohesion-based smells. Detecting envy/long-method
  correctly is not the same as knowing how to fix them; the CLI says so explicitly rather than
  pretending otherwise.
- **100% test accuracy on the synthetic dataset measures separability, not generalization.**
  The real validation target is real OSS repos with refactor-commit-derived labels — that
  evaluation is still ahead.

Full detail on all of these, including exact numbers, lives in
[`PHASE2_NOTES.md`](refactor-engine/PHASE2_NOTES.md).

## Testing

```bash
pytest tests/ -v
```

36 tests, organized by pipeline stage:

| File | Covers |
|---|---|
| `test_parser.py` | AST extraction: methods, calls, field reads/writes |
| `test_metrics.py` | Every structural metric, hand-verified against small examples |
| `test_graph.py` | Method-graph construction: shared-field edges vs. call edges |
| `test_cluster.py` | Community detection: the exact "two separate clusters split correctly" guarantee the suggester depends on |
| `test_suggest.py` | Suggestion generation and the class-naming heuristic |
| `test_pipeline.py` | End-to-end integration on real fixture repos, including directory-exclusion and single-file scanning |

## Roadmap

- Suggestion strategies for Feature Envy (move-method) and Long Method (extract-method)
- Real-world OSS + refactor-commit evaluation set, to replace the synthetic-only accuracy story
- Stateless-utility-class training examples to close the LCOM=1.0 blind spot
- Wire `cbo`/`dit`/`fan_in` into training data that actually exercises them

## License

[MIT](refactor-engine/LICENSE) — do what you want with it.
