"""Why the classifier said what it said -- for ONE specific class.

predict.py answers "what label?". This module answers "which measurement
actually drove that label for THIS class?", which is a different question and
one that a global feature-importance ranking cannot answer: those eight
importance numbers are identical for every class in the codebase, so they can
tell you what the model cares about in general but never what tipped a
particular decision.

METHOD -- single-feature ablation. Each feature in turn is reset to the value
the model considers unremarkable (the training-set mean, which is exactly 0.0
in the scaler's output space) and the class is re-predicted. The resulting
drop in the winning label's probability is how much support that feature's
ACTUAL value was providing. A negative drop means the feature was arguing
*against* the label the model ultimately settled on -- worth surfacing,
because a low-confidence result usually has one of those in it.

This is a local ablation, not SHAP: it measures each feature on its own and
does not attribute interactions between features. For an 8-feature forest
whose importance is concentrated in five columns, that is enough to name the
dominant factor honestly, and it costs 8 predict_proba() calls rather than a
new dependency. Where no single feature carries the decision (every ablation
moves the probability by less than DECISIVE_SUPPORT_THRESHOLD), that is
reported as `single_factor=False` rather than dressed up by promoting a
near-zero winner.
"""

from __future__ import annotations

import numpy as np

from engine.ml.features import FEATURE_COLUMNS  # noqa: F401  (re-exported for callers)

# Same-package internals: the artifacts are lru_cached in predict.py and
# re-loading them here would double the memory for no benefit.
from engine.ml.predict import _load_artifacts, flatten_metrics

# In the scaler's output space the training mean sits at exactly 0.0, so
# writing 0.0 into a column is "make this feature unremarkable" rather than
# "make this feature zero" -- an important difference for a metric like lcom
# where a raw 0.0 is itself a strong, meaningful signal.
NEUTRAL_SCALED_VALUE = 0.0

# Below this, an ablation has not meaningfully moved the model and calling the
# feature "the reason" would be an overstatement.
DECISIVE_SUPPORT_THRESHOLD = 0.01


def training_means() -> dict[str, float]:
    """What each feature averaged across the training set.

    This is read off the fitted scaler rather than hardcoded, so the "typical
    value" quoted in an explanation can never drift away from the numbers the
    shipped model was actually fitted on.
    """
    _, scaler, _, columns = _load_artifacts()
    return {col: float(mean) for col, mean in zip(columns, scaler.mean_)}


def _z_scores(scaled_row: np.ndarray, columns) -> dict[str, float]:
    return {col: float(scaled_row[0, i]) for i, col in enumerate(columns)}


def explain_prediction(metrics_dict: dict) -> dict:
    """Local attribution for one class's prediction.

    Returns the predicted label and confidence (identical to
    predict_smell_with_confidence(), computed from the same call) plus a
    per-feature breakdown:

        {
          "label": str,
          "confidence": float,
          "single_factor": bool,     # did one feature carry the decision?
          "contributions": [
            {
              "feature": "lcom",
              "value": 0.71,          # raw, unscaled -- what the code measures
              "typical": 0.33,        # the training-set mean for this feature
              "z_score": 1.02,        # how unusual this value is, in std devs
              "support": 0.18,        # probability lost if neutralised
              "importance": 0.229,    # the model's GLOBAL weight on it
              "used_by_model": True,  # False for zero-importance columns
            }, ...
          ],                          # ranked, strongest support first
        }
    """
    # Columns come from the selected model, not a module constant: the
    # explanation must describe the features the model actually used.
    model, scaler, label_encoder, columns = _load_artifacts()

    features = flatten_metrics(metrics_dict)
    row = np.array([[features[col] for col in columns]], dtype=float)
    scaled = scaler.transform(row)

    probabilities = model.predict_proba(scaled)[0]
    best = int(np.argmax(probabilities))
    base_probability = float(probabilities[best])

    means = training_means()
    z_scores = _z_scores(scaled, columns)
    importances = model.feature_importances_

    contributions = []
    for i, col in enumerate(columns):
        neutralised = scaled.copy()
        neutralised[0, i] = NEUTRAL_SCALED_VALUE
        without = float(model.predict_proba(neutralised)[0][best])

        contributions.append(
            {
                "feature": col,
                "value": float(features[col]),
                "typical": means[col],
                "z_score": z_scores[col],
                "support": base_probability - without,
                "importance": float(importances[i]),
                # A zero-importance column is one the forest never split on.
                # Ranking it would put a feature the model provably ignores at
                # the top of an explanation of what the model did.
                #
                # Which columns those are depends on the MODEL, not the metric:
                # cbo/dit/fan_in are constant across the synthetic training set
                # so the shipped classifier ignores all three, while the
                # real-world-trained model (engine/ml/bundle.py) does use them.
                # Anything consuming this flag must handle either case -- see
                # the language table in engine/reasoning.py.
                "used_by_model": float(importances[i]) > 0.0,
            }
        )

    usable = [c for c in contributions if c["used_by_model"]]
    strongest = max((c["support"] for c in usable), default=0.0)
    single_factor = strongest >= DECISIVE_SUPPORT_THRESHOLD

    if single_factor:
        ranked = sorted(usable, key=lambda c: (-c["support"], -abs(c["z_score"])))
    else:
        # Nothing moved the model on its own -- the label came out of a
        # combination. Fall back to "most important feature that is also most
        # unusual for this class", which is the best available ordering when
        # ablation has nothing to say.
        ranked = sorted(usable, key=lambda c: -(c["importance"] * abs(c["z_score"])))

    ranked += [c for c in contributions if not c["used_by_model"]]

    return {
        "label": str(label_encoder.inverse_transform([best])[0]),
        "confidence": base_probability,
        "single_factor": single_factor,
        "contributions": ranked,
    }
