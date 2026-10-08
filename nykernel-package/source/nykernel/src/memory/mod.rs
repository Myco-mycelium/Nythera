//! Phase 2: memory management.
//!
//! Three pieces, deliberately kept in separate files with separate
//! responsibilities: `frame_allocator` (which physical frames are
//! free), `paging` (mapping virtual pages to physical frames), and
//! `heap` (using both to back `#[global_allocator]`). Each is usable
//! independently -- Phase 3's future kernel-stack allocation, for
//! example, needs `frame_allocator` + `paging` but not `heap`.

pub mod frame_allocator;
pub mod guard_demo;
pub mod heap;
pub mod paging;
pub mod tlb_demo;
