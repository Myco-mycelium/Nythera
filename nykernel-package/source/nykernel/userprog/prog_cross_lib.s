/*
 * prog_cross_lib.s -- the PROVIDER half of the cross-object
 * resolution increment: a real `ld -shared` shared object that
 * exports symbols another (later-loaded, real) .so REFERENCES
 * UNDEFINED. It carries no code of its own -- the interesting facts
 * about this file are its dynamic tables (DT_HASH/DT_SYMTAB/
 * DT_STRTAB, filled by `ld -shared` for exported symbols even with
 * no relocations of this object's own) and the runtime addresses of
 * its exports, which experimental::elf::SymbolRegistry resolves
 * FOR the app object's undefined-symbol relocations.
 *
 *   export: cross_value -- the data symbol the app dereferences
 *     through its cross-object-resolved GOT slot (both kernel-side
 *     and in ring 3).
 *   export: dup_symbol -- a DELIBERATE same-named duplicate of the
 *     app object's own export, with a distinguishable immediate, so
 *     the demo can verify the documented interposition rule
 *     (relocated object's OWN dynsym wins; registry matches never
 *     displace self-definitions) rather than assert it.
 */

.section .data
.align 8
.globl cross_value
cross_value:
    /* The payload the app dereferences through its resolved GOT
     * slot; only-ever-reported if the cross-object resolution
     * landed on THIS object (not on the app's same-named export). */
    .quad 0xACE5F00DD1CE5EED

.align 8
.globl dup_symbol
dup_symbol:
    /* Deliberately distinct from the app's dup_symbol immediate:
     * whichever object the resolution rules select, the observed
     * VALUE (and the resolved ADDRESS kernel-side) is unambiguous. */
    .quad 0x00000000BAD0B100
