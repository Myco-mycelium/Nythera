/*
 * boot.s -- Nyrqis kernel bring-up: Multiboot2 entry, 32-bit protected
 * mode -> 64-bit long mode transition, minimal identity paging.
 *
 * A Multiboot2-compliant loader (QEMU's built-in loader via `-kernel`,
 * or GRUB) places the CPU in 32-bit protected mode with paging
 * disabled, EFLAGS.ID checkable, and jumps to this file's `_start`
 * with the multiboot magic in %eax and a pointer to the multiboot
 * info structure in %ebx (Multiboot2 spec §3.2). Everything from here
 * to the jump into long_mode_start is necessarily hand-written assembly
 * -- there is no Rust yet, because the CPU cannot execute 64-bit
 * Rust-compiled code until long mode is actually enabled.
 *
 * This corresponds to NPS-001 (Kernel Architecture and Boot) phases
 * "loader handoff" through "long mode entry" -- see src/boot_phase.rs
 * for where the explicit, order-validated phase state machine picks
 * up once we're in Rust.
 */

.set MB2_MAGIC,        0xe85250d6
.set MB2_ARCH_I386,    0
.set MB2_HEADER_LEN,   (multiboot_header_end - multiboot_header)
.set MB2_CHECKSUM,     -(MB2_MAGIC + MB2_ARCH_I386 + MB2_HEADER_LEN)

.section .multiboot_header, "a"
.align 8
multiboot_header:
    .long MB2_MAGIC
    .long MB2_ARCH_I386
    .long MB2_HEADER_LEN
    .long MB2_CHECKSUM
    # end tag (type=0, flags=0, size=8) -- required, terminates the tag list
    .align 8
    .word 0
    .word 0
    .long 8
multiboot_header_end:

.section .bss
.align 4096
# Four top-level paging structures, built by hand below: one PML4
# entry, one PDPT entry, and 512 PD entries using 2MiB pages -- this
# identity-maps the first 1GiB of physical memory, which is enough to
# cover the kernel image itself, the boot info, and early scratch
# memory. A proper physical frame allocator + higher-half remap is
# Phase 2 work (see IMPLEMENTATION_STATUS.md); this is intentionally
# the smallest paging setup that gets long mode enabled at all.
p4_table:
    .skip 4096
p3_table:
    .skip 4096
p2_table:
    .skip 4096
.align 16
stack_bottom:
    .skip 65536   # 64KiB early boot stack -- generous for pre-Rust setup
stack_top:

# Saved by the 32-bit entry so 64-bit code can pass them to Rust.
.align 8
saved_multiboot_magic: .long 0
saved_multiboot_info:  .long 0

.section .rodata
gdt64:
    .quad 0                                            # null descriptor
.set gdt64_code, . - gdt64
    .quad (1<<43) | (1<<44) | (1<<47) | (1<<53)         # 64-bit code segment
gdt64_pointer:
    .word . - gdt64 - 1
    .quad gdt64

.section .text
.code32
.global _start
.type _start, @function
_start:
    # Never trust the bootloader's interrupt state. Until the PIC is
    # remapped (moving IRQ0-7 off vectors 0x08-0x0F, which collide
    # with CPU exception vectors -- notably IRQ0's default vector 0x08
    # is the SAME number as #DF, Double Fault), any hardware interrupt
    # arriving here would be misdelivered as if it were a CPU
    # exception. Phase 1 doesn't need hardware interrupts at all yet
    # (see IMPLEMENTATION_STATUS.md for PIC/APIC remap as later work),
    # so the correct and simplest fix is: stay masked the entire time.
    cli
    mov $stack_top, %esp
    mov %eax, saved_multiboot_magic
    mov %ebx, saved_multiboot_info

    call check_multiboot
    call check_cpuid
    call check_long_mode

    call setup_page_tables
    call enable_paging

    lgdt gdt64_pointer
    ljmp $gdt64_code, $long_mode_start

# --- sanity checks, each halts with a distinct VGA error code on failure ---
# (VGA text memory at 0xb8000 is usable this early with zero setup,
# which is exactly why it's used here instead of the serial driver --
# the serial driver is Rust code we can't call yet.)

check_multiboot:
    cmp $0x36d76289, %eax
    jne .no_multiboot
    ret
.no_multiboot:
    mov $0x30, %al      # '0'
    jmp error

check_cpuid:
    # Flip EFLAGS.ID (bit 21); if it stays flipped, CPUID is supported.
    pushf
    pop %eax
    mov %eax, %ecx
    xor $(1 << 21), %eax
    push %eax
    popf
    pushf
    pop %eax
    push %ecx
    popf
    cmp %ecx, %eax
    je .no_cpuid
    ret
.no_cpuid:
    mov $0x31, %al      # '1'
    jmp error

check_long_mode:
    mov $0x80000000, %eax
    cpuid
    cmp $0x80000001, %eax
    jb .no_long_mode
    mov $0x80000001, %eax
    cpuid
    test $(1 << 29), %edx   # LM bit
    jz .no_long_mode
    ret
.no_long_mode:
    mov $0x32, %al      # '2'
    jmp error

setup_page_tables:
    # PML4[0] -> PDPT (present, writable)
    mov $p3_table, %eax
    or $0b11, %eax
    mov %eax, (p4_table)

    # PDPT[0] -> PD (present, writable)
    mov $p2_table, %eax
    or $0b11, %eax
    mov %eax, (p3_table)

    # PD[0..512] -> 2MiB pages covering 0..1GiB (present, writable, huge)
    mov $0, %ecx
.map_p2_table:
    mov $0x200000, %eax
    mul %ecx
    or $0b10000011, %eax    # present + writable + huge(2MiB)
    mov %eax, p2_table(,%ecx,8)
    inc %ecx
    cmp $512, %ecx
    jne .map_p2_table
    ret

enable_paging:
    mov $p4_table, %eax
    mov %eax, %cr3

    mov %cr4, %eax
    or $(1 << 5), %eax      # PAE
    mov %eax, %cr4

    mov $0xC0000080, %ecx   # EFER MSR
    rdmsr
    or $(1 << 8), %eax      # LME
    wrmsr

    mov %cr0, %eax
    or $(1 << 31), %eax     # PG
    mov %eax, %cr0
    ret

error:
    # AL holds a single ASCII error code; write "ERR: X" in white-on-red
    # to the top-left of the VGA text buffer, then halt forever.
    mov $0x4f524f45, (0xb8000)   # "ER" red-on-white attr bytes interleaved
    mov $0x4f3a4f52, (0xb8004)   # "R:"
    mov $0x4f204f20, (0xb8008)   # "  "
    mov %al, (0xb800a)
    mov $0x4f, (0xb800b)
    hlt
.hang: jmp .hang

.code64
.global long_mode_start
long_mode_start:
    # Long mode ignores most segmentation; still load null selectors
    # into the data segment registers for a clean, spec-sane state.
    mov $0, %ax
    mov %ax, %ss
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %fs
    mov %ax, %gs

    mov $stack_top, %rsp
    mov $0, %rbp

    # SysV x86_64 calling convention: first two integer args in rdi, rsi.
    xor %rdi, %rdi
    xor %rsi, %rsi
    mov saved_multiboot_magic, %edi
    mov saved_multiboot_info, %esi

    call kernel_main

    # kernel_main is `-> !` and must not return; this is a safety net only.
2:  hlt
    jmp 2b

/*
 * uefi_entry -- Phase 10's native UEFI handoff (boot/uefi/ stub).
 *
 * The Multiboot2 path above enters at 32 bits and BUILDS long mode;
 * UEFI firmware hands control to the stub in long mode already, with
 * the stub's own identity page tables active and ExitBootServices
 * completed. The stub parses the embedded kernel image, then jumps
 * HERE with the same SysV register contract kernel_main has always had:
 *   RDI = Multiboot2 boot-loader magic (0x36d76289 -- the stub passes
 *         the boot-loader magic the spec puts in EAX, not a stub magic;
 *         its synthesized info IS a Multiboot2 info structure),
 *   RSI = the synthesized MBI's physical (== identity) address.
 * Mechanical setup mirrors long_mode_start above: early-boot stack,
 * zero rbp, straight into kernel_main. "cli" is not needed: the UEFI
 * spec guarantees interrupts are disabled when ExitBootServices
 * completes, and no service call happens after that point.
 */
.global uefi_entry
uefi_entry:
    # The firmware's segment registers are still live here (SS/DS/ES =
    # 0x30, FS/GS = 0x38 under OVMF). Long mode ignores most of them,
    # but every IRET the kernel's exception handlers execute re-validates
    # SS against the kernel's own GDT (which kernel_main rebinds), where
    # selector 0x30 is the TSS descriptor -- a General Protection Fault
    # per interrupt return. Load null data selectors first.
    mov $0, %ax
    mov %ax, %ss
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %fs
    mov %ax, %gs

    mov $stack_top, %rsp
    mov $0, %rbp
    call kernel_main

    # kernel_main is `-> !`; safety net only.
2:  hlt
    jmp 2b
