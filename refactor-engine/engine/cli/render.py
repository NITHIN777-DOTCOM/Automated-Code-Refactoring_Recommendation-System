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
    elif suggestions and suggestions[0]["type"] == "unsupported_smell_type":
        body_parts.append(Text(suggestions[0]["note"], style="dim"))
    else:
        body_parts.append(Text("No clear extraction boundary found.", style="dim"))

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
    elif suggestions and suggestions[0]["type"] == "unsupported_smell_type":
        parts.append(Text(SMELL_EXPLANATIONS.get(smell, {}).get("fix", ""), style="dim"))
    else:
        parts.append(Text("This class doesn't split apart cleanly along its data -- worth a manual look.", style="dim"))

    return Panel(
        Group(*parts),
        title=f"[bold]{name}[/bold] [dim]({filename})[/dim]",
        border_style=color,
        padding=(1, 2),
    )


def print_analyze_report(results: dict, repo_path: str, file_count: int, audience: str = "dev"):
    print_analyze_summary(results, repo_path, file_count)

    flagged = {n: e for n, e in results.items() if e["predicted_smell"] != "Clean"}
    panel_fn = _simple_class_panel if audience == "simple" else _dev_class_panel

    for name, entry in flagged.items():
        console.print(panel_fn(name, entry))
        console.print()

    if not flagged:
        console.print("[bold green]No code smells detected.[/bold green]")


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
