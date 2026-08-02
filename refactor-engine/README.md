# refactor-engine

A Python static-analysis engine that parses Python source files into structured
class/method metadata, for use in automated refactoring tooling.

v1 uses Python's built-in `ast` module only (no tree-sitter yet), so it
supports Python source files.

## Structure

```
refactor-engine/
  engine/
    parser.py      # AST walking logic (parse_file, parse_repo)
    metrics.py     # metric calculation functions
    models.py      # ClassInfo, MethodInfo, MetricResult dataclasses
    serializer.py  # metrics_to_json()
  sample_repo/      # small fixture repo for smoke-testing a full scan
  tests/
    test_parser.py
    test_metrics.py
  run_scan.py       # CLI entrypoint: scan a repo and write a JSON report
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

## Running a scan

`run_scan.py` ties the pipeline together end-to-end: `parse_repo()` ->
`compute_all_metrics()` -> `metrics_to_json()`, writing a JSON report and
printing a short summary.

```bash
python run_scan.py sample_repo --output report.json
```

This scans the bundled `sample_repo/` fixture (a cohesive class, a
low-cohesion "god class", and a pair of tightly coupled classes) and prints
something like:

```
Scanned 3 files, 4 classes, 22 methods
Report written to report.json
```

Inspect `report.json` to see per-class metrics (`lcom`, `cbo`, `fan_in`,
`fan_out`, `depth_of_inheritance`, `class_length`) and per-method metrics
(`cyclomatic_complexity`, `length`). You can point it at any Python repo:

```bash
python run_scan.py /path/to/other/repo --output other_report.json
```

## Running tests

```bash
pytest
```
