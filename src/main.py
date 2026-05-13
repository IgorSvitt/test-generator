"""CLI entry point for the test generation pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.config import load_config
from src.logger import Panel, Table, console
from src.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Automatic unit test generation with LLM feedback loop.")
    parser.add_argument("--project-root", default=".", help="Project root directory.")
    parser.add_argument("--file", required=True, help="Relative path to source file.")
    parser.add_argument("--function", required=True, help="Function name to test.")
    parser.add_argument(
        "--mutation-threshold",
        type=float,
        default=80.0,
        help="Target mutation score (0.0 to 100.0).",
    )
    parser.add_argument("--max-iterations", type=int, default=3, help="Maximum feedback loops.")
    return parser.parse_args()


def main() -> int:
    """Run pipeline and print a concise execution report."""
    args = parse_args()
    console.print(
        Panel(
            f"[dim]File:[/dim]     {args.file}\n[dim]Function:[/dim] {args.function}",
            title="[bold]Test Generator[/bold]",
            border_style="blue",
        )
    )
    cfg = load_config()

    result = run_pipeline(
        project_root=args.project_root,
        source_file=args.file,
        function_name=args.function,
        cfg=cfg,
        mutation_score_threshold=args.mutation_threshold,
        max_iterations=args.max_iterations,
    )

    output_path = result.output_path
    output_path.write_text(result.test_code, encoding="utf-8")

    console.print("")
    table = Table(border_style="blue", show_header=False)
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("Function", result.function_name)
    table.add_row("Mutation Score", f"{result.mutation_score}%")
    table.add_row("Tests generated", str(result.tests_count))
    table.add_row("Iterations", str(result.iterations))
    table.add_row("Time elapsed", f"{result.elapsed:.1f}s")
    console.print(Panel(table, title="[bold]Results[/bold]", border_style="blue"))
    console.print("")
    console.print(f"[bold green]Saved: {_display_path(output_path)}[/bold green]")
    return 0 if result.mutation_score >= args.mutation_threshold else 1


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
