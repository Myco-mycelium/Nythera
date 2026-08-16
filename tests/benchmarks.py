#!/usr/bin/env python3
"""Consolidated benchmarks for the Nyrqis Linux Backend.

Runs every benchmark in `tests/BENCHMARK_PLAN.md` that is runnable on
this host in one reproducible script:

- §1 IPC round-trip latency (NPS-003 §3): the `call` primitive, p50/p95/p99.
- §3 IPC token-bucket defaults (ADR-0009): sustained rate under the
  default bucket.
- §2 Zstd compression levels (ADR-0007): level sweep (imports the
  standalone `benchmark_zstd.py`).
- §4 FUSE overhead (ADR-0016): NyFS operation-handler throughput vs native
  file I/O on the same disk, as a proxy for the real FUSE-vs-ext4
  comparison (which requires a live FUSE mount). With per-block CoW
  (2026-08-12) this reports two access patterns — 4 KiB sequential writes
  (per-call overhead dominates) and 1 MiB-chunk streaming (per-block CoW
  win) — plus a block-size sweep on the 4 KiB pattern.
- §4 (live mount, 2026-08-12): ``--nyfs-mount`` drives the same patterns
  through a REAL kernel FUSE mount (fusepy + /dev/fuse + fusermount;
  skipped when absent) vs native I/O, and reports how the kernel batches
  write requests to the daemon.
- §5 (persisted image, 2026-08-12): ``--nyfs-persist`` builds a
  deterministic mixed asset corpus, saves it to disk (durability,
  NPS-004 §7), reloads it, and measures the loaded-image read patterns
  plus the end-to-end storage compression ratio.
- §5 (save() commit-cost levers, 2026-08-12): ``--save-levers`` measures
  the knobs the fsync-bound finding named — block size (64 KiB / 256 KiB /
  1 MiB), ``save(batched_fsync=True)`` group-commit, and the new
  ``save(use_journal=True)`` append-only journal (one fsync per
  transaction) — on the same corpus, each verified by a save -> load ->
  read round-trip.
- §5 (cross-snapshot dedup, 2026-08-12): ``--snapshot-dedup`` measures
  how much block-store space a snapshot chain really costs when 20% of
  the corpus changes between snapshots (CoW block sharing).
- §2 (codec comparison, 2026-08-12): ``--codec`` compares zstd level 3
  (NyFS default) against zlib level 6 (stdlib; python-lz4 is not
  installed on this host) on the ``benchmark_zstd`` corpus.
- §2 (real-corpus ratio, 2026-08-12): ``--real-corpus`` runs the
  end-to-end compression-ratio measurement on a deterministic sample of
  real files from ``/usr/share`` (fonts, locale, man, mime, zoneinfo,
  applications).
- §18 (container launch-plan primitives, 2026-08-13): ``--container``
  measures the ADR-0020 priority #5 primitives — launcher argv
  (FIND-BACKEND-004), cgroup v1/v2 plan (FIND-BACKEND-003), uid/gid
  root maps, NPS-010 §4 state machine — on the pure-Python floor, and
  the Rust FFI path too when the crate is built (dev host has no Rust
  toolchain — CI or a host with the crate adds the FFI numbers).
- §25 (container cold-start, 2026-08-15): ``--launcher-coldstart``
  A/Bs the compiled launcher-init (`rust/launcher`, ADR-0020) against
  the Python launcher — real spawn→wait latency, same session,
  skip-gated on unprivileged user namespaces.
- §26 (NyVault byte path, 2026-08-15): ``--vault-io`` measures the
  FUSE-passthrough data plane through the CALL/REPLY loop — write/read
  p50/p95 per payload (4 KiB and the 32 KiB per-call cap) through
  ``NyVaultOperations`` against a real in-process storage service,
  plaintext and ADR-0023-encrypted.
- §27 (live encrypted NyVault mount, 2026-08-15): ``--vault-mount-io``
  benchmarks the ENCRYPTED volume through a REAL kernel FUSE mount
  (ADR-0022's data-plane mount) vs native I/O, in the §19 isolated
  child process; every kernel request is a storage-service CALL into
  the AEAD block layer.
- §28 (quota ledger refresh, 2026-08-16): ``--ledger-refresh`` times
  the storage service's per-commit usage refresh (ADR-0022 accounting
  — the NyFS tree walk + attribution + the on-disk physical-byte
  stat) on volumes of 1 k and 10 k files.

Usage:
  python3 tests/benchmarks.py --all       # everything (default)
  python3 tests/benchmarks.py --ipc       # §1 IPC round-trip
  python3 tests/benchmarks.py --ipc-transport  # §20 over the real UDS transport
  python3 tests/benchmarks.py --bucket    # §3 token-bucket defaults
  python3 tests/benchmarks.py --zstd      # §2 Zstd level sweep
  python3 tests/benchmarks.py --nyfs      # §4 NyFS vs native proxy
  python3 tests/benchmarks.py --nyfs-mount  # §4 live-mount FUSE vs native
  python3 tests/benchmarks.py --nyfs-persist  # §5 persisted-image lifecycle
  python3 tests/benchmarks.py --save-levers   # §5 save() commit-cost levers
  python3 tests/benchmarks.py --snapshot-dedup  # §5 cross-snapshot dedup
  python3 tests/benchmarks.py --codec        # §2 zstd vs zlib codec compare
  python3 tests/benchmarks.py --real-corpus  # §2 real-corpus ratio
  python3 tests/benchmarks.py --container    # §18 launch-plan primitives
  python3 tests/benchmarks.py --launcher-coldstart  # §25 cold-start A/B
  python3 tests/benchmarks.py --vault-io     # §26 NyVault byte path
  python3 tests/benchmarks.py --vault-mount-io  # §27 live encrypted mount
  python3 tests/benchmarks.py --ledger-refresh  # §28 quota ledger refresh

Honesty notes (NPC-002 §5.2):
- These are FIRST-PASS microbenchmarks on this host, not the full plan
  methodology (which requires two containers, load variants, and a real
  FUSE mount). The in-process IPC path (§1) bounds the control-plane
  cost; §20 measures the final IPC wire cost over the real Unix-domain
  datagram transport (`--ipc-transport`). A shared-memory transport
  remains an alternative/complement (deferred).
- Results belong in `tests/BENCHMARK_RESULTS.md`, not in this file.
- A full `--all` run takes roughly 1–3 minutes (the Zstd level sweep is
  the long pole at ~30 s, and `--nyfs-mount` adds ~15–20 s where the
  host supports a live FUSE mount); use the individual flags to re-run
  one section quickly.
- The live-mount benchmark runs in an isolated child process (2026-08-14,
  §19 incident fix): a wedged kernel FUSE request puts a process in
  un-interruptible D-state that in-process watchdogs cannot clear, so
  the parent enforces a timeout, kills the child group, and lazily
  unmounts — the runner survives and reports the section as skipped.
  A truly wedged child (D-state) may require root or a reboot to clear.
"""

import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "source" / "nyhal-linux-backend"))

from ipc.core import IPCManager, TokenBucket  # noqa: E402
from ipc.transport import IPCDatagramServer  # noqa: E402
from ipc.service import BackendStatusService  # noqa: E402
from ipc import loop as ipc_loop  # noqa: E402
from fuse.nyfs import NyFSFilesystem, NyFSOperations  # noqa: E402

IPC_ITERATIONS = 20000
FS_TOTAL_BYTES = 8 * 1024 * 1024
FS_CHUNK = 4096
SMALL_FILES = 1000


def percentile(sorted_values, pct):
    idx = int(len(sorted_values) * pct)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _spawn_responder(mgr, endpoint, payload_size, stop):
    def responder():
        while not stop.is_set():
            msg = mgr.receive(endpoint.endpoint_id, timeout_s=0.1)
            if msg is not None and msg.message_type.value == "call":
                mgr.reply(msg.message_id, b"r" * payload_size)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    return thread


def benchmark_ipc_roundtrip(n=IPC_ITERATIONS, payload_size=64):
    """p50/p95/p99 of the `call` primitive (BENCHMARK_PLAN §1).

    The endpoints are given a deliberately high token budget so the
    measured distribution is the primitive's control-plane latency, not
    the default rate limiter (whose throttle behaviour is measured
    separately by ``benchmark_default_bucket``).
    """
    mgr = IPCManager()
    roomy = TokenBucket(bucket_size=1_000_000, tokens_per_second=1_000_000.0)
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    cli = mgr.create_endpoint("container-cli", "ep-cli")
    svc.rate_limit = roomy  # measure the primitive, not the limiter
    cli.rate_limit = roomy
    payload = b"x" * payload_size
    stop = threading.Event()
    thread = _spawn_responder(mgr, svc, payload_size, stop)
    try:
        for _ in range(200):  # Warmup.
            mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=5.0)
        latencies = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=5.0)
            latencies.append((time.perf_counter_ns() - t0) / 1000.0)  # microseconds
    finally:
        stop.set()
        thread.join(timeout=1.0)
    latencies.sort()
    return {
        "iterations": n,
        "payload_bytes": payload_size,
        "p50_us": round(percentile(latencies, 0.50), 2),
        "p95_us": round(percentile(latencies, 0.95), 2),
        "p99_us": round(percentile(latencies, 0.99), 2),
        "mean_us": round(statistics.mean(latencies), 2),
        "max_us": round(latencies[-1], 2),
    }


TRANSPORT_IPC_WARMUP = 200


def benchmark_ipc_transport_roundtrip(n=IPC_ITERATIONS, payload_size=64):
    """p50/p95/p99 of a CALL/REPLY over the REAL cross-process transport
    (``ipc/transport.py``, BENCHMARK_PLAN §1): client and server in
    SEPARATE processes, framed by the wire codec over    ``AF_UNIX``
    ``SOCK_DGRAM`` with kernel ``SO_PASSCRED`` sender identity (the
    client runs in a separate process; the server side runs in the
    benchmark's own thread, so the datagram exchange is genuinely
    cross-process). This is the number NPS-003 §6.1's <100 us gate is
    about — the in-process ``benchmark_ipc_roundtrip`` bounds the
    control plane only.

    The server authenticates the client by its kernel-attached pid
    (registered before the ready handshake, so no datagram is dropped
    by the registry), and the endpoint gets a deliberately roomy token
    budget so the distribution is the wire cost, not ADR-0009's default
    limiter. The client measures per-call wall latency in its own
    process and reports the same percentile shape as the in-process
    benchmark.
    """
    base = tempfile.mkdtemp(prefix="nyrqis-ipc-bench-")
    svc_path = os.path.join(base, "svc.sock")
    cli_path = os.path.join(base, "cli.sock")
    ready_path = os.path.join(base, "ready")
    out_path = os.path.join(base, "client_results.json")

    mgr = IPCManager()
    roomy = TokenBucket(bucket_size=1_000_000, tokens_per_second=1_000_000.0)
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    svc.rate_limit = roomy  # measure the primitive, not the limiter
    server = IPCDatagramServer(mgr, "ep-svc", svc_path)
    server.bind()
    stop = threading.Event()

    def handler(msg, sender, sender_path):
        server.reply(sender_path, msg.message_id, b"r" * payload_size)

    server.on_call = handler
    threading.Thread(target=server.serve, args=(stop,), daemon=True).start()

    backend_dir = str(Path(__file__).resolve().parent.parent
                      / "source" / "nyhal-linux-backend")
    client_src = (
        "import json, os, statistics, sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "(_backend_dir, cli_path, svc_path, out_path, ready_path, "
        "n_s, payload_s, warm_s) = sys.argv[1:]\n"
        "deadline = time.time() + 10\n"
        "while not os.path.exists(ready_path) and time.time() < deadline:\n"
        "    time.sleep(0.005)\n"
        "from ipc.transport import IPCClient\n"
        "c = IPCClient('bench-cli', cli_path).bind()\n"
        "payload = b'x' * int(payload_s)\n"
        "for _ in range(int(warm_s)):\n"
        "    c.call(svc_path, payload, timeout_s=5)\n"
        "lats = []\n"
        "for _ in range(int(n_s)):\n"
        "    t0 = time.perf_counter_ns()\n"
        "    c.call(svc_path, payload, timeout_s=5)\n"
        "    lats.append((time.perf_counter_ns() - t0) / 1000.0)\n"
        "lats.sort()\n"
        "def pct(v, p):\n"
        "    idx = int(len(v) * p)\n"
        "    return v[min(idx, len(v) - 1)]\n"
        "res = {'iterations': int(n_s), 'payload_bytes': int(payload_s),\n"
        "       'p50_us': round(pct(lats, 0.50), 2),\n"
        "       'p95_us': round(pct(lats, 0.95), 2),\n"
        "       'p99_us': round(pct(lats, 0.99), 2),\n"
        "       'mean_us': round(statistics.mean(lats), 2),\n"
        "       'min_us': round(lats[0], 2), 'max_us': round(lats[-1], 2)}\n"
        "with open(out_path, 'w') as fh:\n"
        "    json.dump(res, fh)\n"
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", client_src, backend_dir, cli_path,
             svc_path, out_path, ready_path, str(n), str(payload_size),
             str(TRANSPORT_IPC_WARMUP)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        # Register the client's pid BEFORE it may send (it waits on the
        # ready marker - no TOCTOU, no dropped datagrams).
        server.pid_registry = {proc.pid: "bench-cli"}
        with open(ready_path, "w") as fh:
            fh.write("go")
        try:
            out, _ = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            # A wedged client must not orphan the runner: kill it and
            # report the section as failed (the file's own §19 note
            # requires the runner to survive child failure).
            proc.kill()
            proc.communicate()
            return {"error": "client timed out after 120s"}
        if proc.returncode != 0:
            return {"error": f"client failed (rc={proc.returncode}): {out[-300:]}"}
        try:
            with open(out_path) as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            return {"error": f"client produced no valid result: {out[-300:]}"}
    finally:
        stop.set()
        server.close()
        shutil.rmtree(base, ignore_errors=True)
    return result


IPCD_IPC_WARMUP = 200


def benchmark_ipcd_roundtrip(n=IPC_ITERATIONS, payload_size=11):
    """ADR-0021 A/B: Python floor vs Rust serving loop — the wire p50
    of the status service's ``ping`` over the REAL cross-process
    transport. Both sides serve the SAME request (``{"op": "ping"}``)
    with the SAME byte-identical reply (the floor via
    ``BackendStatusService``, the loop via its built-in ping handler),
    and the client measures per-call wall latency in its own process.

    The loop is the first NyRuntime-shaped artifact (ADR-0021): it owns
    poll → recvmsg → parse → authorize → reply inside the Rust process
    and crosses the boundary once per batch (a bounded drain per step),
    so the per-message ctypes boundary tax is paid once per batch, not
    twice per round trip. ADR-0021's close gate — the loop's wire p50
    must BEAT the floor in a same-session A/B AND meet NPS-003 §6.1's
    <100 µs median — is judged on this section's numbers.
    """

    def run_side(kind):
        base = tempfile.mkdtemp(prefix=f"nyrqis-ipcd-{kind}-")
        svc_path = os.path.join(base, "svc.sock")
        cli_path = os.path.join(base, "cli.sock")
        ready_path = os.path.join(base, "ready")
        out_path = os.path.join(base, "client_results.json")
        mgr = IPCManager()
        server = IPCDatagramServer(
            mgr, "ep-svc", svc_path, trusted_uids={os.getuid()})
        server.bind()
        stop = threading.Event()
        loop = None
        if kind == "floor":
            BackendStatusService().attach(server)
            threading.Thread(
                target=server.serve, args=(stop,), daemon=True).start()
        backend_dir = str(Path(__file__).resolve().parent.parent
                          / "source" / "nyhal-linux-backend")
        client_src = (
            "import json, os, statistics, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "(_backend_dir, cli_path, svc_path, out_path, ready_path, "
            "n_s, warm_s) = sys.argv[1:]\n"
            "deadline = time.time() + 10\n"
            "while not os.path.exists(ready_path) and time.time() < deadline:\n"
            "    time.sleep(0.005)\n"
            "from ipc.transport import IPCClient\n"
            "c = IPCClient('bench-cli', cli_path).bind()\n"
            "payload = b'{\\\"op\\\": \\\"ping\\\"}'\n"
            "for _ in range(int(warm_s)):\n"
            "    c.call(svc_path, payload, timeout_s=5)\n"
            "lats = []\n"
            "for _ in range(int(n_s)):\n"
            "    t0 = time.perf_counter_ns()\n"
            "    c.call(svc_path, payload, timeout_s=5)\n"
            "    lats.append((time.perf_counter_ns() - t0) / 1000.0)\n"
            "lats.sort()\n"
            "def pct(v, p):\n"
            "    idx = int(len(v) * p)\n"
            "    return v[min(idx, len(v) - 1)]\n"
            "res = {'iterations': int(n_s), 'payload_bytes': 11,\n"
            "       'p50_us': round(pct(lats, 0.50), 2),\n"
            "       'p95_us': round(pct(lats, 0.95), 2),\n"
            "       'p99_us': round(pct(lats, 0.99), 2),\n"
            "       'mean_us': round(statistics.mean(lats), 2),\n"
            "       'min_us': round(lats[0], 2), 'max_us': round(lats[-1], 2)}\n"
            "with open(out_path, 'w') as fh:\n"
            "    json.dump(res, fh)\n"
        )
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", client_src, backend_dir, cli_path,
                 svc_path, out_path, ready_path, str(n),
                 str(IPCD_IPC_WARMUP)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            # Register the client's pid BEFORE it may send (it waits on
            # the ready marker). The loop's policy is fixed at creation,
            # so create it now that the pid is known.
            if kind == "loop":
                loop = ipc_loop.IpcdLoop(
                    server.endpoint._sock.fileno(),
                    batch_max=64,
                    pids={proc.pid: "bench-cli"},
                    trusted_uids=[os.getuid()],
                )

                def drive():
                    while not stop.is_set():
                        try:
                            loop.step(100)
                        except Exception:
                            break

                threading.Thread(target=drive, daemon=True).start()
            server.pid_registry = {proc.pid: "bench-cli"}
            with open(ready_path, "w") as fh:
                fh.write("go")
            try:
                out, _ = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return {"error": "client timed out after 120s"}
            if proc.returncode != 0:
                return {"error": f"client failed (rc={proc.returncode}): {out[-300:]}"}
            try:
                with open(out_path) as fh:
                    result = json.load(fh)
            except (OSError, ValueError):
                return {"error": f"client produced no valid result: {out[-300:]}"}
        finally:
            stop.set()
            if loop is not None:
                loop.close()
            server.close()
            shutil.rmtree(base, ignore_errors=True)
        return result

    floor = run_side("floor")
    loop = run_side("loop")
    return {"floor": floor, "loop": loop}


def benchmark_ipcd_dispatch(n=IPC_ITERATIONS, payload_size=11, op="bogus"):
    """ADR-0021 decision point 1 A/B — the non-ping dispatch handoff:
    the wire p50 of a NON-ping op over the REAL transport, Python
    floor vs Rust loop. ``op="bogus"`` exercises the status service's
    deterministic ``unknown operation`` reply; ``op="status"`` (see
    ``benchmark_ipcd_control``) exercises a REAL control op with the
    full CAP_SYSTEM_INFO authorization + handler work.

    The loop cannot answer these ops itself: it queues the request, the
    driver drains the batch (one boundary crossing), the Python service
    handler builds the reply with the same codec the floor uses, and
    the loop routes it (one boundary crossing back) — the batch
    boundary replaces the floor's per-message loop. The reply is
    byte-identical in both backends, so the A/B isolates the dispatch
    path's cost vs the floor.
    """

    def run_side(kind):
        base = tempfile.mkdtemp(prefix=f"nyrqis-ipcd-dispatch-{kind}-")
        svc_path = os.path.join(base, "svc.sock")
        cli_path = os.path.join(base, "cli.sock")
        ready_path = os.path.join(base, "ready")
        out_path = os.path.join(base, "client_results.json")
        mgr = IPCManager()
        server = IPCDatagramServer(
            mgr, "ep-svc", svc_path, trusted_uids={os.getuid()})
        server.bind()
        stop = threading.Event()
        loop = None
        dispatcher = None
        if kind == "floor":
            BackendStatusService().attach(server)
            threading.Thread(
                target=server.serve, args=(stop,), daemon=True).start()
        backend_dir = str(Path(__file__).resolve().parent.parent
                          / "source" / "nyhal-linux-backend")
        wire_payload = json.dumps({"op": op}).encode()
        client_src = (
            "import json, os, statistics, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "(_backend_dir, cli_path, svc_path, out_path, ready_path, "
            "n_s, warm_s, payload_b64) = sys.argv[1:]\n"
            "import base64\n"
            "payload = base64.b64decode(payload_b64)\n"
            "deadline = time.time() + 10\n"
            "while not os.path.exists(ready_path) and time.time() < deadline:\n"
            "    time.sleep(0.005)\n"
            "from ipc.transport import IPCClient\n"
            "c = IPCClient('bench-cli', cli_path).bind()\n"
            "for _ in range(int(warm_s)):\n"
            "    c.call(svc_path, payload, timeout_s=5)\n"
            "lats = []\n"
            "for _ in range(int(n_s)):\n"
            "    t0 = time.perf_counter_ns()\n"
            "    c.call(svc_path, payload, timeout_s=5)\n"
            "    lats.append((time.perf_counter_ns() - t0) / 1000.0)\n"
            "lats.sort()\n"
            "def pct(v, p):\n"
            "    idx = int(len(v) * p)\n"
            "    return v[min(idx, len(v) - 1)]\n"
            "res = {'iterations': int(n_s), 'payload_bytes': len(payload),\n"
            "       'p50_us': round(pct(lats, 0.50), 2),\n"
            "       'p95_us': round(pct(lats, 0.95), 2),\n"
            "       'p99_us': round(pct(lats, 0.99), 2),\n"
            "       'mean_us': round(statistics.mean(lats), 2),\n"
            "       'min_us': round(lats[0], 2), 'max_us': round(lats[-1], 2)}\n"
            "with open(out_path, 'w') as fh:\n"
            "    json.dump(res, fh)\n"
        )
        try:
            import base64
            proc = subprocess.Popen(
                [sys.executable, "-c", client_src, backend_dir, cli_path,
                 svc_path, out_path, ready_path, str(n),
                 str(IPCD_IPC_WARMUP),
                 base64.b64encode(wire_payload).decode()],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            if kind == "loop":
                loop = ipc_loop.IpcdLoop(
                    server.endpoint._sock.fileno(),
                    batch_max=64,
                    pids={proc.pid: "bench-cli"},
                    trusted_uids=[os.getuid()],
                )
                from ipc.dispatch import IpcdLoopDispatcher
                from ipc.service import ServiceRouter
                router = ServiceRouter()
                router.register("status", BackendStatusService())
                dispatcher = IpcdLoopDispatcher(loop, router)

                def drive():
                    while not stop.is_set():
                        try:
                            dispatcher.serve_once(100)
                        except Exception:
                            break

                threading.Thread(target=drive, daemon=True).start()
            server.pid_registry = {proc.pid: "bench-cli"}
            with open(ready_path, "w") as fh:
                fh.write("go")
            try:
                out, _ = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return {"error": "client timed out after 120s"}
            if proc.returncode != 0:
                return {"error": f"client failed (rc={proc.returncode}): {out[-300:]}"}
            try:
                with open(out_path) as fh:
                    result = json.load(fh)
            except (OSError, ValueError):
                return {"error": f"client produced no valid result: {out[-300:]}"}
        finally:
            stop.set()
            if loop is not None:
                loop.close()
            server.close()
            shutil.rmtree(base, ignore_errors=True)
        return result

    floor = run_side("floor")
    loop = run_side("loop")
    return {"floor": floor, "loop": loop}


def benchmark_ipcd_control(n=IPC_ITERATIONS):
    """The MAIN-socket control-op A/B — the wire p50 of a REAL control
    op (``{"op": "status"}`` → the full CAP_SYSTEM_INFO authorization
    + status handler) over the real transport, Python floor vs Rust
    loop. The main service socket serves status/control through the
    loop when the crate is present (floor fallback crate-less), so this
    measures what a daemon operator actually pays per control op on
    each path. The status handler runs in Python on BOTH sides, so the
    A/B isolates the serving path's cost on top of the handler — the
    question the earlier synthetic ``bogus`` dispatch A/B (§23) left
    open for a real op.
    """
    return benchmark_ipcd_dispatch(n=n, op="status")


def benchmark_ipcd_refresh(n=20000):
    """ADR-0021 per-container pid-table refresh cost: the isolated
    ``set_policy`` FFI call the daemon makes on every container
    spawn/terminate. In-process (no network) — a bound loop socket with
    an empty policy, refreshed ``n`` times with a small pid table. The
    refresh is a plain-data policy push across the boundary; this is
    its per-call cost in the daemon's lifecycle path.
    """
    base = tempfile.mkdtemp(prefix="nyrqis-ipcd-refresh-")
    svc_path = os.path.join(base, "svc.sock")
    mgr = IPCManager()
    server = IPCDatagramServer(mgr, "ep-svc", svc_path)
    server.bind()
    loop = None
    try:
        loop = ipc_loop.IpcdLoop(
            server.endpoint._sock.fileno(), batch_max=64)
        lats = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            loop.set_policy(pids={os.getpid(): "bench-cli"})
            lats.append((time.perf_counter_ns() - t0) / 1000.0)
        lats.sort()

        def pct(v, p):
            idx = int(len(v) * p)
            return v[min(idx, len(v) - 1)]

        return {
            "iterations": n,
            "p50_us": round(pct(lats, 0.50), 3),
            "p95_us": round(pct(lats, 0.95), 3),
            "p99_us": round(pct(lats, 0.99), 3),
            "mean_us": round(statistics.mean(lats), 3),
            "min_us": round(lats[0], 3),
            "max_us": round(lats[-1], 3),
        }
    finally:
        if loop is not None:
            loop.close()
        server.close()
        shutil.rmtree(base, ignore_errors=True)


def benchmark_default_bucket(duration_s=2.0, payload_size=64):
    """Sustained round-trips under the DEFAULT token bucket (ADR-0009 §3).

    ``create_endpoint`` defaults to ``TokenBucket(100, 50/s)``. This
    measures how many call round-trips per second that actually sustains
    (and how many are throttled) — the raw data point ADR-0009's default
    parameter decision needs.
    """
    mgr = IPCManager()
    svc = mgr.create_endpoint("container-svc", "ep-svc")  # default bucket
    cli = mgr.create_endpoint("container-cli", "ep-cli")  # default bucket
    payload = b"x" * payload_size
    stop = threading.Event()
    thread = _spawn_responder(mgr, svc, payload_size, stop)
    succeeded = 0
    throttled = 0
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        # A throttled call returns None (send_message refused a token) —
        # it does not raise, so None must be counted as throttled.
        if mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=0.5) is None:
            throttled += 1
        else:
            succeeded += 1
    stop.set()
    thread.join(timeout=1.0)
    return {
        "duration_s": duration_s,
        "succeeded": succeeded,
        "throttled": throttled,
        "sustained_calls_per_sec": round(succeeded / duration_s, 1),
        "throttled_per_sec": round(throttled / duration_s, 1),
    }


def _nyfs_throughput(fs, total, chunk, random_offsets=False):
    """Write ``total`` bytes in ``chunk`` pieces; return MB/s."""
    f = fs.create_file("/bench.bin")
    data = os.urandom(4096)
    t0 = time.perf_counter()
    if random_offsets:
        # 4 KiB scatter across a 16 MiB address space (asset-like).
        # Fixed seed (7) so runs are reproducible.
        import random

        rng = random.Random(7)
        off = 0
        written = 0
        while written < total:
            off = rng.randrange(0, 16 * 1024 * 1024, 4096)
            fs.write(f, data, off)
            written += len(data)
    else:
        off = 0
        for _ in range(total // chunk):
            fs.write(f, data, off)
            off += chunk
    elapsed = time.perf_counter() - t0
    return round(total / elapsed / (1024 * 1024), 2) if elapsed else float("inf")


def benchmark_nyfs_vs_native():
    """NyFS operations-layer throughput vs native file I/O (§4 proxy).

    With per-block CoW (2026-08-12) the access pattern matters, so this
    reports two: 4 KiB sequential writes (per-call overhead dominates —
    each write compresses/checksums a full ``block_size`` block) and
    1 MiB-chunk streaming (per-block CoW's write-amplification win).
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Native baseline: sequential write + read on the same disk.
        native_path = os.path.join(tmp, "native.bin")
        data = os.urandom(FS_CHUNK)
        t0 = time.perf_counter()
        with open(native_path, "wb") as fh:
            for _ in range(FS_TOTAL_BYTES // FS_CHUNK):
                fh.write(data)
        native_write_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        with open(native_path, "rb") as fh:
            while fh.read(FS_CHUNK):
                pass
        native_read_s = time.perf_counter() - t0

        nyfs_root = os.path.join(tmp, "nyfs")

        # Access pattern A: 4 KiB sequential writes (old benchmark shape).
        fs_a = NyFSFilesystem(os.path.join(nyfs_root, "a"))
        small_write_mbps = _nyfs_throughput(fs_a, FS_TOTAL_BYTES, FS_CHUNK)

        # Access pattern B: 1 MiB-chunk streaming writes.
        fs_b = NyFSFilesystem(os.path.join(nyfs_root, "b"))
        stream_write_mbps = _nyfs_throughput(
            fs_b, FS_TOTAL_BYTES, 1024 * 1024)

        # Access pattern C: 4 KiB scattered writes (random offsets).
        fs_c = NyFSFilesystem(os.path.join(nyfs_root, "c"))
        scatter_write_mbps = _nyfs_throughput(
            fs_c, FS_TOTAL_BYTES, FS_CHUNK, random_offsets=True)

        # Sequential 8 MiB read through the operation handlers. The file
        # is written in 1 MiB chunks (single pass per block) so the read
        # timing measures reads, not the write path.
        fs_d = NyFSFilesystem(os.path.join(nyfs_root, "d"))
        f = fs_d.create_file("/bench.bin")
        off = 0
        for _ in range(FS_TOTAL_BYTES // (1024 * 1024)):
            fs_d.write(f, os.urandom(1024 * 1024), off)
            off += 1024 * 1024
        t0 = time.perf_counter()
        for i in range(FS_TOTAL_BYTES // FS_CHUNK):
            fs_d.read(f, FS_CHUNK, (i * FS_CHUNK) % FS_TOTAL_BYTES)
        nyfs_read_s = time.perf_counter() - t0

        # Block-size sweep on the 4 KiB-write pattern (tuning data for
        # the block_size default decision, not a decision itself).
        sweep = {}
        for bs in (4096, 16384, 65536, 262144):
            fs_s = NyFSFilesystem(os.path.join(nyfs_root, f"s{bs}"),
                                  block_size=bs)
            sweep[bs] = _nyfs_throughput(fs_s, FS_TOTAL_BYTES, FS_CHUNK)

        # Small-file creation (many game assets, NPS-006 §5).
        ops = NyFSOperations(fs_a)
        t0 = time.perf_counter()
        for i in range(SMALL_FILES):
            ops.mknod(f"/asset_{i}.dat", 0o644, 0)
        small_create_s = time.perf_counter() - t0

    def mbps(total, seconds):
        return round(total / seconds / (1024 * 1024), 2) if seconds else float("inf")

    return {
        "total_bytes": FS_TOTAL_BYTES,
        "small_files": SMALL_FILES,
        "block_size": NyFSFilesystem.BLOCK_SIZE,
        "native_write_mbps": mbps(FS_TOTAL_BYTES, native_write_s),
        "native_read_mbps": mbps(FS_TOTAL_BYTES, native_read_s),
        "nyfs_write_4k_mbps": small_write_mbps,
        "nyfs_write_1m_mbps": stream_write_mbps,
        "nyfs_write_scatter_mbps": scatter_write_mbps,
        "nyfs_read_mbps": mbps(FS_TOTAL_BYTES, nyfs_read_s),
        "small_create_per_sec": round(SMALL_FILES / small_create_s, 1),
        "block_size_sweep_4k_mbps": sweep,
    }


def _fuse_mount_available() -> bool:
    """True when a live FUSE mount can be attempted on this host."""
    try:
        if not os.path.exists("/dev/fuse"):
            return False
        import shutil

        if shutil.which("fusermount3") is None and shutil.which("fusermount") is None:
            return False
        from fuse.nyfs import _import_fusepy

        return _import_fusepy() is not None
    except Exception:
        return False


class _CountingOps(NyFSOperations):
    """Wraps the ops layer to count what the kernel actually sends us."""

    def __init__(self, fs):
        super().__init__(fs)
        self.write_calls = 0
        self.max_write = 0

    def write(self, path, data, offset, fh=None):
        self.write_calls += 1
        self.max_write = max(self.max_write, len(data))
        return super().write(path, data, offset, fh)


def _nyfs_mount_worker(total=16 * 1024 * 1024):
    """The live-mount benchmark itself — run in an isolated child
    process by ``benchmark_nyfs_mount`` (never call directly).

    The parent passes a fresh mountpoint via ``NYRQIS_BENCH_MNT`` and
    enforces a timeout; a wedged kernel FUSE request leaves the process
    in un-interruptible D-state that neither SIGKILL nor an in-process
    ``os._exit(99)`` can clear (exit_group blocks on the stuck thread),
    so the only safe containment is a killable child. The in-process
    watchdog remains as a first line of defence for slow-but-not-wedged
    mounts; the parent's timeout is the real guard.
    """
    from fuse.nyfs import NyFSMount

    def mbps(bytes_, seconds):
        return round(bytes_ / seconds / (1024 * 1024), 2) if seconds else float("inf")

    def bench_write(path, chunk, size):
        data = os.urandom(chunk)
        with open(path, "wb") as fh:
            t0 = time.perf_counter()
            off = 0
            while off < size:
                fh.write(data[:size - off])
                off += chunk
            fh.flush()
            return mbps(size, time.perf_counter() - t0)

    def bench_read(path, chunk, size):
        with open(path, "rb") as fh:
            t0 = time.perf_counter()
            read = 0
            while read < size:
                fh.read(chunk)
                read += chunk
            return mbps(size, time.perf_counter() - t0)

    mnt = os.environ["NYRQIS_BENCH_MNT"]
    base = os.path.dirname(mnt)  # the parent's isolated mkdtemp dir
    total = int(os.environ.get("NYRQIS_BENCH_TOTAL", total))
    native_dir = os.path.join(base, "native")
    os.makedirs(native_dir, exist_ok=True)
    fs = NyFSFilesystem(os.path.join(base, "fs"))
    ops = _CountingOps(fs)
    m = NyFSMount(fs, mnt)
    m.operations = ops
    # First-line watchdog only — see module docstring note; the parent's
    # timeout is what actually contains a D-state wedge.
    watchdog = threading.Timer(60.0, lambda: os._exit(99))
    watchdog.start()
    try:
        if not m.mount(foreground=True, blocking=False):
            return {"skipped": "mount could not be started"}
        if not m.wait_ready(timeout=5.0):
            return {"skipped": "mount never became live"}

        results = {}
        for chunk, tag in ((1024 * 1024, "1m"), (4096, "4k")):
            results[f"write_{tag}_fuse_mbps"] = bench_write(
                os.path.join(mnt, "b.bin"), chunk, total)
            results[f"write_{tag}_native_mbps"] = bench_write(
                os.path.join(native_dir, "b.bin"), chunk, total)
        for chunk, tag in ((1024 * 1024, "1m"), (4096, "4k")):
            results[f"read_{tag}_fuse_mbps"] = bench_read(
                os.path.join(mnt, "b.bin"), chunk, total)
            results[f"read_{tag}_native_mbps"] = bench_read(
                os.path.join(native_dir, "b.bin"), chunk, total)

        # Kernel write batching: how does the daemon see a 1 MiB write?
        ops.write_calls = ops.max_write = 0
        with open(os.path.join(mnt, "b.bin"), "wb") as fh:
            fh.write(os.urandom(1024 * 1024))
            fh.flush()
        results["write_requests_per_1m"] = ops.write_calls
        results["max_write_request_bytes"] = ops.max_write
        results["total_bytes"] = total
        return results
    finally:
        watchdog.cancel()
        try:
            m.unmount()
        except Exception:
            pass
        try:
            subprocess.run(["fusermount3", "-u", mnt],
                           capture_output=True, timeout=5)
        except Exception:
            pass


def benchmark_nyfs_mount(total=16 * 1024 * 1024, timeout_s=150):
    """Through a REAL FUSE mount vs native I/O on the same tmp dir (§4).

    First-pass, environment-gated (skipped when fusepy, /dev/fuse, or
    fusermount is unavailable). Honesty caveats:
    - The native baseline is the same ``tempfile`` location as the
      backing store (ext4 on this host, ``/dev/sda2``) — a real
      disk-backed comparison; hot data lands in the page cache, as it
      would for any disk-backed filesystem.
    - Reads run with the kernel page cache + readahead active (real
      users get the same), which batches 4 KiB user reads into larger
      daemon requests.
    - The kernel's write batching to the daemon is reported explicitly.
      NyFS negotiates FUSE_CAP_BIG_WRITES + FUSE_CAP_WRITEBACK_CACHE +
      FUSE_CAP_MAX_PAGES in the INIT handshake (``NyFSMount``
      ``writeback_cache=True``, the default), so writes batch at 128 KiB
      instead of the 4 KiB pages a stock fusepy mount gets.
    - **The mount runs in an isolated child process** (2026-08-14,
      §19 incident fix): the parent enforces ``timeout_s`` and, on a
      wedged kernel FUSE request, kills the child group and lazily
      unmounts instead of hanging the whole run. A truly wedged child
      (D-state) may survive SIGKILL — it then needs root
      (``echo 1 > /sys/fs/fuse/connections/N/abort``) or a reboot —
      but the runner always survives and reports the section as
      skipped. See BENCHMARK_RESULTS.md §19 incident note.
    """
    if not _fuse_mount_available():
        return {"skipped": "no fusepy / /dev/fuse / fusermount on this host"}

    # Parent creates the mountpoint so it can lazily unmount a wedged
    # child's mount even if the child is unkillable.
    base = tempfile.mkdtemp(prefix="nyrqis-bench-§4-")
    mnt = os.path.join(base, "mnt")
    os.makedirs(mnt, exist_ok=True)
    env = dict(os.environ)
    env["NYRQIS_BENCH_MNT"] = mnt
    env["NYRQIS_BENCH_TOTAL"] = str(total)
    proc = subprocess.Popen(
        [sys.executable, "-B", os.path.abspath(__file__),
         "--nyfs-mount-child"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # Wedged mount: contain it. SIGTERM first (a merely-slow child
        # exits), then SIGKILL to the whole group, then lazy-unmount so
        # the mount doesn't linger in the namespace. A D-state child
        # survives both — documented, needs root/reboot — but the
        # runner returns and the consolidated run continues.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                subprocess.run(["fusermount3", "-uz", mnt],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            try:  # best-effort reap (a D-state child ignores SIGKILL)
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return {
            "skipped": "live mount timed out after %ss (wedged FUSE "
                       "request); child %s may require root abort or "
                       "reboot to clear" % (timeout_s, proc.pid),
        }
    try:
        result = json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {
            "skipped": "live-mount child failed (rc=%s): %s"
                       % (proc.returncode, err.decode()[:300]),
        }
    return result


def _vault_mount_worker():
    """The live encrypted-NyVault-mount benchmark — run in an isolated
    child process by ``benchmark_vault_mount_io`` (never call
    directly), mirroring ``_nyfs_mount_worker``'s containment: the
    parent enforces the timeout and kills the group on a wedged FUSE
    request.

    The volume is ENCRYPTED at rest (ADR-0023) and mounted through the
    FUSE passthrough, so every kernel request rides a storage-service
    CALL into the AEAD block layer. Writes defer the durable commit
    (§27 write-commit batching + group commit): the service persists at
    fsync, at close/unmount, or at the commit-interval tick — so a
    burst of short-lived files pays ONE save per interval instead of
    one per close (the ``small_files`` pattern below is that case).
    """
    import json as _json
    from backend import keys
    from ipc.transport import IPCClient, DEFAULT_OPERATOR_ID
    from fuse.vault_mount import NyVaultMount

    def mbps(bytes_, seconds):
        return round(bytes_ / seconds / (1024 * 1024), 2) if seconds else float("inf")

    def bench_write(path, chunk, size):
        data = os.urandom(min(chunk, size))
        with open(path, "wb") as fh:
            t0 = time.perf_counter()
            off = 0
            while off < size:
                fh.write(data[:size - off])
                off += chunk
            fh.flush()
            return mbps(size, time.perf_counter() - t0)

    def bench_read(path, chunk, size):
        with open(path, "rb") as fh:
            t0 = time.perf_counter()
            read = 0
            while read < size:
                fh.read(chunk)
                read += chunk
            return mbps(size, time.perf_counter() - t0)

    tmp = tempfile.mkdtemp(prefix="nyrqis-vault-mnt-bench-")
    sock = os.path.join(tmp, "status.sock")
    vault = os.path.join(tmp, "vault")
    key = os.path.join(tmp, "vault.key")
    mnt = os.path.join(tmp, "mnt")
    native_dir = os.path.join(tmp, "native")
    os.makedirs(mnt, exist_ok=True)
    os.makedirs(native_dir, exist_ok=True)
    with open(key, "wb") as f:
        f.write(keys.make_blob_any(b"bench-mount-secret"))

    import nyrqis_backend
    host = nyrqis_backend.StatusServiceHost(
        socket_path=sock, backend_version="9.9.9",
        vault_dir=vault, vault_key_file=key,
        vault_passphrase="bench-mount-secret")
    host.start()
    client = IPCClient(DEFAULT_OPERATOR_ID,
                       os.path.join(tmp, "ctl.sock")).bind()
    try:
        reply = client.call(sock, _json.dumps({
            "service": "storage", "op": "volume_create",
            "name": "bench"}).encode("utf-8"))
        vid = _json.loads(reply.payload.decode("utf-8"))["volume_id"]
        m = NyVaultMount(client, sock, vid, mnt)
        if not m.mount(foreground=True, blocking=False):
            return {"skipped": "mount could not be started"}
        watchdog = threading.Timer(90.0, lambda: os._exit(99))
        watchdog.start()
        try:
            time.sleep(2.0)  # the FUSE loop establishes the kernel mount
            results = {}
            # Small writes: 256 KiB at 4 KiB per syscall — the honest
            # durability cost (a save() per CALL).
            results["write_4k_fuse_mbps"] = bench_write(
                os.path.join(mnt, "b.bin"), 4096, 256 * 1024)
            # Streaming: 1 MiB at 1 MiB syscalls (kernel-batched).
            results["write_1m_fuse_mbps"] = bench_write(
                os.path.join(mnt, "b.bin"), 1024 * 1024, 1024 * 1024)
            results["write_1m_native_mbps"] = bench_write(
                os.path.join(native_dir, "b.bin"),
                1024 * 1024, 1024 * 1024)
            for chunk, tag in ((1024 * 1024, "1m"), (4096, "4k")):
                results[f"read_{tag}_fuse_mbps"] = bench_read(
                    os.path.join(mnt, "b.bin"), chunk, 1024 * 1024)
                results[f"read_{tag}_native_mbps"] = bench_read(
                    os.path.join(native_dir, "b.bin"), chunk, 1024 * 1024)
            # Short-lived-file burst: 100 files of 4 KiB each,
            # open/write/close — the per-close-commit case that group
            # commit amortizes (one save per interval, not per close).
            def bench_small_files(directory, count=100, size=4096):
                payload = os.urandom(size)
                t0 = time.perf_counter()
                for i in range(count):
                    with open(os.path.join(directory, "f%03d.bin" % i),
                              "wb") as fh:
                        fh.write(payload)
                return round(count / (time.perf_counter() - t0), 1)
            results["small_files_fuse_per_s"] = bench_small_files(mnt)
            results["small_files_native_per_s"] = bench_small_files(
                native_dir)
            results["total_bytes"] = 1024 * 1024
            return results
        finally:
            watchdog.cancel()
            try:
                subprocess.run(["fusermount3", "-u", mnt],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            try:
                m.unmount()
            except Exception:
                pass
    finally:
        client.close()
        host.stop()


def benchmark_vault_mount_io(timeout_s=150):
    """Live ENCRYPTED NyVault FUSE mount vs native I/O (§27,
    2026-08-15): the same real-kernel-mount shape as §4/§6, but the
    mounted volume is ADR-0023-encrypted at rest and every kernel
    request is a storage-service CALL through the passthrough. Runs in
    an isolated child process (the §19 containment pattern); skipped
    without fusepy / /dev/fuse / fusermount.
    """
    if not _fuse_mount_available():
        return {"skipped": "no fusepy / /dev/fuse / fusermount on this host"}
    base = tempfile.mkdtemp(prefix="nyrqis-vault-bench-")
    proc = subprocess.Popen(
        [sys.executable, "-B", os.path.abspath(__file__),
         "--vault-mount-child"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return {
            "skipped": "encrypted live mount timed out after %ss (wedged "
                       "FUSE request); child %s may require root abort or "
                       "reboot to clear" % (timeout_s, proc.pid),
        }
    try:
        result = json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {
            "skipped": "encrypted live-mount child failed (rc=%s): %s"
                       % (proc.returncode, err.decode()[:300]),
        }
    return result


def _state_tree_bytes(state_dir) -> int:
    """Total bytes under the NyFS state tree (blocks + journal + inode
    tables + metadata) — the end-to-end on-disk footprint."""
    total = 0
    for root, _dirs, files in os.walk(state_dir):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total


def _build_persist_corpus(seed: int = 11):
    """Deterministic mixed asset corpus shared by the persisted-image
    benchmarks: ~150 small compressible text-like files, 30 medium files
    of mixed compressibility, and 5 large streaming files.

    Seeded (no ``os.urandom``), so the exact byte image reproduces
    across runs. Returns ``(corpus, total_logical)``.
    """
    import random

    rng = random.Random(seed)
    corpus = []
    # ~150 small text-like files (compressible).
    for i in range(150):
        n = rng.randint(10, 200)
        body = ("The quick brown fox jumps over the lazy dog. " * n).encode()
        corpus.append((f"/assets/text_{i}.txt", body))
    # 30 medium files: mixed compressibility.
    for i in range(30):
        size = rng.randint(64_000, 200_000)
        if i % 3 == 0:
            body = rng.randbytes(size)  # pseudo-random, incompressible
        elif i % 3 == 1:
            body = (b"level-data-v1;" * (size // 13 + 1))[:size]
        else:
            body = rng.randbytes(size)
        corpus.append((f"/assets/med_{i}.bin", body))
    # 5 large streaming files (~1-4 MiB, compressible).
    for i in range(5):
        n = rng.randint(1, 4)
        body = (b"stream-chunk;" * (1024 * 1024 // 13)) * n
        corpus.append((f"/assets/big_{i}.dat", body))

    total_logical = sum(len(b) for _, b in corpus)
    return corpus, total_logical


def benchmark_nyfs_persisted():
    """Persisted-image lifecycle: save/load, ratio, loaded-image reads.

    Builds a deterministic mixed asset corpus (compressible text-like,
    incompressible binary, and large streaming files — seed 11), writes
    it through the NyFS ops layer, ``save()``s it to disk (NPS-004 §7
    durability), reloads with ``load()``, and measures:
    - commit cost: save() time and on-disk block-store size;
    - end-to-end storage compression ratio (logical / on-disk bytes) —
      a first-pass data point for BENCHMARK_PLAN §2's "compression
      ratio > 30%" question, on a synthetic corpus (the plan's real
      asset corpus remains unmeasured);
    - loaded-image reads: sequential streaming of large files and
      small-file random access — the "installed once, read many times"
      shape that matters for gaming loads (NPS-006 §5).
    """
    with tempfile.TemporaryDirectory() as tmp:
        corpus, total_logical = _build_persist_corpus()
        fs = NyFSFilesystem(os.path.join(tmp, "fs"))
        fs.mkdir("/assets")

        # Write the corpus.
        t0 = time.perf_counter()
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        write_s = time.perf_counter() - t0

        # Commit. Pinned to the interleaved path (use_journal=False) so
        # this section keeps measuring the fsync-per-block durability
        # contract baseline documented in §7; journal commit (the
        # default) is measured separately in §9.
        t0 = time.perf_counter()
        fs.save(use_journal=False)
        save_s = time.perf_counter() - t0
        # Re-save of an unchanged state (immutable-block skip path).
        t0 = time.perf_counter()
        fs.save(use_journal=False)
        resave_s = time.perf_counter() - t0
        # End-to-end on-disk footprint: block store + inode tables + any
        # snapshot/metadata files under the state tree.
        state_dir = os.path.join(tmp, "fs", "state")
        on_disk = _state_tree_bytes(state_dir)

        # Reload.
        t0 = time.perf_counter()
        fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
        load_s = time.perf_counter() - t0

        # Loaded-image reads: sequential streaming of the large files.
        big_total = sum(len(b) for p, b in corpus if len(b) >= 1024 * 1024)
        t0 = time.perf_counter()
        for path, body in corpus:
            if len(body) < 1024 * 1024:
                continue
            f = fs2.resolve(path)
            for off in range(0, len(body), 65536):
                fs2.read(f, 65536, off)
        stream_s = time.perf_counter() - t0

        # Loaded-image reads: small-file random access (asset catalog).
        small = [(p, b) for p, b in corpus if len(b) < 64_000]
        t0 = time.perf_counter()
        for _ in range(3):
            for path, body in small:
                f = fs2.resolve(path)
                fs2.read(f, min(4096, len(body)), 0)
        small_s = time.perf_counter() - t0

        def mbps(bytes_, seconds):
            return round(bytes_ / seconds / (1024 * 1024), 2) if seconds else float("inf")

        return {
            "files": len(corpus),
            "logical_bytes": total_logical,
            "on_disk_bytes": on_disk,
            "compression_ratio": round(total_logical / on_disk, 2),
            "write_corpus_mbps": mbps(total_logical, write_s),
            "save_seconds": round(save_s, 3),
            "resave_seconds": round(resave_s, 3),
            "load_seconds": round(load_s, 3),
            "loaded_stream_read_mbps": mbps(big_total, stream_s),
            "loaded_small_reads_per_sec": round(
                len(small) * 3 / small_s, 1),
        }


def benchmark_save_levers():
    """save() commit-cost levers (BENCHMARK_RESULTS.md §8).

    The §7 fsync-bound finding named three design questions for commit
    cost; the first two are measured here on the same deterministic
    corpus (``_build_persist_corpus``):
    - larger blocks (fewer block files -> fewer per-file fsyncs, at the
      cost of padding waste for small files): 64 KiB (baseline),
      256 KiB, and 1 MiB;
    - batched fsync (``save(batched_fsync=True)``: all temps written,
      then all fsynced, then all renamed) vs the default interleaved
      path, at the default 64 KiB block size;
    - journal commit (``save(use_journal=True)``: one fsync for the
      whole transaction's block payloads, then the metadata swap) at
      the default 64 KiB block size.
    Every config verifies a full save -> load -> read round-trip before
    reporting (``roundtrip_ok``), so a lever that broke durability would
    fail loudly here. Each config repeats twice and reports the minimum
    save time: fsync-bound timings swing ±30% run to run on this host,
    so single-run comparisons would be noise.
    """
    corpus, total_logical = _build_persist_corpus()

    def run(block_size, batched, use_journal=False, repeats=2):
        # fsync-bound timings are noisy on this host (observed ±30% run
        # to run), so each config is repeated and the minimum (least
        # noise-inflated) save time is reported; the other metrics come
        # from the best run.
        best = None
        for _ in range(repeats):
            with tempfile.TemporaryDirectory() as tmp:
                fs = NyFSFilesystem(os.path.join(tmp, "fs"),
                                    block_size=block_size)
                fs.mkdir("/assets")
                for path, body in corpus:
                    fs.write(fs.create_file(path), body)
                t0 = time.perf_counter()
                fs.save(batched_fsync=batched, use_journal=use_journal)
                save_s = time.perf_counter() - t0
                state_dir = os.path.join(tmp, "fs", "state")
                on_disk = _state_tree_bytes(state_dir)
                blocks_dir = os.path.join(state_dir, "blocks")
                n_blocks = (len([n for n in os.listdir(blocks_dir)
                                 if n.endswith(".bin")])
                            if os.path.isdir(blocks_dir) else 0)
                journal = os.path.join(state_dir, "journal.bin")
                j_bytes = (os.path.getsize(journal)
                           if os.path.exists(journal) else 0)
                fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
                ok = all(
                    fs2.read(fs2.resolve(p)) == body
                    for p, body in corpus
                )
                row = {
                    "save_s": round(save_s, 3),
                    "block_files": n_blocks,
                    "journal_bytes": j_bytes,
                    "on_disk_bytes": on_disk,
                    "ratio": round(total_logical / on_disk, 2),
                    "roundtrip_ok": ok,
                }
                if best is None or save_s < best["save_s"]:
                    best = row
        return best

    rows = {
        "64k_interleaved": run(65536, False),
        "64k_batched": run(65536, True),
        "64k_journal": run(65536, False, use_journal=True),
        "256k_interleaved": run(262144, False),
        "1m_interleaved": run(1048576, False),
    }
    out = {"logical_bytes": total_logical}
    for name, row in rows.items():
        out.update({f"{name}_{k}": v for k, v in row.items()})
    return out


def benchmark_snapshot_dedup():
    """Cross-snapshot deduplication measured on disk (BENCHMARK_RESULTS
    §10).

    NyFS dedups by CoW sharing: snapshots reference the same immutable
    blocks, so a save stores each distinct block once. This measures how
    much block-store space a snapshot chain actually costs when 20% of
    the corpus changes between snapshots — vs the naive cost of an
    independent full copy.
    """
    corpus, total_logical = _build_persist_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        fs = NyFSFilesystem(os.path.join(tmp, "fs"))
        fs.mkdir("/assets")
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        snap1 = fs.create_snapshot()
        # Pinned to interleaved so the block-store growth metric is the
        # .bin-file delta documented in §10 (journal mode holds payloads
        # in the journal until compaction).
        fs.save(use_journal=False)
        state_dir = os.path.join(tmp, "fs", "state")
        after_snap1 = _state_tree_bytes(state_dir)

        # Modify ~20% of the corpus: rewrite 30 text files with new
        # content and flip the first 4 KiB of 5 medium files.
        for i in range(30):
            fs.write(fs.resolve(f"/assets/text_{i}.txt"),
                     f"changed-v2;{i}".encode() * 80)
        for i in range(5):
            med = fs.resolve(f"/assets/med_{i}.bin")
            head = fs.read(med, 4096, 0)
            fs.write(med, bytes(b ^ 0xFF for b in head), 0)
        snap2 = fs.create_snapshot()
        fs.save(use_journal=False)
        after_snap2 = _state_tree_bytes(state_dir)
        bins = [n for n in os.listdir(os.path.join(state_dir, "blocks"))
                if n.endswith(".bin")]
        new_bytes = after_snap2 - after_snap1
        return {
            "logical_bytes": total_logical,
            "on_disk_after_snap1": after_snap1,
            "on_disk_after_snap2": after_snap2,
            "new_block_bytes_for_snap2": new_bytes,
            "naive_full_copy_bytes": total_logical,
            "dedup_factor": (round(total_logical / new_bytes, 2)
                              if new_bytes else None),
            "block_files_after_snap2": len(bins),
            "snapshots": 2,
        }


def benchmark_codec_compare():
    """zstd (NyFS default, level 3) vs zlib (stdlib, level 6) on the
    benchmark_zstd corpus — the non-zstd codec comparison that
    BENCHMARK_PLAN §2 lists as pending (BENCHMARK_RESULTS §11).

    python-lz4 is NOT installed on this host, so the plan's LZ4
    comparison is approximated with zlib, a broadly-comparable
    general-purpose codec (installing ``lz4`` via pip would add a true
    LZ4 row).
    """
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "benchmark_zstd",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "benchmark_zstd.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        corpus = mod.build_corpus()
    except ImportError as e:
        return {"error": f"zstandard unavailable: {e}"}

    import zlib
    import zstandard as zstd

    out = {}
    total_in = sum(len(v) for v in corpus.values())
    for name, data in corpus.items():
        zc = zstd.ZstdCompressor(level=3)
        zd = zstd.ZstdDecompressor()
        z3 = zc.compress(data)
        out[f"zstd3_{name}_ratio"] = round(len(data) / len(z3), 2)
        out[f"zstd3_{name}_compress_mbps"] = round(
            mod.throughput(lambda d: zc.compress(d), data, 4))
        out[f"zstd3_{name}_decompress_mbps"] = round(
            mod.throughput(lambda d: zd.decompress(d), z3, 4,
                           size=len(data)))
        zb = zlib.compress(data, 6)
        out[f"zlib6_{name}_ratio"] = round(len(data) / len(zb), 2)
        out[f"zlib6_{name}_compress_mbps"] = round(
            mod.throughput(lambda d: zlib.compress(d, 6), data, 4))
        out[f"zlib6_{name}_decompress_mbps"] = round(
            mod.throughput(lambda d: zlib.decompress(d), zb, 4,
                           size=len(data)))
    zstd_out = sum(len(zstd.ZstdCompressor(level=3).compress(v))
                   for v in corpus.values())
    zlib_out = sum(len(zlib.compress(v, 6)) for v in corpus.values())
    out["zstd3_overall_ratio"] = round(total_in / zstd_out, 2)
    out["zlib6_overall_ratio"] = round(total_in / zlib_out, 2)
    return out


def _build_real_corpus(target_bytes: int = 16 * 1024 * 1024):
    """Deterministic sample of REAL files from the system (/usr/share).

    Subdirectories chosen for variety: zoneinfo (binary timezone data),
    applications (.desktop text), mime (XML/globs), man (compressed
    text), locale (compiled message catalogs), fonts (.ttf binaries).
    Files are taken in sorted-path order per directory until the target
    size is reached, so the selection is deterministic for a given
    system image. Returns ([(path, bytes)], total).
    """
    roots = ["zoneinfo", "applications", "mime", "man", "locale", "fonts"]
    selected = []
    total = 0
    for sub in roots:
        root = os.path.join("/usr/share", sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()  # deterministic traversal for a given image
            for name in sorted(files):
                path = os.path.join(dirpath, name)
                try:
                    size = os.path.getsize(path)
                    if size < 256 or size > 4 * 1024 * 1024:
                        continue
                    if total + size > target_bytes:
                        continue
                    data = open(path, "rb").read()
                except OSError:
                    continue
                selected.append((path, data))
                total += size
                if total >= target_bytes:
                    return selected, total
    return selected, total


def benchmark_real_corpus(target_bytes: int = 16 * 1024 * 1024):
    """End-to-end NyFS compression ratio on a REAL mixed corpus
    (BENCHMARK_RESULTS §12).

    The synthetic §7 corpus is text-heavy (6.42 : 1). Real
    game-adjacent data — already-compressed fonts, locale catalogs,
    compressed man pages — is the honest second data point for
    BENCHMARK_PLAN §2.
    """
    try:
        files, total = _build_real_corpus(target_bytes)
    except Exception as e:
        return {"error": str(e)}
    out = {
        "source": "/usr/share (zoneinfo, applications, mime, man,"
                  " locale, fonts)",
        "files": len(files),
        "logical_bytes": total,
    }

    def pass_write_and_save(use_journal):
        with tempfile.TemporaryDirectory() as tmp:
            fs = NyFSFilesystem(os.path.join(tmp, "fs"))
            fs.mkdir("/assets")
            t0 = time.perf_counter()
            for i, (_path, data) in enumerate(files):
                fs.write(fs.create_file(f"/assets/real_{i}.bin"), data)
            write_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            fs.save(use_journal=use_journal)
            save_s = time.perf_counter() - t0
            state_dir = os.path.join(tmp, "fs", "state")
            on_disk = _state_tree_bytes(state_dir)
            blocks_dir = os.path.join(state_dir, "blocks")
            n_blocks = (len([n for n in os.listdir(blocks_dir)
                             if n.endswith(".bin")])
                        if os.path.isdir(blocks_dir) else 0)
            fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
            ok = all(fs2.read(fs2.resolve(f"/assets/real_{i}.bin")) == data
                     for i, (_p, data) in enumerate(files))
            return {
                "on_disk_bytes": on_disk,
                "compression_ratio": round(total / on_disk, 2),
                "write_mbps": (round(total / write_s / 1e6, 2)
                               if write_s else None),
                "save_seconds": round(save_s, 3),
                "block_files": n_blocks,
                "roundtrip_ok": ok,
            }

    out["interleaved"] = pass_write_and_save(False)
    out["journal"] = pass_write_and_save(True)
    return out


def benchmark_mixed_workload():
    """Mixed read/write/commit loop under journal vs interleaved commit
    (BENCHMARK_RESULTS §13).

    §9 measured a single cold transaction. Real daemons commit
    repeatedly while serving reads and writes, so this section drives a
    deterministic loop: N files, R rounds, each round updating every
    file (CoW), reading it back, and fsync()-committing once. Reports
    end-to-end time, per-commit latency (avg + max), and I/O throughput
    for both commit modes; every run reloads and compares the full
    content before reporting (roundtrip_ok). Each mode builds its own
    workload from the same seed so the I/O is byte-identical across
    modes.
    """
    n_files, rounds, chunk = 16, 6, 16 * 1024
    file_bytes = 64 * 1024

    def run(use_journal):
        rng = __import__("random").Random(13)
        with tempfile.TemporaryDirectory() as tmp:
            fs = NyFSFilesystem(os.path.join(tmp, "fs"))
            paths = []
            t0 = time.perf_counter()
            for i in range(n_files):
                p = f"/mix_{i}.bin"
                fs.write(fs.create_file(p),
                         bytes(rng.randrange(256) for _ in range(file_bytes)))
                paths.append(p)
            write_s = time.perf_counter() - t0

            t0 = time.perf_counter()
            commits = []
            for r in range(rounds):
                for i, p in enumerate(paths):
                    off = (r * chunk) % (file_bytes - chunk + 1)
                    data = bytes(rng.randrange(256) for _ in range(chunk))
                    fs.write(fs.resolve(p), data, offset=off)
                    fs.read(fs.resolve(p), chunk, off)
                c0 = time.perf_counter()
                fs.save(use_journal=use_journal)
                commits.append(time.perf_counter() - c0)
            loop_s = time.perf_counter() - t0

            live = {p: fs.read(fs.resolve(p)) for p in paths}
            fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
            ok = all(fs2.read(fs2.resolve(p)) == live[p] for p in paths)
            total_io = n_files * rounds * chunk
            return {
                "write_mbps": round(n_files * file_bytes / write_s / 1e6, 2),
                "loop_seconds": round(loop_s, 3),
                "commits": rounds,
                "commit_ms_avg": round(sum(commits) / len(commits) * 1000, 2),
                "commit_ms_max": round(max(commits) * 1000, 2),
                "io_mbps": round(total_io / loop_s / 1e6, 2),
                "roundtrip_ok": ok,
            }

    return {"interleaved": run(False), "journal": run(True)}


def benchmark_compaction_cost():
    """The journal compaction pass measured in isolation
    (BENCHMARK_RESULTS §14).

    Journal commits are cheap (~60–70× vs fsync-per-block, §9), but the
    materialize pass they postpone — move referenced blocks into
    ``state/blocks/``, truncate the journal — is a real cost a daemon
    pays somewhere. This section measures it on the same §7 corpus:
    build a journal without ever triggering save()-time compaction (1
    GiB threshold), then time ``compact_journal()`` and report the
    per-block materialize cost alongside the commit time it buys.
    """
    corpus, total_logical = _build_persist_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        fs = NyFSFilesystem(os.path.join(tmp, "fs"),
                            journal_compact_bytes=1 << 30)
        fs.mkdir("/assets")
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        t0 = time.perf_counter()
        fs.save(use_journal=True)
        save_s = time.perf_counter() - t0
        journal_before = fs.journal_bytes()

        t0 = time.perf_counter()
        moved = fs.compact_journal()
        compact_s = time.perf_counter() - t0

        blocks_dir = os.path.join(tmp, "fs", "state", "blocks")
        n_bins = len([n for n in os.listdir(blocks_dir)
                      if n.endswith(".bin")])
        fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
        ok = all(fs2.read(fs2.resolve(p)) == body for p, body in corpus)
        return {
            "logical_bytes": total_logical,
            "journal_commit_s": round(save_s, 3),
            "journal_bytes_before": journal_before,
            "blocks_moved": moved,
            "compaction_s": round(compact_s, 3),
            "per_block_ms": (round(compact_s / moved * 1000, 2)
                              if moved else None),
            "bin_files_after": n_bins,
            "journal_bytes_after": fs.journal_bytes(),
            "roundtrip_ok": ok,
        }


def benchmark_journal_block_size():
    """Journal commit vs block size (BENCHMARK_RESULTS §15).

    §9 left one interplay untested: does block size still matter once
    commit is journaled (one fsync per transaction)? For interleaved
    commit, larger blocks cut save time by cutting the fsync count
    (§8); journal commit fsyncs once regardless of block count, so
    block size should move only the compression ratio (padding) and the
    journal's byte count — not the commit latency. Same §7 corpus,
    each config verified by a full save -> load -> read round-trip.
    """
    corpus, total_logical = _build_persist_corpus()

    def run(block_size, use_journal):
        with tempfile.TemporaryDirectory() as tmp:
            fs = NyFSFilesystem(os.path.join(tmp, "fs"),
                                block_size=block_size)
            fs.mkdir("/assets")
            for path, body in corpus:
                fs.write(fs.create_file(path), body)
            t0 = time.perf_counter()
            fs.save(use_journal=use_journal)
            save_s = time.perf_counter() - t0
            state_dir = os.path.join(tmp, "fs", "state")
            on_disk = _state_tree_bytes(state_dir)
            journal = os.path.join(state_dir, "journal.bin")
            j_bytes = (os.path.getsize(journal)
                       if os.path.exists(journal) else 0)
            fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
            ok = all(fs2.read(fs2.resolve(p)) == body for p, body in corpus)
            return {
                "save_s": round(save_s, 3),
                "journal_bytes": j_bytes,
                "on_disk_bytes": on_disk,
                "ratio": round(total_logical / on_disk, 2),
                "roundtrip_ok": ok,
            }

    return {
        "64k_interleaved_ref": run(65536, False),
        "64k_journal": run(65536, True),
        "256k_journal": run(262144, True),
        "1m_journal": run(1048576, True),
    }


def benchmark_container_primitives(n=50000):
    """Container launch-plan primitives (BENCHMARK_RESULTS §18).

    The pure computations the container manager makes per launch
    (ADR-0020 priority #5): the launcher argv (FIND-BACKEND-004),
    cgroup v1/v2 plan (FIND-BACKEND-003), uid/gid root maps, and the
    NPS-010 §4 state machine. Measures the pure-Python floor (`_py_*`)
    on any host; when the Rust crate is built and findable by the
    codec (`backend.container_codec`), the FFI path is measured too and
    the two compared byte-for-byte. The dev host has no Rust toolchain,
    so this reports the floor here; CI (or a host with the crate) adds
    the FFI numbers.
    """
    from backend import container_codec as cc

    py = "/usr/bin/python3"
    launcher = "/opt/nyrqis/launcher.py"
    command_flat = cc.build_command_flat(["/bin/sh", "-c", "echo hi"])

    def _ops(fn, *args, warmup=2000):
        for _ in range(warmup):
            fn(*args)
        t0 = time.perf_counter_ns()
        for _ in range(n):
            fn(*args)
        return round((time.perf_counter_ns() - t0) / n / 1000.0, 3)  # µs/op

    floor = {
        "launcher_argv_us": _ops(cc._py_launcher_argv, py.encode(),
                                  launcher.encode(), b"ctr-1", b"", 0,
                                  command_flat),
        "cgroup_plan_us": _ops(cc._py_cgroup_plan, b"ctr-1", 512, 1024,
                                50000, 100000),
        "root_maps_us": _ops(cc._py_root_maps, 1000, 1000),
        "transition_valid_us": _ops(cc._py_transition_valid,
                                     "running", "suspended"),
    }

    result = {"iterations": n, "rust_crate_found": False}
    result.update({f"floor_{k}": v for k, v in floor.items()})

    lib = cc._load_rust_backend()
    if lib is not None:
        result["rust_crate_found"] = True
        ffi = {
            "launcher_argv_us": _ops(cc._rust_launcher_argv, lib,
                                      py.encode(), launcher.encode(),
                                      b"ctr-1", b"", 0, command_flat),
            "cgroup_plan_us": _ops(cc._rust_cgroup_plan, lib, b"ctr-1",
                                    512, 1024, 50000, 100000),
            "root_maps_us": _ops(cc._rust_root_maps, lib, 1000, 1000),
            "transition_valid_us": _ops(lib.nyrqis_container_transition_valid,
                                         1, 2),
        }
        result.update({f"ffi_{k}": v for k, v in ffi.items()})
        # Byte-parity: the differential gate's assertion, re-run here.
        parity = (
            cc._rust_launcher_argv(lib, py.encode(), launcher.encode(),
                                   b"ctr-1", b"", 0, command_flat)
            == cc._py_launcher_argv(py.encode(), launcher.encode(),
                                    b"ctr-1", b"", 0, command_flat)
            and cc._rust_cgroup_plan(lib, b"ctr-1", 512, 1024, 50000, 100000)
            == cc._py_cgroup_plan(b"ctr-1", 512, 1024, 50000, 100000)
            and cc._rust_root_maps(lib, 1000, 1000)
            == cc._py_root_maps(1000, 1000)
            and lib.nyrqis_container_transition_valid(1, 2) == 0
        )
        result["byte_parity_ok"] = parity
        for k in ("launcher_argv", "cgroup_plan", "root_maps",
                  "transition_valid"):
            result[f"speedup_{k}_x"] = round(
                result[f"floor_{k}_us"] / result[f"ffi_{k}_us"], 2)
    return result


def benchmark_launcher_coldstart(n=8):
    """Container cold-start A/B (§25, 2026-08-15): the compiled
    launcher-init (`rust/launcher`, ADR-0020) vs the Python launcher
    (launcher.py) — real spawn→wait latency for a trivial command,
    same session, one manager, seccomp off (isolating the init
    process itself from policy-serialization cost). Skip-gated on
    unprivileged user namespaces (a launch probe); reports both sides
    when the compiled binary is present, the Python side alone
    otherwise.
    """
    import backend.container as bc
    from backend import rust_launcher
    from backend.container import ContainerConfig, ContainerManager

    def _probe():
        try:
            m = ContainerManager(
                use_cgroups_v2=False, use_direct_syscalls=True)
            c = m.create(ContainerConfig(
                command=["/bin/true"], seccomp=False))
            m.spawn(c)
            rc = m.wait(c, timeout_s=30)
            return rc == 0
        except Exception:  # noqa: BLE001 - a probe either works or it does not
            return False

    if not _probe():
        return {"skipped": "unprivileged user namespaces not available"}

    def _roundtrip_us(mgr):
        t0 = time.perf_counter_ns()
        c = mgr.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        mgr.spawn(c)
        rc = mgr.wait(c, timeout_s=30)
        t1 = time.perf_counter_ns()
        if rc != 0:
            raise RuntimeError(f"launch failed rc={rc}")
        return (t1 - t0) / 1000.0

    def _run(force_python):
        orig = bc.rust_launcher.available
        if force_python:
            # The locator is uncached (re-stats per call), but patching
            # the module attribute is deterministic either way.
            bc.rust_launcher.available = lambda: False
        try:
            mgr = ContainerManager(
                use_cgroups_v2=False, use_direct_syscalls=True)
            _roundtrip_us(mgr)  # warmup (clone-path dlopen, caches)
            times = [_roundtrip_us(mgr) for _ in range(n)]
        finally:
            bc.rust_launcher.available = orig
        return {
            "mean_us": round(statistics.mean(times), 1),
            "p50_us": round(statistics.median(times), 1),
            "p95_us": round(percentile(sorted(times), 95), 1),
            "iterations": n,
        }

    result = {}
    if rust_launcher.available():
        result["compiled_launcher"] = _run(force_python=False)
        result["python_launcher"] = _run(force_python=True)
        a = result["compiled_launcher"]["p50_us"]
        b = result["python_launcher"]["p50_us"]
        result["compiled_vs_python_p50_x"] = (
            round(b / a, 2) if a else None)
    else:
        result["compiled_launcher"] = "not built on this host"
        result["python_launcher"] = _run(force_python=True)
    return result


def benchmark_ledger_refresh(ns=(1000, 10000)):
    """Quota ledger refresh cost (§28, 2026-08-16).

    ADR-0022 accounting made the per-container usage ledger a cache
    re-derived from the NyFS tree at every commit. This measures that
    refresh — ``StorageService._refresh_usage``: the tree walk (path
    -> size) + last-writer attribution + the on-disk physical-byte
    stat — on volumes of 1 k and 10 k small files. It is the cost the
    accounting increment added to each fsync/interval/close commit,
    and the honest answer to "is the ledger refresh a problem?"
    (in-process, like the other control-plane sections)."""
    from ipc.storage import StorageService
    from ipc.transport import DEFAULT_OPERATOR_ID

    class _Stub:
        def __init__(self):
            self.replies = []

        def reply(self, sender_path, call_id, payload):
            self.replies.append(json.loads(payload.decode("utf-8")))

    def _build(n):
        tmp = tempfile.mkdtemp(prefix="nyrqis-ledger-")
        storage = StorageService(vault_dir=os.path.join(tmp, "vault"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", DEFAULT_OPERATOR_ID,
                               {"name": "v"})
        record = storage._volumes[stub.replies[-1]["volume_id"]]
        nyfs = record["nyfs"]
        for i in range(n):
            path = f"/f{i:05d}"
            nyfs.write(nyfs.create_file(path), b"x" * 64)
        return storage, record

    results = {}
    for n in ns:
        storage, record = _build(n)
        try:
            storage._refresh_usage(record)  # warm (lazy loads)
            t0 = time.perf_counter()
            for _ in range(5):
                storage._refresh_usage(record)
            dt = (time.perf_counter() - t0) / 5
            results[f"{n} files"] = {
                "refresh_ms": round(dt * 1000, 2),
                "files_per_s": int(n / dt) if dt > 0 else 0,
            }
        finally:
            shutil.rmtree(storage.vault_dir, ignore_errors=True)
    return results


def benchmark_vault_io(n=200, payloads=(4096, 32 * 1024)):
    """NyVault byte path through the CALL/REPLY loop (§26, 2026-08-15).

    Measures the FUSE-passthrough data plane the way a mounted client
    sees it: ``NyVaultOperations.write``/``read`` are storage-service
    CALLs, so each iteration is the full loop — CALL encode → datagram
    → capability+handle+path checks → real NyFS I/O → durable
    ``save()`` commit → REPLY decode. Reports p50/p95 per payload
    (4 KiB = the classic metadata-ish call; 32 KiB = the per-call cap,
    the FUSE passthrough's chunk size), plus the encrypted-variant
    delta (block-layer AEAD, ADR-0023) when the PyNaCl floor is
    present. This is the vault's §4 answer: what the byte path costs
    per operation, benchmarked in-process like the other control-plane
    sections.
    """
    import json as _json
    from ipc.transport import (
        IPCClient, IPCManager, IPCDatagramServer, DEFAULT_OPERATOR_ID,
    )
    from ipc.service import ServiceRouter
    from ipc.storage import StorageService
    from fuse.vault_mount import NyVaultOperations

    def _loop(encrypted=False):
        tmp = tempfile.mkdtemp(prefix="nyrqis-vault-io-")
        svc_path = os.path.join(tmp, "svc.sock")
        cli_path = os.path.join(tmp, "cli.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={}, trusted_uids={os.getuid()})
        kek = None
        if encrypted:
            from backend import keys
            kek = keys.unlock(keys.make_kek_blob(b"bench-vault-secret"),
                              b"bench-vault-secret")
        storage = StorageService(
            capability_manager=None,
            vault_dir=os.path.join(tmp, "vault"), kek=kek)
        router = ServiceRouter()
        router.register("storage", storage)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            reply = client.call(svc_path, _json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "bench"}).encode("utf-8"))
            vid = _json.loads(reply.payload.decode("utf-8"))["volume_id"]
            ops = NyVaultOperations(client, svc_path, vid)
            rows = {}
            for size in payloads:
                data = os.urandom(size)
                path = f"/bench-{size}.bin"
                ops.write(path, data, 0)  # warmup (inode, journal, caches)
                w = []
                for _ in range(n):
                    t0 = time.perf_counter_ns()
                    ops.write(path, data, 0)
                    w.append((time.perf_counter_ns() - t0) / 1000.0)
                r = []
                for _ in range(n):
                    t0 = time.perf_counter_ns()
                    ops.read(path, size, 0)
                    r.append((time.perf_counter_ns() - t0) / 1000.0)
                rows[f"{size // 1024}k"] = {
                    "write_p50_us": round(statistics.median(w), 1),
                    "write_p95_us": round(percentile(sorted(w), 95), 1),
                    "read_p50_us": round(statistics.median(r), 1),
                    "read_p95_us": round(percentile(sorted(r), 95), 1),
                    "iterations": n,
                }
            ops.close()
            return rows
        finally:
            client.close()
            stop.set()
            server.close()
            shutil.rmtree(tmp, ignore_errors=True)

    result = {"plaintext": _loop(encrypted=False)}
    try:
        result["encrypted"] = _loop(encrypted=True)
    except Exception as e:  # noqa: BLE001 - the floor may be absent
        result["encrypted"] = {"skipped": f"PyNaCl floor unavailable: {e}"}
    return result


def benchmark_zstd_levels():
    """Zstd level sweep (BENCHMARK_PLAN §2) via benchmark_zstd.py."""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "benchmark_zstd",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "benchmark_zstd.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        corpus = mod.build_corpus()
        rows = {}
        for level in mod.LEVELS:
            results, overall = mod.bench_level(level, corpus)
            rows[level] = {
                "overall_ratio": round(overall, 2),
                "text_ratio": round(results["text"][0], 2),
                "media_ratio": round(results["media"][0], 2),
                "compress_mbps": round(
                    (results["text"][1] + results["media"][1]
                     + results["incompressible"][1]) / 3),
                "decompress_mbps": round(
                    (results["text"][2] + results["media"][2]
                     + results["incompressible"][2]) / 3),
            }
        return rows
    except ImportError as e:
        return {"error": f"zstandard unavailable: {e}"}


def _print_section(title, data):
    print(title)
    for k, v in data.items():
        if isinstance(v, dict):
            # Nested sections (e.g. the ADR-0021 floor/loop A/B) print
            # as an indented sub-block.
            print(f"  {k}:")
            for k2, v2 in v.items():
                print(f"    {k2}: {v2}")
        else:
            print(f"  {k}: {v}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Nyrqis Linux Backend consolidated benchmarks")
    parser.add_argument("--all", action="store_true", help="run everything (default)")
    parser.add_argument("--ipc", action="store_true", help="§1 IPC round-trip latency")
    parser.add_argument("--ipc-transport", action="store_true",
                        help="§20 IPC round-trip over the real UDS transport")
    parser.add_argument("--ipcd", action="store_true",
                        help="§21 ADR-0021 A/B: Python floor vs Rust serving loop")
    parser.add_argument("--ipcd-dispatch", action="store_true",
                        help="§21 non-ping dispatch handoff A/B (floor vs loop)")
    parser.add_argument("--ipcd-refresh", action="store_true",
                        help="§21 pid-table refresh (set_policy) cost")
    parser.add_argument("--ipcd-control", action="store_true",
                        help="§21 main-socket control op A/B — real status op (floor vs loop)")
    parser.add_argument("--bucket", action="store_true", help="§3 token-bucket defaults")
    parser.add_argument("--zstd", action="store_true", help="§2 Zstd level sweep")
    parser.add_argument("--nyfs", action="store_true", help="§4 NyFS vs native proxy")
    parser.add_argument("--nyfs-mount", action="store_true",
                        help="§4 live-mount FUSE vs native")
    parser.add_argument("--nyfs-persist", action="store_true",
                        help="§5 persisted-image lifecycle")
    parser.add_argument("--save-levers", action="store_true",
                        help="§5 save() commit-cost levers (block size, "
                             "batched fsync, journal)")
    parser.add_argument("--snapshot-dedup", action="store_true",
                        help="§5 cross-snapshot dedup measurement")
    parser.add_argument("--codec", action="store_true",
                        help="§2 zstd vs zlib codec comparison")
    parser.add_argument("--real-corpus", action="store_true",
                        help="§2 end-to-end ratio on a real /usr/share corpus")
    parser.add_argument("--mixed-workload", action="store_true",
                        help="§5 mixed read/write/commit loop, journal vs interleaved")
    parser.add_argument("--compaction-cost", action="store_true",
                        help="§5 journal compaction pass cost")
    parser.add_argument("--journal-blocksize", action="store_true",
                        help="§5 journal commit vs block-size interplay")
    parser.add_argument("--container", action="store_true",
                        help="§18 container launch-plan primitives")
    parser.add_argument("--launcher-coldstart", action="store_true",
                        help="§25 container cold-start A/B — compiled "
                             "launcher-init vs the Python launcher")
    parser.add_argument("--vault-io", action="store_true",
                        help="§26 NyVault byte path through the "
                             "CALL/REPLY loop (plaintext + encrypted)")
    parser.add_argument("--vault-mount-io", action="store_true",
                        help="§27 live ENCRYPTED NyVault FUSE mount vs "
                             "native I/O")
    parser.add_argument("--ledger-refresh", action="store_true",
                        help="§28 quota ledger refresh cost (ADR-0022 "
                             "per-commit tree walk)")
    parser.add_argument("--nyfs-mount-child", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--vault-mount-child", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.nyfs_mount_child:
        # Internal: run the live-mount benchmark in this process and
        # emit its result as a single JSON line for the parent
        # (``benchmark_nyfs_mount``). Mountpoint comes from
        # ``NYRQIS_BENCH_MNT``. Never run by hand.
        print(json.dumps(_nyfs_mount_worker()))
        return
    if args.vault_mount_child:
        # Internal: run the encrypted-vault live-mount benchmark in
        # this process (``benchmark_vault_mount_io``). Never by hand.
        print(json.dumps(_vault_mount_worker()))
        return

    selected = (args.ipc or args.ipc_transport or args.ipcd or args.bucket
                or args.zstd or args.nyfs or args.nyfs_mount or args.nyfs_persist
                or args.save_levers or args.snapshot_dedup or args.codec
                or args.real_corpus or args.mixed_workload
                or args.compaction_cost or args.journal_blocksize
                or args.container or args.ipcd_dispatch or args.ipcd_refresh
                or args.ipcd_control or args.launcher_coldstart
                or args.vault_io or args.vault_mount_io
                or args.ledger_refresh)
    if not selected or args.all:
        args.ipc = args.ipc_transport = args.ipcd = True
        args.bucket = args.zstd = args.nyfs = True
        args.nyfs_mount = args.nyfs_persist = args.save_levers = True
        args.snapshot_dedup = args.codec = args.real_corpus = True
        args.mixed_workload = args.compaction_cost = True
        args.journal_blocksize = True
        args.container = True
        args.ipcd_dispatch = args.ipcd_refresh = True
        args.ipcd_control = True
        args.launcher_coldstart = True
        args.vault_io = True
        args.vault_mount_io = True
        args.ledger_refresh = True

    print("Nyrqis Linux Backend — consolidated first-pass benchmarks")
    print("=" * 60)
    if args.ipc:
        _print_section("IPC round-trip, raised token budget (§1):",
                       benchmark_ipc_roundtrip())
    if args.ipc_transport:
        _print_section("IPC round-trip over the real UDS transport (§20):",
                       benchmark_ipc_transport_roundtrip())
    if args.ipcd:
        _print_section("ADR-0021 A/B — floor vs Rust serving loop (§21):",
                       benchmark_ipcd_roundtrip())
    if args.ipcd_dispatch:
        _print_section("ADR-0021 dispatch handoff A/B — non-ping op (§21):",
                       benchmark_ipcd_dispatch())
    if args.ipcd_refresh:
        _print_section("ADR-0021 pid-table refresh cost (§21):",
                       benchmark_ipcd_refresh())
    if args.ipcd_control:
        _print_section("ADR-0021 main-socket control op A/B — status (§21):",
                       benchmark_ipcd_control())
    if args.bucket:
        _print_section("Default token bucket sustained rate (§3):",
                       benchmark_default_bucket())
    if args.zstd:
        _print_section("Zstd level sweep (§2):", benchmark_zstd_levels())
    if args.nyfs:
        _print_section("NyFS vs native (§4 proxy; per-block CoW):",
                       benchmark_nyfs_vs_native())
    if args.nyfs_mount:
        _print_section("NyFS live FUSE mount vs native (§4):",
                       benchmark_nyfs_mount())
    if args.nyfs_persist:
        _print_section("NyFS persisted-image lifecycle (§5):",
                       benchmark_nyfs_persisted())
    if args.save_levers:
        _print_section("NyFS save() commit-cost levers (§5):",
                       benchmark_save_levers())
    if args.snapshot_dedup:
        _print_section("NyFS cross-snapshot dedup (§5):",
                       benchmark_snapshot_dedup())
    if args.codec:
        _print_section("zstd-3 vs zlib-6 codec compare (§2):",
                       benchmark_codec_compare())
    if args.real_corpus:
        _print_section("NyFS real-corpus compression ratio (§2):",
                       benchmark_real_corpus())
    if args.mixed_workload:
        _print_section("NyFS mixed read/write/commit loop (§13):",
                       benchmark_mixed_workload())
    if args.compaction_cost:
        _print_section("NyFS journal compaction pass cost (§14):",
                       benchmark_compaction_cost())
    if args.journal_blocksize:
        _print_section("NyFS journal commit vs block size (§15):",
                       benchmark_journal_block_size())
    if args.container:
        _print_section("Container launch-plan primitives (§18):",
                       benchmark_container_primitives())
    if args.launcher_coldstart:
        _print_section("Container cold-start A/B — compiled launcher-init "
                       "vs Python launcher (§25):",
                       benchmark_launcher_coldstart())
    if args.vault_io:
        _print_section("NyVault byte path through the loop (§26):",
                       benchmark_vault_io())
    if args.vault_mount_io:
        _print_section("Live ENCRYPTED NyVault FUSE mount vs native (§27):",
                       benchmark_vault_mount_io())
    if args.ledger_refresh:
        _print_section("Quota ledger refresh per commit (§28):",
                       benchmark_ledger_refresh())


if __name__ == "__main__":
    main()
