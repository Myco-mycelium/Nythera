//! # Experimental code -- NPS-002/003 were ACCEPTED 2026-10-11; this module is
//! now the conformance-implementation track, not a pre-normative island
//!
//! History, kept because the module's posture was decided by it: for the
//! life of `NPS-002`/`NPS-003`'s `Draft` status (2026-07-12..2026-10-11),
//! everything under `crate::experimental` was an **technology proof**,
//! deliberately isolated from `pic.rs`/`idt.rs` so in the source tree that
//! the separation (``proved-mechanism-only, no conformance claim``) was
//! visible in the tree, per `NPC-001 Project Constitution` §5 (a `Draft`
//! spec is not binding, so no conformance could be claimed).
//!
//! **2026-10-11 (AG decision-log rows I1/I2, owner direction via the
//! recorded session): both specs are `Accepted`.** The isolation is
//! retained as a source-tree fact, but its reason is gone: this module is
//! no longer "pre-normative" -- it is the location where the kernel's
//! NPS-002/NPS-003 conformance work starts, and the first concrete policy
//! increments against the accepted text land here (the bounded per-task
//! activation budget in `scheduler`, and the per-sender backpressure
//! accounting + receive-side contract validation in `ipc`). The
//! convention that this module must visibly track spec status is
//! retained: when either Accepted spec's implementation moves out of
//! `experimental` into the kernel's real subsystems, the move happens
//! under a documented change record, and any residual mechanism proofs
//! (loaders, format parsers -- not policy) stay here labelled by what
//! they are.
//!
//! What remains deliberately out of scope for the batch-1 increments
//! (and for any later ones until a decision record names them):
//! NPS-002 §9's real-time guarantee NUMBERS (a benchmark/tuning
//! follow-up on `ADR-0013`'s accepted weight table, not a gate), and the
//! full capability-scoping design work NPS-003 §5's normative text
//! implies beyond the mechanism-level checks landed below. The
//! boot-verified self-test discipline is unchanged.

pub mod address_space;
pub mod chain;
pub mod crypto;
pub mod dex;
pub mod dex_demo;
pub mod elf;
pub mod elf_dyn_cross_demo;
pub mod elf_dyn_deps_demo;
pub mod elf_dyn_demo;
pub mod lazy_binder;
pub mod elf_dyn_full_demo;
pub mod elf_dyn_plt_demo;
pub mod ipc;
pub mod isolated_tasks;
pub mod isolation_demo;
pub mod pe;
pub mod pe_demo;
pub mod pe_import_demo;
pub mod pe_reloc_demo;
pub mod pie_demo;
pub mod ring3_scheduler;
pub mod scheduler;
pub mod teardown_demo;
pub mod usermode;
