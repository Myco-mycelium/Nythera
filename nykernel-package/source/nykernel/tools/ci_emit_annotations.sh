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
# NOTE on the emitter's shape (load-bearing, caught by a real CI run):
# `fold` never merges input lines -- it only WRAPS overlong ones -- so a
# gate log of many short lines stays one line each, and the naive
# pipeline emits 100+ annotations; GitHub then silently DROPS
# everything past the cap of ~10 ::error:: annotations per step. The
# tail is therefore flattened into a single blob (newlines -> '|')
# FIRST, then hard-wrapped into ~1000-char chunks (the second real-run
# lesson: ~3600-char messages were silently dropped server-side even
# under the 10-per-step cap, so the diagnosis channel stayed empty);
# ~9 numbered chunks ride under that cap on purpose.
set -euo pipefail
LOG="$1"
LABEL="$2"
echo "::error::$LABEL: gate output follows, most recent last"
tail -c 8000 "$LOG" \
    | tr -d '\r' \
    | tr '\n' '|' \
    | fold -w 1000 \
    | awk -v l="$LABEL" '{printf "::error::%s[%d] %s\n", l, NR, $0}'
