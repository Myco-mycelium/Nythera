//! Phase 2: memory management.
//!
//! Three pieces, deliberately kept in separate files with separate
//! responsibilities: `frame_allocator` (which physical frames are
//! free), `paging` (mapping virtual pages to physical frames), and
//! `heap` (using both to back `#[global_allocator]`). Each is usable
//! independently -- `kernel_stack` (guarded kernel stacks for live
//! ring-3 tasks), for example, needs `frame_allocator` + `paging` but
//! not `heap`.

pub mod frame_allocator;
pub mod guard_demo;
pub mod heap;
pub mod kernel_stack;
pub mod paging;
pub mod tlb_demo;
