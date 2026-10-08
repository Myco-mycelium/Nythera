//! # Experimental, pre-normative code -- read before using anything here
//!
//! Everything under `crate::experimental` is a **technology proof**,
//! not a specification-conformant implementation of anything. It
//! exists to demonstrate that a technique works on real hardware
//! (verified via an actual QEMU boot, same evidence standard as the
//! rest of this kernel), not to be the kernel's real process/scheduling
//! subsystem.
//!
//! Specifically: `NPS-002` (Process and Thread Model) is `Draft`,
//! benchmark-blocked at §9, per `NPC-004 Specification Index`. Per
//! `NPC-001 Project Constitution` §5, a `Draft` spec is not binding.
//! Code in this module is therefore explicitly **not** represented as
//! NPS-002 conformance -- no claim is made that this scheduler's
//! design matches what NPS-002 will eventually require (task
//! isolation, capability-scoped resource limits, the real requirements
//! a Draft spec doesn't have settled yet). It is kept in its own
//! module, separate from `pic.rs`/`idt.rs` (which genuinely are
//! NPS-001-territory and are not pre-normative), specifically so that
//! separation is visible in the source tree and not just in a doc
//! comment nobody reads.
//!
//! When NPS-002 reaches `Accepted`, this module should be treated as
//! throwaway reference material for the real implementation, not
//! upgraded in place and relabeled.

pub mod address_space;
pub mod crypto;
pub mod dex;
pub mod dex_demo;
pub mod elf;
pub mod ipc;
pub mod isolated_tasks;
pub mod isolation_demo;
pub mod pe;
pub mod pe_demo;
pub mod pe_reloc_demo;
pub mod pie_demo;
pub mod ring3_scheduler;
pub mod scheduler;
pub mod teardown_demo;
pub mod usermode;
