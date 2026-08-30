"""Fusion value writes must not be wrapped in comp.Lock()/Unlock().

A value write (SetInput / SetExpression / a spline subscript write) wrapped in
a comp lock lands in the graph and reads back correctly — GetInput returns it,
and so does the server's own `get_input` — while the RENDER ignores it
completely.

Measured upstream (348280c) on DaVinci Resolve Studio 19.1.3.7, on a
MediaIn -> Blur(XBlurSize 20) -> MediaOut comp attached to a media-backed clip:

    value written through fusion_comp set_input (locked)   PSNR inf     IGNORED
    same value written with the lock removed               PSNR 24.38dB RENDERED

Replicated on this machine 2026-08-29 (Studio 21.0.3, lockfix-test) and
render-verified against this fork's fixed write paths — see
_shared/editing/experiments/2026-08-29-resolve-animation-adjustments/.

The condition only reproduces on comps whose graph was BUILT through
lock-wrapped AddTool/ConnectInput (exactly what this server does), and any
unlocked value write anywhere in the comp primes it permanently — so this
guard refuses a lock around ANY value write rather than encoding which call
shapes happen to get away with it.

This is a STATIC guard because the real proof needs a render: the failure is
invisible to every readback the API offers, so a reintroduced lock would
otherwise go unnoticed until someone diffed a delivered frame.

Beyond upstream's guard, this one also flags SUBSCRIPT ASSIGNMENTS
(`tool[input][time] = value`) inside a lock region — the keyed-spline write
add_keyframe uses, which upstream's SetInput/SetExpression-only guard misses.

Structural edits (AddTool, ConnectInput, AddModifier, LoadSettings, SetAttrs)
are a different case: they invalidate the render through another path, were
verified upstream to render while locked, and deliberately keep their locks.
"""

from __future__ import annotations

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO_ROOT, "src", "server.py")

# Calls that write a VALUE into a Fusion tool. A lock around any of these is
# the bug. Structural calls are intentionally absent.
VALUE_WRITE_METHODS = frozenset({"SetInput", "SetExpression"})


def _method_name(node: ast.AST):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _value_writes_under(node: ast.AST):
    """Value-write sites anywhere beneath `node`.

    Two shapes: value-write method calls (SetInput/SetExpression), and
    subscript assignments (`x[...] = value` / `x[...][...] = value`) — the
    form a keyed spline write takes.
    """
    found = []
    for child in ast.walk(node):
        name = _method_name(child)
        if name in VALUE_WRITE_METHODS:
            found.append((name, getattr(child, "lineno", "?")))
        elif isinstance(child, ast.Assign) and any(
            isinstance(t, ast.Subscript) for t in child.targets
        ):
            found.append(("subscript-assign", getattr(child, "lineno", "?")))
    return found


def _locked_regions(tree: ast.AST):
    """Try blocks whose finally calls Unlock — the locked-region shape."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        finally_unlocks = any(
            _method_name(getattr(s, "value", None)) == "Unlock"
            for s in node.finalbody
            if isinstance(s, ast.Expr)
        )
        if finally_unlocks:
            yield node


class FusionValueWriteLockTests(unittest.TestCase):
    def test_no_value_write_happens_inside_a_comp_lock(self) -> None:
        tree = ast.parse(open(SERVER, encoding="utf-8").read(), filename=SERVER)

        offenders = []
        for node in _locked_regions(tree):
            for name, lineno in _value_writes_under(
                ast.Module(body=node.body, type_ignores=[])
            ):
                offenders.append(f"{name} at src/server.py:{lineno}")

        self.assertEqual(
            offenders,
            [],
            "Fusion value write(s) wrapped in comp.Lock()/Unlock(). A value "
            "written under a comp lock reads back correctly and is IGNORED at "
            "render (upstream 348280c, PSNR inf vs baseline; replicated here "
            "2026-08-29). Move the write outside; structural edits "
            "(AddTool/ConnectInput/AddModifier) may stay locked."
            "\nOffenders: " + ", ".join(offenders),
        )

    def test_the_guard_can_actually_see_a_violation(self) -> None:
        """A guard that cannot fail is not a guard."""
        bad = ast.parse(
            "comp.Lock()\n"
            "try:\n"
            "    tool.SetInput('XBlurSize', 20)\n"
            "    tool['Gain'][12] = 0.3\n"
            "finally:\n"
            "    comp.Unlock()\n"
        )
        seen = []
        for node in _locked_regions(bad):
            seen += _value_writes_under(ast.Module(body=node.body, type_ignores=[]))
        names = [n for n, _ in seen]
        self.assertIn("SetInput", names, "guard missed a locked SetInput")
        self.assertIn(
            "subscript-assign", names, "guard missed a locked spline subscript write"
        )


if __name__ == "__main__":
    unittest.main()
