# Nyrqis Linux Backend: Design and Implementation Plan

## 1. Introduction

This document outlines the design and implementation plan for the Nyrqis Linux Backend, a critical component of the NyHAL abstraction layer. The goal is to provide a conformant implementation of the NyHAL contract on a standard Linux host, serving as a practical near-term target for the Nyrqis operating system [^1]. This plan is informed by the Nyrqis Manifest [^2], the NyHAL Kernel Abstraction specification (NPS-017) [^3], the NyFS Linux Backend FUSE Architecture Decision Record (ADR-0016) [^4], and the implementation-language strategy (ADR-0020) [^6].

## 2. Nyrqis Vision and Principles

Nyrqis aims to advance personal computing by creating a secure, high-performance, and developer-friendly operating system. Its core principles include simplicity, performance, reliability, security, user ownership, transparency, compatibility, and longevity [^2]. The Linux Backend implementation will adhere to these principles, prioritizing architectural integrity and maintainability.

## 3. NyHAL Linux Backend Requirements (NPS-017 §4)

The NyHAL Linux Backend must implement the following five core requirements to be considered conformant [^3]:

### 3.1. Container Primitives

Provide process/container creation, teardown, suspension, and resource-limit enforcement, satisfying NPS-002 (process/thread model) and NPS-010 (container lifecycle) [^3]. The existing `nyctr.py` proof-of-concept demonstrates basic process and namespace isolation using `unshare(1)` and cgroups v1 [^5].

### 3.2. Capability Enforcement

Act as the sole arbiter of capability validity for its containers, preventing containers from self-issuing or forging access beyond their granted capability set, as per NPS-003 §5.4 and NPS-010 §5 [^3].

### 3.3. IPC Semantics

Implement the `send`/`receive`/`call`/`notify` primitives and endpoint model defined in NPS-003 §3–§4, including capability transfer, attenuation rules, and token-bucket rate limiting (ADR-0009) [^3].

### 3.4. Storage Guarantees

Provide the copy-on-write, snapshot, checksum, and transparent-compression guarantees defined in NPS-004 §4. For the Linux Backend, this will be achieved through a user-space FUSE filesystem, as decided in ADR-0016 [^3] [^4].

### 3.5. Boot and Lifecycle

Reach the boot milestones described in NPS-001 §5, including hardware/host initialization, a trusted first process, service bring-up, and a usable session [^3].

## 4. Implementation Strategy

### 4.1. Container Primitives

*   **Transition from `unshare(1)` to direct syscalls** — **LANDED 2026-08-13 (ADR-0020 migration priority #2)**: the direct-syscall launcher is the default container launch path. `ContainerManager._spawn_direct` forks a namespace-setup child which performs `unshare(CLONE_NEWUSER)` + root uid/gid maps (caller's ids captured *before* the unshare — inside the unmapped namespace `getuid()` reports 65534), `unshare(NEWNS|NEWUTS|NEWIPC)`, `unshare(CLONE_NEWPID)`, then forks the container's PID-1 which mounts a hardened procfs (`mount_proc`, nosuid/nodev/noexec) and execs the launcher; the setup child relays the container PID through a pipe and exits with its status, keeping Popen-compatible `wait()` semantics. Implemented as a **Rust module** (`rust/syscalls/`, ABI 1.1.0: `sethostname`/`prctl`/`unshare`/`mount`/`mount_proc`) behind the versioned FFI boundary (ABI-001), with the pure-`ctypes` path as the fallback floor; `unshare(1)` remains only as an opt-in legacy path. Direct `clone(2)` with a Rust child entry point (no Python between fork and exec) remains future work.
*   **Cgroups v2**: Prioritize cgroups v2 for resource management, as it offers a more unified and hierarchical control mechanism compared to cgroups v1. This will require detecting cgroups v2 availability and falling back to v1 if necessary.
*   **Process Management**: Implement robust mechanisms for container creation, suspension (e.g., via `SIGSTOP`/`SIGCONT` or cgroup freezer), and graceful teardown, ensuring proper cleanup of resources. **Cgroup freezer for suspension — LANDED 2026-08-14**: on cgroups v2 hosts, `suspend()`/`resume()` freeze/thaw the container's **whole cgroup** through `cgroup.freeze` (so descendants and future forks cannot outrun the suspension), `terminate()` thaws first so SIGTERM keeps its graceful window, and SIGSTOP/SIGCONT remains the universal fallback (v1 hosts, failed cgroup setup, failed freeze/thaw writes).
*   **Network namespaces — LANDED 2026-08-14**: `ContainerConfig.network=True` (opt-in, default off) creates the container's own network namespace via `CLONE_NEWNET` (direct-syscall path) or `unshare --net` (legacy path), giving the container a loopback-only isolation boundary — no host interfaces are visible. Outbound connectivity (veth/bridge pairs) is deliberate future work; the seccomp data plane continues to gate `socket`/`connect`/`bind` on capability grants.

### 4.2. Capability Enforcement

*   **Linux Security Modules (LSMs)**: Leverage existing LSMs like AppArmor or SELinux to define and enforce security policies for containers. This will involve generating appropriate policy files based on the capabilities assigned to each Nyrqis container.
*   **Seccomp-bpf**: Utilize `seccomp-bpf` filters to restrict the syscalls available to containers, further limiting their attack surface and enforcing capability boundaries. This will require careful crafting of seccomp profiles.

### 4.3. IPC Semantics

*   **Custom IPC Mechanism**: Design a custom IPC mechanism, likely based on Unix domain sockets or a shared memory approach, to implement the `send`/`receive`/`call`/`notify` primitives. This will allow for efficient and secure communication between Nyrqis components.
*   **Capability Transfer and Attenuation**: Integrate capability transfer and attenuation directly into the IPC mechanism, ensuring that capabilities are correctly validated and restricted when passed between processes.
*   **Token-Bucket Rate Limiting**: Implement token-bucket rate limiting for IPC channels as specified in ADR-0009, preventing resource exhaustion and denial-of-service attacks.

### 4.4. Storage Guarantees (NyFS via FUSE)

*   **FUSE Implementation**: Develop a user-space FUSE daemon that exposes NyFS volumes to containers. This daemon will be responsible for intercepting filesystem operations and applying NyFS's core guarantees.
*   **Copy-on-Write (CoW)**: Implement CoW semantics within the FUSE layer, allowing multiple containers to share a base image while maintaining private, writable overlays.
*   **Snapshotting**: Integrate snapshotting capabilities, enabling the creation of immutable point-in-time copies of NyFS volumes.
*   **Checksumming and Transparent Compression**: Implement data checksumming for integrity verification and transparent compression (using Zstandard as per ADR-0007) for efficient storage, all within the FUSE daemon.

### 4.5. Boot and Lifecycle

*   **Host Integration**: Develop a service (e.g., a systemd unit) that manages the Nyrqis Linux Backend, ensuring it starts at boot and properly initializes the necessary namespaces, cgroups, and FUSE mounts.
*   **Trusted First Process**: Establish a trusted first process within the Nyrqis environment, responsible for launching subsequent containers and enforcing initial security policies.
*   **Service Bring-up**: Define a clear sequence for bringing up Nyrqis services and applications, ensuring dependencies are met and resources are correctly allocated.

## 5. Key Dependencies and Challenges

*   **Linux Kernel Features**: Reliance on specific Linux kernel versions for cgroups v2, user namespaces, and advanced seccomp-bpf features.
*   **FUSE Library**: Integration with a robust FUSE library. The current reference implementation uses `fusepy` in the Python backend; per ADR-0020 the shipped FUSE operations path is a platform-critical execution path and must not depend on the Python interpreter — the production implementation is a Rust (or C, where the host requires it) module behind the versioned FFI boundary (ABI-001), gated on the migration rule's evidence (ADR-0016 FUSE-overhead benchmark data).
*   **Performance Benchmarking**: Thorough benchmarking will be required, especially for FUSE overhead and IPC latency, to ensure the system meets performance targets [^4].
*   **Security Policy Generation**: Developing tools or processes to automatically generate and manage LSM and seccomp policies based on container capabilities.

## 6. High-Level Implementation Roadmap

This roadmap aligns with the existing Nyrqis project milestones where applicable and focuses on iterative development.

1.  **Phase 1: Core Container Primitives (Extension of PoC)**
    *   Refactor `nyctr.py` to use direct `clone()`/`unshare()` syscalls.
    *   Implement cgroups v2 support with fallback to v1.
    *   Add basic container suspension and resumption.

2.  **Phase 2: NyFS FUSE Backend (ADR-0016)**
    *   Develop a minimal FUSE daemon for basic file operations.
    *   Implement Copy-on-Write (CoW) for file and directory operations.
    *   Integrate Zstandard compression and checksumming.
    *   Add snapshotting capabilities.

3.  **Phase 3: Capability Enforcement**
    *   Research and integrate a suitable LSM (AppArmor/SELinux) for policy enforcement.
    *   Implement `seccomp-bpf` profiles for syscall filtering.
    *   Develop a capability management system to generate and apply policies.

4.  **Phase 4: IPC Implementation**
    *   Design and implement the core `send`/`receive`/`call`/`notify` primitives.
    *   Integrate capability transfer and attenuation into IPC.
    *   Implement token-bucket rate limiting for IPC channels.

5.  **Phase 5: Boot and Lifecycle Integration**
    *   Create systemd units or equivalent for backend management.
    *   Implement the trusted first process and service orchestration.
    *   Ensure graceful shutdown and error recovery.

## References

[^1]: Myco-mycelium. (2026). *ADR-0012: Adopt NyHAL as a pluggable kernel abstraction layer*. Nyrqis GitHub Repository. `https://github.com/Myco-mycelium/Nythera/blob/main/docs/reference/adr/ADR-0012-nyhal-pluggable-kernel-backend.md`
[^2]: Myco-mycelium. (2026). *NTM-000: The Nyrqis Manifest*. Nyrqis GitHub Repository. `https://github.com/Myco-mycelium/Nythera/blob/main/docs/00-platform/000-THE_NYRQIS_MANIFEST.md`
[^3]: Myco-mycelium. (2026). *NPS-017: NyHAL — Kernel Abstraction Layer and Backend Contract*. Nyrqis GitHub Repository. `https://github.com/Myco-mycelium/Nythera/blob/main/docs/reference/nps/NPS-017-nyhal-kernel-abstraction.md`
[^4]: Myco-mycelium. (2026). *ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem*. Nyrqis GitHub Repository. `https://github.com/Myco-mycelium/Nythera/blob/main/docs/reference/adr/ADR-0016-nyfs-linux-backend-fuse.md`
[^5]: Myco-mycelium. (2026). *nyctr.py*. Nyrqis GitHub Repository. `https://github.com/Myco-mycelium/Nythera/blob/main/source/nyhal-linux-backend/poc-container/nyctr.py`
[^6]: Myco-mycelium. (2026). *ADR-0020: Implementation Languages and the Platform Boundary*. Nyrqis GitHub Repository. `https://github.com/Myco-mycelium/Nythera/blob/main/docs/reference/adr/ADR-0020-implementation-languages.md`
