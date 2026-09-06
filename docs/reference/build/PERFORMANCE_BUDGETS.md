---
title: Performance Engineering Budgets
document_id: PERF-001
version: 1.0.0
status: Draft
classification: Reference
owners:
  - Nyrqis Architecture
created: 2026-09-06
updated: 2026-09-06
ai_assisted: true
review_cycle: Quarterly
depends_on: [NPS-001, NPS-003, NPS-012, BENCHMARK_PLAN]
---

# PERF-001 — Performance Engineering Budgets

This document defines performance targets for the Nyrqis platform.
These budgets guide development decisions and are validated through
benchmarking (per `tests/BENCHMARK_PLAN.md`).

## 1. Startup Performance

### 1.1 Boot-to-Desktop Target

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Kernel → init | < 500 ms | N/A (Linux backend) | N/A |
| Init → shell ready | < 2 s | ~1.5 s | ✅ Met |
| Shell → interactive | < 500 ms | ~300 ms | ✅ Met |
| **Total boot-to-interactive** | **< 3 s** | **~2.3 s** | ✅ Met |

### 1.2 Application Launch

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Native app cold start | < 500 ms | ~400 ms | ✅ Met |
| Native app warm start | < 200 ms | ~150 ms | ✅ Met |
| Compatibility app cold start | < 2 s | N/A | ⏳ Pending |
| Shell design load | < 100 ms | ~80 ms | ✅ Met |

## 2. Memory Budgets

### 2.1 System Memory

| Component | Budget | Current | Status |
|-----------|--------|---------|--------|
| Kernel + init | < 100 MB | N/A (Linux) | N/A |
| Compositor | < 150 MB | ~120 MB | ✅ Met |
| Shell (desktop) | < 100 MB | ~85 MB | ✅ Met |
| Per-app overhead | < 50 MB | ~40 MB | ✅ Met |
| **Total idle** | **< 500 MB** | **~450 MB** | ✅ Met |

### 2.2 Per-Container Memory

| Tier | Budget | Usage |
|------|--------|-------|
| Critical (shell, compositor) | 512 MB | System processes |
| Standard (apps) | 256 MB | User applications |
| Restricted (plugins) | 128 MB | Third-party code |
| Minimal (services) | 64 MB | Background services |

## 3. IPC Latency

### 3.1 Target Latencies

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| In-process round-trip | < 50 µs | ~72 µs | ⚠️ Over target |
| Over UDS transport | < 100 µs | ~189 µs | ❌ Not met |
| Over transport (Rust) | < 100 µs | ~307 µs | ❌ Not met |

### 3.2 Mitigation Strategy

The IPC latency target is challenging due to:
1. Python ↔ Rust FFI boundary overhead
2. Unix domain socket context switches
3. Serialization/deserialization costs

**Path to resolution:**
- Complete Rust transport migration (ADR-0020)
- Implement zero-copy shared memory path
- Profile and optimize hot paths

## 4. Filesystem Performance

### 4.1 NyFS Targets

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Sequential write (1 MiB blocks) | > 100 MB/s | ~46 MB/s | ⚠️ Below target |
| Sequential read (1 MiB blocks) | > 200 MB/s | ~37 MB/s | ❌ Not met |
| Small file create (4 KiB) | > 10k ops/s | ~3.6k ops/s | ❌ Not met |
| Compression ratio (text) | > 3:1 | 3.17:1 | ✅ Met |
| Compression ratio (mixed) | > 2:1 | 1.29:1 | ⚠️ Below target |

### 4.2 FUSE Overhead

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Write overhead vs native | < 10x | ~25x | ❌ Not met |
| Read overhead vs native | < 5x | ~15x | ❌ Not met |

**Note:** FUSE overhead is inherent to the architecture. The
decision to use FUSE (ADR-0016) was made for development velocity;
kernel module fallback remains open if overhead proves unacceptable
for gaming workloads.

## 5. Gaming Performance

### 5.1 Frame Rate Targets

| Scenario | Target | Notes |
|----------|--------|-------|
| Desktop compositor | 60 fps | VSync-aligned |
| Casual gaming | 60 fps | 1080p native |
| AAA gaming | 60 fps | 1080p with upscaling |
| Handheld mode | 30 fps | 720p native |

### 5.2 Input Latency

| Metric | Target | Notes |
|--------|--------|-------|
| Input → frame | < 16 ms | One frame at 60fps |
| Input → audio | < 20 ms | Perceptual threshold |
| Gamepad → render | < 8 ms | Critical path |

### 5.3 GPU Memory

| Tier | Budget | Usage |
|------|--------|-------|
| Compositor | 256 MB | Framebuffers, textures |
| Game (1080p) | 2 GB | Textures, geometry |
| Game (4K) | 4 GB | With upscaling |

## 6. AI Performance

### 6.1 Inference Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Model load time | < 5 s | First inference |
| Inference latency | < 100 ms | Per request |
| Streaming tokens | > 20 tok/s | Readable speed |
| Memory (7B model) | < 8 GB | Quantized |

### 6.2 Privacy Constraints

| Requirement | Implementation |
|-------------|----------------|
| Local processing | No cloud inference by default |
| Opt-in telemetry | User must enable explicitly |
| Data retention | Clear policy, user-controlled |

## 7. Network Performance

### 7.1 IPC Transport

| Metric | Target | Notes |
|--------|--------|-------|
| Throughput | > 100 MB/s | Large payloads |
| Latency | < 1 ms | Control messages |
| Connections | > 1000 | Concurrent clients |

### 7.2 Package Download

| Metric | Target | Notes |
|--------|--------|-------|
| Download speed | > 10 MB/s | Network permitting |
| Install time | < 5 s | Small packages |
| Verify time | < 1 s | Signature check |

## 8. Power Efficiency

### 8.1 Idle Power

| Component | Budget | Notes |
|-----------|--------|-------|
| Total system idle | < 5 W | Handheld target |
| Display off | < 2 W | Suspend-to-RAM |

### 8.2 Active Power

| Scenario | Budget | Notes |
|----------|--------|-------|
| Desktop use | < 15 W | Balanced mode |
| Gaming (1080p) | < 25 W | Handheld target |
| Gaming (4K) | < 150 W | Desktop target |

## 9. Storage

### 9.1 Install Size

| Component | Budget | Notes |
|-----------|--------|-------|
| Base system | < 2 GB | Minimal install |
| Full system | < 5 GB | With all subsystems |
| Per-app | < 500 MB | Average application |

### 9.2 Runtime Storage

| Metric | Budget | Notes |
|--------|--------|-------|
| Log rotation | < 100 MB | Per boot |
| Cache | < 500 MB | User-controllable |
| Temp files | < 1 GB | Auto-cleaned |

## 10. Verification

### 10.1 Benchmark Cadence

| Frequency | Scope |
|-----------|-------|
| Per commit | Critical path (IPC, startup) |
| Per PR | Full benchmark suite |
| Weekly | Comprehensive performance report |
| Monthly | Regression analysis |

### 10.2 Regression Detection

- **Automated:** CI benchmarks with threshold alerts
- **Manual:** Monthly review of performance trends
- **User-reported:** Issue tracker monitoring

## Revision History

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-09-06 | Initial performance budgets |
