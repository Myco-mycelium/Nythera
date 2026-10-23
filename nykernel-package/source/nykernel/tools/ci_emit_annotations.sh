#!/usr/bin/env bash
#
# ci_emit_annotations.sh -- emit a captured gate log as GitHub ::error::
# annotations, so a failing gate's output is readable through the
# check-runs annotations API even when job logs are not (non-admin
# clones cannot download job logs; annotations are public per check
# run). Context: the runner image under `ubuntu-latest` drifts out from
# under the gates -- GitHub announced the ubuntu-24 -> ubuntu-26 image
# migration beginning 2026-10-19 -- and when a gate then fails on the
# runner while passing locally, the versions plus the tail of the
# captured output are the difference between a diagnosis at cause and a
# guess. The calling step emits its own env/version annotation (it
# knows which tools matter); this script emits the captured output
# tail, numbered and chunk-fitted under the ~10-annotations-per-step
# cap. Numbered so a truncated first chunk is still attributable.
#
# Usage: ci_emit_annotations.sh LOGFILE LABEL
#
set -euo pipefail
LOG="$1"
LABEL="$2"
tail -c 27000 "$LOG" \
    | tr -d '\r' \
    | fold -s -w 3600 \
    | awk -v l="$LABEL" '{printf "::error::%s[%d] %s\n", l, NR, $0}'
