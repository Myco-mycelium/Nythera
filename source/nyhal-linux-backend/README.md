# nyhal-linux-backend

Implementation home for the NyHAL Linux Backend defined in
[`NPS-017`](../../docs/reference/nps/NPS-017-nyhal-kernel-abstraction.md)
and decided in [`ADR-0012`](../../docs/reference/adr/ADR-0012-nyhal-pluggable-kernel-backend.md).

## Current state

- `poc-container/` — a tested proof-of-concept proving the most basic
  container primitive (process/namespace isolation + a cgroup resource
  limit) is achievable on stock Linux. See its README for exactly what it
  does and does not prove. This is a spike, not the start of the real
  implementation's file layout.

Everything else NPS-017 §4 requires — capability enforcement, IPC,
storage (NyFS via FUSE per
[`ADR-0016`](../../docs/reference/adr/ADR-0016-nyfs-linux-backend-fuse.md)),
and boot integration — is unimplemented.
