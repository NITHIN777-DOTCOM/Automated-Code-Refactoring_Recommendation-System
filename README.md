# refactor-engine

A Python static-analysis tool that detects code smells (God Class, Data
Class, Feature Envy, Long Method) and suggests concrete Extract Class
refactorings, using AST metrics + an ML classifier + graph-based clustering.
The CLI is built with `click` + `rich`/`rich-click` for a colorized,
readable terminal experience.

## Quick Start

```bash
cd refactor-engine
pip install -r requirements.txt
python run_scan.py analyze sample_repo
```

That's the whole demo. It scans the bundled `sample_repo/` fixture, classifies
each class, and prints a colorized report of what's wrong and how to fix it —
see [Running a scan](#running-a-scan) below for example output. The CLI
presents itself as `refactor-scan` in its own `--help`/usage text; you invoke
it via `python run_scan.py ...`.

**What each phase does:**

| Phase | Module | What it does |
|---|---|---|
| 1. Parser | `engine/parser.py` | Walks Python source with `ast` and extracts every class into structured `ClassInfo`/`MethodInfo` data (methods, fields, calls). |
| 1. Metrics | `engine/metrics.py` | Computes structural metrics per class/method: LCOM, CBO, cyclomatic complexity, class/method length, fan-in/out, depth of inheritance. |
| 2. Classifier | `engine/ml/` | A RandomForest trained on a synthetic labeled dataset predicts a smell label (or "Clean") from those metrics, with a confidence score. |
| 3. Suggester | `engine/suggester/` | Builds a graph of methods linked by shared-field access, clusters it to find natural sub-groups, and proposes Extract Class suggestions with a heuristic name for each. |
| 4. Pipeline | `engine/pipeline.py` | Wires all three phases together into one `analyze_repo()` call. |
| CLI | `run_scan.py`, `engine/cli/` | click commands (`analyze`, `explain`) with rich-rendered, colorized output. |

## Architecture

```
 source files
      |
      v
  [ Parse ]  engine/parser.py        -> ClassInfo / MethodInfo per class
      |
      v
  [ Metrics ]  engine/metrics.py     -> LCOM, CBO, complexity, length, ...
      |
      v
  [ Classify (ML) ]  engine/ml/      -> smell label + confidence
      |
      v
  [ Cluster (graph) ]  engine/suggester/graph.py, cluster.py
      |                                -> field-sharing method clusters
      v
  [ Suggest ]  engine/suggester/suggest.py
                                       -> Extract Class suggestions
      |
      v
  [ CLI ]  run_scan.py, engine/cli/   -> colorized report (rich/click)
```

v1 uses Python's built-in `ast` module only (no tree-sitter yet), so it
supports Python source files.

## Structure

```
refactor-engine/
  engine/
    parser.py           # AST walking logic (parse_file, parse_repo)
    metrics.py           # metric calculation functions
    models.py             # ClassInfo, MethodInfo, MetricResult dataclasses
    serializer.py          # metrics_to_json()
    pipeline.py             # analyze_repo(): wires parser -> metrics -> ml -> suggester
    ml/
      features.py              # build_feature_matrix(), scaler/encoder persistence
      train.py                  # trains + evaluates the RandomForest classifier
      predict.py                 # predict_smell(), predict_smell_with_confidence()
    suggester/
      graph.py                    # build_method_graph(): methods as a graph
      cluster.py                   # find_extraction_clusters(): community detection
      suggest.py                    # generate_suggestions(): Extract Class proposals
    cli/
      explanations.py              # plain-language smell/model explanations
      render.py                    # rich rendering: banner, reports, explain output
  data/
    generate_synthetic_dataset.py  # builds data/labeled_dataset.csv
    labeled_dataset.csv
  sample_repo/                     # fixture repo for smoke-testing a full scan
  sample_repo_2/                   # second, untuned fixture repo
  tests/
  run_scan.py                      # CLI entrypoint (click app: analyze, explain)
  PHASE2_NOTES.md                  # ML dataset/classifier notes, known limitations
```

## Setup

```bash
cd refactor-engine
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```python
from engine.parser import parse_file, parse_repo

classes = parse_file("some_module.py")
classes = parse_repo("some_project/")

for cls in classes:
    print(cls.name, [m.name for m in cls.methods])
```

For the full detect-and-suggest pipeline:

```python
from engine.pipeline import analyze_repo

results = analyze_repo("some_project/")
for name, entry in results.items():
    print(name, entry["predicted_smell"], entry["confidence"])
```

## CLI commands

```bash
python run_scan.py                        # startup banner + intro
python run_scan.py --help                  # colorized command list
python run_scan.py --version               # refactor-scan, version 0.1.0
python run_scan.py analyze <path>          # console report (default: --audience dev)
python run_scan.py analyze <path> --audience simple      # plain-language report
python run_scan.py analyze <path> --format json --output report.json
python run_scan.py explain "God Class"     # explain a specific smell
python run_scan.py explain --model         # explain how the ML classifier works
```

### Startup screen

Running with no arguments shows a colorized banner (gradient title, one-line
tool description, and a hint to run `--help`):

```
┌──────────────────────────────────────────────────────────────────── v1 ─┐
│                                                                          │
│    R E F A C T O R - E N G I N E                                        │
│    (title rendered in a purple -> cyan gradient)                        │
│                                                                          │
│    AST-powered code smell detection & refactoring suggestions           │
│                                                                          │
│    refactor-engine scans a Python codebase, flags classes with          │
│    structural code smells (God Class, Data Class, Feature Envy, Long    │
│    Method), and suggests concrete ways to split them up. Built for      │
│    developers who want a fast first pass over a codebase before a       │
│    deeper refactor -- point it at a repo and get a prioritized list     │
│    of what to look at first.                                            │
│                                                                          │
│    Run refactor-scan --help to see all available commands.              │
│                                                                          │
└───────────────────────────────────────────────────────────────────────┘
```

## Running a scan

```bash
python run_scan.py analyze sample_repo
```

Each flagged class gets its own colored panel — border color and smell label
match (red = God Class, yellow = Data Class, magenta = Feature Envy, orange =
Long Method, green = Clean); the summary panel shows flagged vs. clean counts:

```
┌─────────────────────────────── Scan Summary ────────────────────────────────┐
│             Repo:  sample_repo                                              │
│    Files scanned:  3                                                        │
│ Classes analyzed:  4                                                        │
│          Flagged:  3   (red)                                                │
│            Clean:  1   (green)                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────── ReportManager (god_class.py) ────────────────────────┐
│  Data Class  60% confidence                          (yellow border/label)  │
│                                                                              │
│  Extract                               → New class          Shared fields   │
│  add_row, clear_rows, compute_total,   ReportRowsCollection rows, total     │
│  reset_total                                                                │
│  render_header, set_author, set_title  ReportAuthorRenderer author, title   │
│  render_footer, set_footer             ReportFooterRenderer footer          │
│  render_logo, set_logo                 ReportLogoRenderer   logo_path       │
└─────────────────────────────────────────────────────────────────────────────┘
```

`--audience simple` renders the same data without jargon — plain-language
smell descriptions, qualitative confidence ("high confidence" / "low
confidence — worth a second look" instead of a raw percentage), and
suggestions phrased as "Move X into a new class..." rather than
extract-class/shared-field terminology. Useful for sharing a report with
someone who isn't reading the metrics themselves.

For a machine-readable report instead:

```bash
python run_scan.py analyze sample_repo --format json --output report.json
```

You can point either mode at any Python repo:

```bash
python run_scan.py analyze /path/to/other/repo --format json --output other_report.json
```

### Explaining a smell or the model

```bash
python run_scan.py explain "God Class"
python run_scan.py explain "Data Class"
python run_scan.py explain "Feature Envy"
python run_scan.py explain "Long Method"
python run_scan.py explain "Clean"
python run_scan.py explain --model
```

Each smell explanation covers what it is, why it matters, and what to do
about it. `explain --model` covers what a RandomForest is, what metrics it
looks at, and how the confidence score is calculated — written for a
non-technical reader.

## Training the classifier

The classifier ships pre-trained (`engine/ml/*.pkl`). To regenerate the
synthetic dataset and retrain from scratch:

```bash
python data/generate_synthetic_dataset.py   # -> data/labeled_dataset.csv
python -m engine.ml.train                   # -> engine/ml/*.pkl + eval report
```

See [PHASE2_NOTES.md](PHASE2_NOTES.md) for dataset methodology, classifier
accuracy, feature importances, and known limitations.

## Running tests

```bash
pytest tests/ -v
```
