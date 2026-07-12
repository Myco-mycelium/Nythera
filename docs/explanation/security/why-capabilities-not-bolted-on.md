# Why Capabilities Aren't Bolted On

*(Explanation — see NPS-010, NPS-011 for the normative specs, and ADR-0004,
ADR-0009 for the formal decision records.)*

## The Problem

Most operating systems add permissions after the fact: an app runs with
broad access by default, and a permission system sits on top, intercepting
specific sensitive calls (camera, location) while leaving everything else
implicitly available. That model works, but it means the "everything else"
category — arbitrary filesystem access, unlimited IPC, background
execution — is trusted by default rather than by decision.

## The Choice

Nythera inverts this. Every container starts with nothing (NPS-010 §4.2)
and is evaluated against an explicit manifest before anything is granted.
The capability registry (NPS-011) is not a list of "sensitive things to
watch out for" — it's the complete list of everything any container is
allowed to do, including things that sound mundane, like drawing a window
(`CAP-DISPLAY`) or receiving keyboard input (`CAP-INPUT`). Those get a
`Default Grant` because their risk tier is genuinely low, not because
they're outside the system.

This is the same idea that shaped the kernel boundary (ADR-0006) and the
shared runtime model (NPS-007/008): rather than making "trusted by
default" and "sandboxed" two different categories of software, everything
goes through the same evaluation, and the *outcome* differs by capability
risk tier, not by application origin.

## Why Rate Limiting Belongs Here Too

ADR-0009's IPC token buckets might look like a performance detail, but they
follow from the same principle: if capability grants are how a container's
data access is bounded, resource limits are how a container's *impact on
other containers* is bounded. Without it, a container holding zero
sensitive capabilities could still degrade the whole system just by
flooding IPC — a denial-of-service path that a purely data-access-focused
permission model would miss entirely.

## What This Buys Us

- "What can this app actually do" has one answer, checkable at
  `NPS-010 §8`'s audit record, not "check the permission list, then also
  check for anything the OS trusts by default."
- Android's permission model maps cleanly onto this system (NPS-008 §5)
  instead of needing its own parallel enforcement path.
- Adding a genuinely new kind of access to the platform later means adding
  a row to NPS-011, not auditing every subsystem to see what implicitly
  already had it.

## What It Costs Us

- More manifest evaluation work at container creation (NPS-010 §4) than a
  "just start the process" model.
- An explicit, ongoing maintenance burden: every new subsystem that wants
  to gate something has to define a capability for it rather than reusing
  an ambient permission.
