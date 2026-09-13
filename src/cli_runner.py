#!/usr/bin/env python3
"""Dispatch DaVinci Resolve MCP compound tools from the command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(CURRENT_DIR)
for import_dir in (CURRENT_DIR, PROJECT_DIR):
    if import_dir not in sys.path:
        sys.path.insert(0, import_dir)

from src import server  # noqa: E402


def _compound_tools() -> Dict[str, Any]:
    """Return every callable registered with the compound FastMCP server."""
    registered = getattr(getattr(server.mcp, "_tool_manager", None), "_tools", {})
    return {
        name: getattr(server, name)
        for name in registered
        if callable(getattr(server, name, None))
    }


def _emit_json(value: Any) -> None:
    json.dump(value, sys.stdout, default=str, indent=2)
    sys.stdout.write("\n")


def _read_params(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if raw == "-":
        raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params is not valid JSON: {exc}") from exc
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise ValueError(
            f"--params must decode to a JSON object, got {type(parsed).__name__}"
        )
    return parsed


def _exit_for_result(result: Any) -> int:
    if not isinstance(result, dict) or "error" not in result:
        return 0
    message = str(result["error"]).lower()
    if "not connected" in message or "is resolve running" in message:
        return 2
    return 1


def _list_tools(tools: Dict[str, Any]) -> int:
    payload: Dict[str, Dict[str, str]] = {}
    for name, function in tools.items():
        doc = (function.__doc__ or "").strip()
        payload[name] = {
            "summary": doc.splitlines()[0] if doc else "",
            "doc": doc,
        }
    _emit_json(payload)
    return 0


def _health(tools: Dict[str, Any]) -> int:
    health: Dict[str, Any] = {
        "resolve_running": False,
        "product": None,
        "version": None,
        "version_string": None,
        "page": None,
        "project_open": False,
        "project_name": None,
        "project_id": None,
    }
    version_result = tools["resolve_control"]("get_version", None)
    if isinstance(version_result, dict) and "error" not in version_result:
        health.update(
            resolve_running=True,
            product=version_result.get("product"),
            version=version_result.get("version"),
            version_string=version_result.get("version_string"),
        )
        page_result = tools["resolve_control"]("get_page", None)
        if isinstance(page_result, dict) and "error" not in page_result:
            health["page"] = page_result.get("page")
        project_result = tools["project_manager"]("get_current", None)
        if isinstance(project_result, dict) and "error" not in project_result:
            health.update(
                project_open=True,
                project_name=project_result.get("name"),
                project_id=project_result.get("id"),
            )
    _emit_json(health)
    return 0 if health["resolve_running"] else 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cli_runner",
        description="Dispatch compound DaVinci Resolve MCP tools.",
    )
    parser.add_argument("tool", nargs="?")
    parser.add_argument("action", nargs="?")
    parser.add_argument("--params")
    parser.add_argument("--list", action="store_true", dest="list_tools")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args(argv)
    tools = _compound_tools()

    if args.list_tools:
        return _list_tools(tools)
    if args.health:
        return _health(tools)
    if not args.tool or not args.action:
        parser.error("missing tool and/or action (or pass --list / --health)")
    if args.tool not in tools:
        _emit_json(
            {
                "error": f"unknown tool '{args.tool}'",
                "valid_tools": sorted(tools),
            }
        )
        return 1
    try:
        params = _read_params(args.params)
        result = tools[args.tool](args.action, params)
    except ValueError as exc:
        _emit_json({"error": str(exc)})
        return 1
    except TypeError as exc:
        _emit_json({"error": f"dispatch type error: {exc}"})
        return 1
    except Exception as exc:  # noqa: BLE001
        _emit_json({"error": f"tool raised {type(exc).__name__}: {exc}"})
        return 1
    _emit_json(result)
    return _exit_for_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
