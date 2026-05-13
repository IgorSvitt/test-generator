"""Mutmut integration for mutation testing feedback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys

from src.logger import console


@dataclass
class MutationRunResult:
    """Outcome of mutation testing including pre-check failures."""

    killed: int
    total: int
    survived_mutants: list[str]
    error: str | None = None


def run_mutation_testing(
    project_root: str,
    source_file: str,
    function_name: str | None,
    test_code: str,
    test_file_path: str,
) -> MutationRunResult:
    """Run pytest first, then mutmut, and return survived mutant descriptions."""
    target_root = Path(project_root).resolve()
    source_path = Path(source_file)
    if not source_path.is_absolute():
        source_path = target_root / source_path
    source_path = source_path.resolve()
    if not source_path.is_relative_to(target_root):
        raise ValueError(f"Source file must be inside project root: {source_path}")
    rel_source = str(source_path.relative_to(target_root))
    test_path = Path(test_file_path).resolve()
    if not test_path.is_relative_to(target_root):
        raise ValueError(f"Test file must be inside project root: {test_path}")

    console.print(f"  [dim]→ Target root: {target_root}[/dim]")
    console.print(f"  [dim]→ Source file: {rel_source}[/dim]")

    console.print("  [dim]→ Running pytest precheck...[/dim]")
    pytest_completed = _run_pytest_precheck(
        project_root=target_root,
        test_code=test_code,
        test_file_path=test_path,
    )
    if pytest_completed.returncode != 0:
        pytest_output = "\n".join(
            part for part in [pytest_completed.stdout.strip(), pytest_completed.stderr.strip()] if part
        ).strip()
        console.print("  [red]✗ Tests failed before mutation testing:[/red]")
        console.print(f"  [dim]{pytest_output[:500]}[/dim]")
        error = "Tests failed before mutation testing. Fix imports first."
        if pytest_output:
            error = f"{error}\n{pytest_output}"
        return MutationRunResult(killed=0, total=0, survived_mutants=[], error=error)

    mutmut_bin = _find_mutmut(target_root)
    try:
        created_conftest = _ensure_conftest(target_root)
        _clear_mutmut_cache(target_root)
        created_setup_cfg = _ensure_mutmut_config(target_root, rel_source, test_path)
        subprocess.run(
            [mutmut_bin, "reset"],
            cwd=str(target_root),
            capture_output=True,
            check=False,
        )
        version_result = subprocess.run(
            [mutmut_bin, "--version"],
            cwd=str(target_root),
            capture_output=True,
            text=True,
            check=False,
        )
        console.print(f"  [dim]→ mutmut version: {version_result.stdout.strip()}[/dim]")
        console.print(f"  [dim]→ Running mutmut on {rel_source}...[/dim]")
        run_completed = subprocess.run(
            [mutmut_bin, "run"],
            cwd=str(target_root),
            capture_output=True,
            text=True,
            check=False,
        )
        console.print(f"  [dim]→ mutmut stdout: {run_completed.stdout[:300]}[/dim]")
        console.print(f"  [dim]→ mutmut stderr: {run_completed.stderr[:300]}[/dim]")
        results_completed = subprocess.run(
            [mutmut_bin, "results", "--all", "true"],
            cwd=str(target_root),
            capture_output=True,
            text=True,
            check=False,
        )

        combined = "\n".join(
            [
                run_completed.stdout.strip(),
                run_completed.stderr.strip(),
                results_completed.stdout.strip(),
                results_completed.stderr.strip(),
            ]
        ).strip()
        statuses = _parse_mutmut_statuses(results_completed.stdout)
        if function_name is not None:
            statuses = _filter_statuses_for_function(statuses, function_name)
        _print_filtered_mutmut_results(statuses, function_name)
        if statuses and all(status == "not checked" for status in statuses.values()):
            return MutationRunResult(
                killed=0,
                total=len(statuses),
                survived_mutants=[],
                error="Mutation run did not check any mutants. Check mutmut pytest configuration.",
            )

        survivors = _extract_survived_mutants(root=target_root, statuses=statuses, results_text=combined)
        killed, total = _calculate_killed_total_from_statuses(statuses)
        if not statuses and _has_unchecked_mutmut_output(combined):
            return MutationRunResult(
                killed=0,
                total=0,
                survived_mutants=[],
                error="Mutation run did not produce parseable mutmut results.",
            )
        if not statuses:
            killed, total = _extract_killed_total(combined, len(survivors))
        console.print(f"  [dim]→ Survived mutants: {survivors}[/dim]")
        return MutationRunResult(
            killed=killed,
            total=total,
            survived_mutants=survivors,
        )
    finally:
        _cleanup_mutmut_artifacts(
            target_root,
            remove_conftest=created_conftest if "created_conftest" in locals() else False,
            remove_setup_cfg=created_setup_cfg if "created_setup_cfg" in locals() else False,
        )
        console.print("  [dim]→ Cleaned up mutmut artifacts[/dim]")


def extract_mutation_score(results_text: str, survived_mutants: list[str]) -> float:
    """Estimate mutation score from mutmut output."""
    ratio_matches = re.findall(r"(\d+)\s*/\s*(\d+)", results_text)
    if ratio_matches:
        killed, total = ratio_matches[-1]
        total_num = int(total)
        killed_num = int(killed)
        if total_num > 0:
            return (killed_num / total_num) * 100.0
    if survived_mutants:
        return 0.0
    return 100.0


def _extract_killed_total(results_text: str, survived_count: int) -> tuple[int, int]:
    ratio_matches = re.findall(r"(\d+)\s*/\s*(\d+)", results_text)
    if ratio_matches:
        killed, total = ratio_matches[-1]
        return int(killed), int(total)
    total_match = re.search(r"(\d+)\s+mutants?", results_text, flags=re.IGNORECASE)
    if total_match:
        total = int(total_match.group(1))
        return max(total - survived_count, 0), total
    if survived_count:
        return 0, survived_count
    return 0, 0


def _has_unchecked_mutmut_output(results_text: str) -> bool:
    lowered = results_text.lower()
    return "not checked" in lowered or "running mutation testing" in lowered


def _run_pytest_precheck(
    project_root: Path, test_code: str, test_file_path: Path
) -> subprocess.CompletedProcess[str]:
    test_file_path.parent.mkdir(parents=True, exist_ok=True)
    test_file_path.write_text(test_code, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file_path), "-v", "--tb=short"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_mutmut_statuses(results_text: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in results_text.splitlines():
        match = re.match(r"\s*([^:\s]+):\s*(.+?)\s*$", line)
        if not match:
            continue
        mutant_name, status = match.groups()
        statuses[mutant_name] = status
    return statuses


def _filter_statuses_for_function(statuses: dict[str, str], function_name: str) -> dict[str, str]:
    marker = f".x_{function_name}__mutmut_"
    return {name: status for name, status in statuses.items() if marker in name}


def _print_filtered_mutmut_results(statuses: dict[str, str], function_name: str | None) -> None:
    label = function_name or "target"
    if not statuses:
        console.print(f"  [dim]→ mutmut results for {label}: no parsed mutants[/dim]")
        return

    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    console.print(f"  [dim]→ mutmut results for {label}: {summary}[/dim]")

    visible_statuses = {"survived", "no tests", "suspicious", "timeout", "not checked"}
    visible = [
        f"{name}: {status}"
        for name, status in statuses.items()
        if status in visible_statuses
    ]
    if visible:
        console.print(f"  [dim]→ mutmut unresolved: {'; '.join(visible[:5])}[/dim]")


def _calculate_killed_total_from_statuses(statuses: dict[str, str]) -> tuple[int, int]:
    ignored_statuses = {"skipped", "not checked"}
    killed_statuses = {"killed", "caught by type check"}
    relevant = [status for status in statuses.values() if status not in ignored_statuses]
    killed = sum(1 for status in relevant if status in killed_statuses)
    return killed, len(relevant)


def _extract_survived_mutants(root: Path, statuses: dict[str, str], results_text: str) -> list[str]:
    weak_statuses = {"survived", "no tests", "suspicious", "timeout"}
    weak_mutants = [name for name, status in statuses.items() if status in weak_statuses]
    if weak_mutants:
        survivors = [_describe_survived_mutant(root=root, mutant_name=mutant_name) for mutant_name in weak_mutants]
        return _dedupe_preserving_order(survivors)

    fallback_lines = []
    for line in (item.strip() for item in results_text.splitlines() if item.strip()):
        lowered = line.lower()
        if "survived" in lowered or "survivor" in lowered:
            fallback_lines.append(line)
    return _dedupe_preserving_order(fallback_lines)


def _extract_survived_mutant_ids(results_text: str) -> list[str]:
    ids: list[str] = []
    for line in results_text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if stripped.isdigit():
            ids.append(stripped)
            continue
        if "survived" not in lowered and "survivor" not in lowered:
            continue
        match = re.search(r"(?:#|mutant\s+)(\d+)", line, flags=re.IGNORECASE)
        if match:
            ids.append(match.group(1))
            continue
        match = re.match(r"(\d+)\b", stripped)
        if match:
            ids.append(match.group(1))
    return _dedupe_preserving_order(ids)


def _describe_survived_mutant(root: Path, mutant_name: str) -> str:
    mutmut_bin = _find_mutmut(root)
    completed = subprocess.run(
        [mutmut_bin, "show", mutant_name],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    show_text = "\n".join(
        part for part in [completed.stdout.strip(), completed.stderr.strip()] if part
    ).strip()
    line = _extract_line_number(show_text) or "unknown"
    original, mutated = _extract_mutation_pair(show_text)
    return f"Survived mutant {mutant_name}: line {line} — {original} → {mutated}"


def _extract_line_number(show_text: str) -> str | None:
    match = re.search(r"@@ -(\d+)", show_text)
    if match:
        return match.group(1)
    match = re.search(r"line\s+(\d+)", show_text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _extract_mutation_pair(show_text: str) -> tuple[str, str]:
    original = "unknown"
    mutated = "unknown"
    for line in show_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            original = line[1:].strip() or original
        elif line.startswith("+"):
            mutated = line[1:].strip() or mutated
        if original != "unknown" and mutated != "unknown":
            break
    return original, mutated


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _find_mutmut(target_root: Path) -> str:
    """Find mutmut binary - prefer target project venv."""
    venv_mutmut = target_root / ".venv" / "bin" / "mutmut"
    if venv_mutmut.exists():
        return str(venv_mutmut)

    current_venv_mutmut = Path(sys.executable).resolve().parent / "mutmut"
    if current_venv_mutmut.exists():
        return str(current_venv_mutmut)

    system_mutmut = shutil.which("mutmut")
    if system_mutmut:
        return system_mutmut

    raise RuntimeError("mutmut not found. Install: pip install mutmut")


def _ensure_mutmut_config(target_root: Path, rel_source: str, test_file_path: Path) -> bool:
    """Write setup.cfg so mutmut 3.x knows what to mutate and how to run pytest."""
    config_path = target_root / "setup.cfg"
    if config_path.exists() and _setup_cfg_matches_target(config_path, rel_source):
        console.print(f"  [dim]→ Using existing setup.cfg: {config_path}[/dim]")
        return False

    rel_test_path = str(test_file_path.relative_to(target_root))
    config_content = f"""[mutmut]
paths_to_mutate = {rel_source}
also_copy = {Path(rel_source).parent}
pytest_add_cli_args =
    {rel_test_path}
    -x
    -q
"""
    config_path.write_text(config_content, encoding="utf-8")
    console.print(f"  [dim]→ Written setup.cfg: {config_path}[/dim]")
    console.print(f"  [dim]→ paths_to_mutate: {rel_source}[/dim]")
    console.print(f"  [dim]→ pytest target: {rel_test_path}[/dim]")
    return True


def _setup_cfg_matches_target(config_path: Path, rel_source: str) -> bool:
    """Return whether an existing setup.cfg already points mutmut at this source."""
    content = config_path.read_text(encoding="utf-8")
    if "[mutmut]" not in content:
        return True
    match = re.search(r"(?m)^\s*paths_to_mutate\s*=\s*(.+?)\s*$", content)
    return bool(match and match.group(1).strip() == rel_source)


def _ensure_conftest(target_root: Path) -> bool:
    conftest = target_root / "conftest.py"
    if not conftest.exists():
        conftest.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))\n",
            encoding="utf-8",
        )
        console.print(f"  [dim]→ Created conftest.py in {target_root}[/dim]")
        return True
    return False


def _clear_mutmut_cache(target_root: Path) -> None:
    cache = target_root / ".mutmut-cache"
    mutants_dir = target_root / "mutants"
    if cache.exists():
        shutil.rmtree(cache)
    if mutants_dir.exists():
        shutil.rmtree(mutants_dir)


def _cleanup_mutmut_artifacts(
    target_root: Path,
    remove_conftest: bool,
    remove_setup_cfg: bool,
) -> None:
    """Remove mutmut generated files from target project."""
    artifacts = [
        target_root / "mutants",
        target_root / ".mutmut-cache",
        target_root / "mutmut.toml",
    ]
    if remove_conftest:
        artifacts.append(target_root / "conftest.py")
    if remove_setup_cfg:
        artifacts.append(target_root / "setup.cfg")
    for artifact in artifacts:
        if artifact.exists():
            if artifact.is_dir():
                shutil.rmtree(artifact)
            else:
                artifact.unlink()
