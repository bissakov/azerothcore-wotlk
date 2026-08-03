#!/usr/bin/env bash

set -euo pipefail

if (( $# < 1 || $# > 2 )); then
    echo "Usage: $0 <base> [head]" >&2
    exit 2
fi

root="$(git rev-parse --show-toplevel)"
cd "$root"

range=("$1")
if (( $# == 2 )); then
    range+=("$2")
fi

printf 'SELECT 1;\n' | sqlfluff lint --config .sqlfluff -

changed_files="$(mktemp)"
trap 'rm -f "$changed_files"' EXIT

git diff --name-only -z --diff-filter=ACDMR "${range[@]}" -- \
    ':(glob)data/sql/**/*.sql' > "$changed_files"
mapfile -d '' safety_files < "$changed_files"

if (( ${#safety_files[@]} )); then
    python apps/ci/check-sql-safety.py "${safety_files[@]}"
fi

git diff --name-only -z --diff-filter=ACMR "${range[@]}" -- \
    ':(glob)data/sql/**/*.sql' > "$changed_files"
mapfile -d '' lint_files < "$changed_files"

if (( ${#lint_files[@]} )); then
    sqlfluff lint --config .sqlfluff "${lint_files[@]}"
fi
