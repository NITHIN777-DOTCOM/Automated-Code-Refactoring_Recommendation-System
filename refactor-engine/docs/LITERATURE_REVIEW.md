# Literature Review: Metrics and Features for Python Code Smell Detection

**Comparing our detector's feature set against four published Python code smell studies.**

*Prepared 24 August 2026. Scope: identify metrics used in the literature that we do not compute, and decide which are worth adding.*

---

## Executive summary

Four papers were located, and three of the four were obtained in full text. The
headline finding is uncomfortable but useful:

> **None of the four papers uses a single coupling or cohesion metric.** Every
> feature across all four is a size, counting, or Halstead-volume metric computed
> from one syntactic unit in isolation. Our eight features include four
> relational metrics (`cbo`, `lcom`, `fan_in`, `fan_out`) that have no counterpart
> anywhere in this literature.

That cuts both ways. It means our feature set is *not* behind the state of the
practice on structural coupling — it is ahead of it. It also means **the specific
problem we set out to solve (the ORCIDOAuth2 false positive, which needs a true
foreign-data-access metric) is not solved by any of these four papers.** The fix
has to come from Lanza & Marinescu's original ATFD/FDP definitions, which we
already cite in `engine/thresholds.py` but implement only as a call-name proxy.

What these papers *do* offer that we lack is a much sharper treatment of **size**.
We measure class and method length as a raw physical line span. They separate
logical lines, source lines, comment lines, and blank lines. Section 5
demonstrates with measured data that this crude size measurement is a direct,
load-bearing cause of the ORCIDOAuth2 misclassification — 47% of that class's
"length" is comments and blank lines.

**Top recommendations** (detail in §6): (1) record foreign attribute reads and
derive real ATFD + FDP; (2) replace raw line-span with logical/source/comment/blank
line counts; (3) add parameter count; (4) add message-chain length; (5) treat
Halstead as optional and low priority.

---

## 1. Attribution correction

Before the summaries, one correction that matters for a citation list.

Paper 2 was given to us as **"Rathee & Chhabra (ICSEC 2022)"**. The paper matching
that description in every other respect — ICSEC 2022, 115 open-source Python
projects, 39 class-level and 22 function-level metrics — is:

> N. Vatanapakorn, C. Soomlek, and P. Seresangtakul. "Python Code Smell Detection
> Using Machine Learning." *2022 26th International Computer Science and
> Engineering Conference (ICSEC)*, 2022. DOI [10.1109/ICSEC56337.2022.10049330](https://doi.org/10.1109/ICSEC56337.2022.10049330)

Confirmed against the Semantic Scholar record (CorpusID 257158280) and DBLP. There
are researchers named Rathee and Chhabra publishing in the coupling/cohesion metric
space, but they are not the authors of this paper. **Cite Vatanapakorn et al.**

---

## 2. Paper summaries

### Paper 1 — Sandouka & Aljamaan (2023), PeerJ Computer Science

> Sandouka, R., & Aljamaan, H. "Python code smells detection using conventional
> machine learning models." *PeerJ Computer Science* 9:e1370, 2023.
> DOI [10.7717/peerj-cs.1370](https://doi.org/10.7717/peerj-cs.1370) · Full text obtained (open access, via PMC10280480)

**What they did.** Built a Python-specific code smell dataset for two smells —
Large Class (class level) and Long Method (method level) — because prior smell
datasets were overwhelmingly Java. Mined four projects at pinned versions:
**NumPy 1.9.2, Django 1.8.2, Matplotlib 1.4.3, SciPy 0.16.0b2**. Metrics extracted
with the **Radon** tool. Labels taken from the published, expert-validated
**PySmell** dataset rather than from their own thresholds.

**Features — the full list, verbatim from the published CSV** (see §7; note the
paper says "18 features" but the released file has 19):

*Raw metrics (7)*

| Column | Meaning |
| --- | --- |
| `loc` | Total lines of code (physical span) |
| `lloc` | Logical lines of code — one per logical statement |
| `scloc` | Source lines of code — lines with actual code, excluding comments/blanks |
| `comments` | Comment lines |
| `single_comments` | Single-line (`#`) comments |
| `multi_comments` | Lines occupied by multi-line strings / docstrings |
| `blanks` | Blank lines |

*Halstead complexity metrics (12)* — all derived from operator/operand counts

| Column | Meaning |
| --- | --- |
| `h1` | Number of **distinct operators** |
| `h2` | Number of **distinct operands** |
| `n1` | **Total** operators |
| `n2` | **Total** operands |
| `vocabulary` | `h1 + h2` |
| `length` | `n1 + n2` |
| `calculated_length` | `h1·log₂h1 + h2·log₂h2` (Halstead's predicted length) |
| `volume` | `length · log₂(vocabulary)` |
| `difficulty` | `(h1/2) · (n2/h2)` |
| `effort` | `difficulty · volume` |
| `time` | `effort / 18` — estimated seconds to program |
| `bugs` | `volume / 3000` — estimated delivered defects |

**Classifier and results.** Six conventional models (Decision Tree, Random Forest,
Logistic Regression, SVM, MLP, SGD), evaluated on accuracy and MCC.

| Smell | Best model | Accuracy | MCC |
| --- | --- | --: | --: |
| Large Class | Random Forest | 0.927 | 0.77 |
| Long Method | Decision Tree | 0.959 | 0.90 |

---

### Paper 2 — Vatanapakorn, Soomlek & Seresangtakul (2022), ICSEC

> DOI [10.1109/ICSEC56337.2022.10049330](https://doi.org/10.1109/ICSEC56337.2022.10049330) · **Full text NOT obtained** — closed access, Unpaywall reports zero open-access locations; IEEE Xplore and ResearchGate both refused automated access. Summary below is from the verified abstract.

**What they did.** Trained eight ML models on a dataset built from **115 open-source
Python projects**, using **39 class-level** and **22 function-level** software
metrics, to detect five smells: **Long Method, Long Parameter List, Large Class,
Long Scope Chaining, and Long Base Class List**. Applied correlation-based feature
selection (CFS) and logistic-regression forward stepwise selection. Compared against
a "tuning machine" (threshold-tuning) baseline.

**Results.** Up to **99.72% accuracy** on Long Method and Long Base Class List. They
also report identifying a set of high-impact features per smell.

> ⚠️ **The 39 + 22 metric list could not be enumerated.** This is the one genuine gap
> in this review. Two things are worth stating about it:
>
> - The five smell names are *exactly* the PySmell smell taxonomy (Chen et al.), and
>   PySmell's own metrics are pure size/count metrics (verified by reading its
>   source — see §3). So the smell definitions carry no coupling requirement.
> - A 39-metric *class-level* suite is large enough that it plausibly includes a
>   standard OO block (CBO, LCOM, DIT, NOC, RFC, WMC). **This is unverified.** It
>   makes paper 2 the single highest-value follow-up if institutional IEEE access
>   becomes available, because it is the only one of the four that could contain a
>   coupling metric we don't have.
>
> Also note paper 4 explicitly cautions that the 99.72% figure "may be inflated due
> to limited dataset diversity or overfitting."

---

### Paper 3 — Uddin, Knobo, Ferdoshi, Abdullah & Azmain (2024), ICRAI / ACM

> "Enhancing Software Quality: Python Code Smell Detection using Machine Learning
> techniques and Refactoring Long Methods using Extract Method Algorithm."
> *ICRAI 2024*, ACM. DOI [10.1145/3728985.3728993](https://doi.org/10.1145/3728985.3728993) · Full text obtained (ACM gold OA)

**What they did.** Selected 50 GitHub Python repos with >1000 stars (Keras, Django,
Seaborn, SciPy, others), ran the **PySmell** tool over them — it produced output for
33 of the 50 — and merged five smell files into a **multi-label** dataset. Balanced
by selecting equal smelly/non-smelly rows. Then built a naive Extract Method
refactoring algorithm that pulls large if/else blocks out of long methods.

**Features — the complete list is five metrics**, each the defining metric of one
smell (their Table 1, verbatim):

| Metric | Level | Meaning | Smell threshold |
| --- | --- | --- | --- |
| `CLOC` | Class | Class Lines of Code | Large Class: ≥ 35 |
| `MLOC` | Function | Method Lines of Code | Long Method: ≥ 50 |
| `LMC` | Expression | **Length of Message Chain** — how many methods are chained with dots | Long Message Chain: ≥ 4 |
| `PAR` | Function | **Number of Parameters** | Long Parameter List: ≥ 5 |
| `NOC` | Expression | Number of Characters (lambda body) | Long Lambda Function: ≥ 70 |

**Classifier and results.** ANN (two hidden layers, 12 and 6 nodes) reached 87–90%.
Ensemble models via Label Powerset:

| Model | Accuracy | Precision | F1 | Recall | Hamming loss |
| --- | --: | --: | --: | --: | --: |
| AdaBoost | 69% | 96% | 83% | 73% | 0.06 |
| XGBoost | **100%** | 100% | 100% | 100% | 0.0000080 |
| Random Forest | **100%** | 100% | 100% | 100% | 0.0000053 |

> ⚠️ **The 100% figures are tautological, not impressive.** Their input features
> *are* the deterministic threshold metrics that define their labels — Large Class
> is defined as `CLOC ≥ 35`, and `CLOC` is an input column. The classifier is
> recovering a step function it was handed. This is the identical limitation we
> already document in `data/real_world/label_corpus.py`: *"a model trained on these
> labels learns to reproduce the thresholds."* Useful as calibration context — it
> tells us a reported accuracy near 1.0 in this literature usually signals label
> leakage rather than a strong model.

---

### Paper 4 — Rao, Dewangan & Mishra (2025), MDPI Applied Sciences

> "An Empirical Evaluation of Ensemble Models for Python Code Smell Detection."
> *Applied Sciences* 15(13):7472, 2025. DOI [10.3390/app15137472](https://doi.org/10.3390/app15137472) · Full text obtained (open access, CC-BY)

**What they did.** This is a **direct methodological extension of paper 1 on paper 1's
own dataset** — same Large Class and Long Method CSVs, same 18/19 Radon features, same
Zenodo record. Their contribution is preprocessing and ensembles: five ensemble
learners (Bagging, Gradient Boost, Max Voting, AdaBoost, XGBoost), chi-square feature
selection to the top 10, and SMOTE for class balance, with 10-fold cross-validation.

**Features.** Identical to paper 1 (their Appendix A Table A1 reproduces paper 1's
metric list). Their chi-square top-10 selections:

| Dataset | Selected features |
| --- | --- |
| Large Class | `n2, scloc, lloc, n1, multi_comments, single_comments, comments, blanks, h1, LargeClass` |
| Long Method | `n2, scloc, lloc, h2, n1, multi_comments, comments, blanks, h1, Experince Based` |

> ⚠️ **The last entry in each list is the label column, not a feature.**
> `LargeClass` and `Experince Based ` are the target columns in the released CSVs
> (verified — see §7). Chi-square selection appears to have been run over the full
> dataframe including the target. If that selected column was then used as a
> training input, the reported scores reflect target leakage. Stated as an
> observation, since we cannot inspect their code path — but it is a reason to
> discount their headline numbers when calibrating against ours.

**Results.** With chi-square FST + SMOTE:

| Large Class | Accuracy | Precision | Recall | F-measure | Cohen κ | MCC |
| --- | --: | --: | --: | --: | --: | --: |
| Bagging | 0.93 | 0.93 | 0.93 | 0.93 | 0.785 | 0.786 |
| Gradient Boost | 0.94 | 0.93 | 0.94 | 0.93 | 0.795 | 0.798 |
| **Max Voting** | **0.96** | 0.96 | 0.97 | **0.96** | 0.842 | **0.85** |
| AdaBoost | 0.94 | 0.94 | 0.94 | 0.94 | 0.788 | 0.790 |
| XGBoost | 0.93 | 0.93 | 0.93 | 0.93 | 0.819 | 0.813 |

For Long Method, Gradient Boost reached accuracy 0.9845, F-measure 0.98, MCC 0.94.
Without FST/SMOTE, Bagging was best on Large Class (0.95 / MCC 0.827) — i.e. the
preprocessing *lowered* the best Large Class accuracy from 0.95 to... well, raised
the ensemble winner to 0.96 but the comparison is not clean across models.

---

## 3. Supporting source: the PySmell tool

Papers 1, 2 and 3 all trace their labels or smell definitions back to PySmell:

> Chen, Z., et al. "Understanding metric-based detectable smells in Python software:
> A comparative study." *Information and Software Technology* 94:14–29, 2018.
> Tool + data: [github.com/chenzhifei731/Pysmell](https://github.com/chenzhifei731/Pysmell)

We read `pysmell/detection/astChecker.py` directly. Relevant confirmations:

- It is a **pure `ast.NodeVisitor` walker** (Python 2 era) — no type resolution, no
  cross-file analysis.
- Class-level, it computes exactly two things: `baseClassesSize` (`len(node.bases)`)
  and a class line count (distinct line numbers, excluding nested function/class
  bodies and the leading docstring).
- Function/expression level: parameter count, lambda expression character length,
  and an operation count.
- **There is no coupling, cohesion, or foreign-data metric anywhere in the tool.**

This is what lets us say with confidence that papers 1, 3 (and the smell
*definitions* behind 2) carry no ATFD-like metric.

---

## 4. Comparison table: our 8 features vs the union of theirs

**Our current feature set** (`engine/metrics.py`, and the `FEATURE_COLUMNS` order in
the labeled dataset): `cbo`, `lcom`, `class_length`, `avg_method_length`,
`avg_cyclomatic_complexity`, `dit`, `fan_in`, `fan_out`.

Legend — **(a)** functionally equivalent to something we already compute ·
**(b)** genuinely new · **(c)** not computable from AST alone.

| # | Their feature | Paper(s) | What it measures | Verdict |
| --: | --- | --- | --- | --- |
| 1 | `loc` / `CLOC` / `MLOC` | 1, 3, 4 | Physical line span of a class or method | **(a)** — this is exactly our `class_length` / `avg_method_length` (`end_line − start_line`) |
| 2 | `lloc` | 1, 4 | **Logical** lines — one per logical statement | **(b)** — strictly more precise than our line span |
| 3 | `scloc` | 1, 4 | **Source** lines — code only, excluding comments and blanks | **(b)** — high value, see §5 |
| 4 | `comments` | 1, 4 | Comment line count | **(b)** |
| 5 | `single_comments` | 1, 4 | `#` comment lines | **(b)** |
| 6 | `multi_comments` | 1, 4 | Docstring / multi-line-string lines | **(b)** |
| 7 | `blanks` | 1, 4 | Blank lines | **(b)** |
| 8 | `h1` | 1, 4 | Distinct operators | **(b)** — partially overlaps our cyclomatic complexity |
| 9 | `h2` | 1, 4 | Distinct operands | **(b)** |
| 10 | `n1` | 1, 4 | Total operators | **(b)** |
| 11 | `n2` | 1, 4 | Total operands | **(b)** |
| 12 | `vocabulary` | 1, 4 | `h1 + h2` | **(b)**, but *derived* — no information beyond 8–9 |
| 13 | `length` | 1, 4 | `n1 + n2` | **(b)**, derived from 10–11 |
| 14 | `calculated_length` | 1, 4 | `h1·log₂h1 + h2·log₂h2` | **(b)**, derived |
| 15 | `volume` | 1, 4 | `length · log₂vocabulary` | **(b)**, derived |
| 16 | `difficulty` | 1, 4 | `(h1/2)·(n2/h2)` | **(b)**, derived |
| 17 | `effort` | 1, 4 | `difficulty · volume` | **(b)**, derived |
| 18 | `time` | 1, 4 | `effort / 18` — estimated coding seconds | **(b)** but *dimensionally meaningless* — a fixed rescale of `effort`, carries zero extra signal |
| 19 | `bugs` | 1, 4 | `volume / 3000` — estimated defects | **(b)** but same objection — a fixed rescale of `volume` |
| 20 | `PAR` | 2, 3 | Number of parameters of a method | **(b)** — we compute `parameter_count()` in `metrics.py` but **never surface it as a model feature** |
| 21 | `LMC` | 3 | **Length of message chain** — depth of `a.b.c.d` dotted calls | **(b)** — most interesting single item in this table, see §6.4 |
| 22 | `NOC` (chars) | 3 | Character length of a lambda body | **(b)** but narrow — only meaningful for the Long Lambda smell, which we don't detect |
| 23 | `baseClassesSize` / LBCL metric | 2, 3(PySmell) | **Number of base classes** (`len(node.bases)`) | **(b)** — genuinely distinct from our `dit`: DIT is inheritance *depth*, this is *breadth* (multiple inheritance / mixin count) |
| 24 | LSC metric | 2 | Scope chaining depth (nested function scopes) | **(b)** — narrow; targets a smell we don't detect |
| 25 | *(39 class-level + 22 function-level, unenumerated)* | 2 | Unknown | **Unverified** — see §2 paper 2 |

**Category (c) — not computable from AST alone: none.**

This is worth saying out loud in a presentation, because it is a slightly surprising
result: **every metric used across all four papers is statically computable from
source text.** No paper uses execution traces, test coverage, runtime profiling, or
version-history churn. Our AST-only architecture is not a limitation relative to
this literature.

Two honest caveats to that: (i) rows 12–19 are *algebraically derived* from rows
8–11 and add no independent information — a model with all twelve Halstead columns
effectively has four; (ii) for paper 2, "computable from AST alone" is an assumption
we could not verify, and if its 39-metric suite includes CBO/RFC-style metrics, those
need cross-file **name** resolution (which we do) or **type** resolution (which we
don't).

**Features we have that no paper has:** `cbo`, `lcom`, `fan_in`, `fan_out`, `dit`.
Five of our eight. The four relational ones have no counterpart anywhere in this
literature.

---

## 5. The ORCIDOAuth2 case — diagnosed with measurements

This was flagged as the concrete bug the review should address. We measured it
rather than reasoning about it.

**The class:** `ORCIDOAuth2` in
`data/real_world/raw_sources/git_repos/social-core/social_core/backends/orcid.py`,
lines 11–136. Our labeling pipeline assigns it **Feature Envy**; our published-threshold
check disagrees.

**What our tools actually report:**

```
ORCIDOAuth2   class_length=125  lcom=1.00  cbo=0  fan_in=0  fan_out=0  dit=0
              atfd_proxy=0      laa=1.0    3 methods, 2 self-fields
```

**Probe 1 — where does its data actually come from?** We walked the AST counting
attribute reads by receiver:

| Access kind | Count |
| --- | --- |
| `self.X` reads | 2 (`USER_ID_URL`, `USER_DATA_URL`) |
| **Foreign `other.X` reads** | **0 — none at all** |
| Dict/subscript reads on locals & parameters | 7 (`response[...]`, `email_dict[...]`, `emails_list[...]`, …) |

The class's three methods parse an external JSON payload handed in as a *parameter*.
They never touch another class's attributes. **A correctly implemented, fully
type-resolved ATFD would also return 0 here.** The threshold rule is right and the
classifier is wrong.

**Probe 2 — why does the classifier think otherwise?** Two of our features are
misleading it, and both are measurement defects rather than modelling defects:

1. **`lcom = 1.00` is a false signal.** Our LCOM counts method pairs sharing no
   `self.X` field. A class whose methods operate on *parameters* rather than on
   *shared state* scores maximal incohesion by construction, regardless of whether
   anything is wrong. LCOM is the **3rd most important feature** in the retrained
   model (importance 0.1941).

2. **`class_length = 125` is inflated roughly 2×.** Counting the physical span:

   | Component | Lines |
   | --- | --: |
   | Blank | 17 |
   | `#` comment | 42 |
   | Actual code | 67 |
   | **Our reported `class_length`** | **125** |

   **47% of the "length" is comments and whitespace** — this class carries large
   explanatory comment blocks documenting the ORCID API response shape. `class_length`
   is the **single most important feature** in the retrained model (importance 0.2866).

**The contrast case.** The repo already contains a hand-built fixture,
`ambiguous_envy.py`, with a genuinely envious class. Measured side by side:

| Class | LCOM | atfd_proxy | LAA | Genuinely envious? |
| --- | --: | --: | --: | --- |
| `ORCIDOAuth2` (real) | **1.00** | 0 | 1.00 | **No** — self-contained, parameter-driven |
| `Caller` (fixture) | **0.00** | 4 | 0.33 | **Yes** — reaches into `Alpha` and `Beta` |

**LCOM is inverted with respect to real Feature Envy in this pair.** The envious class
scores 0.00 and the innocent one scores 1.00. A model leaning on LCOM to predict
Feature Envy is reading a signal that points the wrong way. That is a precise,
demonstrable account of why our Feature Envy F1 sits at 0.237.

**Does any paper fix this? No.** Papers 1 and 4 have no coupling metric. Paper 3 has
no coupling metric. Paper 2 might, and is inaccessible. The nearest thing in the whole
reviewed set is `LMC` from paper 3, which measures reaching *through* objects
(`a.b.c.d`) and would correctly not fire on ORCIDOAuth2 — helpful but not sufficient
on its own.

**So the fix has to come from the source we already cite** — Lanza & Marinescu's
ATFD/LAA/FDP — implemented against attribute reads rather than call names.

**One further piece of evidence for that.** Our own labeling audit records the
Feature Envy rule's yield at four different resolution scopes:

| Scope | Classes matching the Feature Envy rule |
| --- | --: |
| file | 4 |
| package | 16 |
| repo | 60 |
| corpus | **405** |

A 100× swing driven purely by how wide a net the *name* lookup casts. That is the
signature of name-based rather than type-based resolution: widening the scope does
not find more envy, it finds more coincidental name collisions. It is the strongest
single argument in this document for investing in real type resolution eventually.

---

## 6. Recommended additions, prioritized

Ranked by (value to our weak smells) × (feasibility on our current AST parser).
Effort is judged against `engine/parser.py`, which currently walks module-level
`ClassDef` nodes only, records `self.X` reads as `fields_accessed`, and records call
names as `calls_made` **with the receiver discarded**.

### 6.1 — Foreign attribute access: real `ATFD` + `FDP` ★ top priority

| | |
| --- | --- |
| **What it measures** | `ATFD` (Access To Foreign Data): how many distinct attributes belonging to *other* classes this class reads or writes directly. `FDP` (Foreign Data Providers): how many *distinct classes* those attributes come from. |
| **How it's computed** | Walk each method body for `ast.Attribute` nodes whose `.value` is a name other than `self`. Resolve that name to a class. Count distinct `(class, attribute)` pairs → ATFD; count distinct classes → FDP. |
| **Helps most** | **Feature Envy** (currently F1 0.237, our worst measurable smell), and secondarily **God Class** — Lanza & Marinescu's God Class strategy needs ATFD too, and we currently drop that clause entirely. |
| **Type resolution?** | **Split the work in two.** See below. |
| **Effort** | **Medium** (lite) / **Large** (full) |

This is the direct answer to the ORCIDOAuth2 problem, and it should be staged:

- **Stage 1 — ATFD-lite (medium effort, no type resolution).** Today our parser
  *throws foreign attribute reads away entirely*: `_extract_calls_and_fields()`
  records an `ast.Attribute` only when `child.value.id == self_name`. Everything
  else is dropped on the floor. Recording those reads as
  `foreign_accesses: list[tuple[str, str]]` on `MethodInfo` is a small, contained
  parser change. Resolution stays name-based (same heuristic as our existing
  `fan_out`), so precision is imperfect — but we go from **no signal at all** to a
  real one, and crucially it is an *attribute*-based signal rather than a
  *call-name*-based one, which is what the published ATFD definition actually
  specifies. **This alone would give the classifier a feature that correctly reads
  0 for ORCIDOAuth2 and 2 for `Caller`.**
- **Stage 2 — ATFD-full (large effort, needs type resolution).** Infer the type of
  each receiver (parameter annotations, assignment tracing, import graph). This is
  a genuine architectural lift — a new inference layer the codebase has no
  equivalent of. It is what would collapse the 4 → 405 scope explosion above. **Do
  not start here.** Ship stage 1, measure whether Feature Envy F1 moves, and let
  that decide whether stage 2 is worth it.

`FDP` comes almost free once foreign accesses are recorded, and it is the piece
that distinguishes *"envies one specific class"* (→ Move Method) from *"touches
many classes"* (→ a dispersed-coupling problem needing different advice). Our
suggester currently cannot tell those apart.

### 6.2 — Logical / source / comment / blank line counts ★ best effort-to-value ratio

| | |
| --- | --- |
| **What it measures** | Decomposes a raw line span into `lloc` (logical statements), `scloc` (code lines), `comments`, `multi_comments` (docstrings), `blanks`. |
| **How it's computed** | `tokenize` over the class/method source range, or adopt **Radon** (the tool papers 1 and 4 used) as a dependency. |
| **Helps most** | **Long Method** (F1 0.624) and **God Class** (0.867) — the two smells where our top-two features are both raw line spans. Also directly reduces the ORCIDOAuth2 false positive, where 47% of the measured length is not code. |
| **Type resolution?** | **No.** |
| **Effort** | **Small** |

Our `class_length` and `avg_method_length` carry importance 0.2866 and 0.2176 — the
two strongest features in the model — and both are `end_line − start_line`, which
counts docstrings, comment blocks, and blank lines as though they were logic.
Papers 1 and 4's chi-square selection independently kept `scloc`, `lloc`, `blanks`,
`comments`, and `multi_comments` in the top ten **for both smells**, which is decent
external evidence that the decomposition carries real signal. This is the cheapest
substantial improvement available to us.

### 6.3 — Parameter count (`PAR`)

| | |
| --- | --- |
| **What it measures** | Number of parameters of a method (excluding `self`); aggregate to a class as max and mean. |
| **How it's computed** | `len(node.args.args)` — and we **already have this function**: `parameter_count()` in `engine/metrics.py`. It is simply never surfaced as a model feature. |
| **Helps most** | **Long Method**; also a supporting **God Class** signal (fat constructors). |
| **Type resolution?** | **No.** |
| **Effort** | **Small** — wiring, not implementation. |

Both paper 2 and paper 3 treat Long Parameter List as a first-class smell. We do not
detect it, but the metric is a useful *feature* for the smells we do detect, and the
code already exists.

### 6.4 — Message chain length (`LMC`)

| | |
| --- | --- |
| **What it measures** | Depth of dotted access chains — `self.a.b.c.d`. Paper 3 flags ≥ 4. |
| **How it's computed** | Walk `ast.Attribute` nodes counting nesting depth of the `.value` spine. Report max and mean per class. |
| **Helps most** | **Feature Envy** — a long chain is literally the syntax of reaching through one object to get at another's data. It is the only metric in the reviewed literature that carries any envy signal. |
| **Type resolution?** | **No** — pure syntax, which is exactly what makes it attractive as a cheap partial substitute for ATFD. |
| **Effort** | **Small–Medium** |

Worth pairing with 6.1 rather than treating as an alternative: LMC catches
*Law-of-Demeter*-style reaching that ATFD-lite's name resolution would miss, and it
correctly stays low on ORCIDOAuth2 (whose accesses are single-level subscripts).

### 6.5 — Number of base classes (`NBC`) — small, cheap, mildly useful

| | |
| --- | --- |
| **What it measures** | `len(node.bases)` — inheritance *breadth* (mixin count), as distinct from our `dit` which measures *depth*. |
| **How it's computed** | We already parse `base_classes` in `ClassInfo`; this is `len()` of an existing field. |
| **Helps most** | **God Class** (mixin-heavy classes accumulate responsibility), and it is a known confound for **Data Class** — our WOC logic already resolves inherited methods, so breadth is relevant context. |
| **Type resolution?** | **No.** |
| **Effort** | **Small** — one line. |

### Not recommended: the Halstead block

Papers 1 and 4 supply twelve Halstead columns, but **eight of them are algebraic
transforms of the other four**, and `time` (`effort/18`) and `bugs` (`volume/3000`)
are fixed rescales carrying literally zero additional information. If we ever want
Halstead, adding `h1`, `h2`, `n1`, `n2` captures the entire construct at a quarter
the dimensionality. Given that our tree ensemble already has strong size and
complexity features, and that Halstead volume is heavily collinear with them, the
expected gain is low. **Defer.**

### Summary table

| Priority | Addition | Helps | Type resolution? | Effort |
| --: | --- | --- | --- | --- |
| 1 | `ATFD` + `FDP` (stage 1: name-based) | Feature Envy, God Class | No | Medium |
| 1b | `ATFD` + `FDP` (stage 2: type-resolved) | Feature Envy | **Yes — new layer** | Large |
| 2 | `lloc`, `scloc`, `comments`, `multi_comments`, `blanks` | Long Method, God Class | No | Small |
| 3 | `PAR` (max + mean) | Long Method, God Class | No | Small |
| 4 | `LMC` (max + mean) | Feature Envy | No | Small–Medium |
| 5 | `NBC` | God Class, Data Class | No | Small |
| — | Halstead (12 cols) | — | No | Small but **low value** |

**On Data Class**, which our last run confirmed we cannot measure (n=9, F1 0.000):
nothing in these four papers helps. None of them detects Data Class at all. Its
defining metrics (WOC, NOAM, NOPA) come from Lanza & Marinescu, we already implement
them, and the problem is population scarcity in real code rather than a missing
feature. That remains a data problem, not a metric problem.

---

## 7. Dataset accessibility for the follow-up comparison

We checked each paper for a downloadable dataset and **verified the accessible ones by
actually downloading them.**

### ✅ Papers 1 & 4 — Zenodo, downloaded and inspected

> Sandouka, R., & Aljamaan, H. (2023). *Python Code Smell Datasets* [Data set]. Zenodo.
> DOI [10.5281/zenodo.7512516](https://doi.org/10.5281/zenodo.7512516) · **CC-BY-4.0**

Papers 1 and 4 share this record. Two CSVs, both fetched successfully:

| File | Rows | Columns | Label column | Label balance |
| --- | --: | --: | --- | --- |
| `Python_LargeClassSmell_Dataset.csv` | 1000 | 20 | `LargeClass` | 800 clean / 200 smelly |
| `Python_LongMethodSmell_Dataset.csv` | **894** | 20 | `Experince Based ` *(sic)* | 685 clean / 209 smelly |

Direct download URLs:
```
https://zenodo.org/records/7512516/files/Python_LargeClassSmell_Dataset.csv?download=1
https://zenodo.org/records/7512516/files/Python_LongMethodSmell_Dataset.csv?download=1
```

**Three discrepancies found by inspecting the actual files** — all worth reporting,
and all relevant to how we design the follow-up:

1. **Feature count is 19, not 18.** Both papers say "18 features". The files carry 19
   feature columns plus the label. The likely explanation is that `comments` is
   treated as the sum of `single_comments` + `multi_comments` and not counted, but
   neither paper says so.
2. **Long Method has 894 rows, not 1000.** Both papers state 1000 instances per
   dataset; paper 4 goes further and states "1000 instances each, with only 200
   labeled as smelly and 800 as non-smelly." For Long Method the released file is
   894 rows at 685/209. Paper 4's stated class balance is wrong for that dataset.
3. **There are no identifier columns.** No project name, file path, class or method
   name, or line numbers — only the 19 numeric features and the label.

> ⚠️ **Consequence for the follow-up step, stated plainly:** we **cannot** join their
> rows to source code, so a per-instance comparison of "their computed metrics vs
> ours on the same class" is **not possible from the CSVs alone**. Two workable
> alternatives:
>
> - **(a) Distribution comparison** — run our tool over the same four pinned project
>   versions (NumPy 1.9.2, Django 1.8.2, Matplotlib 1.4.3, SciPy 0.16.0b2), compute
>   our metrics, and compare *distributions* against theirs. Cheap, immediately
>   doable, but not instance-level.
> - **(b) Reconstruct the join** — fetch those four versions, run **Radon** over them
>   to regenerate the 19 features *with* file/class identifiers, and match rows back
>   to their CSV on the feature vector. Feasible because the vectors are
>   high-dimensional enough to be near-unique, but it is real work and matching will
>   be imperfect. This is the only route to a true instance-level comparison.

### ✅ Paper 3 — PySmell tool and study data on GitHub

> [github.com/chenzhifei731/Pysmell](https://github.com/chenzhifei731/Pysmell) — public, last pushed July 2018, **no LICENSE file** (⚠️ check terms before redistributing anything derived from it)

The repo is browsable and contains the pieces paper 3 depended on:

| Path | Contents |
| --- | --- |
| `pysmell/detection/` | The tool itself — `astChecker.py`, `customast.py`, `detector.py` |
| `pysmell/study data/smell occurrences(106)` | Smell occurrence data across the 106 studied projects |
| `pysmell/tuning repository/` | `definite positive`, `definite negative`, `inspected entities`, `suspicious entities` — the expert-labeled tuning set |
| `pysmell/validationrepository/` | Validation set |

Paper 3's *own* derived dataset (the 33-project merged multi-label CSV) is **not**
published — no availability statement appears in the paper. But its inputs are
reproducible: the project list is described (>1000-star repos: Keras, Django,
Seaborn, SciPy, …) and the tool is public. Note it is Python-2 era code and will need
porting to run today.

Paper 4 additionally links its experimental code at
`github.com/seemadewangan/Long-Method-Python-dataset-with-gradient-boost-ensemble-method`
(a `.txt` of model code, not a dataset).

### ❌ Paper 2 — nothing available

Closed access, Unpaywall reports zero OA locations, and no data availability
statement is visible from the abstract or indexing records. **No dataset, and no
metric list.**

### Recommended follow-up

Start with **route (a)** on the Zenodo data — the four pinned project versions are
easy to obtain and our pipeline already handles repo-scale corpora. It answers
"do our metrics land in the same range as the published ones" within a day. Escalate
to **route (b)** only if the distribution comparison shows something surprising that
needs instance-level resolution.

---

## 8. Threats to this review

- **Paper 2 is summarized from its abstract only.** Its 39 class-level and 22
  function-level metrics are the single largest unknown here, and it is the only one
  of the four that plausibly contains the coupling metrics our comparison concluded
  are absent from the literature. The claim *"none of the four papers uses a coupling
  metric"* is **verified for papers 1, 3 and 4 and assumed for paper 2.** If IEEE
  access becomes available, re-checking paper 2 should be the first thing done.
- **Papers 1 and 4 are not independent.** They share a dataset, a feature set, and a
  Zenodo record; paper 4 is an ensemble/preprocessing study *on* paper 1's data.
  Treating them as two independent data points on feature choice would overcount.
  Effectively this review covers **three** distinct feature sets, not four.
- **Reported accuracies are not comparable to ours.** Papers 1, 3 and 4 evaluate on
  balanced or near-balanced binary datasets; our corpus is 87% Clean across five
  classes. Paper 3's 100% is threshold-tautological and paper 4's selection appears
  to include the label column. Use their numbers for context only, never as a target.
- **Our own labels are threshold-derived too.** The same criticism we make of paper 3
  applies in weaker form to us, and is already documented in `label_corpus.py`. Any
  improvement from the recommendations here will be measured as *agreement with
  published thresholds*, not against human judgement.

---

## 9. References

1. Sandouka, R., & Aljamaan, H. (2023). Python code smells detection using conventional machine learning models. *PeerJ Computer Science*, 9, e1370. https://doi.org/10.7717/peerj-cs.1370
2. Vatanapakorn, N., Soomlek, C., & Seresangtakul, P. (2022). Python Code Smell Detection Using Machine Learning. *2022 26th International Computer Science and Engineering Conference (ICSEC)*. https://doi.org/10.1109/ICSEC56337.2022.10049330
3. Uddin, M. S., Knobo, K. Z. Q., Ferdoshi, J., Abdullah, S., & Azmain, M. A. (2024). Enhancing Software Quality: Python Code Smell Detection using Machine Learning techniques and Refactoring Long Methods using Extract Method Algorithm. *Proceedings of ICRAI 2024*, ACM. https://doi.org/10.1145/3728985.3728993
4. Rao, R. S., Dewangan, S., & Mishra, A. (2025). An Empirical Evaluation of Ensemble Models for Python Code Smell Detection. *Applied Sciences*, 15(13), 7472. https://doi.org/10.3390/app15137472
5. Chen, Z., Chen, L., Ma, W., Zhou, X., Zhou, Y., & Xu, B. (2018). Understanding metric-based detectable smells in Python software: A comparative study. *Information and Software Technology*, 94, 14–29.
6. Lanza, M., & Marinescu, R. (2006). *Object-Oriented Metrics in Practice*. Springer. — source of the ATFD / LAA / FDP / WOC detection strategies already cited in `engine/thresholds.py`.
7. Sandouka, R., & Aljamaan, H. (2023). *Python Code Smell Datasets* [Data set]. Zenodo. https://doi.org/10.5281/zenodo.7512516
