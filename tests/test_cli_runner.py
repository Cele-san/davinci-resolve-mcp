"""Offline contract tests for the compound-tool CLI dispatcher."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNNER = PROJECT_ROOT / "src" / "cli_runner.py"
SERVER = PROJECT_ROOT / "src" / "server.py"


def _compound_tool_names() -> set[str]:
    tree = ast.parse(SERVER.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "tool"
                and isinstance(func.value, ast.Name)
                and func.value.id == "mcp"
            ):
                names.add(node.name)
    return names


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_list_matches_every_compound_tool() -> None:
    result = _run("--list")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == _compound_tool_names()


def test_unknown_tool_is_a_structured_error() -> None:
    result = _run("not_a_tool", "noop")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "unknown tool 'not_a_tool'"
    assert set(payload["valid_tools"]) == _compound_tool_names()


def test_params_must_be_a_json_object() -> None:
    result = _run("resolve_control", "get_version", "--params", "[]")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "--params must decode to a JSON object, got list"


def run_all() -> None:
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()


if __name__ == "__main__":
    run_all()
    print("test_cli_runner.py: ok")
