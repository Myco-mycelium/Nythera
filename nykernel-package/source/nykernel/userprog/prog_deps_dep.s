/*
 * prog_deps_dep.s -- the DEPENDENT for experimental::elf_dyn_deps_
 * demo: a real `ld -shared` object that carries, AT ONCE, every
 * resolution class the catalog/interposition/weak increment proves:
 *
 *   DT_NEEDED crosslib2.so.1 + DT_NEEDED crosslib.so.1
 *     -- recorded by the linker from the command-line objects (both
 *        needed providers are inputs; the two-hop chain
 *        dependent -> crosslib2 -> crosslib is what the recursive
 *        loader satisfies dependency-first from the demo's catalog).
 *   cross_value UNDEF @GOTPCREL   -- a versioned GLOB_DAT binding
 *     `@VERS_1.0` against file crosslib.so.1 (readelf-pinned in the
 *     layout gate).
 *   interp_mark UNDEF @GOTPCREL   -- the INTERPOSITION check: an
 *     UNVERSIONED UNDEF GLOB_DAT the registry must answer through
 *     the overlay (interposer.so.1), NOT plain load order.
 *   ghost_fn@VERS_1.0 UNDEF WEAK @GOTPCREL
 *     (`.weak ghost_fn_v1` + `.symver ghost_fn_v1,
 *     ghost_fn@VERS_1.0`, and the reference below) -- the weak-
 *     semantics control: NO provider defines it. Required: the
 *     GLOB_DAT slot holds the NULL slot (0), the load PROCEEDS (the
 *     loader could refuse it as UndefinedSymbol/VersionNotProvided
 *     if the weak tolerance were wrong -- both positive checks and
 *     the relro revoke in the demo pin the refusal NOT happening).
 *   cross_fn called through a real .plt stub -- a JUMP_SLOT bound
 *     LAZILY: the first of THREE calls traps the SHARED
 *     lazy_binder service (vector 0x88, dispatched by this object's
 *     own trampoline page); calls two and three take the direct
 *     path.
 *
 * On its private data page it records cross_value, interp_mark, the
 * ghost GLOB_SLOT ADDRESS (never dereferenced -- that is the honest
 * posture toward a null slot), and the three cross_fn results, then
 * traps vector 0x8A.
 */

.weak ghost_fn

.section .text
.align 16
.globl deps_payload
deps_payload:
    movabs $0x6D000400000, %rdi            /* PRIVATE_ADDR (matches elf_dyn_deps_demo.rs) */
    mov   cross_value@GOTPCREL(%rip), %rax /* versioned GLOB_DAT: provider's cross_value */
    mov   (%rax), %rbx
    mov   %rbx, (%rdi)
    mov   interp_mark@GOTPCREL(%rip), %rax /* UNDEF GLOB_DAT served by the interposition overlay */
    mov   (%rax), %rbx
    mov   %rbx, 8(%rdi)
    mov   ghost_fn@GOTPCREL(%rip), %r8     /* weak UNDEF GLOB_DAT: the SLOT ADDRESS (content is the NULL slot) */
    mov   %r8, 40(%rdi)
    call  cross_fn                          /* PLT: lazy JUMP_SLOT; first call traps the shared service */
    mov   %eax, 16(%rdi)
    call  cross_fn                          /* direct path from here on */
    mov   %eax, 24(%rdi)
    call  cross_fn
    mov   %eax, 32(%rdi)
    int $0x8A
.hang:
    pause
    jmp .hang
