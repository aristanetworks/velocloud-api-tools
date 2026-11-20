#!/usr/bin/env bash
# Run the same checks as CI.
#
# Usage:
#   scripts/check.sh            # run everything
#   scripts/check.sh lint test  # run selected steps
#   scripts/check.sh fix        # apply ruff fixes and formatting
#
# Steps: lint (ruff check + ruff format --check), types (basedpyright), test (pytest)
set -euo pipefail

cd "$(dirname "$0")/.."

lint() {
    uv run ruff check
    uv run ruff format --check
}

types() {
    uv run basedpyright
}

test() {
    uv run pytest
}

fix() {
    uv run ruff check --fix
    uv run ruff format
}

steps=("$@")
if [ ${#steps[@]} -eq 0 ]; then
    steps=(lint types test)
fi

uv sync --all-packages --locked --quiet

for step in "${steps[@]}"; do
    case "$step" in
        lint | types | test | fix)
            echo "==> $step"
            "$step"
            ;;
        *)
            echo "unknown step: $step (expected lint, types, test, or fix)" >&2
            exit 2
            ;;
    esac
done
