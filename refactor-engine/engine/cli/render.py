"""All rich-based rendering for the CLI: the startup banner, the analyze
report (dev and simple audiences), and the explain output. Kept separate
from run_scan.py so the click wiring stays readable."""

from __future__ import annotations

import os

from rich import box
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from engine.cli.explanations import CLASSIFIER_CAVEATS, MODEL_EXPLANATION, SMELL_EXPLANATIONS
from engine.thresholds import rule_for

console = Console()

# Deep purple -> cyan, cycled per character for a lightweight "gradient" text
# effect without pulling in a figlet/gradient dependency.
_GRADIENT = ["#8A2BE2", "#7B4FE0", "#6A5ACD", "#4169E1", "#1E90FF", "#00BFFF", "#00CED1"]

_SMELL_COLORS = {
    "Clean": "green",
    "God Class": "red",
    "Data Class": "yellow",
    "Feature Envy": "magenta",
    "Long Method": "orange3",
    "Long Parameter List": "cyan",
    "Duplicate Code": "bright_blue",
}


def _gradient_text(s: str, *, bold: bool = True) -> Text:
    text = Text()
    n = len(_GRADIENT)
    for i, ch in enumerate(s):
        style = f"{'bold ' if bold else ''}{_GRADIENT[i % n]}"
        text.append(ch, style=style)
    return text


def print_banner():
    title = _gradient_text("R E F A C T O R - E N G I N E")
    subtitle = Text("AST-powered code smell detection & refactoring suggestions", style="italic dim")

    description = Text(
        "refactor-engine scans a Python codebase, flags classes with structural "
        "code smells (God Class, Data Class, Feature Envy, Long Method), and "
        "suggests concrete ways to split them up. Built for developers who want "
        "a fast first pass over a codebase before a deeper refactor -- point it "
        "at a repo and get a prioritized list of what to look at first.",
        style="white",
    )

    hint = Text.from_markup("Run [bold cyan]refactor-scan --help[/bold cyan] to see all available commands.")

    body = Group(title, Text(""), subtitle, Text(""), description, Text(""), hint)
    console.print(Panel(body, border_style="#6A5ACD", padding=(1, 4), title="[bold]v1[/bold]", title_align="right"))


def _confidence_style(confidence: float) -> str:
    if confidence >= 0.8:
        return "bold green"
    if confidence >= 0.6:
        return "bold yellow"
    return "bold red"


def _dev_headline_metric(smell: str, confidence: float) -> str:
    """Duplicate Code carries the strongest pair's similarity in the
    confidence slot (see the pipeline), and "100% confidence" would read as
    certainty about exactly the finding this tool is least certain about.
    Name the number for what it actually is."""
    if smell == "Duplicate Code":
        return f"[bold bright_blue]{confidence:.0%} structural match[/bold bright_blue]"
    return f"[{_confidence_style(confidence)}]{confidence:.0%} confidence[/]"


def _confidence_word(confidence: float) -> str:
    if confidence >= 0.8:
        return "high confidence"
    if confidence >= 0.6:
        return "moderate confidence"
    return "low confidence — worth a second look"


def print_analyze_summary(results: dict, repo_path: str, file_count: int):
    class_count = len(results)
    flagged = {n: e for n, e in results.items() if e["predicted_smell"] != "Clean"}
    clean_count = class_count - len(flagged)

    summary = Table.grid(padding=(0, 2))
    summary.add_column(justify="right", style="bold")
    summary.add_column()
    summary.add_row("Repo:", repo_path)
    summary.add_row("Files scanned:", str(file_count))
    summary.add_row("Classes analyzed:", str(class_count))
    summary.add_row("Flagged:", f"[bold red]{len(flagged)}[/bold red]")
    summary.add_row("Clean:", f"[bold green]{clean_count}[/bold green]")

    console.print(Panel(summary, title="[bold]Scan Summary[/bold]", border_style="#6A5ACD"))
    console.print()


def _group_by_method(method_suggestions: list[dict]) -> list[tuple[str, list[dict]]]:
    """Extract Method suggestions arrive flat but read best grouped under the
    method they came from, in first-seen order."""
    grouped: dict[str, list[dict]] = {}
    for s in method_suggestions:
        grouped.setdefault(s["source_method"], []).append(s)
    return list(grouped.items())


def _dev_class_panel(name: str, entry: dict) -> Panel:
    smell = entry["predicted_smell"]
    color = _SMELL_COLORS.get(smell, "white")
    filename = os.path.basename(entry["file_path"])

    header = Text.from_markup(
        f"[bold {color}]{smell}[/bold {color}]  {_dev_headline_metric(smell, entry['confidence'])}"
    )

    body_parts = [header, Text("")]

    suggestions = entry["suggestions"]
    extract_suggestions = [s for s in suggestions if s["type"] == "extract_class"]
    method_suggestions = [s for s in suggestions if s["type"] == "extract_method"]
    move_suggestions = [s for s in suggestions if s["type"] == "move_method"]
    unclear_envy = [s for s in suggestions if s["type"] == "no_clear_envy_target"]
    long_param_suggestions = [s for s in suggestions if s["type"] == "long_parameter_list"]
    duplicate_suggestions = [s for s in suggestions if s["type"] == "duplicate_code"]
    unsupported_suggestions = [s for s in suggestions if s["type"] == "unsupported_smell_type"]

    printed_primary_finding = False

    if extract_suggestions:
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1, 0, 0))
        table.add_column("Extract")
        table.add_column("→ New class")
        table.add_column("Shared fields")
        for s in extract_suggestions:
            table.add_row(
                ", ".join(s["methods_to_extract"]),
                f"[bold cyan]{s['suggested_name']}[/bold cyan]",
                ", ".join(s["shared_fields"]),
            )
        body_parts.append(table)
        printed_primary_finding = True
    elif move_suggestions or unclear_envy:
        if move_suggestions:
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1, 0, 0))
            table.add_column("Move method")
            table.add_column("→ Into class")
            table.add_column("Calls out / own data")
            for s in move_suggestions:
                table.add_row(
                    s["source_method"],
                    f"[bold cyan]{s['target_class']}[/bold cyan]",
                    f"{s['external_references']} / {s['own_data_uses']}",
                )
            body_parts.append(table)
            printed_primary_finding = True
        for s in unclear_envy:
            if printed_primary_finding:
                body_parts.append(Text(""))
            body_parts.append(Text(s["note"], style="dim"))
            printed_primary_finding = True
    elif method_suggestions:
        for source_method, group in _group_by_method(method_suggestions):
            if printed_primary_finding:
                body_parts.append(Text(""))
            body_parts.append(
                Text.from_markup(
                    f"[bold]{source_method}()[/bold] [dim]— {group[0]['method_line_count']} lines, "
                    f"{len(group)} logical steps[/dim]"
                )
            )
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1, 0, 0))
            table.add_column("Lines")
            table.add_column("→ Helper method")
            table.add_column("Handles")
            for s in group:
                params = ", ".join(s["suggested_params"])
                table.add_row(
                    f"{s['lines'][0]}-{s['lines'][1]}",
                    f"[bold cyan]{s['suggested_name']}({params})[/bold cyan]",
                    s["description"],
                )
            body_parts.append(table)
            printed_primary_finding = True
    elif unsupported_suggestions:
        body_parts.append(Text(unsupported_suggestions[0]["note"], style="dim"))
        printed_primary_finding = True
    elif not long_param_suggestions and not duplicate_suggestions:
        body_parts.append(Text("No clear extraction boundary found.", style="dim"))

    # Additive, not exclusive: a class can be flagged for something else AND
    # separately have a method with too many parameters.
    for s in long_param_suggestions:
        if printed_primary_finding:
            body_parts.append(Text(""))
        body_parts.append(
            Text.from_markup(f"[bold cyan]Long parameter list:[/bold cyan] {', '.join(s['methods'])}")
        )
        body_parts.append(Text(s["note"], style="dim"))
        printed_primary_finding = True

    # Also additive, and the similarity column is printed rather than hidden
    # behind a verdict: the score is what makes the finding checkable, and a
    # borderline 0.81 should look different to the reader than a 1.0.
    for s in duplicate_suggestions:
        if printed_primary_finding:
            body_parts.append(Text(""))
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1, 0, 0))
        table.add_column("Similar methods")
        table.add_column("Structural similarity")
        for pair in s["pairs"]:
            method_a, method_b = pair["methods"]
            table.add_row(
                f"[bold bright_blue]{method_a}()[/bold bright_blue] ~ "
                f"[bold bright_blue]{method_b}()[/bold bright_blue]",
                f"{pair['similarity']:.0%}",
            )
        body_parts.append(table)
        body_parts.append(Text(""))
        body_parts.append(Text(s["note"], style="dim"))
        printed_primary_finding = True

    return Panel(
        Group(*body_parts),
        title=f"[bold]{name}[/bold] [dim]({filename})[/dim]",
        border_style=color,
        padding=(1, 2),
    )


def _simple_class_panel(name: str, entry: dict) -> Panel:
    smell = entry["predicted_smell"]
    color = _SMELL_COLORS.get(smell, "white")
    filename = os.path.basename(entry["file_path"])
    what = SMELL_EXPLANATIONS.get(smell, {}).get("what", "")

    # Same reasoning as _dev_headline_metric: "high confidence" would overstate
    # a similarity score.
    if smell == "Duplicate Code":
        headline_metric = f"the closest pair is {entry['confidence']:.0%} alike in shape"
    else:
        headline_metric = _confidence_word(entry["confidence"])

    parts = [
        Text.from_markup(f"[bold {color}]{smell}[/bold {color}] ({headline_metric})"),
        Text(""),
        Text(what, style="white"),
        Text(""),
    ]

    suggestions = entry["suggestions"]
    extract_suggestions = [s for s in suggestions if s["type"] == "extract_class"]
    method_suggestions = [s for s in suggestions if s["type"] == "extract_method"]
    move_suggestions = [s for s in suggestions if s["type"] == "move_method"]
    unclear_envy = [s for s in suggestions if s["type"] == "no_clear_envy_target"]
    long_param_suggestions = [s for s in suggestions if s["type"] == "long_parameter_list"]
    duplicate_suggestions = [s for s in suggestions if s["type"] == "duplicate_code"]
    unsupported_suggestions = [s for s in suggestions if s["type"] == "unsupported_smell_type"]

    printed_primary_finding = False

    if extract_suggestions:
        parts.append(Text("Suggested next step:", style="bold"))
        for s in extract_suggestions:
            methods = ", ".join(s["methods_to_extract"])
            parts.append(
                Text.from_markup(
                    f"  • Move [bold]{methods}[/bold] into a new class "
                    f"(maybe called [bold cyan]{s['suggested_name']}[/bold cyan]) -- "
                    f"they work together on data the rest of the class doesn't use."
                )
            )
        printed_primary_finding = True
    elif move_suggestions or unclear_envy:
        if move_suggestions:
            parts.append(Text("Suggested next step:", style="bold"))
            for s in move_suggestions:
                parts.append(
                    Text.from_markup(
                        f"  • [bold]{s['source_method']}[/bold] works with "
                        f"[bold cyan]{s['target_class']}[/bold cyan]'s data far more than "
                        f"its own ({s['external_references']} uses vs "
                        f"{s['own_data_uses']}) -- it probably belongs in "
                        f"[bold cyan]{s['target_class']}[/bold cyan]."
                    )
                )
            printed_primary_finding = True
        for s in unclear_envy:
            if printed_primary_finding:
                parts.append(Text(""))
            parts.append(Text(s["note"], style="dim"))
            printed_primary_finding = True
    elif method_suggestions:
        for source_method, group in _group_by_method(method_suggestions):
            parts.append(
                Text.from_markup(
                    f"[bold]{source_method}()[/bold] is "
                    f"{group[0]['method_line_count']} lines long and does "
                    f"{len(group)} separate things. Suggested next steps:"
                )
            )
            for s in group:
                parts.append(
                    Text.from_markup(
                        f"  • Lines {s['lines'][0]}-{s['lines'][1]} handle "
                        f"{s['description']} -- pull them out into their own smaller "
                        f"method (maybe called [bold cyan]{s['suggested_name']}[/bold cyan])."
                    )
                )
        printed_primary_finding = True
    elif unsupported_suggestions:
        parts.append(Text(SMELL_EXPLANATIONS.get(smell, {}).get("fix", ""), style="dim"))
        printed_primary_finding = True
    elif not long_param_suggestions and not duplicate_suggestions:
        parts.append(Text("This class doesn't split apart cleanly along its data -- worth a manual look.", style="dim"))

    # Additive, not exclusive: a class can be flagged for something else AND
    # separately have a method with too many parameters.
    for s in long_param_suggestions:
        if printed_primary_finding:
            parts.append(Text(""))
        methods = ", ".join(s["methods"])
        parts.append(
            Text.from_markup(
                f"  • [bold]{methods}[/bold] {'takes' if len(s['methods']) == 1 else 'take'} too many "
                f"parameters -- consider grouping related ones into a single object."
            )
        )
        printed_primary_finding = True

    # Same caveat as the dev panel, in plain language: this reader is the one
    # least likely to know that "similar shape" and "does the same thing" are
    # different claims, so the uncertainty is stated as part of the finding
    # rather than tucked away underneath it.
    for s in duplicate_suggestions:
        if printed_primary_finding:
            parts.append(Text(""))
        for pair in s["pairs"]:
            method_a, method_b = pair["methods"]
            parts.append(
                Text.from_markup(
                    f"  • [bold]{method_a}[/bold] and [bold]{method_b}[/bold] are written "
                    f"almost identically ({pair['similarity']:.0%} the same shape, ignoring the "
                    f"names used inside them)."
                )
            )
        parts.append(Text(""))
        parts.append(
            Text(
                "This one is a guess based on the shape of the code, not proof that the two "
                "methods do the same thing -- methods written in the same style often look alike "
                "without being copies. Worth reading both before merging them: if they really do "
                "the same work, one shared method could replace both.",
                style="dim",
            )
        )
        printed_primary_finding = True

    return Panel(
        Group(*parts),
        title=f"[bold]{name}[/bold] [dim]({filename})[/dim]",
        border_style=color,
        padding=(1, 2),
    )


# no_clear_envy_target is deliberately absent: it's an honest "couldn't tell"
# note, not something the reader can act on, so it shouldn't outrank a
# concrete suggestion in the --top ranking.
_ACTIONABLE_SUGGESTION_TYPES = {"extract_class", "extract_method", "move_method"}


def _severity_key(entry: dict):
    # Sorted ascending, so lower keys print first: classes with a concrete
    # extraction suggestion (something actionable) outrank a bare
    # no_clear_split/unsupported_smell_type note at similar confidence, and
    # within each group, higher confidence outranks lower.
    actionable = any(s["type"] in _ACTIONABLE_SUGGESTION_TYPES for s in entry["suggestions"])
    return (0 if actionable else 1, -entry["confidence"])


def _display_path(file_path: str, repo_path: str) -> str:
    """Unused imports are listed per file rather than per class, so a bare
    basename (what the per-class panels use) is more likely to collide
    between files -- show the path relative to the scanned root instead,
    falling back to the raw path if it isn't actually underneath repo_path."""
    try:
        relative = os.path.relpath(file_path, start=repo_path)
    except ValueError:
        return file_path
    return file_path if relative.startswith("..") else relative


def print_unused_imports_section(unused_imports: dict, repo_path: str):
    if not unused_imports:
        return

    total = sum(len(items) for items in unused_imports.values())
    console.print(
        f"[bold]Unused imports[/bold] [dim]({total} across {len(unused_imports)} "
        f"file{'s' if len(unused_imports) != 1 else ''})[/dim]"
    )
    for file_path, items in unused_imports.items():
        console.print(f"  [cyan]{_display_path(file_path, repo_path)}[/cyan]")
        for item in items:
            console.print(f"    line {item['line']}: [dim]{item['display']}[/dim]")
    console.print()


def print_analyze_report(
    results: dict,
    repo_path: str,
    file_count: int,
    audience: str = "dev",
    top: int = 10,
    unused_imports: dict | None = None,
):
    print_analyze_summary(results, repo_path, file_count)

    flagged = {n: e for n, e in results.items() if e["predicted_smell"] != "Clean"}
    ranked = sorted(flagged.items(), key=lambda item: _severity_key(item[1]))

    shown = ranked if not top else ranked[:top]
    panel_fn = _simple_class_panel if audience == "simple" else _dev_class_panel

    for name, entry in shown:
        console.print(panel_fn(name, entry))
        console.print()

    remaining = len(ranked) - len(shown)
    if remaining > 0:
        console.print(
            f"[dim]...and {remaining} more flagged classes. Use [bold]--top 0[/bold] to show all, "
            f"or [bold]--format json[/bold] for the full machine-readable output.[/dim]"
        )
        console.print()

    if not flagged:
        console.print("[bold green]No code smells detected.[/bold green]")
        console.print()

    print_unused_imports_section(unused_imports or {}, repo_path)


def print_smell_explanation(smell_name: str):
    matches = {k: v for k, v in SMELL_EXPLANATIONS.items() if k.lower() == smell_name.lower()}
    if not matches:
        available = ", ".join(SMELL_EXPLANATIONS.keys())
        console.print(f"[bold red]Unknown smell:[/bold red] {smell_name!r}. Available: {available}")
        return

    name, info = next(iter(matches.items()))
    color = _SMELL_COLORS.get(name, "white")

    parts = []
    if info.get("aka"):
        parts.append(Text(f"Also known as: {info['aka']}", style="dim italic"))
        parts.append(Text(""))
    parts.append(Text("What it is:", style="bold"))
    parts.append(Text(info["what"]))
    parts.append(Text(""))
    parts.append(Text("Why it matters:", style="bold"))
    parts.append(Text(info["why_it_matters"]))
    parts.append(Text(""))
    parts.append(Text("What to do about it:", style="bold"))
    parts.append(Text(info["fix"]))

    # Only the checks whose result is genuinely uncertain carry one, so it
    # renders as a distinct warning rather than boilerplate on every smell.
    if info.get("caveat"):
        parts.append(Text(""))
        parts.append(Text("A caveat worth knowing:", style="bold yellow"))
        parts.append(Text(info["caveat"]))

    console.print(Panel(Group(*parts), title=f"[bold {color}]{name}[/bold {color}]", border_style=color, padding=(1, 2)))


def _benchmark_table(benchmarks) -> Table:
    """Measured value beside the published threshold it is judged against.

    The "Meets rule?" column is worded rather than symbolic on purpose.
    Several published conjuncts are LOWER bounds -- a Data Class requires
    WMC < 31 -- so a warning glyph next to "WMC 11" would read as "11 is too
    high" when what it means is "this is low enough to satisfy the Data Class
    condition". A yes/no against the stated rule cannot be misread that way.

    The citation travels in the row rather than in a footnote: the point of
    this section is that a reader can check a claim against the literature
    without leaving the output.
    """
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="bold dim")
    table.add_column("Metric", no_wrap=True)
    table.add_column("Measured", justify="right", no_wrap=True)
    table.add_column("Published", no_wrap=True)
    table.add_column("Meets rule?", justify="center", no_wrap=True)
    table.add_column("Source", style="dim", overflow="fold")

    for b in benchmarks:
        meets = "[bold red]yes[/bold red]" if b.exceeds else "[green]no[/green]"
        value_style = "bold red" if b.exceeds else "green"
        name = b.plain_name + ("[dim] *[/dim]" if b.is_proxy else "")
        table.add_row(
            name,
            f"[{value_style}]{_fmt_metric(b.value)}[/{value_style}]",
            f"{b.operator} {_fmt_metric(b.threshold)}",
            meets,
            f"{b.published_metric} — {b.source_short}",
        )
    return table


def _fmt_metric(value) -> str:
    if isinstance(value, float) and not float(value).is_integer():
        return f"{value:.2f}"
    return str(int(value))


def print_why_summary(reasoning):
    """The terminal half of `refactor-scan why`: the headline, the one factor
    that mattered most, and how the class measures against published
    thresholds.

    The per-class prose stays capped at three lines no matter how much
    reasoning sits behind it -- a file with a dozen flagged classes still has
    to fit on one screen, and anything longer belongs in the HTML report.
    The benchmark table is the deliberate exception: it is the part a reader
    is most likely to have to defend to someone else, so it is shown up
    front rather than hidden behind --output."""
    for cls in reasoning.classes:
        color = _SMELL_COLORS.get(cls.smell, "white")
        console.print(
            f"[bold]{cls.name}[/bold] — [bold {color}]{cls.smell}[/bold {color}] "
            f"[dim]({cls.headline_metric})[/dim]"
        )

        # A grid rather than two print()s so a long reason wraps underneath
        # itself instead of back to the left margin, which on a narrow terminal
        # makes the second line look like a new finding.
        body = Table.grid(padding=(0, 1))
        body.add_column(style="bold", no_wrap=True)
        body.add_column(overflow="fold")
        body.add_row("Top reason:", cls.top_reason)

        # The compressed form, not the full sentence: on a God Class with four
        # extraction candidates the rest is detail, and it belongs in the report.
        body.add_row("Suggestion:" if cls.is_flagged else "Verdict:", cls.short_suggestion)

        console.print(Padding(body, (0, 0, 1, 2), expand=False))

        if cls.benchmarks:
            rule = rule_for(cls.smell)
            header = "[bold]Against published thresholds[/bold]"
            if rule:
                # The formula is what turns a list of independent comparisons
                # into a verdict -- without it, a reader cannot tell whether
                # the rows are ANDed, ORed, or merely informational.
                header += f"  [dim]rule: {rule.formula}[/dim]"
            console.print(Padding(header, (0, 0, 0, 2)))
            console.print(Padding(_benchmark_table(cls.benchmarks), (0, 0, 0, 4), expand=False))
            if any(b.is_proxy for b in cls.benchmarks):
                console.print(
                    Padding(
                        "[dim]* proxy metric — our engine has no exact equivalent; "
                        "see engine/thresholds.py[/dim]",
                        (0, 0, 0, 4),
                    )
                )
            console.print()

    console.print(
        "[dim]Thresholds are published values, not this tool's opinion — "
        "see [bold]engine/thresholds.py[/bold] for each one's source.[/dim]"
    )

    console.print(
        "[dim]Run the same command with [bold]--output report.html[/bold] for the full "
        "reasoning behind each of these.[/dim]"
    )


def print_model_explanation(caveat: str | None = None, what_it_looks_at: str | None = None):
    caveat = caveat if caveat is not None else MODEL_EXPLANATION["caveat"]
    # Both default to the synthetic wording, so calling this with no arguments
    # renders exactly what it always has.
    if what_it_looks_at is None:
        what_it_looks_at = MODEL_EXPLANATION["what_it_looks_at"]
    parts = [
        Text("What it is:", style="bold"),
        Text(MODEL_EXPLANATION["what_it_is"]),
        Text(""),
        Text("What it looks at:", style="bold"),
        Text(what_it_looks_at),
        Text(""),
        Text("How confidence is calculated:", style="bold"),
        Text(MODEL_EXPLANATION["confidence"]),
        Text(""),
        Text("A caveat worth knowing:", style="bold yellow"),
        Text(caveat),
    ]
    console.print(
        Panel(Group(*parts), title="[bold]How the classifier works[/bold]", border_style="#6A5ACD", padding=(1, 2))
    )


# ---------------------------------------------------------------------------
# evaluate: our detector vs real refactoring history
# ---------------------------------------------------------------------------


def _hit_mark(hit: bool) -> Text:
    return Text("HIT ", style="bold green") if hit else Text("miss", style="dim")


def print_evaluation_report(results: dict, show_commits: bool = True) -> None:
    """Render the refactor-history evaluation.

    The caveat is printed FIRST and again under the headline numbers, on
    purpose: this is a lower bound, and a reader skimming for a percentage
    must not be able to reach one without passing the reason it is not a
    recall figure.
    """
    console.print()
    console.print(
        Panel(
            Text(results["caveat"], style="yellow"),
            title="[bold yellow]Read this before the numbers[/bold yellow]",
            border_style="yellow",
        )
    )

    for repo in results["repos"]:
        _print_repo_notes(repo)

    if show_commits:
        for repo in results["repos"]:
            _print_repo_commits(repo)

    _print_evaluation_summary(results)


def _print_repo_notes(repo: dict) -> None:
    """A clear, specific explanation in place of a silently-empty result --
    printed BEFORE the per-commit table so a reader sees why a table might be
    empty or short before they reach it, not after being confused by it."""
    for note in repo.get("notes", []):
        console.print()
        console.print(
            Panel(
                Text(note, style="yellow"),
                title=f"[bold yellow]{repo['repo']}[/bold yellow]",
                border_style="yellow",
            )
        )


def _print_repo_commits(repo: dict) -> None:
    if not repo["commits"]:
        # The explanation already printed via _print_repo_notes -- an empty
        # table here would just be confusing noise on top of it.
        return

    table = Table(
        title=f"[bold]{repo['repo']}[/bold] — per-commit result",
        title_justify="left",
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("commit", style="dim", no_wrap=True)
    table.add_column("detected by", no_wrap=True)
    table.add_column("refactor target", overflow="fold")
    table.add_column("signal", no_wrap=True)
    table.add_column("we flagged it as", no_wrap=True)
    table.add_column("", no_wrap=True)

    for commit in repo["commits"]:
        detected = "+".join(commit["detected_by"])
        if not commit["has_structural_target"]:
            table.add_row(
                commit["short_hash"],
                detected,
                Text("(no structural target — keyword only)", style="dim italic"),
                "—",
                "—",
                Text("n/a", style="dim"),
            )
            continue

        for i, target in enumerate(commit["targets"]):
            table.add_row(
                commit["short_hash"] if i == 0 else "",
                detected if i == 0 else "",
                f"{target['class']}  [dim]({target['file']})[/dim]",
                target["signal"].replace("_", " "),
                (
                    f"[bold]{target['flagged_as']}[/bold] ({target['confidence']})"
                    if target["flagged"]
                    else Text("not flagged", style="dim")
                ),
                _hit_mark(target["flagged"]),
            )

    console.print()
    console.print(table)


def _rate(value) -> str:
    return "n/a" if value is None else f"{value:.1%}"


# ---------------------------------------------------------------------------
# trend
# ---------------------------------------------------------------------------

_DIRECTION_STYLES = {
    "improving": ("green", "improving", "▼"),
    "worsening": ("red", "worsening", "▲"),
    "flat": ("yellow", "holding steady", "→"),
}


def _delta_mark(delta) -> Text:
    """Per-row movement in smell RATE against the previous sample.

    Down is good here -- the number is a proportion of classes flagged -- so
    the arrow directions are deliberately the opposite way round from a
    chart where up means progress. The colour reinforces it: green falls,
    red rises.
    """
    if delta is None:
        return Text("—", style="dim")
    if abs(delta) < 0.005:
        return Text("→", style="dim")
    if delta < 0:
        return Text(f"▼{abs(delta):.1%}", style="green")
    return Text(f"▲{delta:.1%}", style="red")


def _count_cell(value: int, smell: str) -> Text:
    """A zero is written as a dim dot, not "0".

    A wide grid of literal zeros reads as noise and hides the columns that
    actually have counts in them; the point of this table is which numbers
    move, and a placeholder makes those stand out.
    """
    if not value:
        return Text("·", style="dim")
    return Text(str(value), style=_SMELL_COLORS.get(smell, "white"))


def _trend_table(report: dict) -> Table:
    """Eleven columns of numbers have to survive an 80-column terminal, so
    this is laid out tightly on purpose: no vertical rules (box=SIMPLE_HEAD),
    abbreviated smell headers, and the period label ("2024-07") in place of
    the full commit date where the interval already implies one. The
    alternative -- letting rich ellipsize -- turns "52.8%" into "52…" and
    makes the whole table useless."""
    samples = report["samples"]

    # Long Parameter List and Duplicate Code are uncommon, and an all-zero
    # column costs width that the smell columns need. Shown only when it
    # would carry a number.
    show_other = any(s["other_smells"] for s in samples)

    # In commits mode there are no calendar periods, so the full date is the
    # only label available.
    period_header = "date" if report["interval"] == "commits" else "period"

    table = Table(
        title=f"[bold]{report['repo']}[/bold] — code smells over time "
              f"([dim]{report['interval']}, {report['sample_count']} sample"
              f"{'' if report['sample_count'] == 1 else 's'}[/dim])",
        title_justify="left",
        header_style="bold cyan",
        caption="smelly = flagged / total classes analyzed at that commit · "
                "god/data/envy/long = God Class, Data Class, Feature Envy, Long Method · "
                "oth = Long Parameter List / Duplicate Code",
        caption_justify="left",
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        expand=False,
    )
    table.add_column(period_header, style="dim", no_wrap=True)
    table.add_column("commit", style="dim", no_wrap=True)
    table.add_column("god", justify="right", no_wrap=True)
    table.add_column("data", justify="right", no_wrap=True)
    table.add_column("envy", justify="right", no_wrap=True)
    table.add_column("long", justify="right", no_wrap=True)
    if show_other:
        table.add_column("oth", justify="right", no_wrap=True)
    table.add_column("smelly", justify="right", no_wrap=True)
    table.add_column("rate", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)

    for sample in samples:
        smells = sample["smells"]
        row = [
            sample["period"] if report["interval"] != "commits" else sample["date"],
            sample["short_hash"][:7],
            _count_cell(smells["God Class"], "God Class"),
            _count_cell(smells["Data Class"], "Data Class"),
            _count_cell(smells["Feature Envy"], "Feature Envy"),
            _count_cell(smells["Long Method"], "Long Method"),
        ]
        if show_other:
            row.append(_count_cell(sample["other_smells"], "Long Parameter List"))
        row += [
            # The denominator rides in the same cell as the count. Keeping
            # them in separate columns let a reader take "44" as the whole
            # story; "44/91" cannot be read without its scale.
            Text.assemble(
                (str(sample["smelly_classes"]), "bold"),
                ("/", "dim"),
                (str(sample["total_classes"]), "dim"),
            ),
            _rate(sample["smell_rate"]),
            _delta_mark(sample["rate_delta"]),
        ]
        table.add_row(*row)

    return table


def _trend_summary_panel(report: dict) -> Panel:
    summary = report["summary"]
    direction = summary["direction"]
    colour, word, arrow = _DIRECTION_STYLES.get(direction, ("dim", "no direction yet", "—"))

    body = Table(box=None, show_header=False, pad_edge=False)
    body.add_column(style="bold")
    body.add_column(justify="right")

    if summary["first"] and summary["last"]:
        body.add_row(
            "Span",
            f"{summary['first']['date']} → {summary['last']['date']}"
            f"  [dim]({report['sample_count']} point"
            f"{'' if report['sample_count'] == 1 else 's'})[/dim]",
        )

    if summary["sparkline"]:
        # The shape of the smell rate, low block to high block. It sits with
        # the exact percentages above rather than replacing them -- it shows
        # the path between the endpoints, which two numbers cannot.
        body.add_row("Smell rate", f"[{colour}]{summary['sparkline']}[/{colour}]")

    if direction is not None:
        change = summary["rate_change"]
        body.add_row(
            "Direction",
            f"[bold {colour}]{arrow} {word}[/bold {colour}]"
            + (f"  [dim]({change:+.1%} rate)[/dim]" if change is not None else ""),
        )

    body.add_row("", "")
    body.add_row("Classes", f"{summary['class_change']:+d}")
    body.add_row("Smelly classes", f"{summary['smelly_change']:+d}")

    for smell, change in summary["by_smell_change"].items():
        if change:
            smell_colour = _SMELL_COLORS.get(smell, "white")
            body.add_row(f"  [{smell_colour}]{smell}[/{smell_colour}]", f"{change:+d}")

    return Panel(
        body,
        title=f"[bold]First → last[/bold]",
        border_style=colour if direction else "cyan",
        expand=False,
    )


def print_trend_report(report: dict) -> None:
    """Render a history trend: the per-sample table, then the movement.

    Notes come FIRST, for the same reason they do in the evaluation report:
    a short table on a shallow clone looks identical to a short table on a
    young project, and the reader needs to know which one they are looking
    at before they start reading numbers off it.
    """
    for note in report.get("notes", []):
        console.print()
        console.print(
            Panel(
                Text(note, style="yellow"),
                title=f"[bold yellow]{report['repo']}[/bold yellow]",
                border_style="yellow",
            )
        )

    if not report["samples"]:
        console.print()
        console.print("[yellow]No commits could be sampled — nothing to plot.[/yellow]")
        return

    console.print()
    console.print(_trend_table(report))
    console.print()
    console.print(_trend_summary_panel(report))
    console.print()
    console.print(
        Text(
            "Rate is smelly classes ÷ total classes. Direction is read from the RATE, not the "
            "raw count — a codebase that grew can add smelly classes while getting "
            "proportionally cleaner.",
            style="dim italic",
        )
    )
    console.print()


def _print_evaluation_summary(results: dict) -> None:
    overall = results["overall"]

    headline = Table(box=None, show_header=False, pad_edge=False)
    headline.add_column(style="bold")
    headline.add_column(justify="right")
    headline.add_row("Commits evaluated", str(overall["commits_evaluated"]))
    headline.add_row(
        "  with a structural target (checkable)", str(overall["commits_with_structural_target"])
    )
    headline.add_row(
        "  keyword-only (no verifiable target)",
        str(overall["commits_without_structural_target"]),
    )
    headline.add_row("", "")
    headline.add_row(
        "[green]TARGETED hit rate[/green] (commits)",
        f"[bold green]{overall['commits_hit']}/{overall['commits_with_structural_target']}"
        f" = {_rate(overall['targeted_hit_rate'])}[/bold green]",
    )
    headline.add_row(
        "TARGETED hit rate (individual targets)",
        f"{overall['targets_flagged']}/{overall['targets_total']}"
        f" = {_rate(overall['target_level_hit_rate'])}",
    )
    headline.add_row(
        "[dim]FILE-LEVEL hit rate (much weaker)[/dim]",
        f"[dim]{overall['file_level_hits']}/{overall['commits_evaluated']}"
        f" = {_rate(overall['file_level_hit_rate'])}[/dim]",
    )

    console.print()
    console.print(
        Panel(headline, title="[bold]Aggregate[/bold]", border_style="cyan", expand=False)
    )

    if overall["by_smell_type"]:
        smell_table = Table(
            title="Which smell flagged the refactor target",
            title_justify="left",
            header_style="bold cyan",
            expand=False,
        )
        smell_table.add_column("smell")
        smell_table.add_column("targets caught", justify="right")
        for smell, count in overall["by_smell_type"].items():
            colour = _SMELL_COLORS.get(smell, "white")
            smell_table.add_row(f"[{colour}]{smell}[/{colour}]", str(count))
        console.print()
        console.print(smell_table)

    if overall["by_signal"]:
        signal_table = Table(
            title="By what the real commit did",
            title_justify="left",
            header_style="bold cyan",
            expand=False,
        )
        signal_table.add_column("structural signal")
        signal_table.add_column("targets", justify="right")
        signal_table.add_column("flagged", justify="right")
        signal_table.add_column("rate", justify="right")
        for signal, stats in sorted(overall["by_signal"].items()):
            rate = stats["flagged"] / stats["targets"] if stats["targets"] else None
            signal_table.add_row(
                signal.replace("_", " "),
                str(stats["targets"]),
                str(stats["flagged"]),
                _rate(rate),
            )
        console.print()
        console.print(signal_table)

    if overall["by_detector"]:
        detector_table = Table(
            title="By how the commit was found (these are different populations)",
            title_justify="left",
            header_style="bold cyan",
            expand=False,
        )
        detector_table.add_column("detector")
        detector_table.add_column("commits", justify="right")
        detector_table.add_column("checkable", justify="right")
        detector_table.add_column("hits", justify="right")
        detector_table.add_column("targeted rate", justify="right")
        detector_table.add_column("file-level hits", justify="right")
        for name, stats in sorted(overall["by_detector"].items()):
            rate = stats["hits"] / stats["with_target"] if stats["with_target"] else None
            detector_table.add_row(
                name,
                str(stats["commits"]),
                str(stats["with_target"]),
                str(stats["hits"]),
                _rate(rate),
                str(stats["file_level_hits"]),
            )
        console.print()
        console.print(detector_table)

    console.print()
    console.print(
        Text(results["caveat"], style="yellow italic"),
        style="yellow",
    )
    console.print()
