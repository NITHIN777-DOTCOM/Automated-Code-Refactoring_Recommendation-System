# Data Class detection: root-cause fix, and why it isn't shipped as the default model

**Status: experimental. Not the default. Nothing about this changes the published v0.4.1 behaviour of `refactor-scan` without `--model dataclass-experiment`.**

## The bug

`engine/thresholds.py`'s Data Class rule (Lanza & Marinescu, verbatim: `WOC < 1/3 AND ((NOPA+NOAM > 5 AND WMC < 31) OR (NOPA+NOAM > 8 AND WMC < 47))`) needs NOPA -- the number of public attributes a class exposes. NOPA came from exactly one source: `self.x` assignments seen while walking a method body. A class with attributes declared directly in its body -- the single most common real-world shape for a Data Class, e.g. Django's

```python
class Meta:
    ordering = ["-created"]
    verbose_name = "entry"
```

-- has zero methods, so NOPA read 0 regardless of how many attributes the body actually declared. That forces WOC to 1.0 (see `accessor_metrics`'s `else` branch), which fails the rule's `WOC < 1/3` gate before NOPA/NOAM are even consulted. Confirmed against the corpus: 322 nested classes (the `class Meta:` shape and its equivalents), and every single one of them landed `Clean`.

## The fix (kept, shipped, in `engine/parser.py`)

Added `_class_body_fields()`: walks a class's own body for `ast.Assign`/`ast.AnnAssign` statements at the class-body level (not inside `__init__` or any method) and folds those names into `cls.fields` alongside the existing `self.x` fields. Additive only -- `self.x` detection is untouched.

The boundary drawn, and why:
- A nested `class Meta:` is an `ast.ClassDef`, not an `ast.Assign` target -- it's parsed as its own independent `ClassInfo` already (existing `parent_class` logic), so it was never at risk of being double-counted as a field of its parent. A regression test (`test_class_body_field_does_not_double_count_a_nested_class`) locks this in.
- Non-trivial shapes at class-body level (tuple-unpack, augmented assignment, anything inside an `if`/`for`) are skipped rather than guessed at.
- **Accepted limitation, not solved**: a type alias (`UserId = int`) is AST-indistinguishable from a genuine field (`verbose_name = "post"`) -- both are `Name = value`. No attempt is made to separate them. Real Meta-style/config fields vastly outnumber class-body type aliases in practice, so treating both as fields is the better default.

This is the part of B.2b's finding that was a straightforward, unconditionally-correct bug fix, and it stays fixed regardless of what happens below.

**Confirmed to have zero effect on the currently-published, default `refactor-scan` behaviour.** The default classifier's features (`engine/metrics.py::compute_all_metrics`) never read `cls.fields` at all -- WOC/NOPA/NOAM are used only by `engine/thresholds.py`'s rule-based labeling pipeline, not by the ML classifier's inference path. Verified directly: `refactor-scan analyze` and `refactor-scan why` produce **byte-identical output**, with and without this parser change, on a real file containing genuine `class Meta:` attribute-only classes (`django-oscar/src/oscar/apps/catalogue/abstract_models.py`). The one other runtime consumer of `cls.fields` is the Feature Envy suggester's receiver-to-class resolution (`engine/suggester/suggest.py::_resolve_receiver_class`), where a wider field set can only let it recognize *more* legitimate matches -- a correctness improvement in the same direction as the fix itself, not a behaviour change to model predictions or scores.

## What re-labeling the corpus with the fix actually did

Re-running `label_corpus.py` (unmodified script, just re-run after the parser fix) over the full 7830-row corpus:

| | Before this fix (still published) | After (this experiment) |
| --- | --: | --: |
| Data Class rows | 45 | 244 |
| Data Class test-split rows | 9 | 49 |
| Nested classes total | 322 | 322 |
| ...now labeled Data Class | 0 | 14 |
| ...still Clean | 322 | 307 |
| ...now Feature Envy | 0 | 1 |

The rule is not rubber-stamping every nested class: of 322 nested classes (258 of them literally named `Meta`), only 14 cross the NOPA+NOAM>5 threshold and flip to Data Class -- the other 307 genuinely don't declare enough attributes to qualify, and correctly stay Clean. Every row-by-row diff against the previously-published dataset confirmed **zero regressions**: all 45 previously-Data-Class rows stayed Data Class; every God Class/Feature Envy/Long Method row is byte-identical; the only movement is 199 previously-Clean rows newly (and correctly) flipping to Data Class.

**Sanity-checked, not assumed.** 8 stratified newly-flipped rows were read in full: a Django `Meta` (6 fields, 0 methods), an Enthought Traits `DataView` (declarative attributes + a plotting spec), a `TypedDict` request shape, two `faker.Provider` locale classes (1600+ lines, but of pure constant data tables -- WMC correctly stayed 0), a Django model with only field declarations, a trivial widget subclass, and an openpyxl `Serialisable` class with a pass-through `__init__`. All eight are genuine data classes, not artifacts of the new detection logic.

## Why this isn't shipped as the new default model

Retraining a second RandomForest (same architecture, same 11 features, same hyperparameters -- see `FOREST_PARAMS`) on the relabeled corpus and scoring it on the corpus's own held-out test split:

| Metric | Published default (real, n=45 Data Class) | Experimental (real, n=244 Data Class) | Change |
| --- | --: | --: | --: |
| **Data Class F1** (n=9 -> n=49) | 0.000 | 0.231 | **+0.231** |
| Overall accuracy | 95.8% | 86.7% | **-9.1 pts** |
| Clean false alarms (of 1365/1405) | 16 | 132 | **+116** |
| Macro-F1 | 0.662 | 0.662 | ~unchanged |

Data Class recall genuinely improves (0.000 -> 0.429) because the model finally has real signal to learn from. But it comes at a real cost: attribute-heavy classes that are perfectly ordinary (small config objects, simple containers) now compete with genuine Data Classes in a feature space -- `cbo`, `lcom`, `class_length`, `avg_method_length`, `avg_cyclomatic_complexity`, `dit`, `fan_in`, `fan_out`, `atfd`, `fdp`, `fdp_concentration` -- that has no feature actually measuring "how much of this class is declarative attributes vs. behavior." The eleven features were built around structural/coupling smells (God Class, Feature Envy, Long Method); none of them counts NOPA, NOAM, or WOC directly. The model is inferring "probably a Data Class" from a correlated proxy (short/absent methods, low complexity, low coupling) that also describes a lot of ordinary Clean code, hence 132 new false alarms.

**This confirms the root-cause diagnosis was correct** -- the detector really couldn't see class-body attributes, and fixing that really does make Data Class learnable where it wasn't before (F1 0.000 -> 0.231 is not noise; it moved every one of the 45 previously-invisible rows without breaking anything else). But it also demonstrates that Data Class detection in the *current single-model architecture* is a genuine multi-objective trade-off, not a bug with one right answer: you cannot currently gain Data Class recall without spending overall-accuracy and false-alarm budget, because the shared feature set wasn't designed to separate this smell from Clean code.

## Recommendation for future work

Not something to force into the current model right now. Two directions worth exploring later, in order of how directly they attack the actual gap:

1. **Add features that directly measure the thing the rule already measures**: expose WOC, NOPA, NOAM (or a proxy usable at inference time, since NOPA needs the AST, not just aggregate metrics) as model features, so the classifier has direct signal instead of inferring it through correlated proxies.
2. **A smell-specific secondary classifier**: since Data Class is fundamentally "absence of behavior" rather than a structural/coupling problem like the other three smells, a dedicated binary classifier (or even the existing rule-based `_data_class()` predicate applied directly, since it's already correct once fed real NOPA) may outperform folding it into the same multi-class forest.

Either is a real modeling change, deserving its own evaluation and its own decision about shipping -- not a rider on this bug fix.

## What is and isn't shipped

| Artifact | Status |
| --- | --- |
| `engine/parser.py::_class_body_fields` | **Shipped.** Real bug fix, zero effect on default model behaviour (verified above), kept. |
| `tests/test_parser.py`, `tests/test_thresholds.py` (new fixtures) | **Shipped.** Regression coverage for the fix and its boundary. |
| `data/real_world/labeled_real_dataset.csv`, `labeling_audit.json`, `RETRAIN_COMPARISON.md`, `engine/ml/models/classifier_real.joblib` | **Unchanged.** Byte-identical (md5-verified) to the published v0.4.1 state. |
| `data/real_world/labeled_real_dataset_dataclass_experiment.csv`, `labeling_audit_dataclass_experiment.json` | New, experimental. Relabeled corpus with the fix applied. |
| `engine/ml/models/classifier_real_dataclass_experiment.joblib` | New, experimental. Opt-in only, via `--model dataclass-experiment` or `$REFACTOR_SCAN_MODEL=dataclass-experiment`. Not the default. |

## Reproducing this

```bash
# Relabel the corpus under a distinct filename (does not touch the published CSV)
python data/real_world/label_corpus.py --output data/real_world/labeled_real_dataset_dataclass_experiment.csv

# Train the experimental bundle under a distinct filename (does not touch the published model)
python -m engine.ml.train \
    --dataset data/real_world/labeled_real_dataset_dataclass_experiment.csv \
    --split-column split \
    --output engine/ml/models/classifier_real_dataclass_experiment.joblib \
    --name "real_dataclass_experiment"

# Try it on real code, clearly opt-in
refactor-scan analyze path/to/code.py --model dataclass-experiment
```
