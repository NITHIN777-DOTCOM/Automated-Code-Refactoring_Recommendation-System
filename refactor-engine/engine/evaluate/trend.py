"""Track how a repository's code smells change across its own history.

`analyze` answers "what is wrong with this code right now". This module
answers the follow-up question a team actually cares about: "is it getting
better or worse?" It samples the repository's commit history at regular
intervals, reconstructs the whole codebase as it stood at each sampled
commit, runs the ordinary analysis pipeline over it, and records the smell
counts alongside the total number of classes.

WHY TOTAL CLASSES IS RECORDED, NOT JUST SMELL COUNTS
----------------------------------------------------
A raw count going up is ambiguous: a codebase that doubled in size and kept
the same proportion of smelly classes has not got worse, and one that shed
half its modules has not got better. Every sample therefore carries
`total_classes` and a derived `smell_rate`, and the trend DIRECTION is
computed from the rate, never from the raw count. The raw counts are still
reported because they are what a reviewer recognises, but they are shown
next to the denominator that makes them readable.

WHY `git archive` AND NOT `git checkout`
-----------------------------------------
This module inherits git_mining.py's contract: the user's working tree is
left exactly as it was found. Reconstructing a historical state with
`git checkout` (or `git stash`, or a detached-HEAD dance) would move real
files on disk and could destroy uncommitted work if it were interrupted.
`git archive` is a read-only query -- it streams a tarball of a tree-ish to
stdout and touches nothing in the repository -- so the historical state is
materialised in a scratch directory OUTSIDE the repo and analysed there.
The same _reject_output_dir_inside_repo() guard that protects mining output
is applied to that scratch directory, so even a caller who explicitly passes
a workspace path cannot aim it into the work tree.

WHY THE SAMPLING FUNCTIONS ARE PURE
-----------------------------------
`bucket_commits` and `sample_commits` operate on a plain list of commit
dicts, not on a repository. The interesting logic -- one sample per calendar
month, evenly spaced subsampling, always keeping the endpoints -- is then
testable against hand-written commit lists without cloning anything, which
is what makes the edge cases (a repo with one commit, a repo with fewer
commits than --max-samples) cheap to cover.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime

from engine.evaluate.git_mining import (
    _reject_output_dir_inside_repo,
    _validate_git_repo,
    commit_count,
    is_shallow_clone,
)
from engine.pipeline import analyze_repo

logger = logging.getLogger(__name__)

INTERVALS = ("commits", "weekly", "monthly")

# Monthly is the default because it is the only interval that gives a useful
# number of points across the range of repositories people actually have.
# Weekly buckets a 5-year project into ~260 points (all but 24 of which get
# thrown away by --max-samples anyway), and `commits` depends entirely on
# project velocity -- every 50th commit is a fortnight on one project and
# two years on another. Calendar months are comparable across repos.
DEFAULT_INTERVAL = "monthly"

# Each sample re-analyses the ENTIRE codebase at that commit, so this is a
# per-sample cost measured in seconds, not milliseconds. 24 points is a
# readable table and, at monthly resolution, two years of history.
DEFAULT_MAX_SAMPLES = 24

# Hard ceiling independent of what a caller passes, in the same spirit as
# git_mining's _MAX_COMMITS_CEILING: --max-samples 5000 on a large repo would
# run for hours, and silently accepting it is worse than clamping it.
MAX_SAMPLES_CEILING = 100

# How far back the history walk itself goes. This is not a cap on samples --
# it is a cap on the `git log` used to CHOOSE them. A repository with more
# commits than this is sampled across its most recent slice, and the report
# says so rather than implying the trend covers the whole project.
MAX_LOG_COMMITS = 20000

# The four smells the trained classifier emits. Fixed order so the table
# columns and the JSON keys are stable between runs and between repos --
# a caller diffing two trend reports must not see columns reshuffle.
TRACKED_SMELLS = ("God Class", "Data Class", "Feature Envy", "Long Method")

CLEAN_LABEL = "Clean"

# Below this change in smell RATE (one percentage point) the direction is
# reported as flat. Without a dead band, noise in the last decimal place
# would render as a confident "improving" on a codebase that did not move.
FLAT_RATE_EPSILON = 0.01

# Low-to-high blocks for the rate sparkline.
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


# ---------------------------------------------------------------------------
# Reading history
# ---------------------------------------------------------------------------

# Unit separator: safe inside a commit subject in a way that a comma, a pipe
# or a tab is not.
_FIELD_SEP = "\x1f"


def read_commits(repo_path: str, max_log_commits: int = MAX_LOG_COMMITS) -> list[dict]:
    """Every commit reachable from HEAD, OLDEST FIRST, capped at
    `max_log_commits` (which keeps the most RECENT ones -- git log walks
    backwards from HEAD, and a truncated history should end at the present).

    Each entry is {"commit_hash", "timestamp", "date", "subject"}.
    `timestamp` is a timezone-aware datetime used for bucketing; `date` is
    the YYYY-MM-DD string shown in reports.

    Committer date (%cI), not author date: a rebased or cherry-picked commit
    can carry an author date from years earlier, which would drop it into the
    wrong calendar bucket and put the history table out of order.
    """
    _validate_git_repo(repo_path)

    result = subprocess.run(
        [
            "git", "-C", repo_path, "log", f"-n{max(1, max_log_commits)}",
            f"--format=%H{_FIELD_SEP}%cI{_FIELD_SEP}%s",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        # The common cause is a repository with no commits at all, where git
        # log exits non-zero rather than printing nothing.
        raise ValueError(
            f"Could not read commit history from {repo_path!r}: "
            f"{result.stderr.strip() or 'the repository has no commits yet'}"
        )

    commits = []
    for line in result.stdout.splitlines():
        parts = line.split(_FIELD_SEP, 2)
        if len(parts) < 2:
            continue
        commit_hash, iso_date = parts[0], parts[1]
        subject = parts[2] if len(parts) > 2 else ""
        try:
            timestamp = datetime.fromisoformat(iso_date)
        except ValueError:
            logger.warning("Unparsable commit date %r for %s; skipping", iso_date, commit_hash)
            continue
        commits.append(
            {
                "commit_hash": commit_hash,
                "timestamp": timestamp,
                "date": timestamp.date().isoformat(),
                "subject": subject,
            }
        )

    # git log is newest-first; a trend reads left to right through time.
    commits.reverse()
    return commits


# ---------------------------------------------------------------------------
# Choosing which commits to sample (pure -- no git, no filesystem)
# ---------------------------------------------------------------------------


def period_key(timestamp: datetime, interval: str) -> str:
    """The calendar bucket `timestamp` falls in, as a sortable label.

    ISO week numbering for "weekly" rather than "day-of-year // 7", because
    the ISO year of a date in late December can be the NEXT year -- getting
    that wrong puts two adjacent commits in buckets a year apart.
    """
    if interval == "monthly":
        return f"{timestamp.year:04d}-{timestamp.month:02d}"
    if interval == "weekly":
        iso_year, iso_week, _ = timestamp.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    raise ValueError(f"period_key() is only meaningful for weekly/monthly, not {interval!r}")


def bucket_commits(commits: list[dict], interval: str) -> list[dict]:
    """One representative commit per calendar period, oldest first.

    The representative is the LAST commit in each bucket: "the state of the
    codebase at the end of that month" is a well-defined thing to measure,
    whereas the first commit of a month reflects work done in the previous
    one. Each returned commit gains a "period" key naming its bucket.

    Periods with no commits are simply absent -- a project that went quiet
    for six months produces no rows for those months rather than six
    duplicate rows repeating the last known state.
    """
    if interval == "commits":
        return list(commits)

    representatives: dict[str, dict] = {}
    for commit in commits:
        key = period_key(commit["timestamp"], interval)
        # Later commits overwrite earlier ones in the same bucket, and
        # `commits` is oldest-first, so what survives is the bucket's last.
        representatives[key] = {**commit, "period": key}

    return [representatives[key] for key in sorted(representatives)]


def _evenly_spaced(items: list, count: int) -> list:
    """`count` items spanning `items` end to end, including both endpoints.

    Endpoint inclusion is the point: a trend whose first and last rows are
    not the oldest and newest available states understates the very change
    the command exists to show.
    """
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        # One sample can only honestly be "the latest state".
        return [items[-1]]

    last = len(items) - 1
    indexes = sorted({round(i * last / (count - 1)) for i in range(count)})
    return [items[i] for i in indexes]


def sample_commits(
    commits: list[dict], interval: str = DEFAULT_INTERVAL, max_samples: int = DEFAULT_MAX_SAMPLES
) -> list[dict]:
    """The commits to analyse, oldest first, at most `max_samples` of them.

    Two steps, deliberately separate: bucket by calendar period (a no-op for
    interval="commits"), then thin the result down to `max_samples` by even
    spacing across the WHOLE range rather than by taking the most recent N.
    Spanning the range is what makes this a trend -- keeping only the newest
    24 monthly points on a ten-year project would silently answer a different
    question than the one asked.
    """
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}, not {interval!r}")

    max_samples = max(1, min(max_samples, MAX_SAMPLES_CEILING))
    return _evenly_spaced(bucket_commits(commits, interval), max_samples)


# ---------------------------------------------------------------------------
# Materialising a historical state (read-only)
# ---------------------------------------------------------------------------


def export_python_tree(repo_path: str, commit_hash: str, dest_dir: str) -> int:
    """Write every .py file as it existed at `commit_hash` into `dest_dir`,
    returning how many files were written.

    Read-only with respect to the repository: `git archive` streams a tarball
    of the tree to stdout and never writes into the work tree, moves HEAD, or
    touches the index. `dest_dir` is checked against the repo the same way
    mining output is, so it cannot be aimed inside the work tree either.

    A commit containing no Python at all makes `git archive` exit non-zero
    ("pathspec did not match any files"). That is a legitimate point on a
    trend -- the codebase had no Python yet -- so it returns 0 rather than
    raising.
    """
    _validate_git_repo(repo_path)
    _reject_output_dir_inside_repo(repo_path, dest_dir)

    os.makedirs(dest_dir, exist_ok=True)

    # Streamed to a file rather than captured in memory: the archive of a
    # large project's sources is tens of megabytes, and it is written once
    # and read once.
    handle, tar_path = tempfile.mkstemp(suffix=".tar")
    try:
        with os.fdopen(handle, "wb") as tar_file:
            result = subprocess.run(
                ["git", "-C", repo_path, "archive", "--format=tar", commit_hash, "*.py"],
                stdout=tar_file, stderr=subprocess.PIPE, text=False,
            )
        if result.returncode != 0:
            logger.info(
                "No Python files at %s in %s (%s)",
                commit_hash[:12], repo_path,
                (result.stderr or b"").decode("utf-8", "replace").strip(),
            )
            return 0

        with tarfile.open(tar_path) as archive:
            members = [m for m in archive.getmembers() if m.isfile()]
            try:
                # 'data' rejects absolute paths, parent traversal, links and
                # device files. git archive would not produce those, but an
                # extraction that only behaves because of what the producer
                # promises is one bad tarball away from writing outside
                # dest_dir.
                archive.extractall(dest_dir, members=members, filter="data")
            except TypeError:
                # Python built before the extraction-filter backport.
                archive.extractall(dest_dir, members=members)  # noqa: S202

        return len(members)
    finally:
        try:
            os.remove(tar_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def smell_counts(results: dict) -> dict:
    """Reduce one `analyze_repo()` result to the numbers a trend row needs.

    Every one of TRACKED_SMELLS is present with an explicit 0 even when it
    did not occur, so the table has no gaps and a JSON consumer can index
    the key without a `.get`. Findings outside that set -- Long Parameter
    List and Duplicate Code, which are threshold checks layered on top of
    the classifier rather than labels it emits -- are summed into `other`
    so that `smelly` stays equal to the sum of the reported columns instead
    of mysteriously exceeding them.
    """
    classes = results.get("classes") or {}

    by_smell = {smell: 0 for smell in TRACKED_SMELLS}
    other = 0
    clean = 0

    for entry in classes.values():
        smell = entry.get("predicted_smell")
        if smell == CLEAN_LABEL or not smell:
            clean += 1
        elif smell in by_smell:
            by_smell[smell] += 1
        else:
            other += 1

    total = len(classes)
    smelly = total - clean

    return {
        "total_classes": total,
        "clean_classes": clean,
        "smelly_classes": smelly,
        "smells": by_smell,
        "other_smells": other,
        # None, not 0.0, when there is nothing to take a proportion of: a
        # commit with no classes has an UNDEFINED smell rate, and rendering
        # that as a clean 0% would put a fake improvement on the chart.
        "smell_rate": (smelly / total) if total else None,
    }


def _direction(first_rate: float | None, last_rate: float | None) -> str | None:
    if first_rate is None or last_rate is None:
        return None
    change = last_rate - first_rate
    if abs(change) < FLAT_RATE_EPSILON:
        return "flat"
    return "worsening" if change > 0 else "improving"


def sparkline(values: list) -> str:
    """A one-line shape for a series, with None rendered as a gap.

    Scaled between the series' own min and max, so it shows the shape of the
    change and not its absolute size -- it sits next to the exact numbers in
    the table, which is where magnitude is read from.
    """
    present = [v for v in values if v is not None]
    if not present:
        return ""
    low, high = min(present), max(present)
    span = high - low

    out = []
    for value in values:
        if value is None:
            out.append(" ")
        elif span == 0:
            # A flat series is flat, not "all at the top of the range".
            out.append(_SPARK_BLOCKS[0])
        else:
            index = int((value - low) / span * (len(_SPARK_BLOCKS) - 1))
            out.append(_SPARK_BLOCKS[index])
    return "".join(out)


# ---------------------------------------------------------------------------
# The trend itself
# ---------------------------------------------------------------------------


def _notes(
    commits: list[dict], samples: list[dict], repo_path: str, max_samples: int, interval: str,
    max_log_commits: int = MAX_LOG_COMMITS,
) -> list[str]:
    """Say plainly when a result covers less history than the reader expects.

    Each of these cases otherwise produces a short, unremarkable-looking
    table that quietly means something different from what it appears to.
    """
    notes = []

    if len(samples) <= 1:
        notes.append(
            f"Only {len(samples)} sample point available — this is a snapshot, not a trend. "
            f"A direction needs at least two points to compare."
        )

    if is_shallow_clone(repo_path):
        notes.append(
            "This is a shallow clone, so the history shown starts where the clone was "
            "truncated, not at the project's first commit. Deepen it "
            "(git fetch --deepen=N) for a longer trend."
        )

    # Against the EFFECTIVE limit, not the module constant: collect_trend()
    # takes max_log_commits as a parameter, and comparing to the default
    # would stay silent about a truncation the caller actually asked for.
    if len(commits) >= max_log_commits:
        notes.append(
            f"History walk capped at the {max_log_commits} most recent commits; the trend "
            f"covers that slice rather than the project's full history."
        )

    if interval != "commits" and len(samples) < max_samples and len(samples) > 1:
        periods = "weeks" if interval == "weekly" else "months"
        notes.append(
            f"The repository spans {len(samples)} active {periods}, fewer than the "
            f"{max_samples} requested — every available period was sampled."
        )

    return notes


def collect_trend(
    repo_path: str,
    interval: str = DEFAULT_INTERVAL,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    exclude: list[str] | None = None,
    workspace: str | None = None,
    on_progress=None,
    max_log_commits: int = MAX_LOG_COMMITS,
) -> dict:
    """Analyse the codebase at each sampled commit and return the full trend.

    `workspace`, if given, is where historical states are materialised; it
    must be outside the repository (enforced) and its contents are the
    caller's to keep. Left unset, a temporary directory is used and removed
    afterwards. Either way, each sample is extracted into its own empty
    subdirectory -- reusing one directory would leave files from an earlier
    commit behind and count classes that had already been deleted.

    `on_progress`, if given, is called as `on_progress(done, total, label)`
    after each sample completes. Note this is a DIFFERENT signature from
    git_mining.find_refactoring_candidates()'s third argument (a running
    candidate count): here the slow unit is a whole-codebase analysis, so
    the useful thing to show is which commit is being worked on.
    """
    _validate_git_repo(repo_path)
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}, not {interval!r}")

    requested_samples = max(1, min(max_samples, MAX_SAMPLES_CEILING))

    commits = read_commits(repo_path, max_log_commits=max_log_commits)
    if not commits:
        raise ValueError(f"{repo_path!r} has no commits to analyse")

    selected = sample_commits(commits, interval=interval, max_samples=requested_samples)

    # Validate the workspace BEFORE any analysis runs. Discovering that the
    # destination is illegal after twenty minutes of scanning would be a
    # rude way to enforce a rule that costs nothing to check up front.
    owns_workspace = workspace is None
    if workspace is not None:
        _reject_output_dir_inside_repo(repo_path, workspace)
        os.makedirs(workspace, exist_ok=True)
    else:
        workspace = tempfile.mkdtemp(prefix="refactor-scan-trend-")

    samples = []
    try:
        for index, commit in enumerate(selected, start=1):
            short = commit["commit_hash"][:12]
            sample_dir = os.path.join(workspace, f"{index:03d}-{short}")

            files = export_python_tree(repo_path, commit["commit_hash"], sample_dir)
            if files:
                results = analyze_repo(sample_dir, exclude=exclude)
            else:
                results = {"classes": {}, "unused_imports": {}}

            samples.append(
                {
                    "commit_hash": commit["commit_hash"],
                    "short_hash": short,
                    "date": commit["date"],
                    "period": commit.get("period", commit["date"]),
                    "subject": commit["subject"],
                    "files_analyzed": files,
                    **smell_counts(results),
                }
            )

            if on_progress is not None:
                on_progress(index, len(selected), f"{commit['date']} ({short})")

            # Freed as we go: keeping every historical checkout alive would
            # hold N copies of the codebase on disk at once for no reason.
            if owns_workspace:
                shutil.rmtree(sample_dir, ignore_errors=True)
    finally:
        if owns_workspace:
            shutil.rmtree(workspace, ignore_errors=True)

    _attach_deltas(samples)

    return {
        "repo": os.path.basename(os.path.abspath(str(repo_path).rstrip("/\\"))),
        "repo_path": os.path.abspath(str(repo_path)),
        "interval": interval,
        "shallow_clone": is_shallow_clone(repo_path),
        "commits_available": commit_count(repo_path),
        "commits_walked": len(commits),
        "samples_requested": requested_samples,
        "sample_count": len(samples),
        "samples": samples,
        "summary": _summarize(samples),
        "notes": _notes(
            commits, samples, repo_path, requested_samples, interval, max_log_commits
        ),
    }


def _attach_deltas(samples: list[dict]) -> None:
    """Give each sample its change in smell rate from the previous one.

    The rate, not the raw count: consecutive samples on a growing codebase
    routinely add smelly classes while getting proportionally cleaner, and
    an arrow driven by the raw count would point the wrong way on exactly
    the repositories where the answer matters.
    """
    previous_rate = None
    for sample in samples:
        rate = sample["smell_rate"]
        if rate is None or previous_rate is None:
            sample["rate_delta"] = None
        else:
            sample["rate_delta"] = rate - previous_rate
        if rate is not None:
            previous_rate = rate


def _summarize(samples: list[dict]) -> dict:
    """First-versus-last comparison, plus the series for the sparkline."""
    if not samples:
        return {
            "first": None, "last": None, "direction": None,
            "rate_change": None, "class_change": None,
            "smelly_change": None, "by_smell_change": {},
            "rate_series": [], "sparkline": "",
        }

    first, last = samples[0], samples[-1]
    rate_series = [s["smell_rate"] for s in samples]

    rate_change = (
        None if first["smell_rate"] is None or last["smell_rate"] is None
        else last["smell_rate"] - first["smell_rate"]
    )

    return {
        "first": {"date": first["date"], "short_hash": first["short_hash"]},
        "last": {"date": last["date"], "short_hash": last["short_hash"]},
        # Only meaningful with two or more points; a single sample has
        # nothing to have moved away from.
        "direction": _direction(first["smell_rate"], last["smell_rate"]) if len(samples) > 1 else None,
        "rate_change": rate_change,
        "class_change": last["total_classes"] - first["total_classes"],
        "smelly_change": last["smelly_classes"] - first["smelly_classes"],
        "by_smell_change": {
            smell: last["smells"][smell] - first["smells"][smell] for smell in TRACKED_SMELLS
        },
        "rate_series": rate_series,
        "sparkline": sparkline(rate_series),
    }
