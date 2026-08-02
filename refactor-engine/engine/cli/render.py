"""All rich-based rendering for the CLI: the startup banner, the analyze
report (dev and simple audiences), and the explain output. Kept separate
from run_scan.py so the click wiring stays readable."""

from __future__ import annotations

import os

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from engine.cli.explanations import MODEL_EXPLANATION, SMELL_EXPLANATIONS

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
        f"[bold {color}]{smell}[/bold {color}]  "
        f"[{_confidence_style(entry['confidence'])}]{entry['confidence']:.0%} confidence[/]"
    )

    body_parts = [header, Text("")]

    suggestions = entry["suggestions"]
    extract_suggestions = [s for s in suggestions if s["type"] == "extract_class"]
    method_suggestions = [s for s in suggestions if s["type"] == "extract_method"]
    move_suggestions = [s for s in suggestions if s["type"] == "move_method"]
    unclear_envy = [s for s in suggestions if s["type"] == "no_clear_envy_target"]
    long_param_suggestions = [s for s in suggestions if s["type"] == "long_parameter_list"]
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
    elif not long_param_suggestions:
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

    parts = [
        Text.from_markup(f"[bold {color}]{smell}[/bold {color}] ({_confidence_word(entry['confidence'])})"),
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
    elif not long_param_suggestions:
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

    console.print(Panel(Group(*parts), title=f"[bold {color}]{name}[/bold {color}]", border_style=color, padding=(1, 2)))


def print_model_explanation():
    parts = [
        Text("What it is:", style="bold"),
        Text(MODEL_EXPLANATION["what_it_is"]),
        Text(""),
        Text("What it looks at:", style="bold"),
        Text(MODEL_EXPLANATION["what_it_looks_at"]),
        Text(""),
        Text("How confidence is calculated:", style="bold"),
        Text(MODEL_EXPLANATION["confidence"]),
        Text(""),
        Text("A caveat worth knowing:", style="bold yellow"),
        Text(MODEL_EXPLANATION["caveat"]),
    ]
    console.print(
        Panel(Group(*parts), title="[bold]How the classifier works[/bold]", border_style="#6A5ACD", padding=(1, 2))
    )
