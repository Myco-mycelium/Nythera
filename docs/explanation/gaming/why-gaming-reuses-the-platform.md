# Why Gaming Features Reuse the Platform, Not the Other Way Around

*(Explanation — see NPS-012, NPS-013, NPS-014 for the normative specs, and
ADR-0010 for the formal decision record.)*

## The Problem

It would be easy to treat "gaming" as a special mode that gets its own
rules — elevated access for performance, a separate input pipeline for
latency, bundled content for convenience. Every one of those shortcuts
would undermine something the rest of the platform already spent six
milestones establishing: that a container's access is defined by its
capabilities (NPS-010, NPS-011), not by what kind of software it happens
to be running.

## The Choice

Nothing in this milestone gets a carve-out. Controllers deliver input
through the exact same `CAP-INPUT` capability and IPC path as a keyboard
(NPS-012 §3.2). Graphics features are exposed as ordinary Vulkan
capabilities an application queries (NPS-013 §3.1) rather than a
Nythera-specific "gaming API" layered on top. Even the Emulator Hub — the
part of the design most tempted toward a shortcut, since ROM libraries are
exactly the kind of loose file collection the rest of the platform tries
to move away from — still runs each emulator in its own container with
scoped capabilities (NPS-014 §5), not broad filesystem access "because
it's just an emulator."

The one place this milestone did make a real decision rather than just
extend an existing one: choosing Vulkan (ADR-0010) as what native
rendering and the Windows DirectX-to-Vulkan translation (already assumed
back in NPS-007) both actually target. That decision was overdue — NPS-007
had been describing a translation without ever naming what it translates
*to*.

## What This Buys Us

- A security review of "the gaming subsystem" is really just a review of
  ordinary containers requesting ordinary capabilities — there's no
  separate, less-scrutinized code path to audit.
- HDR, VRR, ray tracing, and upscaling all inherit the same
  graceful-degradation rule (NPS-013 §3.2): unsupported hardware means
  reduced fidelity, never a refusal to launch.
- The Emulator Hub can be honest about what it is — an organizer for files
  the user already has — without pretending to solve ROM/BIOS acquisition,
  which was never the platform's problem to solve (NPS-014 §3).

## What It Costs Us

- No performance shortcuts specific to gaming; latency-sensitive input and
  rendering have to earn their priority through the same scheduling
  (NPS-002 §6.2) and IPC (NPS-003, ADR-0009) mechanisms everything else
  uses, rather than bypassing them.
- Vendor-specific technologies (Steam Input, FSR, XeSS) are explicitly
  kept optional (NPS-012 §4, NPS-013 §7.2) rather than deeply integrated,
  which means slightly more integration work to keep the platform's
  default path vendor-neutral.
