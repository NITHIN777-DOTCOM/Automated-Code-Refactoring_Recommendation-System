"""Tests for the two-model setup: bundles, model selection, and the retrain comparison.

These guard the failure modes that are SILENT -- the ones where nothing raises
and the numbers are simply wrong:

  * training a second model overwriting the shipped model's scaler, leaving the
    shipped model paired with another dataset's preprocessing;
  * a model being scored through the wrong scaler;
  * predictions compared as encoder integers when two encoders disagree on
    which integer means which smell;
  * the held-out rows leaking into the scaler that training is fitted under;
  * the explanation layer assuming which features a model uses.

Each of those produces plausible-looking output, so a test is the only thing
standing between them and a confidently wrong result in a report.
"""

from __future__ import annotations

import os

import joblib
import numpy as np
import pandas as pd
import pytest

from engine.ml import train
from engine.ml.bundle import (
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    ModelBundle,
    load_bundle,
    resolve_model_spec,
    save_bundle,
)
from engine.ml.features import (
    ENCODER_PATH,
    EXTENDED_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    SCALER_PATH,
    columns_for,
    fit_scaler,
)


def _bundle_columns(bundle):
    """The feature list a bundle was fitted under.

    Models are no longer all 8-column: one trained after real ATFD/FDP
    landed takes 11. A test that hardcodes FEATURE_COLUMNS against an
    arbitrary bundle is asserting the wrong thing.
    """
    return list(bundle.metadata.get("features") or FEATURE_COLUMNS)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

REAL_DATASET = os.path.join(_ROOT, "data", "real_world", "labeled_real_dataset.csv")
DEFAULT_MODEL_PATH = os.path.join(_ROOT, "engine", "ml", "smell_classifier.pkl")
REAL_BUNDLE = os.path.join(_ROOT, "engine", "ml", "models", "classifier_real.joblib")

pytestmark = pytest.mark.skipif(
    not os.path.exists(REAL_DATASET),
    reason="real-world corpus not present in this checkout",
)


def _digest(path):
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# The core safety guarantee
# ---------------------------------------------------------------------------


def test_training_a_bundle_does_not_touch_the_default_artifacts(tmp_path):
    """The whole reason bundles exist.

    build_feature_matrix() re-fits and re-writes the shared scaler. If a second
    training run went through that path it would leave smell_classifier.pkl --
    the shipped model -- reading inputs centred on a different dataset's means,
    with nothing raising and every prediction quietly shifting.
    """
    before = {path: _digest(path) for path in (DEFAULT_MODEL_PATH, SCALER_PATH, ENCODER_PATH)}

    train.main([
        "--dataset", REAL_DATASET,
        "--split-column", "split",
        "--output", str(tmp_path / "throwaway.joblib"),
    ])

    for path, content in before.items():
        assert _digest(path) == content, f"{os.path.basename(path)} was modified by a bundle run"


def test_bundle_round_trips_and_predicts_identically(tmp_path):
    bundle_path = tmp_path / "round_trip.joblib"
    train.main([
        "--dataset", REAL_DATASET,
        "--split-column", "split",
        "--output", str(bundle_path),
    ])

    bundle = load_bundle(str(bundle_path))
    assert isinstance(bundle, ModelBundle)
    assert set(bundle.metadata) >= {"dataset", "rows", "train_rows", "test_rows", "split"}

    df = pd.read_csv(REAL_DATASET)
    test_df = df[df["split"] == "test"]
    scaled = bundle.scaler.transform(test_df[_bundle_columns(bundle)].values)

    # Loading through as_artifacts() must be the same three objects.
    model, scaler, encoder = bundle.as_artifacts()
    assert np.array_equal(model.predict(scaled), bundle.model.predict(scaled))
    assert list(encoder.classes_) == list(bundle.label_encoder.classes_)
    assert scaler is bundle.scaler


def test_loading_a_loose_pkl_as_a_bundle_is_rejected(tmp_path):
    """A bare estimator must not be mistaken for a bundle -- it has no scaler,
    so it would silently inherit whatever preprocessing was lying around."""
    stray = tmp_path / "not_a_bundle.joblib"
    joblib.dump(joblib.load(DEFAULT_MODEL_PATH), stray)

    with pytest.raises(TypeError, match="not a model bundle"):
        load_bundle(str(stray))


# ---------------------------------------------------------------------------
# Model selection stays opt-in
# ---------------------------------------------------------------------------


def test_the_default_model_is_the_synthetic_one(monkeypatch):
    """Switching production over is a separate, deliberate decision."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    assert DEFAULT_MODEL == "synthetic"
    assert resolve_model_spec() == "synthetic"


def test_environment_variable_selects_the_real_model(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "real")
    assert resolve_model_spec().endswith("classifier_real.joblib")


def test_an_explicit_choice_beats_the_environment(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "real")
    assert resolve_model_spec("synthetic") == "synthetic"


def test_an_unknown_model_name_raises_rather_than_falling_back(monkeypatch):
    """A typo must not silently evaluate the default model under another name."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    with pytest.raises(ValueError, match="Unknown model"):
        resolve_model_spec("realistic")


# ---------------------------------------------------------------------------
# Split handling
# ---------------------------------------------------------------------------


def test_the_fixed_split_column_is_honoured_exactly():
    df = pd.read_csv(REAL_DATASET)
    is_train, is_test = train._masks_from_column(df, "split")

    assert is_train.sum() == (df["split"] == "train").sum()
    assert is_test.sum() == (df["split"] == "test").sum()
    # Every row lands on exactly one side.
    assert not (is_train & is_test).any()
    assert (is_train | is_test).all()


def test_a_split_column_with_unexpected_values_is_rejected():
    df = pd.DataFrame({"split": ["train", "test", "validation"]})
    with pytest.raises(ValueError, match="unexpected values"):
        train._masks_from_column(df, "split")


def test_a_split_column_missing_a_side_is_rejected():
    df = pd.DataFrame({"split": ["train", "train"]})
    with pytest.raises(ValueError, match="both train and test"):
        train._masks_from_column(df, "split")


def test_the_scaler_is_fitted_on_training_rows_only():
    """No held-out row may contribute to the means the model trains under."""
    df = pd.read_csv(REAL_DATASET)
    train_df = df[df["split"] == "train"]

    columns = columns_for(train_df)
    _, scaler = fit_scaler(train_df, columns)
    assert np.allclose(scaler.mean_, train_df[columns].mean().values)

    # And the shipped real bundle was actually built that way.
    if os.path.exists(REAL_BUNDLE):
        bundle = load_bundle(REAL_BUNDLE)
        bundle_cols = _bundle_columns(bundle)
        assert np.allclose(bundle.scaler.mean_, train_df[bundle_cols].mean().values)
        assert not np.allclose(bundle.scaler.mean_, df[bundle_cols].mean().values)


# ---------------------------------------------------------------------------
# The comparison itself
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.path.exists(REAL_BUNDLE), reason="real model bundle not trained yet")
def test_both_models_are_scored_on_the_identical_test_rows():
    from engine.evaluate.compare_models import compare

    results = compare()
    expected = (pd.read_csv(REAL_DATASET)["split"] == "test").sum()

    assert results["test_size"] == expected
    for key in ("synthetic", "real"):
        score = results[key]
        assert sum(c.support for c in score.per_class) == expected
        assert score.class_names == results["class_names"]


@pytest.mark.skipif(not os.path.exists(REAL_BUNDLE), reason="real model bundle not trained yet")
def test_each_model_is_applied_with_its_own_scaler():
    """Scoring a model through the other's scaler would measure a model that
    was never trained. The two scalers must genuinely differ."""
    from engine.evaluate.compare_models import compare

    synthetic_scaler = joblib.load(SCALER_PATH)
    real_scaler = load_bundle(REAL_BUNDLE).scaler

    # The two scalers now differ in SHAPE as well as in values: the shipped
    # synthetic model predates real ATFD/FDP and was fitted on 8 columns,
    # the real-data model on 11. Different lengths already prove they are
    # not interchangeable; only compare values when the shapes match.
    if synthetic_scaler.mean_.shape == real_scaler.mean_.shape:
        assert not np.allclose(synthetic_scaler.mean_, real_scaler.mean_)
    else:
        assert len(real_scaler.mean_) == len(EXTENDED_FEATURE_COLUMNS)
        assert len(synthetic_scaler.mean_) == len(FEATURE_COLUMNS)

    # And the real model genuinely outperforms on real data -- the finding the
    # whole comparison exists to establish.
    results = compare()
    assert results["real"].accuracy > results["synthetic"].accuracy


@pytest.mark.skipif(not os.path.exists(REAL_BUNDLE), reason="real model bundle not trained yet")
def test_predictions_are_compared_as_label_strings():
    """Two encoders fitted on different datasets can map the same smell to
    different integers. Scores must be built from names, not codes."""
    from engine.evaluate.compare_models import compare

    results = compare()
    valid = set(results["class_names"])
    for key in ("synthetic", "real"):
        predictions = results[key].predictions
        assert predictions.dtype.kind in "OU", "predictions must be labels, not codes"
        assert set(predictions) <= valid


# ---------------------------------------------------------------------------
# The explanation layer must not assume which features a model uses
# ---------------------------------------------------------------------------


def test_every_feature_has_wording_for_every_level():
    """Regression guard for a real bug.

    cbo, dit and fan_in are constant at zero in the synthetic training set, so
    the shipped model gives them zero importance and the explanation layer only
    ever reached their "unused" wording. The real-data model does use all eight
    features, and the missing high/typical/low entries raised KeyError mid-report.
    """
    from engine.reasoning import _METRIC_LANGUAGE

    for feature in EXTENDED_FEATURE_COLUMNS:
        language = _METRIC_LANGUAGE[feature]
        for level in ("high", "typical", "low"):
            assert level in language, f"{feature} has no {level!r} wording"
            assert isinstance(language[level], str) and language[level].strip()
        assert callable(language["detail"])


@pytest.mark.skipif(not os.path.exists(REAL_BUNDLE), reason="real model bundle not trained yet")
def test_the_real_model_uses_every_feature():
    """The improvement story rests on this: the synthetic model effectively ran
    on five inputs because three were constant."""
    bundle = load_bundle(REAL_BUNDLE)
    assert all(importance > 0.0 for importance in bundle.model.feature_importances_)

    synthetic = joblib.load(DEFAULT_MODEL_PATH)
    assert any(importance == 0.0 for importance in synthetic.feature_importances_)
