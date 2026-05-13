"""Static analyzer for function-level prompt context."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


SIDE_EFFECT_IMPORT_MARKERS: tuple[str, ...] = ("requests", "sqlalchemy", "redis", "boto3", "open")


class FunctionContext(TypedDict):
    """Context returned by static analysis for one target function."""

    function_name: str
    args: list[tuple[str, str]]
    return_type: str
    calls: list[str]
    side_effects: list[str]
    imports: list[str]
    function_code: str
    call_graph: dict[str, list[str]]


@dataclass
class _ModuleData:
    imports: list[str]
    call_graph: dict[str, list[str]]
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]


class _CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            self.calls.append(name)
        self.generic_visit(node)


def analyze_function(source_file: str, function_name: str) -> FunctionContext:
    """Analyze one function inside a Python source file."""
    path = Path(source_file).resolve()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module_data = _collect_module_data(tree)

    if function_name not in module_data.functions:
        raise ValueError(f"Function '{function_name}' was not found in {path}")

    fn_node = module_data.functions[function_name]
    args = _extract_args(fn_node)
    return_type = ast.unparse(fn_node.returns) if fn_node.returns is not None else "None"
    function_code = ast.get_source_segment(source, fn_node) or ""
    side_effects = _detect_side_effects(module_data.imports, module_data.call_graph.get(function_name, []))
    calls = module_data.call_graph.get(function_name, [])

    return FunctionContext(
        function_name=function_name,
        args=args,
        return_type=return_type,
        calls=calls,
        side_effects=side_effects,
        imports=module_data.imports,
        function_code=function_code,
        call_graph=module_data.call_graph,
    )


def _collect_module_data(tree: ast.AST) -> _ModuleData:
    imports = _collect_imports(tree)
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    call_graph: dict[str, list[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
            collector = _CallCollector()
            collector.visit(node)
            call_graph[node.name] = sorted(set(collector.calls))

    return _ModuleData(imports=imports, call_graph=call_graph, functions=functions)


def _collect_imports(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            imports.append(module_name)
    return sorted(set(name for name in imports if name))


def _extract_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str]]:
    args: list[tuple[str, str]] = []
    for arg in node.args.args:
        annotation = ast.unparse(arg.annotation) if arg.annotation is not None else "unknown"
        args.append((arg.arg, annotation))
    return args


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        if base:
            return f"{base}.{node.attr}"
        return node.attr
    return ""


def _detect_side_effects(imports: list[str], calls: list[str]) -> list[str]:
    effects: list[str] = []
    lowered_imports = [item.lower() for item in imports]
    lowered_calls = [item.lower() for item in calls]

    for marker in SIDE_EFFECT_IMPORT_MARKERS:
        has_import = any(marker in imported for imported in lowered_imports)
        has_open_call = marker == "open" and any(call_name.startswith("open") for call_name in lowered_calls)
        if has_import or has_open_call:
            effects.append(marker)
    return effects
