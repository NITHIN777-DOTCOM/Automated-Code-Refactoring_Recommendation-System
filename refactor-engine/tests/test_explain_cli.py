"""`refactor-scan explain --model` prints a caveat that matches the classifier
it is describing -- synthetic (default), real-corpus, or a custom bundle.

The `--model` flag stays a boolean mode switch ("explain the classifier" vs
"explain a smell"); `--classifier NAME` picks *which* classifier's explanation
to print, reusing the shared alias/$REFACTOR_SCAN_MODEL/path resolution.

The caveat *selection* is tested directly against `_classifier_caveat` -- the
rich panel rendering collapses under CliRunner's narrow capture width, so
asserting on rendered console text is not reliable. The CLI is exercised for
exit codes and argument-validation errors, which print plainly.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from engine.cli.explanations import (
    CLASSIFIER_CAVEATS,
    CLASSIFIER_FEATURE_COUNTS,
    CLASSIFIER_FEATURE_SUMMARIES,
    MODEL_EXPLANATION,
)
from engine.cli.main import _classifier_caveat, _classifier_feature_summary, cli
from engine.ml.bundle import MODEL_ENV_VAR, REAL_MODEL_PATH
from engine.ml.features import EXTENDED_FEATURE_COLUMNS, FEATURE_COLUMNS


def _run(args, env=None):
    return CliRunner().invoke(cli, args, env=env)


def test_synthetic_caveat_text_is_unchanged_byte_for_byte():
    # The exact wording the tool has always shipped for the default model.
    assert MODEL_EXPLANATION["caveat"] == (
        "The model was trained on synthetic, programmatically generated example "
        "classes, not real-world code. It's a useful first pass, but treat its "
        "labels as a starting point for a human to review, not a verdict."
    )
    assert CLASSIFIER_CAVEATS["synthetic"] == MODEL_EXPLANATION["caveat"]


def test_default_and_explicit_synthetic_resolve_to_the_synthetic_caveat():
    assert _classifier_caveat(None) == MODEL_EXPLANATION["caveat"]
    assert _classifier_caveat("synthetic") == MODEL_EXPLANATION["caveat"]


def test_real_classifier_caveat_describes_the_real_corpus():
    caveat = _classifier_caveat("real")
    assert "9,151 real-world Python files" in caveat
    assert "ETH Py150" in caveat and "CodeSearchNet" in caveat
    assert "Lanza & Marinescu 2006" in caveat
    assert "RETRAIN_COMPARISON.md" in caveat
    # Honestly scoped -- not a claim of perfection.
    assert "not a verdict" in caveat
    assert "synthetic, programmatically generated" not in caveat


def test_real_model_path_also_maps_to_the_real_caveat():
    assert _classifier_caveat(str(REAL_MODEL_PATH)) == CLASSIFIER_CAVEATS["real"]


def test_real_classifier_selectable_via_env_var(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "real")
    assert _classifier_caveat(None) == CLASSIFIER_CAVEATS["real"]


def test_custom_bundle_path_gets_a_generic_provenance_caveat(tmp_path):
    bundle = tmp_path / "mymodel.joblib"
    bundle.write_bytes(b"only the path is resolved here, never loaded")
    caveat = _classifier_caveat(str(bundle))
    assert caveat == CLASSIFIER_CAVEATS["custom"]
    assert "custom, user-provided classifier" in caveat
    assert "No training provenance" in caveat
    assert "9,151" not in caveat
    assert "synthetic, programmatically generated" not in caveat


def test_unknown_classifier_name_is_rejected_at_the_cli():
    result = _run(["explain", "--model", "--classifier", "nonsense"])
    assert result.exit_code != 0
    assert "Unknown model" in result.output


def test_classifier_without_model_is_an_error():
    result = _run(["explain", "God Class", "--classifier", "real"])
    assert result.exit_code != 0
    assert "--classifier only applies together with --model" in result.output


def test_model_flag_still_switches_mode_without_a_smell_name():
    # --model with no smell name must not raise "provide a smell name".
    result = _run(["explain", "--model"])
    assert result.exit_code == 0


def test_feature_counts_are_read_from_the_feature_lists_not_written_out():
    # The whole point of deriving them: adding a column to features.py must not
    # leave the explanation quoting a number no model uses.
    assert CLASSIFIER_FEATURE_COUNTS["synthetic"] == len(FEATURE_COLUMNS) == 8
    assert CLASSIFIER_FEATURE_COUNTS["real"] == len(EXTENDED_FEATURE_COLUMNS) == 11
    # A user-supplied bundle declares its own columns; claiming one would be a guess.
    assert CLASSIFIER_FEATURE_COUNTS["custom"] is None


def test_synthetic_feature_summary_is_unchanged_and_says_eight():
    summary = _classifier_feature_summary(None)
    assert summary == MODEL_EXPLANATION["what_it_looks_at"]
    assert summary == CLASSIFIER_FEATURE_SUMMARIES["synthetic"]
    assert "eight structural measurements" in summary
    assert "eleven" not in summary


def test_real_feature_summary_says_eleven_and_names_the_extra_metrics():
    summary = _classifier_feature_summary("real")
    assert "eleven structural measurements" in summary
    assert "ATFD" in summary and "FDP" in summary
    # The bug this replaced: the real model was described as reading eight.
    assert "eight structural measurements" not in summary


def test_real_model_path_and_env_var_also_get_the_eleven_feature_summary(monkeypatch):
    assert _classifier_feature_summary(str(REAL_MODEL_PATH)) == CLASSIFIER_FEATURE_SUMMARIES["real"]
    monkeypatch.setenv(MODEL_ENV_VAR, "real")
    assert _classifier_feature_summary(None) == CLASSIFIER_FEATURE_SUMMARIES["real"]


def test_custom_bundle_summary_claims_no_feature_count(tmp_path):
    bundle = tmp_path / "mymodel.joblib"
    bundle.write_bytes(b"only the path is resolved here, never loaded")
    summary = _classifier_feature_summary(str(bundle))
    assert summary == CLASSIFIER_FEATURE_SUMMARIES["custom"]
    assert "eight" not in summary and "eleven" not in summary


def test_caveat_and_feature_summary_never_describe_different_classifiers():
    # Both are looked up from one resolved key, so they move together.
    for choice, key in [(None, "synthetic"), ("synthetic", "synthetic"), ("real", "real")]:
        assert _classifier_caveat(choice) == CLASSIFIER_CAVEATS[key]
        assert _classifier_feature_summary(choice) == CLASSIFIER_FEATURE_SUMMARIES[key]


def test_real_caveat_repo_count_matches_the_corpus_manifest():
    import json
    import os

    manifest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "real_world", "corpus_manifest.json",
    )
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    repos = set()
    for source in manifest["sources"]:
        if source.get("category") == "git_repos":
            repos.update(item["name"] for item in source.get("items", []))

    assert len(repos) == 44
    assert f"{len(repos)} open-source GitHub repositories" in CLASSIFIER_CAVEATS["real"]


def test_default_explain_output_is_byte_identical_to_synthetic(capsys):
    from engine.cli.render import print_model_explanation

    print_model_explanation()
    default = capsys.readouterr().out
    print_model_explanation(caveat=CLASSIFIER_CAVEATS["synthetic"])
    explicit = capsys.readouterr().out
    assert default == explicit
