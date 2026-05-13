"""Main orchestration pipeline for generation, execution, and mutation feedback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

from src.analyzer import analyze_function
from src.config import LLMConfig
from src.generator import build_prompt, generate_tests
from src.logger import console
from src.mutator import run_mutation_testing
from src.runner import run_generated_tests


TEST_FILE_HEADER = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

"""


@dataclass
class PipelineResult:
    """Final output of the pipeline."""

    function_name: str
    test_code: str
    output_path: Path
    mutation_score: int
    iterations: int
    tests_count: int
    elapsed: float
    survived_mutants: list[str]


def run_pipeline(
    project_root: str,
    source_file: str,
    function_name: str,
    cfg: LLMConfig,
    mutation_score_threshold: float = 80.0,
    max_iterations: int = 3,
) -> PipelineResult:
    """Run the full test-generation loop with mutation feedback."""
    started_at = time.perf_counter()
    root = Path(project_root).resolve()
    source_abs = root / source_file
    if not source_abs.exists():
        raise FileNotFoundError(f"Source file not found: {source_abs}")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive.")
    _ensure_src_package(source_abs)
    output_path = get_output_test_path(str(source_abs), function_name)

    previous_failure: str | None = None
    survived_mutants: list[str] = []
    latest_code = ""
    mutation_score = 0
    context = None

    for iteration in range(1, max_iterations + 1):
        console.print("")
        console.print(f" [bold]Iteration {iteration}/{max_iterations}[/bold]")

        if iteration == 1:
            analyze_started = time.perf_counter()
            with console.status("  🔍  Analyzing..."):
                context = analyze_function(source_file=str(source_abs), function_name=function_name)
            console.print(_format_step("🔍", "Analyzing...", time.perf_counter() - analyze_started))
        if context is None:
            raise RuntimeError("Function analysis context was not initialized.")

        module_name = _build_module_name(source_abs)
        generate_label = (
            f"Regenerating ({len(survived_mutants)} mutants survived)..."
            if survived_mutants
            else "Generating tests..."
        )
        generate_started = time.perf_counter()
        prompt = build_prompt(
            analysis_context=context,
            survived_mutants=survived_mutants,
            previous_failure=previous_failure,
            module_name=module_name,
            source_path=str(source_abs),
            output_test_path=str(output_path),
        )
        with console.status(f"  🤖  {generate_label}"):
            latest_code = generate_tests(prompt=prompt, cfg=cfg)
        latest_code = _finalize_test_code(latest_code)
        output_path.write_text(latest_code, encoding="utf-8")
        console.print(_format_step("🤖", generate_label, time.perf_counter() - generate_started))

        tests_started = time.perf_counter()
        with console.status("  🧪  Running tests..."):
            run_result = run_generated_tests(
                project_root=str(root),
                test_code=latest_code,
                test_file_path=str(output_path),
            )
        test_suffix = (
            f"✓ {run_result['passed_count']} passed" if run_result["passed"] else "✗ failed"
        )
        console.print(_format_step("🧪", "Running tests...", time.perf_counter() - tests_started, test_suffix))
        if not run_result["passed"]:
            previous_failure = run_result["error"] or run_result["output"]
            _print_test_failure(run_result)
            continue

        previous_failure = None
        mutation_started = time.perf_counter()
        with console.status("  ☠️  Mutation testing..."):
            mutation_result = run_mutation_testing(
                project_root=str(root),
                source_file=source_file,
                function_name=function_name,
                test_code=latest_code,
                test_file_path=str(output_path),
            )
        if mutation_result.error:
            previous_failure = mutation_result.error
            console.print(
                _format_step("☠️", "Mutation testing...", time.perf_counter() - mutation_started, "✗ blocked")
            )
            _print_failure_block("Mutation pre-check failures", mutation_result.error)
            continue
        survived_mutants = mutation_result.survived_mutants
        mutation_score = _calculate_mutation_score(
            killed=mutation_result.killed,
            total=mutation_result.total,
        )
        mutation_suffix = (
            f"✓ {mutation_result.killed}/{mutation_result.total} killed"
            if mutation_result.total
            else "✓ no mutants"
        )
        console.print(
            _format_step(
                "☠️",
                "Mutation testing...",
                time.perf_counter() - mutation_started,
                mutation_suffix,
            )
        )
        if survived_mutants:
            preview = ", ".join(survived_mutants[:3])
            if len(survived_mutants) > 3:
                preview += "..."
            console.print(f"  [yellow]→ survived: {preview}[/yellow]")

        if mutation_score >= mutation_score_threshold and not survived_mutants:
            result = PipelineResult(
                function_name=function_name,
                test_code=latest_code,
                output_path=output_path,
                mutation_score=mutation_score,
                iterations=iteration,
                tests_count=_count_generated_tests(latest_code),
                elapsed=time.perf_counter() - started_at,
                survived_mutants=survived_mutants,
            )
            return result

    result = PipelineResult(
        function_name=function_name,
        test_code=latest_code,
        output_path=output_path,
        mutation_score=mutation_score,
        iterations=max_iterations,
        tests_count=_count_generated_tests(latest_code),
        elapsed=time.perf_counter() - started_at,
        survived_mutants=survived_mutants,
    )
    return result


def _count_generated_tests(test_code: str) -> int:
    return sum(1 for line in test_code.splitlines() if line.strip().startswith("def test_"))


def _calculate_mutation_score(killed: int, total: int) -> int:
    if total <= 0:
        return 100
    return int(round((killed / total) * 100))


def _format_step(icon: str, label: str, elapsed: float, suffix: str = "") -> str:
    base = f"  {icon}  {label}"
    done = "done"
    if suffix:
        return f"{base:<36}{done:>6}  {elapsed:>4.1f}s  {suffix}"
    return f"{base:<36}{done:>6}  {elapsed:>4.1f}s"


def _print_test_failure(run_result: dict[str, object]) -> None:
    output = str(run_result.get("output", "")).strip()
    error = str(run_result.get("error", "")).strip()
    console.print("[red]── Test failures ──[/red]")
    if output:
        console.print(output[-3000:])
    if error and error not in output:
        console.print(error[-1000:])


def _print_failure_block(title: str, content: str) -> None:
    console.print(f"[red]── {title} ──[/red]")
    console.print(content[-3000:])


def get_output_test_path(source_file: str, function_name: str) -> Path:
    source_path = Path(source_file).resolve()
    project_root = source_path.parent.parent
    tests_dir = project_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_filename = f"test_{source_path.stem}.py"
    return tests_dir / test_filename


def _build_module_name(source_file: Path) -> str:
    module_path = source_file.with_suffix("")
    if "src" in module_path.parts:
        src_index = module_path.parts.index("src")
        relative_parts = module_path.parts[src_index + 1 :]
        if relative_parts:
            return ".".join(relative_parts)
    return module_path.stem


def _clean_llm_test_code(llm_code: str) -> str:
    """Remove any sys.path manipulation added by LLM."""
    llm_code = re.sub(r"^import sys\n", "", llm_code, flags=re.MULTILINE)
    llm_code = re.sub(r"^from pathlib import Path\n", "", llm_code, flags=re.MULTILINE)
    llm_code = re.sub(r"^sys\.path\.insert\(.*?\)\n", "", llm_code, flags=re.MULTILINE)
    llm_code = llm_code.lstrip("\n")
    return llm_code


def _finalize_test_code(llm_code: str) -> str:
    return TEST_FILE_HEADER + _clean_llm_test_code(llm_code)


def _ensure_src_package(source_file: Path) -> None:
    src_dir = source_file.parent
    init_file = src_dir / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")
