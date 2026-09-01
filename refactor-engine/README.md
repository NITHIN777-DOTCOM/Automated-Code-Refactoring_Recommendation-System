<div align="center">

# Automated-Code-Refactoring_Recommendation-System

**Point it at a Python codebase. It tells you what's wrong, why, and exactly which lines to move where.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](refactor-engine/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](refactor-engine/pyproject.toml)
[![Tests: 257 passing](https://img.shields.io/badge/tests-257%20passing-brightgreen.svg)](refactor-engine/tests/)
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
- [Show me why](#show-me-why)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [CLI reference](#cli-reference)
- [Is it getting better or worse?](#is-it-getting-better-or-worse)
- [Proof: checked against real refactoring history](#proof-checked-against-real-refactoring-history)
- [How the classifier works](#how-the-classifier-works)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations-and-were-proud-of-documenting-them)
- [GitHub Action](#github-action)
- [Testing](#testing)
- [Changelog](#changelog)
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
| **5. Explain** | `refactor-scan why` reopens all four stages and shows the working — which measurement actually drove the model's answer for *this* class, how the graph got grouped, and how that became the suggestion. In plain language, and in a standalone HTML report if you want the full picture. |

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

## Show me why

A tool that says *"God Class, 56% confidence"* and stops is asking you to take its word for
it. `refactor-scan why` shows the working instead — what was measured, what the classifier
made of that, how the methods were grouped, and how those three add up to the suggestion.

**1. The short version.** Point it at a file and get the headline plus the single factor that
mattered most, per class. Multi-class files are fine — this stays a few lines per class no
matter how many there are, so it never floods your terminal:

```
$ refactor-scan why sample_repo/coupled.py

PaymentProcessor — God Class (56% confidence)
  Top reason: Its methods are short -- mostly a line or two each. That is the
              pattern the model most associates with God Class.
  Suggestion: no clean split found along the data its methods use -- worth a manual look

Order — Feature Envy (56% confidence)
  Top reason: This class leans heavily on other classes -- a lot of what it
              does is really driving code that lives somewhere else.
  Suggestion: move checkout() into PaymentProcessor (+1 more)

Run the same command with --output report.html for the full reasoning behind each of these.
```

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

Each class opens with just its name, its label and one plain sentence. The four reasoning
steps underneath — what was measured, what the model made of it, how the methods were
grouped, and what to do — are collapsed `<details>` sections, each showing a one-line
takeaway so the page can be skimmed shut. Open one and you get the full working: every
metric as a sentence with the raw number underneath, the model's per-factor ablation, and an
SVG of the method graph with each suggested group marked by a numbered arc. Native
`<details>` does the collapsing; there is still no JavaScript on the page. `--class` works
here too, to narrow the report to one class.

**4. Nothing wrong?** On a file where everything comes back Clean, it says so and stops:

```
$ refactor-scan why sample_repo/clean.py
No smells detected — nothing to explain.
```

(With `--output` it writes the report anyway, covering why each class was judged Clean — a
scripted step asked to produce a file shouldn't silently produce nothing.)

### How the model's own reasoning is worked out

Feature importances are the same eight numbers for every class in your codebase, so they can
tell you what the model cares about in general but never what tipped one particular
decision. `why` uses **single-feature ablation** instead: each measurement in turn is reset
to the value the model finds unremarkable (the training-set mean) and the class is
re-predicted. How far confidence falls is how much that measurement was really doing — which
is why the report can say *"if this one measurement were ordinary instead, confidence in
Feature Envy would fall by about 49 points"* and mean it literally.

It also reports the measurements that argued **against** the label the model picked. On a
56%-confidence call that's usually the most useful line on the page.

Two honest details it won't hide from you:

- **`cbo`, `dit` and `fan_in` are marked "not used by the model"** wherever they appear. They
  are constant across the training data, so the forest never learned to use them — see
  [Known limitations](#known-limitations-and-were-proud-of-documenting-them).
- **Long Parameter List and Duplicate Code aren't model findings at all.** They're a counting
  rule and a similarity check layered on top. When one of those is what flagged a class, the
  report says so outright rather than explaining a decision the classifier didn't make.

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
  └───────┬───────┘   -> colorized terminal report or machine-readable JSON
          v
  ┌───────────────┐
  │    EXPLAIN    │   engine/reasoning.py, engine/ml/explain.py
  └───────────────┘   -> `why`: per-class ablation + graph, terminal or HTML
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

refactor-scan why <file.py>                      # the reasoning behind a file's results
refactor-scan why <file.py> --class Order        # narrow it to one class
refactor-scan why <file.py> --output report.html # full illustrated report instead
refactor-scan why <file.py> --class Order --output order.html

refactor-scan explain "God Class"                # what it is, why it matters, how to fix it
refactor-scan explain "Data Class"
refactor-scan explain "Feature Envy"
refactor-scan explain "Long Method"
refactor-scan explain --model                    # how the classifier itself works
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
direction of travel:

```
$ refactor-scan trend path/to/click --interval commits --max-samples 8

 date         commit    god   data   envy   long   smelly    rate
 ────────────────────────────────────────────────────────────────────────
 2020-06-11   9cfa961     9      4     19     21    53/66   80.3%   —
 2021-03-01   40e7756    10      4     20     23    57/81   70.4%   ▼9.9%
 2021-10-25   e415d3a    12      2     21     23    58/80   72.5%   ▲2.1%
 2023-06-28   3fcf338    12      3     21     23    59/83   71.1%   ▼1.4%
 2024-11-08   2cabbe3    11      2     28     26    67/95   70.5%   ▼0.6%
 2025-08-16   868230d    11      2     26     27   66/103   64.1%   ▼6.4%
 2026-04-16   76552ff    13      2     29     28   72/114   63.2%   ▼0.9%
 2026-08-09   9c4dfda    13      2     43     31   89/142   62.7%   →

┌─────────────────── First → last ────────────────────┐
│ Span            2020-06-11 → 2026-08-09  (8 points) │
│ Smell rate                                 █▄▄▄▄▁▁▁ │
│ Direction                ▼ improving  (-17.6% rate) │
│                                                     │
│ Classes                                         +76 │
│ Smelly classes                                  +36 │
│   Feature Envy                                  +24 │
└─────────────────────────────────────────────────────┘
```

Read those last two rows together, because they are the reason the command records a
denominator at all: click gained **36 smelly classes** over this stretch, and got
**proportionally cleaner** — 80.3% of its classes were flagged at the start and 62.7% at the
end, because the codebase more than doubled in the same period. A tool reporting raw counts
alone would have called that a regression. The trend direction is therefore always read from
the *rate*, never from the count.

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

## Proof: checked against real refactoring history

Anyone can claim a smell detector "works." The `evaluate` command exists to make that claim
checkable: it mines a real git repository's history for commits that look like refactorings,
re-runs the analyzer on the code **as it existed the moment before** each one, and reports
whether the tool independently flagged the exact class the maintainers then went on to
restructure. No cherry-picking — it's the same detector, the same thresholds, run blind
against history it never saw.

A commit is only counted as *checkable* when a structural diff can name a specific class that
changed shape — a method moved to another class, a class renamed, methods extracted without
the class ballooning in size. A commit whose message merely contains the word "refactor" but
shows no such structural signature contributes nothing to the score; see the caveat below for
why that distinction matters.

Run against [`click`](https://github.com/pallets/click) — a widely used, professionally
maintained CLI framework, 1,795 commits of real history, nothing staged or written for this
demo:

```
$ refactor-scan evaluate path/to/click --max-commits 400

┌────────────────────────── Read this before the numbers ───────────────────────────┐
│ 'Was refactored' is NOT 'was smelly'. Developers refactor for many reasons        │
│ unrelated to detectable smells (renames, API changes, new features), so this      │
│ hit rate is a LOWER BOUND on what the tool could plausibly have flagged -- not    │
│ a rigorous precision/recall benchmark against verified ground truth.              │
└──────────────────────────────────────────────────────────────────────────────────┘

click — per-commit result
┌──────────────┬─────────────┬───────────────────────┬───────────────────┬─────────────────────┬──────┐
│ commit       │ detected by │ refactor target       │ signal             │ we flagged it as    │      │
├──────────────┼─────────────┼───────────────────────┼───────────────────┼─────────────────────┼──────┤
│ 0585f456baa6 │ structural  │ ParamType (types.py)  │ method extracted   │ God Class (0.44)    │ HIT  │
│ 7a0a3447f6dd │ structural  │ KeepOpenFile (utils.py)│ method moved       │ God Class (0.71)    │ HIT  │
│              │             │ LazyFile (utils.py)   │ method moved       │ Feature Envy (0.39) │ HIT  │
│              │             │ PacifyFlushWrapper    │ method moved       │ not flagged         │ miss │
│ 8f300853dc5f │ structural  │ Parameter (core.py)   │ method extracted   │ Long Method (0.43)  │ HIT  │
│ ...          │             │ (4 more rows)         │                    │                     │      │
│ 2fdde68324c3 │ keyword     │ (no structural target — keyword only)      │ —                   │ n/a  │
└──────────────┴─────────────┴───────────────────────┴───────────────────┴─────────────────────┴──────┘

┌────────────────── Aggregate ───────────────────┐
│ Commits evaluated                             8 │
│   with a structural target (checkable)        6 │
│   keyword-only (no verifiable target)         2 │
│                                                 │
│ TARGETED hit rate (commits)     6/6 = 100.0%   │
│ TARGETED hit rate (targets)     8/9 = 88.9%    │
│ FILE-LEVEL hit rate (weaker)    8/8 = 100.0%   │
└──────────────────────────────────────────────┘

Which smell flagged the target: Long Method (5), God Class (2), Feature Envy (1)
By detector: structural → 6/6 checkable, 6 hits (100%) · keyword-only → 0 checkable, 2 file-level hits
```

**What this actually proves, in plain language:** every one of the six click commits that
genuinely restructured a class — moving a method, extracting one, renaming a class — had
already been independently flagged by this tool as smelly, *before* the commit that fixed it,
using nothing but the code as it stood at that point in history. The one miss
(`PacifyFlushWrapper`) was a tiny wrapper class renamed for a Python-2-cleanup pass, not a
structural problem — exactly the kind of refactor-that-wasn't-about-a-smell the caveat above
warns about, and the tool correctly left it alone rather than guessing.

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
    duplication.py   # structural (name-stripped) method similarity
    reasoning.py      # assembles the `why` explanation: measurements + model + graph
    ml/                # features.py, train.py, predict.py, explain.py + the .pkl artifacts
    suggester/          # graph.py, cluster.py, blocks.py, suggest.py
    cli/                 # main.py, render.py, explanations.py, html_report.py
  data/
    generate_synthetic_dataset.py   # builds the 400-row training set from scratch
    labeled_dataset.csv
  sample_repo/, sample_repo_2/       # fixture repos used to validate every phase
  tests/                              # 257 tests across every module above
  run_scan.py                          # dev entrypoint (`pip install .` gives you `refactor-scan`)
  pyproject.toml                       # packaged as a real installable CLI, model included
  PHASE2_NOTES.md                       # classifier methodology & honest limitations
```

Every pipeline stage above has its own dedicated test file.

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

## GitHub Action

Run `refactor-scan` on every pull request, automatically. It scans only the Python files
the PR actually changed (never the whole repo), and posts one comment summarizing what it
found — smell, confidence, and a suggested fix per class. By default the check is purely
informational: it never fails CI or blocks a merge, so it's safe to drop into a repo without
a team conversation first.

Here's what the comment looks like on a PR that touches `order.py` and `report_manager.py`:

> ⚠️ **2 code smells** found in 2 of 2 changed Python files.
>
> | File | Class | Smell | Confidence | Suggested fix |
> | --- | --- | --- | --: | --- |
> | `report_manager.py` | `ReportManager` | God Class | 71% | Extract `render_footer`, `set_footer` into `ReportFooterRenderer` |
> | `order.py` | `Order` | Feature Envy | 56% | Move `checkout()` into `PaymentProcessor` |
>
> <sub>Want the full reasoning? Run it locally: `refactor-scan why order.py --class Order`</sub>
>
> <sub>Only files changed in this PR were scanned — pre-existing code elsewhere is not
> reported. This check is informational and does not block merging.</sub>

### Adding it to your own repo

1. Copy [`.github/workflows/refactor-scan.yml`](.github/workflows/refactor-scan.yml) into
   your repo at the same path.
2. In that file, replace the local install line with the published package:

   ```yaml
   - name: Install refactor-scan
     run: pip install "refactor-scan>=0.4.0"
   ```

That's it — no config file, no secrets, no repo settings to change. The workflow already
has the minimum permissions it needs (`pull-requests: write`, to post/update its own
comment) and triggers on `opened`, `synchronize`, and `reopened` pull request events.

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
pytest tests/ -v
```

257 tests, organized by pipeline stage:

| File | Covers |
|---|---|
| `test_parser.py` | AST extraction: methods, calls, field reads/writes |
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

## Changelog

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

- Stateless-utility-class training examples to close the LCOM=1.0 blind spot
- Wire `cbo`/`dit`/`fan_in` into training data that actually exercises them

## License

[MIT](refactor-engine/LICENSE) — do what you want with it.
