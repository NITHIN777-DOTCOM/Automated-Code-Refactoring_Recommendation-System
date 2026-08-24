# Retraining the smell classifier on real-world code

*Generated 24 August 2026 by `python -m engine.evaluate.report`.*

This compares the classifier that currently ships — trained on generated example classes — against a new one trained on real classes mined from open-source Python. **Both are measured on exactly the same held-out real code: the 1566 classes marked `split == "test"` in `labeled_real_dataset.csv`, which neither model was trained on.**

## The headline

| Measured on real held-out code | Old model (synthetic) | New model (real data) | Change |
| --- | --: | --: | --: |
| **Overall accuracy** | 45.9% | 95.8% | **+49.9 pts** |
| **Macro-F1** (all 5 smells weighted equally) | 0.203 | 0.662 | **+0.459** |
| **Weighted F1** (weighted by how common each smell is) | 0.579 | 0.954 | **+0.375** |
| **False alarms on clean code** (of 1405) | 759 (54.0%) | 16 (1.1%) | **743 fewer** |

> The old model gets fewer than half of real classes right and raises a false alarm on **54.0% of clean code**. That is the finding that justifies the retraining: on a 550-class codebase it would interrupt a developer 759 times with nothing to show them.

## Per-class F1, side by side

| Smell | Test n | Weight of evidence | Old F1 | New F1 | Change |
| --- | --: | --- | --: | --: | --: |
| **Clean** | 1405 | solid | 0.629 | 0.979 | **+0.349** |
| **Data Class** | 9 | not meaningful | 0.000 | 0.000 | no change |
| **Feature Envy** | 32 | indicative | 0.033 | 0.710 | **+0.677** |
| **God Class** | 56 | indicative | 0.062 | 0.919 | **+0.857** |
| **Long Method** | 64 | indicative | 0.289 | 0.703 | **+0.414** |

## Full breakdown: precision, recall and F1

*Precision = when the model claims this smell, how often is it right. Recall = of the classes that really have this smell, how many it finds.*

### Synthetic-trained (current default)

*RandomForest trained on 400 generated example classes, 80 per smell.* Trained on `data/labeled_dataset.csv (synthetic, n=400)`.

| Smell | Precision | Recall | F1 | n | 95% CI on recall |
| --- | --: | --: | --: | --: | --: |
| Clean | 0.997 | 0.460 | 0.629 | 1405 | 0.43 – 0.49 |
| Data Class | 0.000 | 0.000 | 0.000 | 9 | 0.00 – 0.30 |
| Feature Envy | 0.018 | 0.156 | 0.033 | 32 | 0.07 – 0.32 |
| God Class | 0.038 | 0.179 | 0.062 | 56 | 0.10 – 0.30 |
| Long Method | 0.172 | 0.906 | 0.289 | 64 | 0.81 – 0.96 |
| **Overall accuracy** |  |  | **0.459** | 1566 |  |
| **Macro avg F1** |  |  | **0.203** | 1566 |  |
| **Weighted avg F1** |  |  | **0.579** | 1566 |  |

### Real-data-trained (new)

*RandomForest trained on 6264 real classes mined from open-source Python.* Trained on `data/real_world/labeled_real_dataset.csv (real, n=7830)`.

| Smell | Precision | Recall | F1 | n | 95% CI on recall |
| --- | --: | --: | --: | --: | --: |
| Clean | 0.969 | 0.989 | 0.979 | 1405 | 0.98 – 0.99 |
| Data Class | 0.000 | 0.000 | 0.000 | 9 | 0.00 – 0.30 |
| Feature Envy | 0.733 | 0.688 | 0.710 | 32 | 0.51 – 0.82 |
| God Class | 0.927 | 0.911 | 0.919 | 56 | 0.81 – 0.96 |
| Long Method | 0.830 | 0.609 | 0.703 | 64 | 0.49 – 0.72 |
| **Overall accuracy** |  |  | **0.958** | 1566 |  |
| **Macro avg F1** |  |  | **0.662** | 1566 |  |
| **Weighted avg F1** |  |  | **0.954** | 1566 |  |

## Which of these numbers can actually be trusted

The real corpus is dominated by ordinary code — 85.8% of it is `Clean` — so the per-class numbers rest on wildly different amounts of evidence. Reading the F1 column without reading the `n` column next to it would be the single easiest way to draw a wrong conclusion from this table.

| Smell | Test n | Tier | 95% CI on recall | How to read it |
| --- | --: | --- | --: | --- |
| **Clean** | 1405 | `solid` | 0.98 – 0.99 | Large enough to report as a real measurement. |
| **Data Class** | 9 | `not meaningful` | 0.00 – 0.30 | Too few examples to measure anything. Reported for completeness only. |
| **Feature Envy** | 32 | `indicative` | 0.51 – 0.82 | Enough to show the direction; the error bars are wide. |
| **God Class** | 56 | `indicative` | 0.81 – 0.96 | Enough to show the direction; the error bars are wide. |
| **Long Method** | 64 | `indicative` | 0.49 – 0.72 | Enough to show the direction; the error bars are wide. |

**Explicitly, so this is not glossed over:**

- **Data Class (n=9) — not meaningful.** Five test examples cannot measure anything. Its F1 of 0.000 means the model never once predicted Data Class on these five, but the 95% interval on that recall runs from 0.00 to 0.43 — the honest statement is *"we do not know how this model handles Data Class"*, not *"the model fails at Data Class"*.
- **Feature Envy (n=32) — too small to defend.** The score is genuinely poor and the direction is probably real, but with 22 examples a swing of two or three classes moves F1 by more than 10 points.
- **God Class (n=56, `indicative`) and Long Method (n=64, `indicative`) — the two that improved most, and both sit right around the n=30 line.** Good enough to say the new model is clearly better at these — the jumps are far too large to be sampling noise — but not good enough to quote to three decimal places.
- **Clean (n=1405) — solid.** This is the only per-class number in the table with enough behind it to stand on its own, and it is also the one that matters most in practice, because false alarms on clean code are what make a tool get switched off.

## Where each model's mistakes actually go

### Synthetic-trained (current default)

| actual \ predicted | *Clean* | *Data Class* | *Feature Envy* | *God Class* | *Long Method* |
| --- | --: | --: | --: | --: | --: |
| **Clean** | **646** | 42 | 247 | 243 | 227 |
| **Data Class** | 1 | 0 | 3 | 1 | 4 |
| **Feature Envy** | 1 | 0 | **5** | 8 | 18 |
| **God Class** | 0 | 0 | 15 | **10** | 31 |
| **Long Method** | 0 | 0 | 3 | 3 | **58** |

### Real-data-trained (new)

| actual \ predicted | *Clean* | *Data Class* | *Feature Envy* | *God Class* | *Long Method* |
| --- | --: | --: | --: | --: | --: |
| **Clean** | **1389** | 0 | 7 | 2 | 7 |
| **Data Class** | 9 | 0 | 0 | 0 | 0 |
| **Feature Envy** | 10 | 0 | **22** | 0 | 0 |
| **God Class** | 4 | 0 | 0 | **51** | 1 |
| **Long Method** | 22 | 0 | 1 | 2 | **39** |

The old model's row for `Clean` is the whole story: of 1405 genuinely clean classes it labels only 646 as clean and scatters the other 759 across all four smells. It behaves roughly like a model that assumes every class is smelly until proven otherwise — which is exactly what you would expect from training data where 80% of the examples were smelly by construction.

## What changed, in plain language

The classifier was originally taught from 400 example classes that we generated ourselves. Each one was written to demonstrate exactly one problem — a class that was too long, a class whose methods had nothing to do with each other, and so on. Those examples were deliberately clear-cut, because the point of them was to check that the detection machinery worked at all. The model learned them almost perfectly, and its reported accuracy was correspondingly high.

The problem is that real code looks nothing like a textbook example. In our generated data, four out of every five classes had something wrong with them. In real open-source Python, the overwhelming majority of classes are perfectly ordinary. A model taught on the first kind of data arrives at the second kind expecting to find problems everywhere — and duly finds them. Measured against real code it had never seen, the old model got 45.9% of classes right, and flagged 759 of 1405 perfectly healthy classes as having a problem.

So we rebuilt the training data from real code instead: 3,206 classes taken from real open-source Python projects, labelled by measuring them against published thresholds from the code-smell literature rather than by generating them to order. We retrained the same classifier — same algorithm, same settings, same eight measurements, the only difference being what it was shown — and tested it on the same 1566 held-out real classes. Accuracy went from 45.9% to 95.8%, and false alarms on clean code fell from 759 to 16.

The honest caveat is that the new model buys most of that improvement by being much more cautious. It is now very good at recognising ordinary code and leaving it alone, and it is reliable on the two smells with real structural signatures — God Class and Long Method. It is still weak at Feature Envy, and we genuinely cannot say how it handles Data Class, because the real corpus contained only five test examples of it. Those are limitations of the data, not of the retraining, and the next thing worth doing is collecting more examples of the rare smells rather than tuning the model.

## What changed technically

|  | Old | New |
| --- | --- | --- |
| Training data | 400 generated classes | data/real_world/labeled_real_dataset.csv — 2,565 real training rows |
| Class balance | 80 per smell (20% each) | 85.8% Clean, mirroring real code |
| Algorithm | RandomForest, 100 trees, balanced | RandomForest, 100 trees, balanced *(unchanged)* |
| Features | 8 metrics | 8 metrics *(unchanged)* |
| Usable features | 5 of 8 — `cbo`, `dit`, `fan_in` were constant at 0 | **8 of 8** — real code varies on all of them |
| Train/test split | stratified 80/20, re-split each run | the corpus's own fixed `split` column |

The `usable features` row is worth a sentence at a viva. In the generated data three of the eight measurements — coupling, inheritance depth, and how many other classes call into this one — were identical for every single example, so the model could not learn anything from them and effectively ran on five inputs. Real code varies on all eight, so the retrained model uses the full feature set it was designed around.

## Reproducing this

```bash
# 1. Retrain on the real corpus (writes a separate bundle; leaves the default model alone)
python -m engine.ml.train \
    --dataset data/real_world/labeled_real_dataset.csv \
    --split-column split \
    --output engine/ml/models/classifier_real.joblib

# 2. Score both models on the same held-out real rows and regenerate this document
python -m engine.evaluate.report

# 3. Try the new model in the CLI without making it the default
REFACTOR_SCAN_MODEL=real refactor-scan analyze path/to/code.py
```

**The new model is not the default.** The shipped classifier is still the synthetic-trained one; the real-data model is opt-in via `REFACTOR_SCAN_MODEL=real` so the two can be compared on live code before production is switched over. The two models are stored separately and each carries its own scaler, so neither can be accidentally paired with the other's preprocessing.

## Methodology notes

- **Same test rows for both models.** The 1566 rows marked `test` in the corpus, used exactly as the A.2 labelling run fixed them. Neither model was re-split.
- **Each model is applied with its own scaler.** A model's scaler defines the input space it was fitted in; scoring the synthetic model through the real model's scaler would measure a model that never existed.
- **The scaler is fitted on training rows only.** Held-out rows contribute nothing to the means and standard deviations, so no test information reaches training.
- **Predictions are compared as label strings, not encoder integers.** Two encoders fitted on different datasets can map the same smell to different numbers; comparing codes would score each model against a permuted answer key.
- **Recall intervals are Wilson 95%.** The textbook normal interval collapses to [0, 0] at zero successes, which would claim certainty from five examples.
- **Labels are threshold-derived, not hand-verified.** The corpus was labelled by measuring each class against published cutoffs (`engine/thresholds.py`), so these scores measure agreement with those published rules, not with human judgement. That is the same limitation the labelling audit records, and it applies equally to both models, so the comparison between them remains fair.

