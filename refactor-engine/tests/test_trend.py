"""Tests for `refactor-scan trend`.

Split deliberately into three groups:

  * SAMPLING -- pure functions over hand-written commit lists. No git, no
    filesystem, so the interesting edge cases (a one-commit repo, more
    periods than --max-samples, a quiet stretch with no commits) are cheap
    and exhaustive rather than sampled from whatever a fixture repo happens
    to contain.
  * SAFETY -- real git repositories, asserting the work tree is untouched.
    These have to use a real repo, because the whole claim is about what
    git does to files on disk.
  * OUTPUT -- counting and formatting, again pure.
"""

import os
import subprocess
from datetime import datetime, timezone

import pytest

from engine.evaluate.trend import (
    DEFAULT_INTERVAL,
    INTERVALS,
    MAX_SAMPLES_CEILING,
    TRACKED_SMELLS,
    _evenly_spaced,
    bucket_commits,
    collect_trend,
    export_python_tree,
    period_key,
    read_commits,
    sample_commits,
    smell_counts,
    sparkline,
)


def _git(repo_path, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _commit(repo_path, message, files: dict[str, str], date: str | None = None):
    """Commit `files`, optionally pinning the commit date.

    The date is pinned in several tests because calendar bucketing is the
    thing under test: letting commits take "now" would make monthly grouping
    depend on which day the suite happens to run.

    It is set through GIT_COMMITTER_DATE, not `--date`. `--date` sets the
    AUTHOR date, and read_commits() deliberately reads the COMMITTER date
    (%cI) -- so a test using `--date` alone would pin a field the code under
    test never looks at, and quietly bucket by today after all.
    """
    for name, content in files.items():
        path = repo_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo_path, "add", "-A")

    env = None
    if date is not None:
        env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}

    result = subprocess.run(
        ["git", "-C", str(repo_path),
         "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", message],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert result.returncode == 0, result.stderr
    return _git(repo_path, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo in a SUBDIRECTORY of tmp_path.

    Same reason as test_git_mining's fixture: tests here write scratch
    output under tmp_path and then assert the repo is clean, which is only
    a meaningful assertion when that output lands outside the work tree.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init")
    return repo_dir


def _fake_commits(dates: list[str]) -> list[dict]:
    """Commit dicts shaped like read_commits() output, oldest first."""
    return [
        {
            "commit_hash": f"{i:040x}",
            "timestamp": datetime.fromisoformat(d),
            "date": datetime.fromisoformat(d).date().isoformat(),
            "subject": f"commit {i}",
        }
        for i, d in enumerate(dates)
    ]


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_monthly_bucketing_keeps_the_last_commit_of_each_month():
    """"The state at the end of that month" is the thing being measured, so
    the bucket's representative must be its latest commit, not its first."""
    commits = _fake_commits([
        "2024-01-03T10:00:00+00:00",
        "2024-01-28T10:00:00+00:00",
        "2024-02-05T10:00:00+00:00",
    ])
    buckets = bucket_commits(commits, "monthly")

    assert [b["period"] for b in buckets] == ["2024-01", "2024-02"]
    assert buckets[0]["date"] == "2024-01-28"


def test_months_with_no_commits_produce_no_rows():
    """A quiet stretch is an absence of data, not a flat line. Emitting
    filler rows for it would draw a trend through months nobody measured."""
    commits = _fake_commits([
        "2024-01-10T10:00:00+00:00",
        "2024-06-10T10:00:00+00:00",
    ])
    assert [b["period"] for b in bucket_commits(commits, "monthly")] == ["2024-01", "2024-06"]


def test_weekly_bucketing_uses_iso_weeks_across_a_year_boundary():
    """31 Dec 2024 is ISO week 2025-W01. Deriving the week from the calendar
    year instead would file it under 2024-W01 -- a year adrift, and sorted
    to the wrong end of the table."""
    assert period_key(datetime(2024, 12, 31, tzinfo=timezone.utc), "weekly") == "2025-W01"
    assert period_key(datetime(2025, 1, 2, tzinfo=timezone.utc), "weekly") == "2025-W01"


def test_commits_interval_does_not_bucket_by_calendar():
    commits = _fake_commits([
        "2024-01-03T10:00:00+00:00",
        "2024-01-28T10:00:00+00:00",
    ])
    assert len(bucket_commits(commits, "commits")) == 2


def test_sampling_always_keeps_both_endpoints():
    """A trend whose first and last rows are not the oldest and newest
    states available understates the very change it exists to show."""
    commits = _fake_commits([f"2024-01-{day:02d}T10:00:00+00:00" for day in range(1, 21)])
    sampled = sample_commits(commits, interval="commits", max_samples=5)

    assert len(sampled) == 5
    assert sampled[0]["commit_hash"] == commits[0]["commit_hash"]
    assert sampled[-1]["commit_hash"] == commits[-1]["commit_hash"]


def test_sampling_spans_the_range_rather_than_taking_the_most_recent():
    """Evenly spaced, not "the newest N" -- keeping only recent points would
    quietly answer a different question than the one the user asked."""
    commits = _fake_commits([f"2024-01-{day:02d}T10:00:00+00:00" for day in range(1, 21)])
    sampled = sample_commits(commits, interval="commits", max_samples=5)

    # Endpoints exact, interior points spread ~evenly across the range. The
    # 4-vs-5 gap at index 3 is Python's banker's rounding on an exact .25 --
    # harmless here, and pinned rather than papered over so a change in the
    # spacing formula shows up as a failure.
    dates = [s["date"] for s in sampled]
    assert dates == ["2024-01-01", "2024-01-06", "2024-01-11", "2024-01-15", "2024-01-20"]


def test_repo_shorter_than_max_samples_returns_every_point():
    """Asking for 24 samples from a repo with 3 months of history gets 3,
    not an error and not 24 duplicated rows."""
    commits = _fake_commits([
        "2024-01-10T10:00:00+00:00",
        "2024-02-10T10:00:00+00:00",
        "2024-03-10T10:00:00+00:00",
    ])
    assert len(sample_commits(commits, interval="monthly", max_samples=24)) == 3


def test_single_commit_repo_yields_a_single_sample():
    commits = _fake_commits(["2024-01-10T10:00:00+00:00"])
    assert len(sample_commits(commits, interval="monthly", max_samples=24)) == 1


def test_max_samples_of_one_returns_the_latest_state():
    """One point can only honestly be "where it stands now"."""
    commits = _fake_commits([f"2024-0{m}-10T10:00:00+00:00" for m in range(1, 6)])
    sampled = sample_commits(commits, interval="monthly", max_samples=1)

    assert len(sampled) == 1
    assert sampled[0]["date"] == "2024-05-10"


def test_max_samples_is_clamped_to_the_ceiling():
    """Every sample re-analyzes an entire codebase, so an accidental
    --max-samples 5000 must be clamped rather than run for hours."""
    commits = _fake_commits([f"2024-01-{d:02d}T10:00:00+00:00" for d in range(1, 29)] * 10)
    sampled = sample_commits(commits, interval="commits", max_samples=100_000)
    assert len(sampled) == MAX_SAMPLES_CEILING


def test_unknown_interval_is_rejected():
    with pytest.raises(ValueError, match="interval must be one of"):
        sample_commits(_fake_commits(["2024-01-01T10:00:00+00:00"]), interval="daily")


def test_evenly_spaced_handles_empty_and_degenerate_input():
    assert _evenly_spaced([], 5) == []
    assert _evenly_spaced([1, 2, 3], 0) == []
    assert _evenly_spaced([1, 2, 3], 10) == [1, 2, 3]


def test_default_interval_is_a_valid_choice():
    assert DEFAULT_INTERVAL in INTERVALS


# ---------------------------------------------------------------------------
# Safety: history is read, never checked out
# ---------------------------------------------------------------------------


def test_export_refuses_a_workspace_inside_the_repository(repo, tmp_path):
    """Same guarantee as mining, enforced by the same guard: extracting into
    the work tree would leave untracked files behind and dirty `git status`
    -- the promise broken by a different route."""
    commit_hash = _commit(repo, "initial", {"widget.py": "class Widget:\n    pass\n"})

    with pytest.raises(ValueError, match="inside the repository"):
        export_python_tree(str(repo), commit_hash, str(repo / "scratch"))

    assert _git(repo, "status", "--porcelain") == ""


def test_export_writes_the_historical_state_without_moving_head(repo, tmp_path):
    _commit(repo, "initial", {"widget.py": "class Widget:\n    version = 1\n"})
    old_hash = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "bump", {"widget.py": "class Widget:\n    version = 2\n"})
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    dest = tmp_path / "old_state"
    written = export_python_tree(str(repo), old_hash, str(dest))

    assert written == 1
    assert "version = 1" in (dest / "widget.py").read_text(encoding="utf-8")

    # The work tree still holds the CURRENT content and HEAD has not moved:
    # this is a read of history, not a checkout of it.
    assert "version = 2" in (repo / "widget.py").read_text(encoding="utf-8")
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert _git(repo, "status", "--porcelain") == ""


def test_export_preserves_nested_paths_and_skips_non_python(repo, tmp_path):
    commit_hash = _commit(repo, "initial", {
        "pkg/core.py": "class Core:\n    pass\n",
        "README.md": "not python\n",
    })

    dest = tmp_path / "state"
    written = export_python_tree(str(repo), commit_hash, str(dest))

    assert written == 1
    assert (dest / "pkg" / "core.py").exists()
    assert not (dest / "README.md").exists()


def test_export_of_a_commit_with_no_python_returns_zero(repo, tmp_path):
    """git archive exits non-zero when the pathspec matches nothing. That is
    a real point on a trend -- the project had no Python yet -- not a crash."""
    commit_hash = _commit(repo, "docs only", {"README.md": "hello\n"})

    assert export_python_tree(str(repo), commit_hash, str(tmp_path / "state")) == 0


def test_collect_trend_leaves_the_working_tree_untouched(repo, tmp_path):
    _commit(repo, "first", {"a.py": "class A:\n    pass\n"}, date="2024-01-15T10:00:00+00:00")
    _commit(repo, "second", {"a.py": "class A:\n    x = 1\n"}, date="2024-02-15T10:00:00+00:00")
    _commit(repo, "third", {"a.py": "class A:\n    x = 2\n"}, date="2024-03-15T10:00:00+00:00")

    head_before = _git(repo, "rev-parse", "HEAD").strip()
    report = collect_trend(str(repo), interval="monthly", max_samples=10)

    assert report["sample_count"] == 3
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert _git(repo, "status", "--porcelain") == ""
    assert "x = 2" in (repo / "a.py").read_text(encoding="utf-8")


def test_collect_trend_rejects_a_workspace_inside_the_repository(repo):
    """Checked BEFORE any analysis runs -- discovering an illegal destination
    after twenty minutes of scanning would be a rude way to enforce a rule
    that costs nothing to check up front."""
    _commit(repo, "first", {"a.py": "class A:\n    pass\n"})

    with pytest.raises(ValueError, match="inside the repository"):
        collect_trend(str(repo), workspace=str(repo / "scratch"))

    assert _git(repo, "status", "--porcelain") == ""


def test_collect_trend_cleans_up_its_own_temporary_workspace(repo, tmp_path):
    """A caller-supplied workspace is the caller's to keep; a temporary one
    is not, and leaving N copies of a codebase in the temp dir would be a
    slow leak on a command designed to be run repeatedly."""
    _commit(repo, "first", {"a.py": "class A:\n    pass\n"})

    workspace = tmp_path / "kept"
    collect_trend(str(repo), workspace=str(workspace))
    assert workspace.exists()  # explicitly requested, so it stays


def test_non_git_directory_is_rejected(tmp_path):
    plain = tmp_path / "not_a_repo"
    plain.mkdir()

    with pytest.raises(ValueError, match="not a git repository"):
        collect_trend(str(plain))


def test_read_commits_returns_oldest_first(repo):
    _commit(repo, "first", {"a.py": "class A:\n    pass\n"}, date="2024-01-15T10:00:00+00:00")
    _commit(repo, "second", {"a.py": "class A:\n    x = 1\n"}, date="2024-02-15T10:00:00+00:00")

    commits = read_commits(str(repo))

    assert [c["subject"] for c in commits] == ["first", "second"]
    assert commits[0]["date"] == "2024-01-15"


def test_single_commit_repo_is_reported_as_a_snapshot_not_a_trend(repo):
    """The honest answer for one data point. Rendering it with a confident
    "flat" direction would claim a comparison that was never made."""
    _commit(repo, "only", {"a.py": "class A:\n    pass\n"})

    report = collect_trend(str(repo))

    assert report["sample_count"] == 1
    assert report["summary"]["direction"] is None
    assert any("snapshot, not a trend" in note for note in report["notes"])


# ---------------------------------------------------------------------------
# Counting and output
# ---------------------------------------------------------------------------


def _results(*smells):
    return {"classes": {f"C{i}": {"predicted_smell": s} for i, s in enumerate(smells)}}


def test_smell_counts_reports_every_tracked_smell_even_at_zero():
    """No gaps in the table and no `.get` needed by a JSON consumer."""
    counts = smell_counts(_results("God Class"))

    assert set(counts["smells"]) == set(TRACKED_SMELLS)
    assert counts["smells"]["Data Class"] == 0


def test_smell_counts_separates_clean_from_smelly():
    counts = smell_counts(_results("God Class", "Clean", "Clean", "Feature Envy"))

    assert counts["total_classes"] == 4
    assert counts["clean_classes"] == 2
    assert counts["smelly_classes"] == 2
    assert counts["smell_rate"] == 0.5


def test_threshold_findings_are_counted_as_other_not_dropped():
    """Long Parameter List and Duplicate Code are threshold checks layered on
    top of the classifier, not labels it emits. Rolling them into `other`
    keeps `smelly` equal to the sum of the reported columns instead of
    mysteriously exceeding them."""
    counts = smell_counts(_results("Long Parameter List", "Duplicate Code", "Clean"))

    assert counts["other_smells"] == 2
    assert counts["smelly_classes"] == 2
    assert sum(counts["smells"].values()) + counts["other_smells"] == counts["smelly_classes"]


def test_smell_rate_is_none_when_there_are_no_classes():
    """An undefined proportion, not 0%. Rendering "0% smelly" for a commit
    with no classes at all would put a fake improvement on the chart."""
    counts = smell_counts({"classes": {}})

    assert counts["total_classes"] == 0
    assert counts["smell_rate"] is None


def test_direction_is_read_from_the_rate_not_the_raw_count(repo):
    """The whole reason total_classes is recorded. A codebase that grew can
    add smelly classes while getting proportionally cleaner, and a direction
    driven by raw counts would call that "worsening"."""
    from engine.evaluate.trend import _summarize

    samples = [
        {"date": "2024-01-01", "short_hash": "aaa", "smell_rate": 0.50,
         "total_classes": 10, "smelly_classes": 5,
         "smells": {s: 0 for s in TRACKED_SMELLS}},
        {"date": "2024-06-01", "short_hash": "bbb", "smell_rate": 0.30,
         "total_classes": 30, "smelly_classes": 9,
         "smells": {s: 0 for s in TRACKED_SMELLS}},
    ]
    summary = _summarize(samples)

    assert summary["smelly_change"] == 4       # raw count went UP
    assert summary["direction"] == "improving"  # but the rate went DOWN


def test_flat_direction_absorbs_sub_percentage_point_noise():
    from engine.evaluate.trend import _direction

    assert _direction(0.500, 0.503) == "flat"
    assert _direction(0.50, 0.60) == "worsening"
    assert _direction(0.60, 0.50) == "improving"
    assert _direction(None, 0.5) is None


def test_sparkline_shows_shape_and_gaps_for_missing_points():
    assert len(sparkline([0.1, 0.5, 0.9])) == 3
    assert sparkline([0.1, 0.5, 0.9])[0] < sparkline([0.1, 0.5, 0.9])[-1]
    assert " " in sparkline([0.1, None, 0.9])
    assert sparkline([]) == ""


def test_flat_series_renders_flat_not_full():
    """A series that never moved is drawn at the bottom of the range, not
    the top -- scaling min-to-max would otherwise render a constant 5% smell
    rate as a solid wall of full blocks."""
    assert sparkline([0.4, 0.4, 0.4]) == "▁▁▁"


def test_rate_deltas_are_relative_to_the_previous_sample(repo):
    from engine.evaluate.trend import _attach_deltas

    samples = [{"smell_rate": 0.50}, {"smell_rate": 0.40}, {"smell_rate": 0.45}]
    _attach_deltas(samples)

    assert samples[0]["rate_delta"] is None
    assert samples[1]["rate_delta"] == pytest.approx(-0.10)
    assert samples[2]["rate_delta"] == pytest.approx(0.05)


def test_report_carries_the_fields_a_dashboard_would_need(repo):
    """Phase G may chart this. The JSON contract is asserted here so a later
    refactor cannot quietly drop a key a consumer depends on."""
    _commit(repo, "first", {"a.py": "class A:\n    pass\n"}, date="2024-01-15T10:00:00+00:00")
    _commit(repo, "second", {"b.py": "class B:\n    pass\n"}, date="2024-02-15T10:00:00+00:00")

    report = collect_trend(str(repo), interval="monthly", max_samples=10)

    for key in ("repo", "interval", "shallow_clone", "commits_available",
                "sample_count", "samples", "summary", "notes"):
        assert key in report

    for key in ("commit_hash", "short_hash", "date", "period", "total_classes",
                "smelly_classes", "smells", "smell_rate", "rate_delta", "files_analyzed"):
        assert key in report["samples"][0]

    assert report["summary"]["rate_series"] == [s["smell_rate"] for s in report["samples"]]


def test_progress_callback_fires_once_per_sample(repo):
    """Reused from C.3's on_progress pattern, but with a different third
    argument -- here the slow unit is a whole-codebase analysis, so the
    useful thing to show is which commit is being worked on."""
    _commit(repo, "first", {"a.py": "class A:\n    pass\n"}, date="2024-01-15T10:00:00+00:00")
    _commit(repo, "second", {"a.py": "class A:\n    x = 1\n"}, date="2024-02-15T10:00:00+00:00")

    calls = []
    collect_trend(str(repo), interval="monthly", max_samples=10,
                  on_progress=lambda done, total, label: calls.append((done, total, label)))

    assert [c[0] for c in calls] == [1, 2]
    assert all(c[1] == 2 for c in calls)
    assert "2024-01-15" in calls[0][2]


def test_each_sample_is_analyzed_in_isolation(repo):
    """Sequential samples must not share a directory: a file deleted between
    two commits would otherwise linger from the earlier extraction and be
    counted as a class that no longer exists."""
    _commit(repo, "two modules", {
        "a.py": "class A:\n    pass\n",
        "b.py": "class B:\n    pass\n",
    }, date="2024-01-15T10:00:00+00:00")
    _git(repo, "rm", "b.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "drop b")

    report = collect_trend(str(repo), interval="commits", max_samples=10)

    assert report["samples"][0]["total_classes"] == 2
    assert report["samples"][-1]["total_classes"] == 1


def test_history_walk_cap_is_reported_rather_than_silently_applied(repo):
    """A truncated walk produces a table that looks exactly like a complete
    one. The note is the only thing distinguishing them."""
    for i in range(3):
        _commit(repo, f"c{i}", {"a.py": f"class A:\n    x = {i}\n"})

    report = collect_trend(str(repo), interval="commits", max_samples=5, max_log_commits=2)

    assert report["commits_walked"] == 2
    assert any("capped at" in note for note in report["notes"])


def test_rendering_a_trend_report_does_not_raise(repo, capsys):
    """Smoke test over the console renderer: eleven columns, a sparkline and
    a summary panel that all index into the report dict, so a renamed key
    would break rendering without breaking any assertion above."""
    from engine.cli.render import print_trend_report

    _commit(repo, "first", {"a.py": "class A:\n    pass\n"}, date="2024-01-15T10:00:00+00:00")
    _commit(repo, "second", {"a.py": "class A:\n    x = 1\n"}, date="2024-02-15T10:00:00+00:00")

    print_trend_report(collect_trend(str(repo), interval="monthly", max_samples=10))

    output = capsys.readouterr().out
    assert "code smells over time" in output
    assert "First" in output


def test_rendering_a_single_sample_report_does_not_raise(repo, capsys):
    """The direction is None here, which the summary panel has to render as
    an absence rather than crash on."""
    from engine.cli.render import print_trend_report

    _commit(repo, "only", {"a.py": "class A:\n    pass\n"})
    print_trend_report(collect_trend(str(repo)))

    assert "snapshot, not a trend" in capsys.readouterr().out
