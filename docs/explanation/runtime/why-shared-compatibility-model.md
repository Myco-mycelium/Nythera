# Why Windows and Android Compatibility Share One Model

*(Explanation — see NPS-007, NPS-008, NPS-009 for the normative specs, and
ADR-0005, ADR-0008 for the formal decision records.)*

## The Problem

Windows and Android are architecturally nothing alike — one is a Win32/NT
API surface with a registry and DirectX; the other is a Java/Kotlin
application model on top of a Linux kernel with its own permission system.
It would be easy to end up with two unrelated compatibility subsystems,
each with its own idea of what a "sandboxed application" means, and a
native app ecosystem that's trusted differently from both.

## The Choice

Both runtimes (NPS-007, NPS-008) are built as translation layers, not full
emulators, and — more importantly — both are required to place every
application they run inside the exact same container primitive that native
applications use (ADR-0004, NPS-002 §4). A Windows `.exe` and a `.apk`
don't get their own special trust tier; they get a capability set, exactly
like a native app does, and Android's own permission model is mapped
directly onto that same capability system (NPS-008 §5) rather than being
allowed to persist as a second, parallel permission model.

This is the same principle already established for the kernel boundary in
ADR-0006: rather than inventing a new isolation concept for each new
problem, extend the one that already exists.

## What This Buys Us

- A security review only needs to reason about "containers and
  capabilities" once, not once per runtime.
- The adaptive UI shell (NPS-009) can treat windows from any runtime
  identically, because they're all just containers with a presentation
  surface — it doesn't need runtime-specific special cases.
- Game installs, saves, and verification (NPS-006) work the same way
  whether the game is native, Windows-compat, or an Android port.

## What It Costs Us

- Neither runtime gets to take architectural shortcuts a "just run it
  in an emulator" approach might allow — DirectX-to-Vulkan translation and
  an AOSP-based container both take real engineering effort compared to
  full emulation.
- Some real compatibility gaps are being named rather than hidden:
  kernel-level anti-cheat (NPS-007 §9) and Google Play Services dependency
  (NPS-008 §9) are both acknowledged limitations, not solved problems.

## The Shared Open Risk

Both runtimes independently flagged the same hard problem: running x86
code on ARM hardware (or vice versa) for Windows titles, and running
ARM-compiled APKs on x86 hardware. Rather than let two teams solve this
twice, NPS-007 §7 and NPS-008 §7 explicitly point at each other and defer
to shared future work.
