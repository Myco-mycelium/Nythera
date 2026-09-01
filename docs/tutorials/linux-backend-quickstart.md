# Linux Backend Quickstart

*Tutorial — works today. You need Python 3.10+, pip, and (for
encrypted vault features) `pynacl`.*

This tutorial walks through the core workflow of the Nyrqis Linux
Backend: starting the daemon, running containers, using the vault
storage service, and loading a shell UI design.

## Prerequisites

```bash
cd Nyrqis/source/nyhal-linux-backend
pip install pynacl
```

## Step 1: Ping the daemon (without starting one)

The CLI can talk to a running daemon, but it also gives a clean error
when no daemon is running:

```bash
python3 nyrqisctl.py ping
# Expected: no daemon error (this is normal — we haven't started one)
```

## Step 2: Start the daemon

In a separate terminal (or as a background process):

```bash
cd Nyrqis/source/nyhal-linux-backend
python3 nyrqis_backend.py service serve --socket /tmp/nyrqis.sock
```

The daemon binds a Unix-domain socket and starts serving. You'll see
log output indicating the backend is ready.

## Step 3: Check status and health

```bash
# Ping the daemon
python3 nyrqisctl.py ping

# Get daemon status (operator-only: reports version, uptime, container count)
python3 nyrqisctl.py status

# Health diagnostics (serve-loop liveness, container load, IPC registry)
python3 nyrqisctl.py health
```

## Step 4: Run a container

```bash
# Run a simple container (shares the host's namespaces by default)
python3 nyrqisctl.py containers run --command "/bin/echo hello from nyrqis"

# List running containers
python3 nyrqisctl.py containers list

# Run with resource limits
python3 nyrqisctl.py containers run \
  --command "/bin/sh -c 'while true; do sleep 10; done'" \
  --memory-mb 128 \
  --pid-limit 32

# Run with network namespace isolation (own loopback only)
python3 nyrqisctl.py containers run \
  --command "/bin/sh" \
  --network
```

## Step 5: Inspect and manage containers

```bash
# List all containers
python3 nyrqisctl.py containers list

# Show resource stats for a container
python3 nyrqisctl.py containers stats --container <container-id>

# Show lifecycle events
python3 nyrqisctl.py containers events

# Kill a container
python3 nyrqisctl.py containers kill --container <container-id>
```

## Step 6: Use the vault (encrypted storage)

### Initialize an encrypted vault (optional)

If you want at-rest encryption, initialize a key file first:

```bash
python3 nyrqisctl.py vault init --key-file /tmp/vault.key

# Start the daemon with the key
python3 nyrqis_backend.py service serve \
  --socket /tmp/nyrqis.sock \
  --vault-dir /tmp/nyrqis-vault \
  --vault-key-file /tmp/vault.key
```

When prompted, enter a passphrase to derive the KEK (Key Encryption
Key). Every volume created while the daemon runs will be transparently
encrypted at rest.

### Create and use a volume

```bash
# Create a named volume
python3 nyrqisctl.py vault create --name mydata

# List volumes
python3 nyrqisctl.py vault list

# Open a volume (returns a handle)
python3 nyrqisctl.py vault open --name mydata

# Write data to a path in the volume
echo "hello nyrqis" | python3 nyrqisctl.py vault write \
  --name mydata --path /greeting.txt

# Read it back
python3 nyrqisctl.py vault read --name mydata --path /greeting.txt

# Take a snapshot
python3 nyrqisctl.py vault snapshot --name mydata
```

### Cross-container sharing (grants)

```bash
# Grant another container access to a volume
python3 nyrqisctl.py vault grant --name mydata --container other-container

# List grants
python3 nyrqisctl.py vault grants --name mydata

# Revoke access
python3 nyrqisctl.py vault revoke --name mydata --container other-container
```

## Step 7: Load a shell UI design

The NUI import gate validates and persists `.nstudio` designs:

```bash
# Validate a design (check-only)
python3 nyrqisctl.py nui validate \
  source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio

# Load a design as the daemon's shell UI
python3 nyrqisctl.py nui load \
  source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio

# Check what's loaded
python3 nyrqisctl.py nui current
```

## Step 8: Install and launch apps

```bash
# Install an app (from an APK or EXE)
python3 nyrqisctl.py app install --app-path /path/to/app.apk --name "My App"

# List installed apps
python3 nyrqisctl.py app list

# Launch an app
python3 nyrqisctl.py app launch --app-id "android:com.example.myapp"

# Terminate an app
python3 nyrqisctl.py app terminate --app-id "android:com.example.myapp"
```

## Summary of key commands

| Command | Purpose |
|---------|---------|
| `nyrqisctl.py ping` | Check if the daemon is alive |
| `nyrqisctl.py status` | Operator status (version, uptime, containers) |
| `nyrqisctl.py health` | Health diagnostics |
| `nyrqisctl.py containers run` | Start a new container |
| `nyrqisctl.py containers list` | List running containers |
| `nyrqisctl.py containers kill` | Terminate a container |
| `nyrqisctl.py vault init` | Initialize an encrypted vault key |
| `nyrqisctl.py vault create` | Create a named volume |
| `nyrqisctl.py vault write/read` | Store and retrieve data |
| `nyrqisctl.py vault snapshot` | Snapshot a volume |
| `nyrqisctl.py nui validate` | Validate a `.nstudio` design |
| `nyrqisctl.py nui load` | Load a design as the shell UI |
| `nyrqisctl.py app install/launch` | Install and run apps |

## Next steps

- [Operate the Vault](../how-to/operate-the-vault.md) — production
  vault operations, quota management, and disaster recovery
- [Run the Backend Tests](../how-to/run-linux-backend-tests.md) —
  verify the implementation works
- [The NUI Schema Reference](../reference/nui-schema/NUI-SCHEMA.md) —
  the format Nyforge produces and the runtime consumes
