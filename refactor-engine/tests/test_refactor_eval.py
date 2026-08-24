"""Tests for `refactor-scan evaluate` -- the check against real refactoring history.

The core logic is a hit test: given a mined commit, did our detector flag the
class the real commit went on to restructure? Both branches matter and both
are exercised here with synthetic before/after pairs built through the real
mining code, so the fixtures go through exactly the path the CLI does.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from engine.evaluate.git_mining import extract_refactoring_dataset
from engine.evaluate.refactor_eval import (
    CAVEAT,
    available_repos,
    evaluate,
    refactor_targets,
    summarize,
)


def _git(repo_path, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _commit(repo_path, message, files: dict[str, str]):
    for name, content in files.items():
        (repo_path / name).write_text(content, encoding="utf-8")
    _git(repo_path, "add", "-A")
    _git(
        repo_path,
        "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-m", message,
    )
    return _git(repo_path, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init")
    return repo_dir


# A class big and incohesive enough that our detector flags it: many methods,
# each touching only its own field, and long enough to clear LOC > 130.
def _god_class(name: str, extra_method: str = "") -> str:
    fields = "".join(f"        self.f{i} = {i}\n" for i in range(14))
    methods = "".join(
        f"    def m{i}(self, v):\n"
        + "".join(f"        x{j} = v + {j}\n" for j in range(8))
        + f"        self.f{i} = v\n        return self.f{i}\n"
        for i in range(14)
    )
    return f"class {name}:\n    def __init__(self):\n{fields}{methods}{extra_method}"


# ---------------------------------------------------------------------------
# Target extraction
# ---------------------------------------------------------------------------


def test_targets_name_the_before_state_class_for_each_signal():
    candidate = {
        "details": {
            "methods_moved": [
                {"file": "a.py", "method": "render", "from_class": ["Report"], "to_class": ["Printer"]}
            ],
            "classes_renamed": [{"file": "b.py", "from": "Old", "to": "New", "shared_methods": 4}],
            "classes_gaining_methods": [
                {"file": "c.py", "class": "Grower", "methods_gained": 2,
                 "lines_before": 100, "lines_after": 110}
            ],
        }
    }

    targets = {(t["file"], t["class"], t["signal"]) for t in refactor_targets(candidate)}

    # The MOVE names the source class (the one Move Method fixes), the RENAME
    # names the old name, and extraction keeps its name -- all of which are
    # the names that exist in the before state we analyze.
    assert ("a.py", "Report", "method_moved_between_classes") in targets
    assert ("b.py", "Old", "class_renamed") in targets
    assert ("c.py", "Grower", "method_extracted") in targets
    assert ("a.py", "Printer", "method_moved_between_classes") not in targets


def test_keyword_only_commit_yields_no_target():
    """A commit whose only evidence is its message says nothing verifiable
    about WHICH class changed, so it must not be given a guessed target."""
    assert refactor_targets({"details": {"methods_moved": [], "classes_renamed": [],
                                         "classes_gaining_methods": []}}) == []
    assert refactor_targets({}) == []


# ---------------------------------------------------------------------------
# The hit test, both branches, end to end through mining
# ---------------------------------------------------------------------------


def test_hit_when_the_refactored_class_is_one_we_flag(repo, tmp_path):
    """A genuinely smelly class that then gets a method extracted out of it:
    our detector should independently have flagged it in the before state."""
    _commit(repo, "initial import", {"big.py": _god_class("Monolith")})
    _commit(
        repo,
        "pull a helper out of Monolith",
        {"big.py": _god_class("Monolith", extra_method="    def helper(self):\n        return 1\n")},
    )

    out = tmp_path / "history"
    extract_refactoring_dataset(str(repo), str(out), repo_name="sample", scan_limit=10)
    results = evaluate("sample", str(out))

    commit = results["repos"][0]["commits"][0]
    assert commit["has_structural_target"] is True
    assert commit["targets"][0]["class"] == "Monolith"
    assert commit["hit"] is True
    assert commit["targets"][0]["flagged_as"] not in (None, "Clean")
    assert results["overall"]["targeted_hit_rate"] == 1.0


def test_miss_when_the_refactored_class_is_perfectly_clean(repo, tmp_path):
    """A small cohesive class also gets refactored in real life. We should
    NOT have flagged it, and the evaluation must record that as a miss rather
    than quietly excluding it -- that honesty is what makes the hit rate a
    lower bound rather than a tuned number."""
    # Cohesive: every method works on self.total, so LCOM is 0 and none of
    # the smell rules fire. Sized so that adding a method stays inside the
    # 1.25x growth gate, which is what makes the commit register as a
    # structural refactoring at all.
    clean_before = (
        "class Ledger:\n"
        "    def __init__(self):\n"
        "        self.total = 0\n"
        "    def add(self, amount):\n"
        "        self.total = self.total + amount\n"
        "        return self.total\n"
        "    def subtract(self, amount):\n"
        "        self.total = self.total - amount\n"
        "        return self.total\n"
        "    def scale(self, factor):\n"
        "        self.total = self.total * factor\n"
        "        return self.total\n"
        "    def is_positive(self):\n"
        "        return self.total > 0\n"
        "    def describe(self):\n"
        "        return 'total=' + str(self.total)\n"
    )
    clean_after = clean_before + (
        "    def halve(self):\n"
        "        self.total = self.total / 2\n"
        "        return self.total\n"
    )
    _commit(repo, "initial import", {"ledger.py": clean_before})
    _commit(repo, "refactor: pull out a halve helper", {"ledger.py": clean_after})

    out = tmp_path / "history"
    extract_refactoring_dataset(str(repo), str(out), repo_name="sample", scan_limit=10)
    results = evaluate("sample", str(out))

    commit = results["repos"][0]["commits"][0]
    assert commit["has_structural_target"] is True
    assert commit["targets"][0]["class"] == "Ledger"
    assert commit["hit"] is False
    assert commit["targets"][0]["flagged_as"] is None
    assert results["overall"]["targeted_hit_rate"] == 0.0


# ---------------------------------------------------------------------------
# Aggregation and reporting contract
# ---------------------------------------------------------------------------


def test_summary_separates_checkable_commits_from_keyword_only_ones():
    commit_results = [
        {"has_structural_target": True, "hit": True, "detected_by": ["structural"],
         "any_class_flagged_in_changed_files": True,
         "targets": [{"signal": "method_extracted", "flagged": True, "flagged_as": "God Class"}]},
        {"has_structural_target": True, "hit": False, "detected_by": ["structural"],
         "any_class_flagged_in_changed_files": False,
         "targets": [{"signal": "method_extracted", "flagged": False, "flagged_as": None}]},
        {"has_structural_target": False, "hit": False, "detected_by": ["keyword"],
         "any_class_flagged_in_changed_files": True, "targets": []},
    ]

    summary = summarize(commit_results)

    # The keyword-only commit is counted, but NOT in the denominator of the
    # targeted hit rate -- there is nothing about it to check.
    assert summary["commits_evaluated"] == 3
    assert summary["commits_with_structural_target"] == 2
    assert summary["commits_without_structural_target"] == 1
    assert summary["targeted_hit_rate"] == 0.5
    assert summary["by_smell_type"] == {"God Class": 1}
    assert summary["by_signal"]["method_extracted"] == {"targets": 2, "flagged": 1}
    assert summary["by_detector"]["keyword"]["with_target"] == 0
    assert summary["by_detector"]["structural"]["hits"] == 1
    # The weak metric stays separate and is available for every commit.
    assert summary["file_level_hits"] == 2


def test_caveat_travels_with_the_results(repo, tmp_path):
    """The lower-bound warning has to be in the payload, not only in the
    console rendering, or a JSON consumer quotes the number without it."""
    _commit(repo, "initial import", {"big.py": _god_class("Monolith")})
    _commit(repo, "refactor: tweak", {"big.py": _god_class("Monolith", "    def h(self):\n        return 1\n")})

    out = tmp_path / "history"
    extract_refactoring_dataset(str(repo), str(out), repo_name="sample", scan_limit=10)
    results = evaluate("sample", str(out))

    assert results["caveat"] == CAVEAT
    assert "not the same" in CAVEAT.lower() or "NOT" in CAVEAT
    assert "LOWER BOUND" in CAVEAT


def test_evaluating_with_no_repo_covers_everything_already_mined(repo, tmp_path):
    _commit(repo, "initial import", {"big.py": _god_class("Monolith")})
    _commit(repo, "refactor: split", {"big.py": _god_class("Monolith", "    def h(self):\n        return 1\n")})

    out = tmp_path / "history"
    extract_refactoring_dataset(str(repo), str(out), repo_name="sample", scan_limit=10)

    assert available_repos(str(out)) == ["sample"]
    combined = evaluate(None, str(out))
    assert [r["repo"] for r in combined["repos"]] == ["sample"]


def test_unmined_nonexistent_repo_is_a_clear_error(tmp_path):
    out = tmp_path / "history"
    out.mkdir()
    with pytest.raises(ValueError, match="has not been mined"):
        evaluate(str(tmp_path / "nope"), str(out))


def test_evaluating_an_empty_history_dir_is_a_clear_error(tmp_path):
    out = tmp_path / "history"
    out.mkdir()
    with pytest.raises(ValueError, match="No mined repositories"):
        evaluate(None, str(out))


def test_fresh_repo_is_mined_on_demand_then_evaluated(repo, tmp_path):
    """Passing a never-mined repository path runs C.1's mining first rather
    than erroring, and reuses that code rather than reimplementing it."""
    _commit(repo, "initial import", {"big.py": _god_class("Monolith")})
    _commit(repo, "refactor: extract", {"big.py": _god_class("Monolith", "    def h(self):\n        return 1\n")})

    out = tmp_path / "history"
    results = evaluate(str(repo), str(out), scan_limit=10)

    assert (out / "repo.json").exists()
    assert results["overall"]["commits_evaluated"] >= 1
    # Mining must not have dirtied the repository it read.
    assert _git(repo, "status", "--porcelain") == ""


def test_json_output_round_trips(repo, tmp_path):
    _commit(repo, "initial import", {"big.py": _god_class("Monolith")})
    _commit(repo, "refactor: extract", {"big.py": _god_class("Monolith", "    def h(self):\n        return 1\n")})

    out = tmp_path / "history"
    results = evaluate(str(repo), str(out), scan_limit=10)

    payload = json.loads(json.dumps(results))
    assert payload["overall"]["commits_evaluated"] == results["overall"]["commits_evaluated"]
    assert payload["caveat"] == CAVEAT


# ---------------------------------------------------------------------------
# Professional-usability edge cases (D.1)
# ---------------------------------------------------------------------------


def test_zero_candidate_repo_reports_a_clear_note_not_a_crash(repo, tmp_path):
    """A repo with real history but nothing that looks like a refactor --
    must not crash, and must explain itself rather than just showing 0s."""
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    pass\n"})
    _commit(repo, "bump version number", {"widget.py": "class Widget:\n    x = 2\n"})
    _commit(repo, "another small tweak", {"widget.py": "class Widget:\n    x = 3\n"})

    out = tmp_path / "history"
    results = evaluate(str(repo), str(out), scan_limit=10)

    assert results["overall"]["commits_evaluated"] == 0
    assert results["overall"]["targeted_hit_rate"] is None
    repo_result = results["repos"][0]
    assert repo_result["shallow_clone"] is False
    assert repo_result["commits_available"] == 3
    notes = repo_result["notes"]
    assert len(notes) == 1
    assert "no refactoring-like commits" in notes[0].lower()
    assert "--max-commits" in notes[0]
    # And the shallow-clone advice must NOT appear for a normal repo with
    # real (if uninteresting) history.
    assert "shallow" not in notes[0].lower()


def test_shallow_clone_gets_specific_actionable_guidance(repo, tmp_path):
    """A --depth 1 clone (how this project's own corpus repos are fetched --
    see data/real_world/collect_corpus.py) must be diagnosed specifically,
    not reported as an unexplained 0-candidate result."""
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    pass\n"})
    _commit(repo, "refactor: split widget", {"widget.py": "class Widget:\n    x = 2\n"})

    # file:// forces git to actually honour --depth. A plain local path
    # triggers git's "local clone" optimization (hardlinking straight from
    # the source .git), which silently IGNORES --depth and produces a full
    # clone -- confirmed by hand before writing this test.
    shallow_dir = tmp_path / "shallow_clone"
    _git(tmp_path, "clone", "--quiet", "--depth", "1", f"file://{repo}", str(shallow_dir))
    assert _git(shallow_dir, "rev-parse", "--is-shallow-repository").strip() == "true"

    out = tmp_path / "history"
    results = evaluate(str(shallow_dir), str(out), scan_limit=10)

    repo_result = results["repos"][0]
    assert repo_result["shallow_clone"] is True
    assert repo_result["commits_available"] == 1
    notes = repo_result["notes"]
    assert len(notes) == 1
    assert "shallow" in notes[0].lower()
    assert "git" in notes[0].lower() and "fetch" in notes[0].lower() and "--depth" in notes[0]
    # Distinct message from the "history exists but nothing matched" case.
    assert "no refactoring-like commits" not in notes[0].lower()


def test_shallow_repo_that_still_finds_something_keeps_the_caveat(tmp_path):
    """A shallow clone deep enough to contain one comparable commit can still
    yield a candidate -- the shallow-history caveat must still be surfaced
    even when mining wasn't a total blank, since the history is still
    truncated and any hit rate from it should be read with that in mind."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "--quiet")
    _commit(src, "initial import", {"widget.py": _god_class("Widget")})
    _commit(
        src,
        "refactor: pull out a helper",
        {"widget.py": _god_class("Widget", "    def helper(self):\n        return 1\n")},
    )

    # file:// again -- see the comment in the sibling test above.
    shallow_dir = tmp_path / "shallow_clone"
    _git(tmp_path, "clone", "--quiet", "--depth", "2", f"file://{src}", str(shallow_dir))

    history_dir = tmp_path / "history"
    results = evaluate(str(shallow_dir), str(history_dir), scan_limit=10)

    repo_result = results["repos"][0]
    assert repo_result["shallow_clone"] is True
    assert any("shallow" in n.lower() for n in repo_result["notes"])
    # It still found and evaluated the one comparable commit.
    assert results["overall"]["commits_evaluated"] >= 1


def test_non_python_file_changes_are_ignored_throughout(repo, tmp_path):
    """A commit that legitimately restructures a class while ALSO touching a
    JSON config and a text file must be evaluated purely on the .py side --
    the non-Python files must never appear in files_changed or file_pairs,
    and must not affect detection or before/after extraction."""
    _commit(
        repo,
        "initial import",
        {
            "big.py": _god_class("Big"),
            "config.json": '{"version": 1}\n',
            "notes.txt": "notes\n",
        },
    )
    _commit(
        repo,
        "extract a helper and bump config",
        {
            "big.py": _god_class("Big", "    def helper(self):\n        return 1\n"),
            "config.json": '{"version": 2}\n',
        },
    )

    out = tmp_path / "history"
    results = evaluate(str(repo), str(out), scan_limit=10)

    commit = results["repos"][0]["commits"][0]
    manifest = json.loads((out / "repo.json").read_text(encoding="utf-8"))
    candidate = manifest["candidates"][0]

    assert candidate["files_changed"] == ["big.py"]
    assert [p["file"] for p in candidate["file_pairs"]] == ["big.py"]
    assert commit["has_structural_target"] is True
    assert commit["targets"][0]["class"] == "Big"

    # And nothing from config.json/notes.txt was ever written to disk under
    # the extraction output.
    commit_dir = out / "repo" / candidate["short_hash"]
    extracted = [p.name for p in commit_dir.rglob("*") if p.is_file()]
    assert all(name.endswith(".py") for name in extracted)


def test_flag_set_has_no_duplicate_commit_count_knob():
    """--max-commits (CLI) maps onto the single scan_limit parameter; there
    must be exactly one commit-count knob, not --scan-limit AND --max-commits
    doing the same thing under different names."""
    from click.testing import CliRunner

    from engine.cli.main import cli

    result = CliRunner().invoke(cli, ["evaluate", "--help"])

    assert result.exit_code == 0
    assert "--max-commits" in result.output
    assert "--scan-limit" not in result.output
    assert "--max-candidates" in result.output
