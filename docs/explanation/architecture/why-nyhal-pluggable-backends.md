# Why NyHAL Exists: Decoupling the Contracts from the Kernel

*(Explanation — see NPS-017 for the normative spec, ADR-0012 for the formal
decision record, and NPS-001's backend scope note for how it relates to
the original kernel design.)*

## The Problem

ADR-0006 committed Nythera to a hybrid microkernel, and every specification
since — process model, IPC, storage, containers, capabilities — was written
against that target. That's the right long-term architecture, but it has a
real practical cost: a from-scratch kernel is a multi-year effort, and a
project that can't run anything until it's done risks never getting far
enough to find out if the design actually works under real use.

## The Choice

Looking back at NPS-002 through NPS-016, almost none of them actually
describe kernel *internals*. They describe **contracts**: a container must
be capability-scoped, IPC must go through a single arbiter, storage must be
copy-on-write with checksums. NyHAL takes advantage of that: it splits
"what the rest of the OS can rely on" (NyCore) from "what currently
provides it" (a backend), and makes NyKernel one backend among several
rather than the only possible foundation.

The Linux Backend isn't a retreat from ADR-0006 — it's a way to have a
runnable, testable Nythera almost immediately, built on namespaces,
cgroups, and seccomp/LSM to satisfy the exact same container and
capability contracts NPS-002 and NPS-010 already defined. The NyKernel
Backend remains the reference target; NPS-001 didn't get rewritten, it got
scoped — it's now explicitly "the NyKernel Backend's spec," not "the only
possible kernel's spec."

## What This Buys Us

- Application and runtime code (NySDK, NyRuntime) never has to be rewritten
  when a backend changes, because it was never allowed to depend on one
  (NPS-017 §3.2).
- The project can accumulate real usage, bug reports, and design feedback
  on the Linux Backend while NyKernel is still being built, instead of
  waiting years for feedback on a design no one has run yet.
- Every contract written since Milestone 3 turns out to already have been
  backend-agnostic by construction — this wasn't a rewrite, it was
  recognizing what was already true and making it explicit.

## What It Costs Us

- A conformance obligation now exists (NPS-017 §5) that didn't before: a
  backend that satisfies the letter of a contract but not its intent (e.g.
  "isolated" containers that are only isolated by convention) is now
  something that has to be checked for, not just assumed away by having
  one canonical implementation.
- Some real open questions got created rather than answered — whether NyFS
  on Linux is a FUSE filesystem, a kernel module, or something else is
  explicitly deferred (NPS-017 §8) rather than pretended to be solved.
