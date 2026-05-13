"""Shared CLI console with optional Rich support."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import re

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


def _strip_markup(text: str) -> str:
    return re.sub(r"\[/?[^\]]+\]", "", text)


class _FallbackStatus(AbstractContextManager[None]):
    def __init__(self, message: str) -> None:
        self._message = _strip_markup(message)

    def __enter__(self) -> None:
        print(self._message, flush=True)
        return None

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        return None


@dataclass
class Table:
    """Fallback-compatible table."""

    title: str | None = None
    border_style: str | None = None
    show_header: bool = True
    _columns: list[str] = field(default_factory=list)
    _rows: list[tuple[str, ...]] = field(default_factory=list)

    def add_column(self, header: str, style: str | None = None) -> None:
        del style
        self._columns.append(header)

    def add_row(self, *values: str) -> None:
        self._rows.append(values)

    def __str__(self) -> str:
        del self.border_style
        headers = self._columns if self.show_header else []
        rows = [headers] + list(self._rows) if headers else list(self._rows)
        if not rows:
            return self.title or ""

        widths = [max(len(row[idx]) for row in rows) for idx in range(len(rows[0]))]
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
        for row_index, row in enumerate(rows):
            padded = [value.ljust(widths[idx]) for idx, value in enumerate(row)]
            lines.append(" | ".join(padded))
            if row_index == 0 and headers:
                lines.append("-+-".join("-" * width for width in widths))
        return "\n".join(lines)


@dataclass
class Panel:
    """Fallback-compatible panel."""

    renderable: str
    title: str | None = None
    border_style: str | None = None
    padding: tuple[int, int] | None = None

    def __str__(self) -> str:
        del self.border_style
        del self.padding
        title = _strip_markup(self.title) if self.title else ""
        body = _strip_markup(self.renderable)
        lines = body.splitlines() or [body]
        width = max([len(line) for line in lines] + [len(title)])
        top = f"+-{'-' * width}-+"
        rendered: list[str] = [top]
        if title:
            rendered.append(f"| {title.ljust(width)} |")
        for line in lines:
            rendered.append(f"| {line.ljust(width)} |")
        rendered.append(top)
        return "\n".join(rendered)


class _ConsoleWrapper:
    def __init__(self) -> None:
        self._console = _RichConsole() if _HAS_RICH else None

    def print(self, renderable: str | Table | Panel, *, flush: bool = False) -> None:
        del flush
        if self._console is None:
            print(str(renderable), flush=True)
            return
        self._console.print(renderable)

    def status(self, message: str) -> AbstractContextManager[None]:
        if self._console is None:
            return _FallbackStatus(message)
        return self._console.status(message)


if _HAS_RICH:
    Table = _RichTable
    Panel = _RichPanel


console = _ConsoleWrapper()
