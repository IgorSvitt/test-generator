"""LLM-backed test generation module."""

from __future__ import annotations

import json
import re
from typing import TypedDict, cast

from litellm import completion

from src.analyzer import FunctionContext
from src.config import LLMConfig


class _ChatMessage(TypedDict):
    role: str
    content: str


class _ChoiceMessage(TypedDict):
    content: str


class _Choice(TypedDict):
    message: _ChoiceMessage


class _CompletionResponse(TypedDict):
    choices: list[_Choice]


def build_prompt(
    analysis_context: FunctionContext,
    survived_mutants: list[str],
    previous_failure: str | None,
    module_name: str,
    source_path: str,
    output_test_path: str,
) -> str:
    """Build an enriched prompt for generating robust pytest tests."""
    guidance = [
        "Generate pytest tests for the provided target function.",
        "Use exact assertions with concrete expected values.",
        "Do not use weak assertions like 'is not None'.",
        "Cover happy path, edge cases, and error cases.",
        "Mock all side effects with unittest.mock.",
        "Return only Python test code.",
        "CRITICAL RULES FOR TEST FILE STRUCTURE:",
        "1. Do NOT add any sys.path manipulation",
        "2. Do NOT add 'import sys' or 'from pathlib import Path'",
        "3. Do NOT add any sys.path.insert() calls",
        "4. Start file directly with: import pytest",
        "5. Import module directly WITHOUT src prefix:",
        f"   CORRECT: from {module_name} import {analysis_context['function_name']}",
        f"   WRONG:   from src.{module_name} import {analysis_context['function_name']}",
        "   WRONG:   import sys; sys.path.insert(...)",
        "CRITICAL FORBIDDEN PATTERNS - never use these:",
        "1. Never add: pytestmark = pytest.mark.skip(...)",
        "2. Never add: @pytest.mark.skip",
        "3. Never add: pytest.skip() inside tests",
        "4. If you cannot import a module - find another way using Mock or patch",
        "5. Never give up on writing tests - always write real working tests",
        "If module has complex imports, use patch() to mock dependencies:",
        "with patch('payment_service.SomeClass') as mock:",
        "    mock.return_value = ...",
        "- Use pytest fixtures instead of unittest.TestCase",
        "- Use realistic fake classes instead of MagicMock where possible",
        "- Each fake class must implement the same Protocol as the real dependency",
    ]
    if survived_mutants:
        guidance.append("Strengthen tests to kill these survived mutants:")
        guidance.extend(survived_mutants)
        guidance.extend(
            [
                "For each survived mutant, add at least one targeted test that would fail on the mutated code.",
                "If a mutant changes a boundary condition, test the exact boundary value.",
                "Example: if 'amount <= 0' changed to 'amount <= 1', add a success-path test for amount=1.",
                "Keep existing useful tests unless they are wrong; extend coverage instead of replacing it with weaker tests.",
            ]
        )
    if previous_failure:
        guidance.append(f"Fix previous failing tests:\n{previous_failure.strip()}")

    context_dump = json.dumps(analysis_context, indent=2, ensure_ascii=True)
    prompt = (
        "You are a senior Python test engineer.\n\n"
        "### Function Code\n"
        f"{analysis_context['function_code']}\n\n"
        "### Call Graph\n"
        f"{json.dumps(analysis_context['call_graph'], indent=2, ensure_ascii=True)}\n\n"
        "### Types\n"
        f"args={analysis_context['args']}, return={analysis_context['return_type']}\n\n"
        "### Side Effects\n"
        f"{analysis_context['side_effects']}\n\n"
        "### File Locations\n"
        f"The test file will be saved to: {output_test_path}\n"
        f"The source file is at: {source_path}\n\n"
        "### Full Context\n"
        f"{context_dump}\n\n"
        "### Instructions\n"
        + "\n".join(f"- {line}" for line in guidance)
    )
    return prompt


def generate_tests(prompt: str, cfg: LLMConfig) -> str:
    """Call LiteLLM and return parsed Python test code."""
    messages: list[_ChatMessage] = [
        {"role": "system", "content": "You write deterministic, strict pytest tests."},
        {"role": "user", "content": prompt},
    ]
    if cfg.api_key:
        raw_response = completion(  # type: ignore[no-untyped-call]
            model=cfg.model,
            api_key=cfg.api_key,
            api_base=cfg.api_base,
            messages=messages,
            temperature=0.1,
        )
    else:
        raw_response = completion(  # type: ignore[no-untyped-call]
            model=cfg.model,
            api_base=cfg.api_base,
            messages=messages,
            temperature=0.1,
        )
    response = cast(_CompletionResponse, raw_response)

    content = response["choices"][0]["message"]["content"]
    code = parse_generated_tests(content)
    return code


def parse_generated_tests(content: str) -> str:
    """Extract Python code from markdown or return content as-is."""
    fenced_blocks = re.findall(r"```(?:python)?\n(.*?)```", content, flags=re.DOTALL)
    if fenced_blocks:
        return "\n\n".join(block.strip() for block in fenced_blocks if block.strip())
    return content.strip()
