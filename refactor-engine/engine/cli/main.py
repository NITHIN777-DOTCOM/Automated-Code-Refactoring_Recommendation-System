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

from engine.cli.render import (
    console,
    print_analyze_report,
    print_banner,
    print_model_explanation,
    print_smell_explanation,
)
from engine.parser import DEFAULT_EXCLUDED_DIRS, is_excluded_dir
from engine.pipeline import analyze_path
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
        {"name": "Commands", "commands": ["analyze", "explain"]},
    ]
}


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
def analyze(path, output_format, output, audience, top, exclude):
    """Scan PATH (a directory or a single .py file) for code smells and report the results."""
    results = analyze_path(path, exclude=list(exclude))
    file_count = _file_count(path, exclude=exclude)

    if output_format == "json":
        with open(output, "w", encoding="utf-8") as f:
            f.write(metrics_to_json(results))
        console.print(f"[bold]Scanned[/bold] {len(results)} classes across {file_count} files")
        console.print(f"[bold green]Report written to[/bold green] {output}")
    else:
        print_analyze_report(results, path, file_count, audience=audience, top=top)


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
