# Retraining the smell classifier on real-world code

*Generated 20 August 2026 by `python -m engine.evaluate.report`.*

This compares the classifier that currently ships — trained on generated example classes — against a new one trained on real classes mined from open-source Python. **Both are measured on exactly the same held-out real code: the 641 classes marked `split == "test"` in `labeled_real_dataset.csv`, which neither model was trained on.**

## The headline

| Measured on real held-out code | Old model (synthetic) | New model (real data) | Change |
| --- | --: | --: | --: |
| **Overall accuracy** | 42.4% | 91.9% | **+49.5 pts** |
| **Macro-F1** (all 5 smells weighted equally) | 0.215 | 0.528 | **+0.313** |
| **Weighted F1** (weighted by how common each smell is) | 0.528 | 0.903 | **+0.375** |
| **False alarms on clean code** (of 550) | 322 (58.5%) | 9 (1.6%) | **313 fewer** |

> The old model gets fewer than half of real classes right and raises a false alarm on **58.5% of clean code**. That is the finding that justifies the retraining: on a 550-class codebase it would interrupt a developer 322 times with nothing to show them.

## Per-class F1, side by side

| Smell | Test n | Weight of evidence | Old F1 | New F1 | Change |
| --- | --: | --- | --: | --: | --: |
| **Clean** | 550 | solid | 0.586 | 0.958 | **+0.372** |
| **Data Class** | 5 | not meaningful | 0.000 | 0.000 | no change |
| **Feature Envy** | 22 | weak | 0.046 | 0.167 | **+0.121** |
| **God Class** | 29 | weak | 0.105 | 0.847 | **+0.743** |
| **Long Method** | 35 | indicative | 0.337 | 0.667 | **+0.330** |

## Full breakdown: precision, recall and F1

*Precision = when the model claims this smell, how often is it right. Recall = of the classes that really have this smell, how many it finds.*

### Synthetic-trained (current default)

*RandomForest trained on 400 generated example classes, 80 per smell.* Trained on `data/labeled_dataset.csv (synthetic, n=400)`.

| Smell | Precision | Recall | F1 | n | 95% CI on recall |
| --- | --: | --: | --: | --: | --: |
| Clean | 1.000 | 0.415 | 0.586 | 550 | 0.37 – 0.46 |
| Data Class | 0.000 | 0.000 | 0.000 | 5 | 0.00 – 0.43 |
| Feature Envy | 0.028 | 0.136 | 0.046 | 22 | 0.05 – 0.33 |
| God Class | 0.065 | 0.276 | 0.105 | 29 | 0.15 – 0.46 |
| Long Method | 0.205 | 0.943 | 0.337 | 35 | 0.81 – 0.98 |
| **Overall accuracy** |  |  | **0.424** | 641 |  |
| **Macro avg F1** |  |  | **0.215** | 641 |  |
| **Weighted avg F1** |  |  | **0.528** | 641 |  |

### Real-data-trained (new)

*RandomForest trained on 2565 real classes mined from open-source Python.* Trained on `data/real_world/labeled_real_dataset.csv (real, n=3206)`.

| Smell | Precision | Recall | F1 | n | 95% CI on recall |
| --- | --: | --: | --: | --: | --: |
| Clean | 0.934 | 0.984 | 0.958 | 550 | 0.97 – 0.99 |
| Data Class | 0.000 | 0.000 | 0.000 | 5 | 0.00 – 0.43 |
| Feature Envy | 1.000 | 0.091 | 0.167 | 22 | 0.03 – 0.28 |
| God Class | 0.833 | 0.862 | 0.847 | 29 | 0.69 – 0.95 |
| Long Method | 0.750 | 0.600 | 0.667 | 35 | 0.44 – 0.74 |
| **Overall accuracy** |  |  | **0.919** | 641 |  |
| **Macro avg F1** |  |  | **0.528** | 641 |  |
| **Weighted avg F1** |  |  | **0.903** | 641 |  |

## Which of these numbers can actually be trusted

The real corpus is dominated by ordinary code — 85.8% of it is `Clean` — so the per-class numbers rest on wildly different amounts of evidence. Reading the F1 column without reading the `n` column next to it would be the single easiest way to draw a wrong conclusion from this table.

| Smell | Test n | Tier | 95% CI on recall | How to read it |
| --- | --: | --- | --: | --- |
| **Clean** | 550 | `solid` | 0.97 – 0.99 | Large enough to report as a real measurement. |
| **Data Class** | 5 | `not meaningful` | 0.00 – 0.43 | Too few examples to measure anything. Reported for completeness only. |
| **Feature Envy** | 22 | `weak` | 0.03 – 0.28 | One or two predictions move this several points. Treat as a hint. |
| **God Class** | 29 | `weak` | 0.69 – 0.95 | One or two predictions move this several points. Treat as a hint. |
| **Long Method** | 35 | `indicative` | 0.44 – 0.74 | Enough to show the direction; the error bars are wide. |

**Explicitly, so this is not glossed over:**

- **Data Class (n=5) — not meaningful.** Five test examples cannot measure anything. Its F1 of 0.000 means the model never once predicted Data Class on these five, but the 95% interval on that recall runs from 0.00 to 0.43 — the honest statement is *"we do not know how this model handles Data Class"*, not *"the model fails at Data Class"*.
- **Feature Envy (n=22) — too small to defend.** The score is genuinely poor and the direction is probably real, but with 22 examples a swing of two or three classes moves F1 by more than 10 points.
- **God Class (n=29, `weak`) and Long Method (n=35, `indicative`) — the two that improved most, and both sit right around the n=30 line.** Good enough to say the new model is clearly better at these — the jumps are far too large to be sampling noise — but not good enough to quote to three decimal places.
- **Clean (n=550) — solid.** This is the only per-class number in the table with enough behind it to stand on its own, and it is also the one that matters most in practice, because false alarms on clean code are what make a tool get switched off.

## Where each model's mistakes actually go

### Synthetic-trained (current default)

| actual \ predicted | *Clean* | *Data Class* | *Feature Envy* | *God Class* | *Long Method* |
| --- | --: | --: | --: | --: | --: |
| **Clean** | **228** | 20 | 98 | 111 | 93 |
| **Data Class** | 0 | 0 | 2 | 1 | 2 |
| **Feature Envy** | 0 | 0 | **3** | 2 | 17 |
| **God Class** | 0 | 0 | 5 | **8** | 16 |
| **Long Method** | 0 | 0 | 0 | 2 | **33** |

### Real-data-trained (new)

| actual \ predicted | *Clean* | *Data Class* | *Feature Envy* | *God Class* | *Long Method* |
| --- | --: | --: | --: | --: | --: |
| **Clean** | **541** | 2 | 0 | 2 | 5 |
| **Data Class** | 5 | 0 | 0 | 0 | 0 |
| **Feature Envy** | 15 | 0 | **2** | 3 | 2 |
| **God Class** | 4 | 0 | 0 | **25** | 0 |
| **Long Method** | 14 | 0 | 0 | 0 | **21** |

The old model's row for `Clean` is the whole story: of 550 genuinely clean classes it labels only 228 as clean and scatters the other 322 across all four smells. It behaves roughly like a model that assumes every class is smelly until proven otherwise — which is exactly what you would expect from training data where 80% of the examples were smelly by construction.

## What changed, in plain language

The classifier was originally taught from 400 example classes that we generated ourselves. Each one was written to demonstrate exactly one problem — a class that was too long, a class whose methods had nothing to do with each other, and so on. Those examples were deliberately clear-cut, because the point of them was to check that the detection machinery worked at all. The model learned them almost perfectly, and its reported accuracy was correspondingly high.

The problem is that real code looks nothing like a textbook example. In our generated data, four out of every five classes had something wrong with them. In real open-source Python, the overwhelming majority of classes are perfectly ordinary. A model taught on the first kind of data arrives at the second kind expecting to find problems everywhere — and duly finds them. Measured against real code it had never seen, the old model got 42.4% of classes right, and flagged 322 of 550 perfectly healthy classes as having a problem.

So we rebuilt the training data from real code instead: 3,206 classes taken from real open-source Python projects, labelled by measuring them against published thresholds from the code-smell literature rather than by generating them to order. We retrained the same classifier — same algorithm, same settings, same eight measurements, the only difference being what it was shown — and tested it on the same 641 held-out real classes. Accuracy went from 42.4% to 91.9%, and false alarms on clean code fell from 322 to 9.

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

- **Same test rows for both models.** The 641 rows marked `test` in the corpus, used exactly as the A.2 labelling run fixed them. Neither model was re-split.
- **Each model is applied with its own scaler.** A model's scaler defines the input space it was fitted in; scoring the synthetic model through the real model's scaler would measure a model that never existed.
- **The scaler is fitted on training rows only.** Held-out rows contribute nothing to the means and standard deviations, so no test information reaches training.
- **Predictions are compared as label strings, not encoder integers.** Two encoders fitted on different datasets can map the same smell to different numbers; comparing codes would score each model against a permuted answer key.
- **Recall intervals are Wilson 95%.** The textbook normal interval collapses to [0, 0] at zero successes, which would claim certainty from five examples.
- **Labels are threshold-derived, not hand-verified.** The corpus was labelled by measuring each class against published cutoffs (`engine/thresholds.py`), so these scores measure agreement with those published rules, not with human judgement. That is the same limitation the labelling audit records, and it applies equally to both models, so the comparison between them remains fair.

