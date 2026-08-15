# Nyrqis Packaging

Host-integration artifacts for the Nyrqis Linux backend (implementation_plan.md
§4.5 — Boot and Lifecycle, Host Integration).

## Layout

```
packaging/
  systemd/
    nyrqis-backend.service   # runs the backend daemon at boot
```

## `nyrqis-backend.service`

Runs the backend's status-service daemon (`nyrqis_backend.py service serve`)
as a boot service: it owns the IPC registry + capability manager + container
manager, serves the container-facing status service, and accepts operator
control commands (`nyrqis_backend.py control ...`) over the same transport.

### Install

The unit assumes the backend is installed at `/opt/nyrqis/nyhal-linux-backend`
(the `NYRQIS_BACKEND_DIR` / `ReadOnlyPaths` / `ExecStart` references). Point it
at the checkout if you are running from the source tree instead:

```sh
sudo cp packaging/systemd/nyrqis-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nyrqis-backend
```

### Operate

```sh
# the daemon's control plane (operator-only, authenticated by uid)
python3 source/nyhal-linux-backend/nyrqis_backend.py \
  control --socket /run/nyrqis/status.sock container-list
```

### Notes

- The backend launches containers through unprivileged user namespaces, so the
  unit runs unprivileged (`DynamicUser=true`, `NoNewPrivileges=true`) — it does
  NOT run as root. If your host requires root for cgroup setup, drop
  `DynamicUser=true` and set `User=` explicitly.
- Full systemd namespace/priv sandboxing is deliberately NOT applied: the
  daemon must create user namespaces and cgroups for its containers.
- Validate a unit edit before deploying: `systemd-analyze verify`.

## Logging and persistent state (plan §4.5)

The unit starts the daemon with `--syslog --state-file /run/nyrqis/daemon-state.json`:

- **Syslog** — daemon records are mirrored into the journal via `/dev/log`
  (systemd owns that socket). Read them with `journalctl -u nyrqis-backend`.
  The flag is best effort: a host without a syslog daemon degrades to stderr.
- **Persistent state** — the state file records the daemon identity (pid,
  backend version, socket) and a last-known container manifest, written
  atomically (tmp + `os.replace`, the same discipline NyFS uses). On the next
  start the daemon *reports* what a crashed previous daemon left behind — the
  orphaned container ids are logged (with the state-file path), and the
  status service's `health` op returns a recovery summary (`"recovery"`:
  previous pid + orphan count). The full manifest stays in the state file
  for operator review — the daemon never ships per-container detail over
  the wire. Recovery is reporting, never resumption: orphaned processes are
  **not** auto-killed; review them with
  `nyrqis_backend.py control container-list` and kill manually.

Both flags are optional on plain CLI runs; pass `--state-file ''` to disable
persistence.

## Health probe socket (ADR-0021)

The unit also starts the daemon with `--health-socket /run/nyrqis/health.sock`:

- **What it is** — a dedicated liveness-probe path served by the **Rust
  serving loop** (`rust/ipcd/`, the first NyRuntime-shaped artifact) when the
  crate is present, and by the Python floor's status service otherwise. Both
  answer the operator's `ping` with **byte-identical** replies, so a probe
  cannot tell which backend answered.
- **Why separate** — health probes never contend with container traffic on
  the main service socket, and the loop owns the whole dispatch cycle in
  Rust (poll → recvmsg → parse → authorize → reply), so a probe round trip
  is ~2.8× faster at the median than through the floor (BENCHMARK_RESULTS.md
  §22: loop p50 ~136 µs vs floor ~387–394 µs).
- **Who can use it** — the daemon's own user (the operator, kernel-
  authenticated via `SO_PASSCRED`). Containers keep using the main service
  socket; the loop's per-container pid table is a later increment.
- **Probe it** — any client that can send the wire `ping` (or
  `ipc.transport.IPCClient` with the operator identity); a systemd
  `HealthCheckCommand` can point at it once systemd ≥ 253 is in use.
