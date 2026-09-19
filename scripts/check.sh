#!/usr/bin/env bash
# Run every check that CI runs, in the same order, with the same flags.
# If this is green, CI should be green. Usage: ./scripts/check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
soft() { "$@" || fail=1; }
skip() { printf '   skipped: %s\n' "$1"; }

# Prefer the project virtualenv over whatever happens to be on PATH. The dev
# tools are deliberately not installed system-wide, so a plain `command -v`
# check reported "not installed" and the gate printed "All checks passed"
# having run no tests at all. A gate that skips silently is worse than no gate.
VENV="$ROOT/.venv/bin"
tool() {
    if [ -x "$VENV/$1" ]; then printf '%s\n' "$VENV/$1"
    elif command -v "$1" >/dev/null 2>&1; then command -v "$1"
    fi
}
PY="$VENV/python3"; [ -x "$PY" ] || PY="$(command -v python3)"

step "ruff check"
RUFF="$(tool ruff)"
if [ -n "$RUFF" ]; then
    soft "$RUFF" check .
    # Formatting is not enforced yet: the legacy files moved in step 7 would
    # be rewritten wholesale. Reported, not fatal, until that lands as its
    # own commit.
    # Enforced since the tree was formatted in full. A formatting diff must
    # never ride along with a behavioural change, so this fails the gate.
    "$RUFF" format --check .
else
    skip "ruff not installed (python3 -m venv --system-site-packages .venv && .venv/bin/pip install -e '.[dev]')"
fi

step "mypy"
MYPY="$(tool mypy)"
if [ -n "$MYPY" ]; then
    # Typing is being adopted module by module; see [tool.mypy] excludes.
    "$MYPY" || printf '   (type errors reported, not enforced yet)\n'
else
    skip "mypy not installed"
fi

step "pytest"
# Run from a scratch directory: at the repo root the legacy hearth.py shim
# shadows the installed package and every import of hearth.* fails. This is
# the src/ layout doing its job, and the gate has to respect it.
if "$PY" -c 'import pytest' >/dev/null 2>&1; then
    (
        cd "$(mktemp -d)" || exit 1
        PYTHONPATH="$ROOT/src" "$PY" -m pytest -q \
            --rootdir "$ROOT" -c "$ROOT/pyproject.toml" "$ROOT/tests"
    ) || fail=1
else
    skip "pytest not importable"
fi

step "legacy module compiles"
if [ -f hearth.py ]; then
    soft python3 -m py_compile hearth.py
fi

step "desktop entry"
if command -v desktop-file-validate >/dev/null 2>&1; then
    while IFS= read -r -d '' f; do soft desktop-file-validate "$f"; done \
        < <(find . -name '*.desktop' -not -path './.git/*' -print0)
else
    skip "desktop-file-validate not installed"
fi

step "appstream metadata"
if command -v appstreamcli >/dev/null 2>&1; then
    shopt -s nullglob
    metainfo=(data/metainfo/*.metainfo.xml data/metainfo/*.appdata.xml)
    if [ ${#metainfo[@]} -gt 0 ]; then
        for f in "${metainfo[@]}"; do soft appstreamcli validate --pedantic "$f"; done
    else
        skip "no metainfo file yet"
    fi
else
    skip "appstreamcli not installed"
fi

step "shell scripts"
if command -v shellcheck >/dev/null 2>&1; then
    # Only our own scripts. Without the prunes this walks into .venv and
    # reports findings in vendored virtualenv activate scripts, which is
    # noise we cannot fix and which turned the whole gate red.
    while IFS= read -r -d '' f; do soft shellcheck "$f"; done \
        < <(find . \( -name '.git' -o -name '.venv' -o -name 'venv' \
                      -o -name 'node_modules' -o -name '.mypy_cache' \) -prune -o \
                   -name '*.sh' -print0)
else
    skip "shellcheck not installed"
fi

printf '\n'
if [ "$fail" -ne 0 ]; then
    printf '\033[31mFAILED\033[0m\n'
    exit 1
fi
printf '\033[32mAll checks passed\033[0m\n'
