/*
 * prog_weak_dep.s -- the WEAK-VERSION-FLAG artifact for
 * experimental::elf_dyn_deps_demo: a real `ld -shared` object whose
 * ONLY undefined symbol is a WEAK bind of `cross_value` (a symbol the
 * versioned provider DEFINES under VERS_1.0). Built by linking
 * against the provider WITHOUT any object that binds that version
 * strongly, so the linker is forced to record the version dependency
 * with `VER_FLG_WEAK` on the `.gnu.version_r` aux entry (GNU ld marks
 * the flag when ALL binds in that version's dependency are weak --
 * the layout gate pins the literal `Flags: WEAK` fact). This is the
 * proof the seventh-round weak rule's VERNEED-flag half is not merely
 * parsed but REQUIRED to behave:
 *
 *   against a scope that provides cross_value@VERS_1.0 -- the weak
 *     requirement RESOLVES normally (weak relaxes only the
 *     UNRESOLVED case, never the found case);
 *   against a scope whose provider declares the same SONAME with only
 *     VERS_2.0 (where a STRONG requirement is the typed
 *     `VersionNotProvided` refusal, proven every boot in the full
 *     demo) -- the WEAK requirement takes the NULL slot (0) instead,
 *     the load PROCEEDS, and the GNU_RELRO region still revokes
 *     read-only.
 *
 * The loader never runs this file's entry point -- the proofs are
 * entirely kernel-side slot facts; the reference below exists to make
 * the linker emit the weak GLOB_DAT + VERNEED surface.
 */

.weak cross_value

.section .text
.align 16
.globl weak_payload
weak_payload:
    mov   cross_value@GOTPCREL(%rip), %rax /* WEAK UNDEF, versioned: the slot is the artifact */
    ret                                   /* never executed by the loader; the payload is a placeholder */
