"""
Progress display and output formatting.

Uses Rich for terminal output with intelligent detection:
- TTY: full progress bars with colors
- Non-TTY: simple line output
- --quiet: minimal output
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


class Output:
    """
    Output handler for CLI with progress display.

    Provides a clean, fast progress experience while remaining
    responsive to user cancellation.
    """

    def __init__(
        self,
        verbose: bool = False,
        quiet: bool = False,
        no_progress: bool = False,
        use_color: bool | None = None,
    ):
        self.verbose = verbose
        self.quiet = quiet

        if use_color is None:
            use_color = sys.stdout.isatty()

        self.use_color = use_color and RICH_AVAILABLE

        if RICH_AVAILABLE:
            self.console = Console(
                file=sys.stdout,
                force_terminal=use_color,
                no_color=not use_color,
                quiet=quiet,
            )
        else:
            self.console = None

        self._no_progress = no_progress or quiet or not sys.stdout.isatty()
        self._start_time = time.time()

    def print(self, message: str, style: str | None = None) -> None:
        """Print a message."""
        if self.quiet and not self.verbose:
            return
        if RICH_AVAILABLE and self.console:
            self.console.print(message, style=style)
        else:
            print(message)

    def info(self, message: str) -> None:
        """Print an info message."""
        if self.quiet:
            return
        if RICH_AVAILABLE and self.console:
            self.console.print(f"[cyan]ℹ[/cyan] {message}")
        else:
            print(f"ℹ {message}")

    def success(self, message: str) -> None:
        """Print a success message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"[green]✓[/green] {message}")
        else:
            print(f"✓ {message}")

    def warning(self, message: str) -> None:
        """Print a warning message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"[yellow]⚠[/yellow] {message}")
        else:
            print(f"⚠ {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        """Print an error message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"[red]✗[/red] {message}")
        else:
            print(f"✗ {message}", file=sys.stderr)

    def header(self, message: str) -> None:
        """Print a header message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"\n[bold cyan]⚡ {message}[/bold cyan]\n")
        else:
            print(f"\n⚡ {message}\n")

    @contextmanager
    def progress(self) -> Iterator["Progress | SimpleProgress"]:
        """Context manager for progress display."""
        if self._no_progress or not RICH_AVAILABLE:
            yield SimpleProgress()
        else:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeElapsedColumn(),
                console=self.console,
            )
            with progress:
                yield progress

    def summary(self, title: str, items: list[tuple[str, str]]) -> None:
        """Print a summary table."""
        if not items or self.quiet:
            return

        if RICH_AVAILABLE and self.console:
            table = Table(title=title, show_header=True, header_style="bold cyan")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            for key, value in items:
                table.add_row(key, value)
            self.console.print(table)
        else:
            print(f"\n{title}")
            for key, value in items:
                print(f"  {key}: {value}")


class SimpleProgress:
    """Fallback progress display when Rich is unavailable."""

    def __init__(self):
        self.tasks: dict[int, dict[str, Any]] = {}
        self._next_id = 0

    def add_task(self, description: str, total: int | None = None) -> int:
        task_id = self._next_id
        self._next_id += 1
        self.tasks[task_id] = {
            "description": description,
            "total": total,
            "completed": 0,
        }
        return task_id

    def update(self, task_id: int, completed: int | None = None, total: int | None = None) -> None:
        if task_id in self.tasks:
            if completed is not None:
                self.tasks[task_id]["completed"] = completed
            if total is not None:
                self.tasks[task_id]["total"] = total

    def advance(self, task_id: int, amount: int = 1) -> None:
        if task_id in self.tasks:
            self.tasks[task_id]["completed"] += amount


def format_size(size_bytes: int) -> str:
    """Format a byte count for human reading."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f}MB"
    return f"{size_bytes / 1024 / 1024 / 1024:.2f}GB"


def format_duration(seconds: float) -> str:
    """Format a duration for human reading."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = seconds - m * 60
        return f"{m}m{s:.0f}s"
    h = int(seconds // 3600)
    m = int((seconds - h * 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h}h{m}m{s:.0f}s"
