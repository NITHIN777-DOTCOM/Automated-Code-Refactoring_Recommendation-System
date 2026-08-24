"""Tests for the GitHub Action's PR-comment formatting.

The GitHub API side (find-then-update an existing comment) cannot be
exercised without a live Actions run, so it is kept out of Python entirely
and lives in the workflow's github-script step. Everything that decides what
a reviewer actually READS is a pure function and is tested here.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml

from engine.ci.pr_comment import (
    MARKER,
    MAX_ROWS,
    Finding,
    analyze_files,
    build_comment,
    findings_from_report,
    main,
)

WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".github", "workflows", "refactor-scan.yml",
)


def _report(**classes):
    """An analyze --format json payload with the given {name: entry}."""
    return {"classes": classes, "unused_imports": {}}


def _entry(smell, confidence=0.8, suggestions=None):
    return {
        "predicted_smell": smell,
        "confidence": confidence,
        "file_path": "whatever.py",
        "metrics": {},
        "suggestions": suggestions or [],
    }


# ---------------------------------------------------------------------------
# Reading a report
# ---------------------------------------------------------------------------


def test_clean_classes_are_not_findings():
    report = _report(Tidy=_entry("Clean", 0.99), Big=_entry("God Class", 0.7))

    findings = findings_from_report(report, "src/app.py")

    assert [f.class_name for f in findings] == ["Big"]


def test_finding_uses_the_path_we_scanned_not_the_reports_own():
    """The report's file_path carries native separators -- a Windows runner
    would emit `src\\app.py`, which renders inconsistently next to the
    forward-slash paths git and GitHub use everywhere else."""
    report = _report(Big=_entry("God Class"))
    report["classes"]["Big"]["file_path"] = r"some\other\place.py"

    findings = findings_from_report(report, r"src\app.py")

    assert findings[0].file == "src/app.py"


def test_move_method_suggestion_becomes_one_actionable_line():
    report = _report(
        Order=_entry("Feature Envy", 0.89, [
            {"type": "move_method", "source_method": "checkout",
             "target_class": "PaymentProcessor"},
        ])
    )

    assert findings_from_report(report, "a.py")[0].suggestions == [
        "Move `checkout()` into `PaymentProcessor`"
    ]


def test_move_method_without_a_resolved_class_names_the_receiver():
    """Real ATFD can concentrate envy on an object whose class is outside the
    scan; naming the variable is still more useful than saying nothing."""
    report = _report(
        Order=_entry("Feature Envy", 0.6, [
            {"type": "move_method", "source_method": "run",
             "target_class": None, "target_receiver": "ca"},
        ])
    )

    assert "whatever `ca` holds" in findings_from_report(report, "a.py")[0].suggestions[0]


def test_extract_class_suggestion_caps_the_method_list():
    report = _report(
        Big=_entry("God Class", 0.7, [
            {"type": "extract_class", "suggested_name": "Rows",
             "methods_to_extract": ["a", "b", "c", "d", "e", "f"]},
        ])
    )

    line = findings_from_report(report, "a.py")[0].suggestions[0]

    assert "`a`, `b`, `c`, `d` (+2 more)" in line
    assert "`Rows`" in line


def test_soft_suggestion_types_are_dropped_from_the_comment():
    """no_clear_split and friends are honest results, but a PR comment is
    the wrong place for 'we looked and found nothing conclusive' -- it pushes
    the actionable rows further from the reviewer."""
    report = _report(
        Big=_entry("God Class", 0.7, [
            {"type": "no_clear_split", "note": "no clean boundary"},
            {"type": "unsupported_smell_type", "note": "nothing implemented"},
        ])
    )

    assert findings_from_report(report, "a.py")[0].suggestions == []


# ---------------------------------------------------------------------------
# The comment body
# ---------------------------------------------------------------------------


def test_clean_pr_gets_a_positive_confirmation_not_silence():
    """Zero findings must still produce a comment: silence is ambiguous
    between 'we checked and it is fine' and 'the check never ran'."""
    body = build_comment([], files_scanned=3)

    assert MARKER in body
    assert "No code smells found" in body
    assert "3 changed Python files" in body


def test_single_file_is_not_pluralised():
    assert "1 changed Python file." in build_comment([], files_scanned=1)


def test_findings_render_a_table_worst_first():
    findings = [
        Finding("b.py", "Mild", "Long Method", 0.30, []),
        Finding("a.py", "Severe", "God Class", 0.95, ["Extract `x` into `Y`"]),
    ]

    body = build_comment(findings, files_scanned=2)

    assert "2 code smells" in body
    assert "| File | Class | Smell | Confidence | Suggested fix |" in body
    # Highest confidence first, so a reviewer skimming the top reads the
    # finding the model is most sure about.
    assert body.index("Severe") < body.index("Mild")
    assert "95%" in body and "30%" in body


def test_comment_points_at_the_local_why_command_rather_than_inlining_it():
    findings = [Finding("src/app.py", "Order", "Feature Envy", 0.8, [])]

    body = build_comment(findings, files_scanned=1)

    assert "refactor-scan why src/app.py --class Order" in body
    # The full reasoning report must NOT be dumped into the PR.
    assert len(body) < 4000


def test_extra_suggestions_are_summarised_not_listed():
    findings = [
        Finding("a.py", "Big", "God Class", 0.9, ["Extract `x` into `Y`", "Extract `z` into `W`"])
    ]

    assert "(+1 more)" in build_comment(findings, files_scanned=1)


def test_very_many_findings_are_truncated_with_a_count():
    findings = [
        Finding(f"f{i}.py", f"C{i}", "God Class", 0.5, []) for i in range(MAX_ROWS + 12)
    ]

    body = build_comment(findings, files_scanned=MAX_ROWS + 12)

    assert "and 12 more" in body
    assert len(body) < 60000


def test_unparsable_files_are_reported_without_losing_the_rest():
    findings = [Finding("good.py", "Big", "God Class", 0.8, [])]

    body = build_comment(findings, files_scanned=2, failed_files=["broken.py"])

    assert "`broken.py`" in body
    assert "Could not scan" in body
    # The finding from the file that DID scan is still there.
    assert "`Big`" in body


def test_skipped_files_are_disclosed_rather_than_silently_dropped():
    body = build_comment([], files_scanned=50, skipped_files=7)

    assert "7 more were skipped" in body


def test_every_comment_says_it_is_non_blocking_and_scope_limited():
    for body in (build_comment([], 1), build_comment([Finding("a.py", "C", "God Class", 0.5)], 1)):
        assert "does not block merging" in body
        assert "Only files changed in this PR" in body


def test_marker_is_first_so_the_upsert_can_find_it():
    """The workflow finds its previous comment by this marker. If it ever
    stopped being emitted, every push would post a NEW comment instead of
    editing the old one -- the exact spam this is designed to prevent."""
    assert build_comment([], 1).startswith(MARKER)
    assert build_comment([Finding("a.py", "C", "God Class", 0.5)], 1).startswith(MARKER)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def test_analyze_files_collects_unparsable_files_instead_of_raising():
    def runner(path):
        return None if path == "broken.py" else _report(Big=_entry("God Class"))

    findings, failed, skipped = analyze_files(["ok.py", "broken.py"], runner=runner)

    assert [f.class_name for f in findings] == ["Big"]
    assert failed == ["broken.py"]
    assert skipped == 0


def test_analyze_files_caps_the_number_scanned():
    findings, failed, skipped = analyze_files(
        [f"f{i}.py" for i in range(10)], max_files=3, runner=lambda p: _report()
    )

    assert skipped == 7
    assert failed == []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def test_main_writes_no_comment_when_the_pr_changes_no_python(tmp_path, capsys):
    """A docs-only PR must be a clean no-op: no comment, no failure."""
    out = tmp_path / "body.md"

    exit_code = main(["README.md", "docs/guide.rst", "--output", str(out)])

    assert exit_code == 0
    assert not out.exists()
    assert "No changed Python files" in capsys.readouterr().out


def test_main_defaults_to_non_blocking(tmp_path, monkeypatch):
    """A smell in new code is a prompt for discussion, not a build error."""
    monkeypatch.setattr(
        "engine.ci.pr_comment._run_analyze",
        lambda path: _report(Big=_entry("God Class", 0.9)),
    )
    out = tmp_path / "body.md"

    assert main(["a.py", "--output", str(out)]) == 0
    assert "God Class" in out.read_text(encoding="utf-8")


def test_fail_on_smell_is_opt_in_and_works(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engine.ci.pr_comment._run_analyze",
        lambda path: _report(Big=_entry("God Class", 0.9)),
    )
    out = tmp_path / "body.md"

    assert main(["a.py", "--output", str(out), "--fail-on-smell"]) == 1


def test_fail_on_smell_still_passes_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engine.ci.pr_comment._run_analyze",
        lambda path: _report(Tidy=_entry("Clean", 0.99)),
    )
    out = tmp_path / "body.md"

    assert main(["a.py", "--output", str(out), "--fail-on-smell"]) == 0


def test_main_reads_a_file_list_as_git_diff_emits_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engine.ci.pr_comment._run_analyze",
        lambda path: _report(Big=_entry("God Class", 0.9)),
    )
    listing = tmp_path / "changed.txt"
    listing.write_text("src/a.py\nsrc/b.py\n\n", encoding="utf-8")
    out = tmp_path / "body.md"

    assert main(["--files-from", str(listing), "--output", str(out)]) == 0
    assert "src/a.py" in out.read_text(encoding="utf-8")


def test_main_appends_to_the_actions_job_summary_when_present(tmp_path, monkeypatch):
    """The summary works even on a fork PR, whose token cannot comment --
    so the report is never invisible just because commenting was refused."""
    monkeypatch.setattr(
        "engine.ci.pr_comment._run_analyze",
        lambda path: _report(Big=_entry("God Class", 0.9)),
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    main(["a.py", "--output", str(tmp_path / "body.md")])

    assert MARKER in summary.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The workflow file itself
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.path.exists(WORKFLOW), reason="workflow not present")
def test_workflow_yaml_is_valid_and_wired_correctly():
    with open(WORKFLOW, encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    # PyYAML parses the bare `on:` key as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers["pull_request"]["types"]) >= {"opened", "synchronize"}

    # Commenting needs exactly this scope, and nothing wider.
    assert workflow["permissions"]["pull-requests"] == "write"
    assert workflow["permissions"]["contents"] == "read"

    steps = workflow["jobs"]["scan"]["steps"]
    names = [s["name"] for s in steps]
    assert any("comment" in n.lower() for n in names)

    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    # A merge-base diff needs both branches, so a depth-1 checkout is not enough.
    assert checkout["with"]["fetch-depth"] == 0

    diff_step = next(s for s in steps if "run" in s and "git diff" in s["run"])
    # Deleted files must not reach the scanner: they no longer exist on disk.
    assert "--diff-filter=ACMR" in diff_step["run"]
    # Three-dot compares against the merge base, not the base branch tip.
    assert "..." in diff_step["run"]

    comment_step = next(
        s for s in steps if str(s.get("uses", "")).startswith("actions/github-script")
    )
    script = comment_step["with"]["script"]
    assert MARKER in script
    # Upsert, not append.
    assert "updateComment" in script and "createComment" in script
    # A fork PR's read-only token must not turn into a red check.
    assert comment_step["continue-on-error"] is True


@pytest.mark.skipif(not os.path.exists(WORKFLOW), reason="workflow not present")
def test_workflow_never_fails_the_pr_by_default():
    """The scan step must not be able to fail the build on its own -- this
    check is informational unless a team opts in to --fail-on-smell."""
    with open(WORKFLOW, encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    scan_step = next(
        s for s in workflow["jobs"]["scan"]["steps"]
        if "run" in s and "engine.ci.pr_comment" in s["run"]
    )

    assert scan_step["continue-on-error"] is True
    assert "--fail-on-smell" not in scan_step["run"]
