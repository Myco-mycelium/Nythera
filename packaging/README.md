# Nyrqis Packaging

Host-integration artifacts for the Nyrqis Linux backend (implementation_plan.md
§4.5 — Boot and Lifecycle, Host Integration).

## Layout

```
packaging/
  systemd/
    nyrqis-backend.service   # runs the backend daemon at boot
  completions/
    nyrqisctl.bash           # bash tab-completion for the operator CLI
    nyrqisctl.zsh            # zsh completion for the operator CLI
  man/
    nyrqisctl.1              # man page for the operator CLI
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

# the operator CLI (preferred): same plane, human-readable output
python3 source/nyhal-linux-backend/nyrqisctl.py \
  --socket /run/nyrqis/status.sock status
python3 source/nyhal-linux-backend/nyrqisctl.py containers list
python3 source/nyhal-linux-backend/nyrqisctl.py \
  containers run --network /bin/sleep 30

# the dedicated health socket (ADR-0021) never contends with
# container traffic on the main socket
python3 source/nyhal-linux-backend/nyrqisctl.py \
  --health-socket /run/nyrqis/health.sock health
```

## `nyrqisctl` (operator CLI)

`nyrqisctl.py` (source/nyhal-linux-backend/nyrqisctl.py) is the user-facing
surface of the daemon's control plane: `ping` / `status` / `health` (status
service) and `containers list|run|kill` (control service), over the IPC
transport as the operator identity. Human-readable output by default,
`--json` for raw replies, `--socket` / `--health-socket` to point at the
daemon. Exit status: 0 ok, 1 daemon unreachable or op failed, 2 usage.

### Man page

```sh
sudo install -m 644 packaging/man/nyrqisctl.1 /usr/local/share/man/man1/
sudo mandb   # Debian/Ubuntu; or: gzip -9 /usr/local/share/man/man1/nyrqisctl.1
man nyrqisctl
```

### Shell completion

Bash (Debian/Ubuntu: copy into `/etc/bash_completion.d/` and re-login):

```sh
sudo cp packaging/completions/nyrqisctl.bash /etc/bash_completion.d/nyrqisctl
```

Zsh (add to `$fpath` and `compinit`):

```sh
mkdir -p ~/.zsh/completions
cp packaging/completions/nyrqisctl.zsh ~/.zsh/completions/_nyrqisctl
echo 'fpath=(~/.zsh/completions $fpath); autoload -U compinit; compinit' >> ~/.zshrc
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
