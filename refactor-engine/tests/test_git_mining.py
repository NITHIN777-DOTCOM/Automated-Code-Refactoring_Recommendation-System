import json
import subprocess

import pytest

from engine.evaluate.git_mining import (
    DEFAULT_KEYWORDS,
    checkout_before_state,
    extract_refactoring_dataset,
    find_refactor_commits,
    find_refactoring_candidates,
    structural_signals,
)


def _git(repo_path, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _commit(repo_path, message, files: dict[str, str]):
    for name, content in files.items():
        (repo_path / name).write_text(content, encoding="utf-8")
    _git(repo_path, "add", "-A")
    _git(
        repo_path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "commit", "-m", message,
    )
    return _git(repo_path, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo in a SUBDIRECTORY of tmp_path, never tmp_path itself.

    This previously returned tmp_path directly, which made
    test_checkout_before_state_writes_parent_version_without_touching_repo
    self-contradictory: it takes both `repo` and `tmp_path`, so its
    `tmp_path / "before_state"` output directory was created INSIDE the
    repository it then asserted was clean. `git status` reported the stray
    `?? before_state/` and the test failed -- correctly, for a scenario that
    could never have passed. Giving the repo its own directory means output
    written under tmp_path lands outside the work tree, which is the
    situation the assertion was actually written to describe.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init")
    return repo_dir


def test_finds_commits_matching_default_keywords(repo):
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    pass\n"})
    refactor_hash = _commit(
        repo,
        "refactor: split Widget into two classes",
        {
            "widget.py": (
                "class Widget:\n"
                "    def use(self):\n"
                "        return Helper().assist()\n"
                "\n"
                "class Helper:\n"
                "    def assist(self):\n"
                "        return True\n"
            )
        },
    )
    _commit(repo, "add unrelated docs", {"README.md": "hello\n"})

    results = find_refactor_commits(str(repo))

    assert len(results) == 1
    entry = results[0]
    assert entry["commit_hash"] == refactor_hash
    assert entry["message"] == "refactor: split Widget into two classes"
    assert entry["files_changed"] == ["widget.py"]
    assert set(entry["classes_touched"]) == {"Widget", "Helper"}


def test_no_matching_commits_returns_empty_list(repo):
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    pass\n"})
    _commit(repo, "bump version number", {"widget.py": "class Widget:\n    x = 2\n"})

    assert find_refactor_commits(repo, keywords=["refactor"]) == []


def test_custom_keywords_override_defaults(repo):
    _commit(repo, "initial import", {"a.py": "class A:\n    pass\n"})
    matching_hash = _commit(repo, "tidy up the imports", {"a.py": "class A:\n    x = 1\n"})

    results = find_refactor_commits(str(repo), keywords=["tidy up"])

    assert len(results) == 1
    assert results[0]["commit_hash"] == matching_hash


def test_keyword_matching_is_case_insensitive(repo):
    _commit(repo, "initial import", {"a.py": "class A:\n    pass\n"})
    matching_hash = _commit(repo, "REFACTOR the pipeline", {"a.py": "class A:\n    x = 1\n"})

    results = find_refactor_commits(str(repo))

    assert results[0]["commit_hash"] == matching_hash


def test_max_commits_caps_the_result_count(repo):
    for i in range(5):
        _commit(repo, f"refactor step {i}", {"a.py": f"class A:\n    x = {i}\n"})

    results = find_refactor_commits(str(repo), max_commits=2)

    assert len(results) == 2


def test_non_git_directory_raises_a_clear_error(tmp_path):
    """A directory that is not a repository must be rejected.

    The match string is deliberately the shorter "not a git repository": this
    assertion used to require the phrase "not INSIDE a git repository", which
    could only hold on a machine where no ancestor of the temp directory is a
    repo. On a machine where `git init` was ever run in the home directory --
    where pytest's tmp_path lives -- the old check genuinely reported "inside
    a work tree" and raised nothing at all. See test below for that case.
    """
    with pytest.raises(ValueError, match="not a git repository"):
        find_refactor_commits(str(tmp_path))


def test_directory_inside_another_repo_is_rejected_rather_than_silently_mined(repo, tmp_path):
    """The bug the environment was hiding.

    `git rev-parse --is-inside-work-tree` walks upwards, so a plain
    subdirectory of a repository answers "true". Mining it used to succeed
    and silently return the ENCLOSING repository's commits. It must raise,
    and the error must point at the real root.
    """
    _commit(repo, "refactor: initial", {"widget.py": "class Widget:\n    pass\n"})
    nested = repo / "package" / "submodule"
    nested.mkdir(parents=True)

    with pytest.raises(ValueError, match="not a git repository root"):
        find_refactor_commits(str(nested))


def test_extraction_refuses_to_write_inside_the_repository(repo, tmp_path):
    """The promise is "never dirties the work tree". Writing output into the
    repo would keep every tracked file untouched and still leave `git status`
    dirty with untracked files, so it is refused up front."""
    commit_hash = _commit(repo, "refactor: widget", {"widget.py": "class Widget:\n    x = 1\n"})

    with pytest.raises(ValueError, match="inside the repository"):
        checkout_before_state(str(repo), commit_hash, str(repo / "before_state"))

    assert _git(repo, "status", "--porcelain") == ""


def test_checkout_before_state_writes_parent_version_without_touching_repo(repo, tmp_path):
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    version = 1\n"})
    refactor_hash = _commit(
        repo, "refactor: bump widget version", {"widget.py": "class Widget:\n    version = 2\n"}
    )

    output_dir = tmp_path / "before_state"
    result_path = checkout_before_state(str(repo), refactor_hash, str(output_dir))

    assert result_path == str(output_dir)
    before_content = (output_dir / "widget.py").read_text(encoding="utf-8")
    assert "version = 1" in before_content

    # The real repo's working tree must be untouched -- it should still show
    # the *current* (post-refactor) content, not have been reset to the
    # parent commit.
    current_content = (repo / "widget.py").read_text(encoding="utf-8")
    assert "version = 2" in current_content

    status = _git(repo, "status", "--porcelain")
    assert status == ""


def test_checkout_before_state_skips_files_added_by_the_commit(repo, tmp_path):
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    pass\n"})
    refactor_hash = _commit(
        repo,
        "refactor: extract a new helper file",
        {
            "widget.py": "class Widget:\n    pass\n",
            "helper.py": "class Helper:\n    pass\n",
        },
    )

    output_dir = tmp_path / "before_state"
    checkout_before_state(str(repo), refactor_hash, str(output_dir))

    # helper.py didn't exist before this commit, so there's no "before"
    # version of it to write.
    assert not (output_dir / "helper.py").exists()


def test_default_keywords_are_exposed_for_callers_to_extend():
    assert "refactor" in DEFAULT_KEYWORDS
    assert "extract method" in DEFAULT_KEYWORDS


# ---------------------------------------------------------------------------
# Structural refactor detection
# ---------------------------------------------------------------------------


def test_structural_detection_spots_a_method_moved_between_classes(repo):
    _commit(
        repo,
        "initial import",
        {
            "shop.py": (
                "class Order:\n"
                "    def total(self):\n"
                "        return 1\n"
                "    def format_receipt(self):\n"
                "        return 'receipt'\n"
                "\n"
                "class Printer:\n"
                "    def send(self):\n"
                "        return True\n"
            )
        },
    )
    # format_receipt moves Order -> Printer. Message says nothing useful.
    moved = _commit(
        repo,
        "tweak output",
        {
            "shop.py": (
                "class Order:\n"
                "    def total(self):\n"
                "        return 1\n"
                "\n"
                "class Printer:\n"
                "    def send(self):\n"
                "        return True\n"
                "    def format_receipt(self):\n"
                "        return 'receipt'\n"
            )
        },
    )

    signals = structural_signals(str(repo), moved)

    assert "method_moved_between_classes" in signals["signals"]
    assert signals["structural_match"]
    move = signals["details"]["methods_moved"][0]
    assert move["method"] == "format_receipt"
    assert move["from_class"] == ["Order"]
    assert move["to_class"] == ["Printer"]


def test_structural_detection_spots_a_renamed_class(repo):
    body = (
        "    def load(self):\n        return 1\n"
        "    def save(self):\n        return 2\n"
        "    def clear(self):\n        return 3\n"
    )
    _commit(repo, "initial import", {"store.py": "class DataStore:\n" + body})
    renamed = _commit(repo, "housekeeping", {"store.py": "class Repository:\n" + body})

    signals = structural_signals(str(repo), renamed)

    assert "class_renamed" in signals["signals"]
    rename = signals["details"]["classes_renamed"][0]
    assert rename["from"] == "DataStore"
    assert rename["to"] == "Repository"


def test_unrelated_class_swap_is_not_reported_as_a_rename(repo):
    """A deleted class and an added one that share no interface are two
    separate changes, not a rename -- the overlap gate must reject them."""
    _commit(
        repo,
        "initial import",
        {
            "a.py": (
                "class Alpha:\n"
                "    def one(self):\n        return 1\n"
                "    def two(self):\n        return 2\n"
            )
        },
    )
    swapped = _commit(
        repo,
        "replace alpha",
        {
            "a.py": (
                "class Beta:\n"
                "    def entirely(self):\n        return 1\n"
                "    def different(self):\n        return 2\n"
            )
        },
    )

    signals = structural_signals(str(repo), swapped)

    assert "class_renamed" not in signals["signals"]


def test_balanced_churn_alone_does_not_qualify_a_commit(repo):
    """Any edit-in-place looks balanced. On its own it must not make a commit
    a structural candidate, or the evaluation set fills with noise."""
    _commit(repo, "initial import", {"a.py": "class A:\n    def run(self):\n        return 1\n"})
    tweaked = _commit(
        repo, "change return value", {"a.py": "class A:\n    def run(self):\n        return 2\n"}
    )

    signals = structural_signals(str(repo), tweaked)

    assert "balanced_churn" in signals["signals"]
    assert not signals["structural_match"]


def test_candidate_finder_reports_which_detector_fired(repo):
    _commit(repo, "initial import", {"a.py": "class A:\n    def run(self):\n        return 1\n"})
    _commit(
        repo,
        "refactor: rename the thing",
        {"a.py": "class A:\n    def run(self):\n        return 2\n"},
    )

    candidates = find_refactoring_candidates(str(repo), scan_limit=10)

    assert len(candidates) == 1
    assert candidates[0]["detected_by"] == ["keyword"]
    assert "refactor" in candidates[0]["matched_keywords"]


def test_require_structural_drops_keyword_only_commits(repo):
    _commit(repo, "initial import", {"a.py": "class A:\n    def run(self):\n        return 1\n"})
    _commit(
        repo,
        "refactor: not really a refactor",
        {"a.py": "class A:\n    def run(self):\n        return 2\n"},
    )

    assert find_refactoring_candidates(str(repo), scan_limit=10, require_structural=True) == []


def test_extract_dataset_writes_before_and_after_without_dirtying_the_repo(repo, tmp_path):
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    version = 1\n"})
    _commit(
        repo,
        "refactor: bump widget version",
        {"widget.py": "class Widget:\n    version = 2\n"},
    )

    out = tmp_path / "history"
    manifest = extract_refactoring_dataset(
        str(repo), str(out), repo_name="sample", scan_limit=10
    )

    assert manifest["candidate_count"] == 1
    entry = manifest["candidates"][0]
    pair = entry["file_pairs"][0]

    before = out / pair["before_path"]
    after = out / pair["after_path"]
    assert "version = 1" in before.read_text(encoding="utf-8")
    assert "version = 2" in after.read_text(encoding="utf-8")

    # The manifest is on disk and round-trips.
    written = json.loads((out / "sample.json").read_text(encoding="utf-8"))
    assert written["candidate_count"] == 1

    # And the repository is exactly as it was.
    assert _git(repo, "status", "--porcelain") == ""
    assert "version = 2" in (repo / "widget.py").read_text(encoding="utf-8")


def test_extract_dataset_records_null_for_a_file_with_no_before_state(repo, tmp_path):
    _commit(repo, "initial import", {"widget.py": "class Widget:\n    pass\n"})
    _commit(
        repo,
        "refactor: extract helper into its own module",
        {
            "widget.py": "class Widget:\n    pass\n",
            "helper.py": "class Helper:\n    def assist(self):\n        return 1\n",
        },
    )

    out = tmp_path / "history"
    manifest = extract_refactoring_dataset(
        str(repo), str(out), repo_name="sample", scan_limit=10
    )

    pairs = {p["file"]: p for p in manifest["candidates"][0]["file_pairs"]}
    assert pairs["helper.py"]["before_path"] is None
    assert pairs["helper.py"]["after_path"] is not None
