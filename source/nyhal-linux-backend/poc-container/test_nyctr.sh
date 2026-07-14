#!/bin/sh
# Repeatable version of the manual verification used when this PoC was
# first written. Not a real test framework - just enough to catch a
# regression if this script is edited later. Exits non-zero on any
# unexpected result.
set -e
cd "$(dirname "$0")"

fail() { echo "FAIL: $1" >&2; exit 1; }

echo "=== Test 1: hostname isolation ==="
HOST_HOSTNAME=$(hostname)
CTR_HOSTNAME=$(python3 nyctr.py --hostname nyctr-test-host -- sh -c 'hostname')
[ "$CTR_HOSTNAME" = "nyctr-test-host" ] || fail "expected container hostname 'nyctr-test-host', got '$CTR_HOSTNAME'"
[ "$CTR_HOSTNAME" != "$HOST_HOSTNAME" ] || fail "container hostname should differ from host"
echo "ok"

echo "=== Test 2: PID namespace isolation (container sees itself as PID 1) ==="
CTR_PID=$(python3 nyctr.py -- sh -c 'echo $$')
[ "$CTR_PID" = "1" ] || fail "expected container's own PID to be 1, got '$CTR_PID'"
echo "ok"

echo "=== Test 3: exit code propagation ==="
python3 nyctr.py -- sh -c 'exit 42' && EXIT_CODE=0 || EXIT_CODE=$?
[ "$EXIT_CODE" = "42" ] || fail "expected exit code 42, got $EXIT_CODE"
echo "ok"

echo "=== Test 4: cgroup created during run, cleaned up after ==="
python3 nyctr.py --memory-mb 32 --pid-limit 8 -- sh -c 'sleep 0.3' &
BGPID=$!
sleep 0.1
MID_RUN=$(ls /sys/fs/cgroup/memory/ 2>/dev/null | grep -c nyctr || true)
[ "$MID_RUN" -ge 1 ] || fail "expected a nyctr cgroup to exist mid-run"
wait "$BGPID"
AFTER=$(ls /sys/fs/cgroup/memory/ 2>/dev/null | grep -c nyctr || true)
[ "$AFTER" = "0" ] || fail "expected the nyctr cgroup to be removed after teardown, found $AFTER remaining"
echo "ok"

echo
echo "All tests passed."
