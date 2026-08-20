"""refactor-scan CLI: click app definition.

Lives inside the package (rather than as a top-level script) so it can be
wired up as a proper console-script entry point in pyproject.toml and work
identically whether invoked via `python run_scan.py` in a checkout or as the
installed `refactor-scan` command.
"""

from __future__ import annotations

import os
import sys

import rich_click as click

from engine.cli.html_report import write_report
from engine.cli.render import (
    console,
    print_analyze_report,
    print_banner,
    print_model_explanation,
    print_smell_explanation,
    print_why_summary,
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
        {"name": "Commands", "commands": ["analyze", "why", "explain"]},
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


def main():
    """console_scripts entry point (see pyproject.toml)."""
    cli(prog_name="refactor-scan")


if __name__ == "__main__":
    main()
