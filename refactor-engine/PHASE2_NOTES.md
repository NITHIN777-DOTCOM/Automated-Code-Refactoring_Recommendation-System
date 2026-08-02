# Phase 2 Notes — Code Smell Classifier

Reference material for the final report. Covers dataset construction, the
trained classifier, and known limitations.

## 1. Dataset: synthetic, not a real public dataset

**Path taken: synthetic generation** (`data/generate_synthetic_dataset.py` →
`data/labeled_dataset.csv`, 400 rows).

Real public code-smell datasets were evaluated first and rejected:

| Dataset | Why not used |
|---|---|
| [MLCQ](https://zenodo.org/records/3666840) (Madeyski & Lewowski, 2020) | Java source. Also ships reviewer-labeled *code snippets*, not a metrics table — using it would require re-deriving metrics from raw Java. |
| [Fontana et al. / Qualitas Corpus](https://zenodo.org/records/842778) | Java source. Its precomputed metric columns come from a Java analyzer and don't correspond to what our extractor produces. |

Our Phase 1 engine (`engine/parser.py`) is a Python-only `ast` walker. Using
either dataset would have meant building a separate Java analyzer — out of
scope for v1.

### How the synthetic data is built

For each of 5 labels (**God Class, Long Method, Feature Envy, Data Class,
Clean**), 80 Python classes are generated programmatically, written to real
`.py` files, and run through **our actual Phase 1 pipeline** (`parse_file` +
`compute_all_metrics`). No metric values are hand-fabricated.

Two design decisions worth recording:

- **Per-sample parsing.** Each sample is parsed in isolation rather than via
  one `parse_repo()` over the whole corpus. `cbo`/`fan_in`/`fan_out` are
  computed by name-matching against `all_classes`, and generated classes
  deliberately reuse generic method names (`get_value_0`), which would create
  spurious cross-sample coupling under a single bulk parse.
- **Deliberate overlap.** An earlier version produced perfectly separated
  clusters and the resulting model misread a realistic mid-size god class
  (`ReportManager`: 12 methods, 40 lines, lcom 0.71) as a Data Class at
  **100% confidence**. Fixed by (a) widening God Class ranges to 8–25 methods
  with fields *shared* between methods, and (b) adding a trivial-method
  "bloated manager" God Class variant plus borderline variants of the other
  labels (`BORDERLINE_RATE = 0.3`).

## 2. Classifier

`RandomForestClassifier(n_estimators=100, class_weight='balanced',
random_state=42)`, trained on an 80/20 stratified split (320 train / 80 test).
Features are median-imputed and `StandardScaler`-normalized.

### Accuracy: 100% on the held-out synthetic test set

```
              precision    recall  f1-score   support
       Clean      1.000     1.000     1.000        16
  Data Class      1.000     1.000     1.000        16
Feature Envy      1.000     1.000     1.000        16
   God Class      1.000     1.000     1.000        16
 Long Method      1.000     1.000     1.000        16
    accuracy                          1.000        80
```

Confusion matrix is perfectly diagonal.

> **This number measures synthetic separability, not real-world
> generalization.** Each class was constructed to exhibit exactly one smell,
> so the clusters do not overlap enough at the decision boundary to cost
> accuracy. It must not be reported as a generalization result. The real
> validation is the later evaluation phase against real OSS repositories with
> refactor-commit-derived labels.

Confidence calibration is the metric that actually improved after the
overlap work:

| | Before overlap fix | After |
|---|---|---|
| `ReportManager` confidence | 1.000 (wrong) | 0.600 (wrong, appropriately uncertain) |
| Test predictions at 100% confidence | ~all | 57/80 |
| Test predictions below 90% confidence | ~0 | 6/80 |
| Minimum test confidence | ~1.0 | 0.811 |

### Feature importances

| Rank | Feature | Importance |
|---|---|---|
| 1 | `avg_method_length` | 0.2742 |
| 2 | `lcom` | 0.2287 |
| 3 | `class_length` | 0.2005 |
| 4 | `avg_cyclomatic_complexity` | 0.1583 |
| 5 | `fan_out` | 0.1382 |
| 6 | `fan_in` | 0.0000 |
| 7 | `dit` | 0.0000 |
| 8 | `cbo` | 0.0000 |

These align with the smell definitions: `lcom` + `class_length` drive God
Class, `avg_cyclomatic_complexity` + `avg_method_length` drive Long Method,
`fan_out` drives Feature Envy.

Per-class raw feature means:

| label | lcom | class_length | avg_method_length | avg_cc | fan_out |
|---|---|---|---|---|---|
| Clean | 0.00 | 18.20 | 2.08 | 1.20 | 0.00 |
| Data Class | 0.76 | 37.66 | 1.72 | 1.17 | 0.00 |
| Feature Envy | 0.10 | 13.05 | 4.15 | 1.00 | 3.95 |
| God Class | 0.78 | 64.38 | 2.40 | 1.29 | 0.00 |
| Long Method | 0.00 | 61.26 | 23.96 | 18.72 | 0.00 |

## 3. Known limitations

### 3.1 `cbo`, `dit`, and `fan_in` are constant-zero and unused

All three have **exactly 0.0000 importance** because they are zero in all 400
rows. This is effectively a **5-feature model**.

- `dit` — no generator emits inheritance.
- `fan_in` — no generated class is called by another.
- `cbo` — our heuristic only fires on constructor-style `ClassName()` calls;
  the Feature Envy generator couples via a passed-in instance
  (`provider.get_value_0()`), which registers as `fan_out` instead.

Consequence: if these metrics carry signal on real code, the model has never
learned to use them. They should either be exercised by the generator or
dropped from the feature set before the evaluation phase.

### 3.2 Stateless/utility classes are out-of-distribution

Training data contains no stateless/utility classes (LCOM=1.0 from zero
self-access); model may misclassify such classes as God/Data Class due to
out-of-distribution extrapolation. Example: `PaymentProcessor` (3 methods,
8 lines) → predicted **God Class @ 0.56 confidence**.

`lcom` spans 0.00–0.88 across the whole dataset because every generator gives
its class instance state. A class whose methods never touch `self` scores
lcom == 1.0 — above anything seen in training — and the forest extrapolates
toward the high-lcom labels regardless of how small the class is.

Left open deliberately. Closing it would mean adding a stateless-utility
generator (module-level helpers, `@staticmethod`-only classes).

### 3.3 `ReportManager` is still misclassified

Predicted **Data Class @ 0.600** (God Class @ 0.400) — the right answer is
God Class. Confidence is now honest about the ambiguity, but the label is
still wrong. Current predictions on `sample_repo/`:

| Class | Predicted | Confidence | lcom | len | avg_mlen | avg_cc | fan_out |
|---|---|---|---|---|---|---|---|
| `Counter` | Clean | 0.750 | 0.00 | 11 | 1.00 | 1.00 | 0 |
| `Order` | Feature Envy | 0.560 | 0.00 | 14 | 3.00 | 1.33 | 4 |
| `PaymentProcessor` | God Class ✗ | 0.560 | 1.00 | 8 | 1.00 | 1.00 | 0 |
| `ReportManager` | Data Class ✗ | 0.600 | 0.71 | 40 | 1.42 | 1.00 | 0 |

## 4. Artifacts

Saved under `engine/ml/`, all loadable independently of `train.py`:

| File | Contents |
|---|---|
| `smell_classifier.pkl` | Trained RandomForest |
| `scaler.pkl` | Fitted `StandardScaler` |
| `label_encoder.pkl` | Fitted `LabelEncoder` (integer → smell name) |

Inference entry point is `engine/ml/predict.py`:

```python
from engine.parser import parse_file
from engine.metrics import compute_all_metrics
from engine.ml.predict import predict_smell, predict_smell_with_confidence

metrics = compute_all_metrics(parse_file("some_module.py"))
predict_smell(metrics["SomeClass"])                  # -> "God Class"
predict_smell_with_confidence(metrics["SomeClass"])  # -> ("God Class", 0.56)
```

`predict.py` loads the three artifacts lazily (`lru_cache`) and never imports
`train.py`. Its `flatten_metrics()` must stay in sync with `_row_for_class()`
in `data/generate_synthetic_dataset.py` — that function produced the training
rows, so divergence is train/serve skew (`dit` ← `depth_of_inheritance`, and
the two `avg_*` features are means over the per-method sub-dict).

## 5. Reproducing

```bash
python data/generate_synthetic_dataset.py   # -> data/labeled_dataset.csv
python -m engine.ml.train                   # -> engine/ml/*.pkl + eval report
```

`random_state=42` throughout, and the generator seeds with `random.seed(42)`,
so both steps are deterministic.
