#!/usr/bin/env bash
# check_nstudio_designs.sh — validate every shipped .nstudio design
# document against the NFS-001 contract tables (the same gate the
# daemon's import path enforces, ADR-0025), via nst_validate.py.
#
# Contract (pinned by TestCheckNstudioDesigns in
# source/nyhal-linux-backend/tests/test_ide_extension.py):
# - Discovers the backend tree's .nstudio files itself (no hardcoded
#   list that rots when a design is added).
# - Fails closed: a missing backend tree, a missing validator, or any
#   error diagnostic exits 1 with a ::error:: annotation AND the
#   diagnostics JSON on stdout.
# - A zero-design tree is a finding too (exits 1) — silence here would
#   hide a mass deletion the same way a silent checker hides a stale
#   gate (the Session 13 lesson).
# - Optional argument: an alternate backend root (the contract tests
#   build sandbox trees with a stub validator; the real tree is never
#   mutated by a test).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="${1:-$REPO_ROOT/source/nyhal-linux-backend}"
VALIDATOR="$BACKEND/nst_validate.py"

if [[ ! -f "$VALIDATOR" ]]; then
    echo "::error::nst_validate.py not found at $VALIDATOR — the design gate cannot run"
    exit 1
fi

# collect_files <dir...> — print .nstudio paths, one per line
files=()
while IFS= read -r f; do
    files+=("$f")
done < <(find "$BACKEND/shell" "$BACKEND/examples" "$BACKEND/tests/fixtures/nstudio" \
            -name '*.nstudio' -type f 2>/dev/null | sort)

if [[ ${#files[@]} -eq 0 ]]; then
    echo "::error::no .nstudio design documents found under shell/, examples/, or tests/fixtures/nstudio — a mass deletion is a finding, not a pass"
    exit 1
fi

echo "Validating ${#files[@]} .nstudio document(s)..."
# nst_validate exits 0 when all valid, 1 when error diagnostics exist
# (stdout still carries the JSON). Capture WITHOUT tripping set -e —
# the first draft aborted here and failed closed *silently*, which
# betrays the whole point of annotating the failure for the operator.
set +e
output="$(python3 "$VALIDATOR" "${files[@]}" 2>&1)"
rc=$?
set -e

if [[ $rc -ne 0 && $rc -ne 1 ]]; then
    echo "::error::nst_validate failed unexpectedly (exit $rc) — treating as a gate failure"
    echo "$output"
    exit 1
fi

diagnostic_count="$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])))' "$output" 2>/dev/null || echo "unparseable")"
if [[ "$diagnostic_count" == "0" ]]; then
    echo "NSTUDIO DESIGNS: OK (${#files[@]} document(s), 0 diagnostics)"
    exit 0
fi
if [[ "$diagnostic_count" == "unparseable" ]]; then
    echo "::error::nst_validate produced unparseable output — treating as a gate failure"
    echo "$output"
    exit 1
fi
echo "$output"
echo "::error::$diagnostic_count .nstudio diagnostic(s) — the designs no longer pass the import gate"
exit 1
