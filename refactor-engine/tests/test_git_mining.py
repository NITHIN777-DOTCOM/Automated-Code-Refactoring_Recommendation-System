import subprocess

import pytest

from engine.evaluate.git_mining import (
    DEFAULT_KEYWORDS,
    checkout_before_state,
    find_refactor_commits,
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
    _git(tmp_path, "init")
    return tmp_path


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
    with pytest.raises(ValueError, match="not inside a git repository"):
        find_refactor_commits(str(tmp_path))


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
