"""Command-line interface.

    blastradius diff  BASE HEAD                       what changed in the spec
    blastradius scan  -C consumers/                   what HTTP calls consumers make
    blastradius check BASE HEAD -C consumers/         who will break (the main command)
    blastradius export myapp.main:app -o openapi.json export a FastAPI app's spec

Exit codes (so CI can pass or fail a pull request):
    0  OK
    1  the --fail-on condition was met
    2  bad input (unreadable spec, missing folder, ...)
"""

from __future__ import annotations

import importlib
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .diff import diff_specs
from .impact import analyze
from .models import CallSite, Impact, Severity
from .report import change_to_dict, print_analysis, print_changes, to_json, to_markdown
from .scanner import scan_consumer
from .spec import Spec, SpecError

app = typer.Typer(
    help="Predict which services an API change will break, before it ships.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err = Console(stderr=True)


class Format(str, Enum):
    table = "table"
    markdown = "markdown"
    json = "json"


class FailOn(str, Enum):
    impacted = "impacted"  # fail only if a consumer will break (default)
    breaking = "breaking"  # fail on any breaking change in the spec, used or not
    never = "never"  # report only


BaseArg = Annotated[Path, typer.Argument(help="The current OpenAPI spec (what's live).")]
HeadArg = Annotated[Path, typer.Argument(help="The proposed OpenAPI spec (this PR).")]
ConsumerOpt = Annotated[
    list[Path] | None,
    typer.Option("--consumer", "-c", help="A consumer service's source folder. Repeatable."),
]
ConsumersDirOpt = Annotated[
    Path | None,
    typer.Option("--consumers-dir", "-C", help="A folder whose subfolders are each a consumer."),
]
FormatOpt = Annotated[Format, typer.Option("--format", "-f", help="Output format.")]
OutputOpt = Annotated[
    Path | None, typer.Option("--output", "-o", help="Write the report to a file.")
]


def _fail(message: str) -> typer.Exit:
    err.print(f"[bold red]error:[/] {escape(message)}")
    return typer.Exit(2)


def _load(path: Path) -> Spec:
    try:
        return Spec.load(path)
    except SpecError as e:
        raise _fail(str(e)) from e


def _consumer_dirs(consumers: list[Path] | None, consumers_dir: Path | None) -> list[Path]:
    dirs = list(consumers or [])
    if consumers_dir is not None:
        if not consumers_dir.is_dir():
            raise _fail(f"not a folder: {consumers_dir}")
        dirs += sorted(
            p for p in consumers_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    for d in dirs:
        if not d.is_dir():
            raise _fail(f"not a folder: {d}")
    return dirs


def _scan(dirs: list[Path]) -> list[CallSite]:
    return [site for d in dirs for site in scan_consumer(d)]


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        print(text, end="")  # plain print: no Rich formatting in Markdown/JSON
    else:
        output.write_text(text, encoding="utf-8")
        err.print(f"[dim]Written to {escape(str(output))}[/]")


def _version(value: bool) -> None:
    if value:
        print(f"blastradius {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    pass


@app.command()
def diff(
    base: BaseArg,
    head: HeadArg,
    show_all: Annotated[bool, typer.Option("--all", help="Also list safe changes.")] = False,
    fmt: FormatOpt = Format.table,
) -> None:
    """Show what changed between two specs, without looking at consumers."""
    changes = diff_specs(_load(base), _load(head))
    if fmt is Format.table:
        print_changes(changes, console, show_safe=show_all)
    elif fmt is Format.json:
        print(json.dumps([change_to_dict(c) for c in changes], indent=2))
    else:
        raise _fail("diff supports --format table or json")
    raise typer.Exit(1 if any(c.severity is Severity.BREAKING for c in changes) else 0)


@app.command()
def scan(consumer: ConsumerOpt = None, consumers_dir: ConsumersDirOpt = None) -> None:
    """List the HTTP calls found in consumer code (useful for debugging matches)."""
    dirs = _consumer_dirs(consumer, consumers_dir)
    if not dirs:
        raise _fail("give at least one --consumer or --consumers-dir")
    sites = _scan(dirs)
    table = Table(header_style="bold", box=None, padding=(0, 2, 0, 0))
    for column in ("Consumer", "Location", "Method", "URL"):
        table.add_column(column)
    for s in sites:
        table.add_row(escape(s.consumer), escape(s.location), s.method, escape(s.url))
    console.print(table)
    console.print(f"[dim]{len(sites)} call sites in {len(dirs)} consumers[/]")


@app.command()
def check(
    base: BaseArg,
    head: HeadArg,
    consumer: ConsumerOpt = None,
    consumers_dir: ConsumersDirOpt = None,
    fmt: FormatOpt = Format.table,
    output: OutputOpt = None,
    fail_on: Annotated[
        FailOn, typer.Option("--fail-on", help="When to exit with code 1.")
    ] = FailOn.impacted,
) -> None:
    """Find which consumers a spec change will break."""
    base_spec, head_spec = _load(base), _load(head)
    dirs = _consumer_dirs(consumer, consumers_dir)
    if not dirs:
        err.print(
            "[yellow]warning:[/] no consumers given, so impact can't be checked. "
            "Use --consumer or --consumers-dir."
        )
    analysis = analyze(base_spec, head_spec, _scan(dirs), [d.name for d in dirs])

    if fmt is Format.table:
        if output is not None:
            raise _fail("--output needs --format markdown or json")
        print_analysis(analysis, console)
    elif fmt is Format.markdown:
        _emit(to_markdown(analysis), output)
    else:
        _emit(to_json(analysis), output)

    failed = (fail_on is FailOn.impacted and analysis.consumers_with(Impact.WILL_BREAK)) or (
        fail_on is FailOn.breaking and analysis.breaking
    )
    raise typer.Exit(1 if failed else 0)


@app.command()
def export(
    target: Annotated[
        str, typer.Argument(help="The app object as module:variable, e.g. myapp.main:app")
    ],
    output: OutputOpt = None,
) -> None:
    """Export the OpenAPI spec of a FastAPI app (or any object with an .openapi() method)."""
    module_name, _, attr = target.partition(":")
    if not module_name or not attr:
        raise _fail(f"expected module:variable, e.g. myapp.main:app (got {target!r})")

    # Import from the current folder, the way `uvicorn myapp.main:app` does.
    sys.path.insert(0, str(Path.cwd()))
    try:
        module = importlib.import_module(module_name)
    except Exception as e:  # any import error is the user's to fix
        raise _fail(f"could not import {module_name}: {e}") from e

    obj = getattr(module, attr, None)
    if obj is None:
        raise _fail(f"{module_name} has no variable named {attr!r}")
    if not callable(getattr(obj, "openapi", None)):
        raise _fail(f"{target} has no .openapi() method; is it a FastAPI app?")

    _emit(json.dumps(obj.openapi(), indent=2) + "\n", output)
