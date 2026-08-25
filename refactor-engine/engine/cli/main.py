"""refactor-scan CLI: click app definition.

Lives inside the package (rather than as a top-level script) so it can be
wired up as a proper console-script entry point in pyproject.toml and work
identically whether invoked via `python run_scan.py` in a checkout or as the
installed `refactor-scan` command.
"""

from __future__ import annotations

import json
import os
import sys

import rich_click as click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from engine.cli.html_report import write_report
from engine.cli.render import (
    console,
    print_analyze_report,
    print_banner,
    print_evaluation_report,
    print_model_explanation,
    print_smell_explanation,
    print_trend_report,
    print_why_summary,
)
from engine.evaluate.git_mining import MAX_SCAN_LIMIT_CEILING
from engine.evaluate.refactor_eval import evaluate as run_evaluation
from engine.evaluate.trend import (
    DEFAULT_INTERVAL,
    DEFAULT_MAX_SAMPLES,
    INTERVALS,
    MAX_SAMPLES_CEILING,
    collect_trend,
)
from engine.ml.bundle import MODEL_ENV_VAR, describe_model
from engine.parser import DEFAULT_EXCLUDED_DIRS, is_excluded_dir
from engine.pipeline import analyze_path
from engine.reasoning import ClassNotFoundError, explain_file
from engine.serializer import metrics_to_json

for _stream in (sys.stdout, sys.stderr):
    if _stream.encoding and _stream.encoding.lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8")

__version__ = "0.1.0"

click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_ERRORS_SUGGESTION = "magenta italic"
click.rich_click.MAX_WIDTH = 100
click.rich_click.STYLE_OPTION = "bold cyan"
click.rich_click.STYLE_ARGUMENT = "bold cyan"
click.rich_click.STYLE_COMMAND = "bold magenta"
click.rich_click.STYLE_HELPTEXT_FIRST_LINE = "bold"
click.rich_click.COMMAND_GROUPS = {
    "refactor-scan": [
        {"name": "Commands", "commands": ["analyze", "why", "explain", "evaluate", "trend"]},
    ]
}


# Shared by analyze and why. The real-data-trained classifier is opt-in rather
# than default -- see engine/ml/bundle.py -- and this is how a caller opts in
# for a single run without changing anything on disk.
_MODEL_OPTION = click.option(
    "--model", "model_choice", metavar="NAME",
    help="Classifier to use: 'synthetic' (default, shipped) or 'real' (trained on the "
         "real-world corpus), or a path to a .joblib bundle. Also settable via "
         f"${MODEL_ENV_VAR}.",
)


def _activate_model(model_choice):
    """Select the classifier for this run, and say so when it isn't the default.

    Set in the environment rather than threaded through analyze_path() and
    explain_file(): the choice has to reach predict.py at the bottom of the
    call stack, and every layer in between would otherwise grow a parameter it
    does nothing with. Announced whenever it is not the default, so a number in
    the output can never be silently attributable to the wrong model.
    """
    if model_choice:
        os.environ[MODEL_ENV_VAR] = model_choice

    try:
        described = describe_model()
    except (ValueError, FileNotFoundError) as exc:
        raise click.UsageError(str(exc))

    if not described["is_default"]:
        console.print(
            f"[yellow]Using the non-default classifier[/yellow] "
            f"[bold]{described['name']}[/bold] (trained on {described['trained_on']})."
        )


def _file_count(path, exclude=()):
    if os.path.isfile(path):
        return 1 if path.endswith(".py") else 0

    excluded = DEFAULT_EXCLUDED_DIRS | set(exclude)
    count = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not is_excluded_dir(d, excluded)]
        count += sum(1 for filename in files if filename.endswith(".py"))
    return count


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="refactor-scan")
@click.pass_context
def cli(ctx):
    """refactor-engine: AST-based code smell detection and refactoring suggestions."""
    if ctx.invoked_subcommand is None:
        print_banner()


@cli.command()
@click.argument("path", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.option(
    "--format", "output_format", type=click.Choice(["console", "json"]), default="console",
    help="Output format.",
)
@click.option("--output", default="report.json", help="Report path (--format json only).")
@click.option(
    "--audience", type=click.Choice(["dev", "simple"]), default="dev",
    help="dev: technical detail. simple: plain-language for non-engineers.",
)
@click.option(
    "--top", type=int, default=10,
    help="Show at most N flagged classes in console output, most severe first. 0 shows all.",
)
@click.option(
    "--exclude", multiple=True, metavar="DIRNAME",
    help="Additional directory name to skip (repeatable). venv/.venv/env/test_env/"
    "__pycache__/site-packages/.git/node_modules/build/dist/*.egg-info are always skipped.",
)
@_MODEL_OPTION
def analyze(path, output_format, output, audience, top, exclude, model_choice):
    """Scan PATH (a directory or a single .py file) for code smells and report the results."""
    _activate_model(model_choice)
    results = analyze_path(path, exclude=list(exclude))
    file_count = _file_count(path, exclude=exclude)

    if output_format == "json":
        with open(output, "w", encoding="utf-8") as f:
            f.write(metrics_to_json(results))
        console.print(f"[bold]Scanned[/bold] {len(results['classes'])} classes across {file_count} files")
        console.print(f"[bold green]Report written to[/bold green] {output}")
    else:
        print_analyze_report(
            results["classes"], path, file_count, audience=audience, top=top,
            unused_imports=results["unused_imports"],
        )


@cli.command()
@click.argument("path", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option(
    "--class", "class_name", metavar="CLASSNAME",
    help="Explain only this class instead of every flagged class in the file.",
)
@click.option(
    "--output", type=click.Path(dir_okay=False), metavar="FILE.html",
    help="Write the full illustrated reasoning to an HTML file instead of summarizing here.",
)
@_MODEL_OPTION
def why(path, class_name, output, model_choice):
    """Show the reasoning behind PATH's results -- what was measured, what the classifier made
    of it, and how the suggestion was reached.

    Without --output this prints a short summary: the headline finding and the single factor
    that mattered most, per class. With --output it writes a full illustrated report -- every
    measurement in plain language, the model's own working, and a drawing of how the methods
    were grouped.
    """
    _activate_model(model_choice)
    try:
        reasoning = explain_file(path, class_name=class_name, clean_fallback=bool(output))
    except ClassNotFoundError as exc:
        available = ", ".join(exc.available) if exc.available else "none -- this file defines no classes"
        raise click.UsageError(f"No class named {exc.class_name!r} in {path}. Classes here: {available}.")

    if not reasoning.classes:
        if reasoning.total_classes == 0:
            console.print(f"[dim]No classes found in {path} — nothing to explain.[/dim]")
        elif class_name:
            # The class exists (explain_file would have raised otherwise) but
            # the pipeline couldn't analyze it. Saying "no smells detected"
            # here would report a clean bill of health we never established.
            console.print(
                f"[yellow]{class_name} was found but could not be analyzed[/yellow] — "
                f"it was skipped during the scan, so there is no result to explain."
            )
        else:
            console.print("[bold green]No smells detected — nothing to explain.[/bold green]")
        return

    if output:
        # Confirmation only. Printing the summary here as well would mean the
        # reader has to decide which of two answers to the same question is
        # the one they asked for.
        write_report(reasoning, output)
        count = len(reasoning.classes)
        console.print(
            f"[bold green]Full explanation for {count} class{'' if count == 1 else 'es'} "
            f"written to[/bold green] {output}"
        )
        return

    print_why_summary(reasoning)


@cli.command()
@click.argument("smell_name", required=False)
@click.option("--model", is_flag=True, help="Explain how the ML classifier works instead of a smell.")
def explain(smell_name, model):
    """Explain a code smell (e.g. "God Class"), or how the classifier works with --model."""
    if model:
        print_model_explanation()
        return
    if not smell_name:
        raise click.UsageError('Provide a smell name (e.g. explain "God Class") or use --model.')
    print_smell_explanation(smell_name)


@cli.command()
@click.argument(
    "repo", required=False, type=click.Path(exists=False, file_okay=False, dir_okay=True)
)
@click.option(
    "--history-dir", default=os.path.join("data", "refactor_history"), metavar="DIR",
    help="Where mined before/after pairs live. Default: data/refactor_history.",
)
@click.option(
    "--format", "output_format", type=click.Choice(["console", "json"]), default="console",
    help="Output format.",
)
@click.option("--output", default="evaluation.json", help="Report path (--format json only).")
@click.option(
    "--remine", is_flag=True,
    help="Re-mine REPO even if it has already been mined into --history-dir.",
)
@click.option(
    "--max-commits", "scan_limit", type=int, default=400, metavar="N",
    help="How many commits back (from HEAD) to scan when mining a repo (mining only). "
         f"Capped at {MAX_SCAN_LIMIT_CEILING} regardless of what's passed. This is the "
         "ONLY commit-count knob -- distinct from --max-candidates below, which caps how "
         "many matches to KEEP, not how far back to look.",
)
@click.option(
    "--max-candidates", type=int, default=40,
    help="Cap on refactoring candidates extracted per repo (mining only). Not the same "
         "knob as --max-commits: this limits how many MATCHES are kept once found.",
)
@click.option(
    "--no-commits", is_flag=True,
    help="Console output: show only the aggregate summary, not the per-commit table.",
)
@click.option(
    "--no-progress", is_flag=True,
    help="Suppress the progress bar shown while mining a large, not-yet-mined repo.",
)
@_MODEL_OPTION
def evaluate(
    repo, history_dir, output_format, output, remine, scan_limit, max_candidates,
    no_commits, no_progress, model_choice,
):
    """Check the detector against real refactoring history.

    Runs the analysis over the BEFORE state of each mined refactoring commit
    and reports how often we independently flagged the class the developers
    then went on to restructure.

    REPO may be a path to a git repository (mined first if not already), or
    the name of a repository already mined into --history-dir. Omit it to
    evaluate every repository already mined there.

    Note the hit rate is a LOWER BOUND, not a recall figure: being refactored
    is not the same as being smelly.
    """
    _activate_model(model_choice)

    on_progress = None
    progress_ctx = None
    if not no_progress:
        # Only mining is slow (one or more `git show` calls per commit); a
        # repo that's already mined just reads its manifest and analyzes the
        # before-states, so this bar only ever appears when there's actually
        # something to wait for -- evaluate() never calls on_progress
        # otherwise (see its docstring).
        progress_ctx = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total} commits"),
            TextColumn("[green]{task.fields[found]} candidate(s) found[/green]"),
            console=console,
            transient=True,
        )
        task_id = None

        def on_progress(walked, total, found):  # noqa: F811 -- intentional shadow
            nonlocal task_id
            if task_id is None:
                task_id = progress_ctx.add_task("Mining commit history", total=total, found=found)
            progress_ctx.update(task_id, completed=walked, found=found)

    try:
        if progress_ctx is not None:
            with progress_ctx:
                results = run_evaluation(
                    repo, history_dir, remine=remine,
                    scan_limit=scan_limit, max_candidates=max_candidates,
                    on_progress=on_progress,
                )
        else:
            results = run_evaluation(
                repo, history_dir, remine=remine,
                scan_limit=scan_limit, max_candidates=max_candidates,
            )
    except (ValueError, FileNotFoundError) as exc:
        raise click.UsageError(str(exc)) from exc

    if output_format == "json":
        with open(output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        overall = results["overall"]
        console.print(
            f"[bold]Evaluated[/bold] {overall['commits_evaluated']} refactoring commits "
            f"across {len(results['repos'])} repo(s)"
        )
        console.print(f"[bold green]Report written to[/bold green] {output}")
        for note in results.get("notes", []):
            console.print(f"[yellow]Note:[/yellow] {note}")
        console.print(f"[yellow]{results['caveat']}[/yellow]")
    else:
        print_evaluation_report(results, show_commits=not no_commits)


@cli.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option(
    "--interval", type=click.Choice(INTERVALS), default=DEFAULT_INTERVAL, show_default=True,
    help="How to space the history samples. 'monthly'/'weekly' take the last commit in each "
         "calendar period, which is comparable across repos of different velocity. 'commits' "
         "spaces them evenly by commit count instead, which tracks activity rather than time.",
)
@click.option(
    "--max-samples", type=int, default=DEFAULT_MAX_SAMPLES, show_default=True, metavar="N",
    help="Cap on how many historical points are analyzed, spread evenly across the whole "
         f"available range (both endpoints always included). Capped at {MAX_SAMPLES_CEILING} "
         "regardless of what's passed -- every sample re-analyzes the entire codebase.",
)
@click.option(
    "--format", "output_format", type=click.Choice(["console", "json"]), default="console",
    help="Output format.",
)
@click.option("--output", default="trend.json", help="Report path (--format json only).")
@click.option(
    "--exclude", multiple=True, metavar="DIRNAME",
    help="Additional directory name to skip at every sampled commit (repeatable). The usual "
         "venv/build/__pycache__/node_modules set is always skipped.",
)
@click.option(
    "--workspace", type=click.Path(file_okay=False), metavar="DIR",
    help="Where to materialize each historical state. Must be OUTSIDE the repository; "
         "defaults to a temporary directory that is deleted afterwards. The repository "
         "itself is never modified either way -- history is read with `git archive`, "
         "never `git checkout`.",
)
@click.option(
    "--no-progress", is_flag=True,
    help="Suppress the progress bar shown while sampling.",
)
@_MODEL_OPTION
def trend(
    repo, interval, max_samples, output_format, output, exclude, workspace,
    no_progress, model_choice,
):
    """Show how REPO's code smells have changed across its own commit history.

    Samples REPO's history at regular intervals, reconstructs the whole
    codebase as it stood at each sampled commit, and runs the ordinary
    analysis over it -- turning a one-time scan into ongoing code-health
    monitoring.

    Counts are reported against the total number of classes at each point,
    and the trend DIRECTION is read from that rate rather than from the raw
    counts: a codebase that grew can add smelly classes while getting
    proportionally cleaner.

    REPO must be a git repository ROOT. Your working tree is never touched --
    each historical state is read out with `git archive` (a read-only query)
    into a scratch directory outside the repo.
    """
    _activate_model(model_choice)

    on_progress = None
    progress_ctx = None
    if not no_progress:
        # Unlike evaluate's bar, this one always has something to wait for:
        # every sample re-analyzes an entire codebase, so even a small run is
        # tens of seconds and a silent terminal looks like a hang.
        progress_ctx = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total} commits"),
            TextColumn("[dim]{task.fields[label]}[/dim]"),
            console=console,
            transient=True,
        )
        task_id = None

        def on_progress(done, total, label):  # noqa: F811 -- intentional shadow
            nonlocal task_id
            if task_id is None:
                task_id = progress_ctx.add_task(
                    "Analyzing history", total=total, label=label
                )
            progress_ctx.update(task_id, completed=done, label=label)

    try:
        if progress_ctx is not None:
            with progress_ctx:
                report = collect_trend(
                    repo, interval=interval, max_samples=max_samples,
                    exclude=list(exclude), workspace=workspace, on_progress=on_progress,
                )
        else:
            report = collect_trend(
                repo, interval=interval, max_samples=max_samples,
                exclude=list(exclude), workspace=workspace,
            )
    except (ValueError, FileNotFoundError) as exc:
        raise click.UsageError(str(exc)) from exc

    if output_format == "json":
        with open(output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        console.print(
            f"[bold]Sampled[/bold] {report['sample_count']} point(s) across "
            f"{report['commits_walked']} commits of {report['repo']}"
        )
        console.print(f"[bold green]Report written to[/bold green] {output}")
        for note in report.get("notes", []):
            console.print(f"[yellow]Note:[/yellow] {note}")
    else:
        print_trend_report(report)


def main():
    """console_scripts entry point (see pyproject.toml)."""
    cli(prog_name="refactor-scan")


if __name__ == "__main__":
    main()
