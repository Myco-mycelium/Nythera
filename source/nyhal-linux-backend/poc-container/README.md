# nyctr — Container Primitive Proof-of-Concept

**Status: spike / proof-of-concept. Not production code. Not a NyHAL
Linux Backend implementation.**

## What this is

A single script (`nyctr.py`) that proves one narrow claim: a command can
run inside an isolated PID/mount/UTS/user namespace, with a basic
cgroup-enforced memory and process-count limit, and tear down cleanly
afterward — using nothing but stock Linux tooling (`unshare(1)`, cgroup
v1 `memory`/`pids` controllers).

This exists to de-risk the "is the approach even viable" question ahead
of real Linux Backend implementation work (`NPS-017` §6), per
`REPOSITORY_STATE.md`'s next actions. It is intentionally the smallest
possible thing that could prove something true.

## What this proves (tested, not just asserted)

- **Process isolation** (NPS-002 §4, part of the container boundary): a
  process inside the namespace sees itself as PID 1 and cannot see host
  processes.
- **Hostname isolation**: the UTS namespace lets a container have its own
  hostname independent of the host.
- **Basic resource limiting** (a sliver of NPS-010 §7): a cgroup is
  created per container invocation with a memory and pid-count limit, and
  is removed on teardown.
- **Exit code propagation**: the caller correctly observes the isolated
  process's exit status.

All four were verified by actually running the script in the environment
it was written in — see the inline test commands in the commit that added
this file. This is not a claim made without having run it.

## What this explicitly does NOT prove or implement

Per `NPS-017` §4 and §5 (backend conformance), a real Linux Backend must
satisfy all of: container primitives, **capability enforcement**, **IPC
semantics**, **storage guarantees**, and **boot integration**. This PoC
only touches the first, and only a slice of it:

- **No capability enforcement** (NPS-017 §4.2 / NPS-011). There is no
  seccomp or LSM policy here — this script does not implement or even
  attempt Nythera's capability model. A process inside this "container"
  has whatever access the unprivileged user namespace + cgroup grants it,
  which is a Linux security boundary, not a Nythera one.
- **No IPC** (NPS-017 §4.3 / NPS-003). There is no `send`/`receive`/`call`
  primitive, no endpoint model, no capability-transfer semantics.
- **No storage integration** (NPS-017 §4.4 / NPS-004, NPS-006). The mount
  namespace is isolated but nothing implements NyFS, `.nygi` images, or
  overlays here.
- **No boot integration** (NPS-017 §4.5 / NPS-001 §5). This is invoked
  directly by a human running a script, not by any service manager.
- **cgroup v1, not v2.** The sandbox this was developed in only exposed
  v1 controllers in hybrid mode. A real implementation should target
  cgroup v2 (unified hierarchy) unless there's a specific reason not to.
- **No network namespace.** Deliberately omitted to keep the PoC's blast
  radius small; trivial to add (`--net` to the `unshare` invocation) but
  not exercised or tested here.
- **Root/privileged fallback untested.** This was tested with unprivileged
  user namespaces (`--map-root-user`). Behavior under a Secure-Boot-signed
  init process, or on hardware without unprivileged userns enabled (some
  distros disable it by default), is untested.

## Usage

```
python3 nyctr.py [--hostname NAME] [--memory-mb N] [--pid-limit N] -- <command...>
```

Example:

```
python3 nyctr.py --hostname test1 --memory-mb 32 -- sh -c 'hostname; echo "pid $$"'
```

## Requirements

- Linux with unprivileged user namespaces enabled
  (`kernel.unprivileged_userns_clone=1` on distros that gate it, e.g.
  Debian/Ubuntu derivatives).
- `unshare(1)` (util-linux).
- Read/write access to `/sys/fs/cgroup/memory` and `/sys/fs/cgroup/pids`
  (or their cgroup v2 equivalents — untested, see above).

## Next step, if this line of work continues

A real implementation would very likely move off shelling out to
`unshare(1)` and use `clone()`/`unshare()` syscalls directly, add seccomp
+ LSM policy to actually enforce a capability set (not just "whatever an
unprivileged user namespace happens to allow"), and target cgroup v2. This
script's job was answering "does the basic idea hold together," not
picking the final implementation.
