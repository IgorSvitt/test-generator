"""Pytest runner for generated tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import TypedDict

from src.logger import console


class RunResult(TypedDict):
    """Result of a single pytest execution."""

    passed: bool
    passed_count: int
    output: str
    error: str | None


def run_generated_tests(project_root: str, test_code: str, test_file_path: str) -> RunResult:
    """Write generated tests to a target file and execute it with pytest."""
    del project_root
    test_path = Path(test_file_path).resolve()
    target_root = test_path.parents[1]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(test_code, encoding="utf-8")

    console.print(f"  [dim]→ cwd: {target_root}[/dim]")
    console.print(f"  [dim]→ test file: {test_file_path}[/dim]")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(target_root / "src")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_path),
        "-v",
        "--tb=short",
        "-p",
        "no:cacheprovider",
    ]
    completed = subprocess.run(
        cmd,
        cwd=str(target_root),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    stdout_text = completed.stdout.strip()
    stderr_text = completed.stderr.strip()
    console.print(f"  [dim]→ pytest output: {stdout_text[-1000:]}[/dim]")
    passed_count = stdout_text.count(" PASSED")
    skipped_count = stdout_text.count(" SKIPPED")
    error_count = stdout_text.count(" ERROR")
    console.print(
        f"  [dim]→ passed: {passed_count}, skipped: {skipped_count}, errors: {error_count}[/dim]"
    )
    return RunResult(
        passed=completed.returncode == 0,
        passed_count=passed_count,
        output=stdout_text,
        error=stderr_text or stdout_text,
    )
