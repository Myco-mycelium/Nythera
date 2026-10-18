/*
 * prog_interpos.s -- the INTERPOSITION OVERLAY for
 * experimental::elf_dyn_deps_demo: a real `ld -shared -soname
 * interposer.so.1` provider exporting
 *
 *   interp_mark   -- the overlay's observable: an UNVERSIONED data
 *     symbol with an immediate (0x1111222233334444) that nothing in
 *     the first provider carries. The dependent (prog_deps_dep.s)
 *     references it UNDEF via GOTPCREL, so its GLOB_DAT slot is the
 *     interposition check: registered AFTER the first provider into
 *     the demo's registry and marked `SymbolRegistry::interpose(...)`,
 *     this entry must answer for it even though plain load order
 *     would give the first provider the win. (Deliberately
 *     unversioned: a versioned UNDEF reference would bind the
 *     VERNEED file `crosslib.so.1` and take the version file-binding
 *     path, which the overlay legitimately cannot satisfy -- the
 *     interposition question and the version contract question are
 *     kept independent.)
 *   dup_symbol    -- ALSO exported (a real name conflict with the
 *     first provider's versioned export), so the registry fact that
 *     "two objects carry the same name" is real file fact, not a
 *     demo invention. It stays UNUSED as a reference by the
 *     dependent (see above for why).
 */

.section .data
.align 8
.globl interp_mark
interp_mark:
    .quad 0x1111222233334444

.globl dup_symbol
dup_symbol:
    .quad 0x1111222233334445

.section .text
.align 16
.globl interpose_anchor
interpose_anchor:
    ret
