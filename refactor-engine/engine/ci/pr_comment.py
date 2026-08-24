"""Turn `refactor-scan analyze --format json` output into a PR comment.

Used by the GitHub Action in .github/workflows/refactor-scan.yml, and usable
by any other CI system that can run a Python module and post a comment.

    python -m engine.ci.pr_comment --files-from changed.txt --output body.md

DESIGN: FORMATTING IS PURE, I/O IS NOT
--------------------------------------
build_comment() is a pure function from a list of Findings to a markdown
string, so the part that decides what a reviewer actually reads is unit
tested without a network, a git repo, or a GitHub token. Running the scanner
and writing files is kept in separate, thin functions around it.

WHY IT SHELLS OUT TO THE REAL CLI
---------------------------------
analyze_files() invokes the installed `refactor-scan` entry point rather than
importing engine.pipeline directly. That costs a process start per file, but
it means CI exercises the same packaged command a developer runs locally --
if the console script or the bundled model is broken by packaging, this
catches it instead of quietly working around it via an in-process import.

WHY ONE FILE AT A TIME
----------------------
`analyze` takes a single path, and a PR's changed files are scattered across
the tree rather than sitting under one directory. Analyzing each separately
also keeps the report honest about scope: coupling metrics are computed over
what the scanner was pointed at, and pretending otherwise would misreport
cbo/fan_in/fan_out for a partial checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# Identifies our own comment so repeated pushes EDIT it instead of posting a
# new one. An HTML comment is invisible in rendered markdown but survives a
# round trip through the GitHub API, which is what makes find-then-update
# reliable without storing a comment id anywhere.
MARKER = "<!-- refactor-scan-report -->"

CLEAN_LABEL = "Clean"

# GitHub rejects comment bodies over 65536 characters. Stay well under it:
# a comment that long is unreadable anyway, and the useful behaviour when a
# PR touches a hundred smelly files is to show the worst and say so.
MAX_COMMENT_CHARS = 60000
MAX_ROWS = 40

# A very large PR would otherwise spend minutes starting one process per
# file. Past this the scan is capped and the comment says it was capped --
# silently scanning a subset would be worse than saying so.
DEFAULT_MAX_FILES = 50


@dataclass
class Finding:
    """One flagged class, reduced to what a PR comment needs."""

    file: str
    class_name: str
    smell: str
    confidence: float
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading a scan report
# ---------------------------------------------------------------------------


def _suggestion_line(suggestion: dict) -> str | None:
    """One short, actionable line for a suggestion, or None to omit it.

    The soft "we couldn't find a clean answer" suggestion types are
    deliberately dropped: they are honest and useful in the full local
    report, but in a PR comment they are noise that pushes the actionable
    items further from the reviewer's eye.
    """
    kind = suggestion.get("type")

    if kind == "move_method":
        method = suggestion.get("source_method", "?")
        target = suggestion.get("target_class")
        receiver = suggestion.get("target_receiver")
        destination = f"`{target}`" if target else f"whatever `{receiver}` holds"
        return f"Move `{method}()` into {destination}"

    if kind == "extract_class":
        methods = suggestion.get("methods_to_extract", [])
        name = suggestion.get("suggested_name", "a new class")
        shown = ", ".join(f"`{m}`" for m in methods[:4])
        if len(methods) > 4:
            shown += f" (+{len(methods) - 4} more)"
        return f"Extract {shown} into `{name}`"

    if kind == "extract_method":
        name = suggestion.get("suggested_name") or suggestion.get("source_method", "?")
        return f"Extract a helper (`{name}`) out of the long method"

    if kind == "long_parameter_list":
        methods = suggestion.get("methods", [])
        shown = ", ".join(f"`{m}()`" for m in methods[:3])
        return f"Long parameter list on {shown}"

    if kind == "duplicate_code":
        pairs = suggestion.get("pairs", [])
        if not pairs:
            return None
        first = pairs[0].get("methods", [])
        if len(first) == 2:
            return f"Near-duplicate structure: `{first[0]}()` / `{first[1]}()`"
        return "Near-duplicate method structure"

    # no_clear_split / no_clear_envy_target / unsupported_smell_type: real
    # results, but not something a reviewer can act on inline.
    return None


def findings_from_report(report: dict, source_file: str) -> list[Finding]:
    """Every non-Clean class in one `analyze --format json` report.

    `source_file` is the path we asked the scanner to look at, and it is what
    the comment shows -- NOT the report's own `file_path`, which carries
    native path separators (backslashes on a Windows runner) and would render
    inconsistently against the forward-slash paths git and GitHub use.
    """
    findings = []
    for class_name, entry in sorted((report.get("classes") or {}).items()):
        smell = entry.get("predicted_smell")
        if not smell or smell == CLEAN_LABEL:
            continue

        lines = []
        for suggestion in entry.get("suggestions") or []:
            line = _suggestion_line(suggestion)
            if line and line not in lines:
                lines.append(line)

        findings.append(
            Finding(
                file=source_file.replace("\\", "/"),
                class_name=class_name,
                smell=smell,
                confidence=float(entry.get("confidence") or 0.0),
                suggestions=lines,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Running the scanner
# ---------------------------------------------------------------------------


def analyze_files(
    files: list[str],
    max_files: int = DEFAULT_MAX_FILES,
    runner=None,
) -> tuple[list[Finding], list[str], int]:
    """Scan each file, returning (findings, unscannable_files, skipped_count).

    A file that cannot be scanned -- a syntax error mid-PR is the common case
    -- is collected rather than raised. One unparsable file must not cost the
    reviewer the report on every other file in the PR.

    `runner` is injectable so tests can drive the formatting path without
    spawning a subprocess per fixture.
    """
    runner = runner or _run_analyze

    scanned = files[:max_files]
    skipped = len(files) - len(scanned)

    findings: list[Finding] = []
    failed: list[str] = []
    for path in scanned:
        report = runner(path)
        if report is None:
            failed.append(path.replace("\\", "/"))
            continue
        findings.extend(findings_from_report(report, path))

    return findings, failed, skipped


def _run_analyze(path: str) -> dict | None:
    """`refactor-scan analyze <path> --format json`, parsed. None on failure.

    Writes to a temp file rather than reading stdout because the console
    script prints a human-readable confirmation line alongside the report;
    --output is the documented way to get the JSON on its own.
    """
    handle, out_path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "engine.cli.main",
                "analyze", path, "--format", "json", "--output", out_path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return None
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Building the comment
# ---------------------------------------------------------------------------


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or singular + "s")


def _why_hint(findings: list[Finding]) -> str:
    """Point at the local command instead of inlining the full reasoning.

    A PR comment is the wrong surface for the explainability report -- it is
    long, it is per-class, and it buries the one-line finding a reviewer
    needs. Naming a concrete, copy-pasteable command is more useful than
    reproducing it here.
    """
    example = findings[0]
    return (
        f"<sub>Want the full reasoning — what was measured, what drove the "
        f"classifier, how the suggestion was reached? Run it locally:\n"
        f"`refactor-scan why {example.file} --class {example.class_name}`  "
        f"(add `--output report.html` for the illustrated version).</sub>"
    )


def build_comment(
    findings: list[Finding],
    files_scanned: int,
    failed_files: list[str] | None = None,
    skipped_files: int = 0,
) -> str:
    """The full markdown body, marker included. Pure: no I/O, no globals."""
    failed_files = failed_files or []
    parts = [MARKER, "### 🔍 refactor-scan", ""]

    if not findings:
        parts.append(
            f"✅ **No code smells found** in the "
            f"{files_scanned} changed Python {_plural(files_scanned, 'file')}."
        )
    else:
        file_count = len({f.file for f in findings})
        parts.append(
            f"⚠️ **{len(findings)} code {_plural(len(findings), 'smell')}** "
            f"found in {file_count} of {files_scanned} changed Python "
            f"{_plural(files_scanned, 'file')}."
        )
        parts.append("")
        parts.append("| File | Class | Smell | Confidence | Suggested fix |")
        parts.append("| --- | --- | --- | --: | --- |")

        # Worst first: a reviewer reading only the top rows should be reading
        # the findings the model is most sure about.
        ordered = sorted(findings, key=lambda f: (-f.confidence, f.file, f.class_name))
        for finding in ordered[:MAX_ROWS]:
            fix = finding.suggestions[0] if finding.suggestions else "—"
            if len(finding.suggestions) > 1:
                fix += f" (+{len(finding.suggestions) - 1} more)"
            parts.append(
                f"| `{finding.file}` | `{finding.class_name}` | {finding.smell} "
                f"| {finding.confidence:.0%} | {fix} |"
            )

        if len(ordered) > MAX_ROWS:
            parts.append("")
            parts.append(
                f"<sub>…and {len(ordered) - MAX_ROWS} more, omitted to keep this "
                f"comment readable.</sub>"
            )

        parts.append("")
        parts.append(_why_hint(ordered))

    if failed_files:
        parts.append("")
        shown = ", ".join(f"`{p}`" for p in failed_files[:5])
        if len(failed_files) > 5:
            shown += f" (+{len(failed_files) - 5} more)"
        parts.append(
            f"<sub>⚠️ Could not scan {shown} — unparsable at this commit "
            f"(syntax error, or Python this build cannot parse).</sub>"
        )

    if skipped_files:
        parts.append("")
        parts.append(
            f"<sub>ℹ️ Only the first {files_scanned} changed files were scanned; "
            f"{skipped_files} more were skipped to keep CI fast.</sub>"
        )

    parts.append("")
    parts.append(
        "<sub>Only files changed in this PR were scanned — pre-existing code "
        "elsewhere is not reported. This check is informational and does not "
        "block merging.</sub>"
    )

    body = "\n".join(parts)
    if len(body) > MAX_COMMENT_CHARS:
        keep = MAX_COMMENT_CHARS - len(MARKER) - 120
        body = (
            MARKER
            + "\n"
            + body[len(MARKER) : len(MARKER) + keep]
            + "\n\n<sub>…truncated to fit GitHub's comment size limit.</sub>"
        )
    return body


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _read_file_list(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m engine.ci.pr_comment",
        description="Format refactor-scan results as a GitHub PR comment body.",
    )
    parser.add_argument(
        "--files-from", metavar="FILE",
        help="File containing one changed .py path per line (as `git diff --name-only` emits).",
    )
    parser.add_argument("files", nargs="*", help="Changed .py paths (alternative to --files-from).")
    parser.add_argument(
        "--output", default="refactor-scan-comment.md",
        help="Where to write the markdown comment body.",
    )
    parser.add_argument(
        "--max-files", type=int, default=DEFAULT_MAX_FILES,
        help=f"Cap on files scanned (default {DEFAULT_MAX_FILES}).",
    )
    parser.add_argument(
        "--fail-on-smell", action="store_true",
        help="Exit non-zero when any smell is found. OFF by default: this check is "
             "informational, and a smell in new code is a prompt for discussion, not "
             "a build error. Opt in for stricter enforcement.",
    )
    args = parser.parse_args(argv)

    files = list(args.files)
    if args.files_from:
        files.extend(_read_file_list(args.files_from))
    files = [f for f in files if f.endswith(".py")]

    if not files:
        # Not an error, and deliberately no comment: a docs-only PR should not
        # get a "nothing to report" note on every push. The workflow guards
        # this case too; this is the belt to that braces.
        print("No changed Python files -- nothing to report.")
        return 0

    findings, failed, skipped = analyze_files(files, max_files=args.max_files)
    body = build_comment(
        findings,
        files_scanned=min(len(files), args.max_files),
        failed_files=failed,
        skipped_files=skipped,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"Wrote comment body to {args.output} ({len(findings)} finding(s)).")

    # Also surface it in the Actions run itself, which works even when the
    # token cannot comment (a PR from a fork gets a read-only token).
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(body + "\n")
        except OSError:
            pass

    if args.fail_on_smell and findings:
        print(f"--fail-on-smell set and {len(findings)} finding(s) present; failing.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
