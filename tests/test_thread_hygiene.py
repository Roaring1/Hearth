"""Guard against shadowing ``threading.Thread`` internals.

A ``Thread`` subclass that assigns ``self._stop = threading.Event()``
replaces the real ``Thread._stop`` method with an Event. ``join()`` calls
that method on CPython 3.11 and 3.12, so the thread blows up with
``TypeError: 'Event' object is not callable``. CPython 3.13+ happens not
to reach that path, which is exactly why the bug survived local runs and
only ever showed up in CI.

The check is static so it fires on every interpreter, not just the ones
that would crash, and so it covers threads that are awkward to build in a
test (real subprocesses, sockets).
"""

from __future__ import annotations

import ast
import pathlib
import threading

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "hearth"

# Private ``Thread`` machinery that has existed across the versions we
# support. It is listed explicitly because it is *not* stable: CPython
# 3.14 no longer exposes ``_stop`` as a method, so a purely dynamic scan
# of ``dir(threading.Thread)`` would quietly stop catching the very bug
# this test exists for whenever the dev box is newer than CI.
KNOWN_INTERNALS = frozenset(
    {
        "_bootstrap",
        "_bootstrap_inner",
        "_delete",
        "_reset_internal_locks",
        "_set_ident",
        "_set_native_id",
        "_set_tstate_lock",
        "_stop",
        "_wait_for_tstate_lock",
    }
)

# Names a Thread subclass must not bind on itself.
RESERVED = KNOWN_INTERNALS | frozenset(
    name for name in dir(threading.Thread) if callable(getattr(threading.Thread, name, None))
)


def _is_thread_subclass(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Thread":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Thread":
            return True
    return False


def _self_attributes(node: ast.ClassDef) -> set[str]:
    found: set[str] = set()
    for sub in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, ast.AnnAssign):
            targets = [sub.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                found.add(target.attr)
    return found


def _thread_classes() -> list[tuple[str, ast.ClassDef]]:
    classes: list[tuple[str, ast.ClassDef]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_thread_subclass(node):
                rel = path.relative_to(SRC.parent.parent)
                classes.append((f"{rel}::{node.name}", node))
    return classes


THREAD_CLASSES = _thread_classes()


def test_the_scan_actually_finds_threads() -> None:
    # If a refactor moves the threads around, an empty scan would make the
    # test below vacuously pass.
    assert THREAD_CLASSES, "no threading.Thread subclasses found under src/hearth"


@pytest.mark.parametrize("label,node", THREAD_CLASSES, ids=[label for label, _ in THREAD_CLASSES])
def test_thread_subclass_does_not_shadow_thread_internals(label: str, node: ast.ClassDef) -> None:
    clashes = sorted(_self_attributes(node) & RESERVED)
    assert not clashes, (
        f"{label} assigns {clashes}, shadowing a threading.Thread method. "
        "Rename the attribute (e.g. _stop -> _stopping)."
    )
