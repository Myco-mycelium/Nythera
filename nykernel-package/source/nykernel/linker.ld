/*
 * linker.ld -- places the kernel at the classic 1MiB physical load
 * address. Multiboot2 requires the header appear within the first
 * 32KiB of the file, hence .multiboot_header is placed first and
 * given its own PT_LOAD-eligible position immediately at the load
 * address.
 *
 * This is a low-half, identity-mapped-only layout -- appropriate for
 * Phase 1 bring-up. A higher-half kernel layout (conventional for a
 * production microkernel, and implied but not yet made explicit by
 * NPS-001) is out of scope here and tracked in IMPLEMENTATION_STATUS.md
 * as Phase 2+ work.
 */

ENTRY(_start)

SECTIONS
{
    . = 1M;
    _kernel_start = .;

    .boot :
    {
        KEEP(*(.multiboot_header))
    }

    .text :
    {
        *(.text .text.*)
    }

    .rodata :
    {
        *(.rodata .rodata.*)
    }

    .data :
    {
        *(.data .data.*)
    }

    .bss :
    {
        *(COMMON)
        *(.bss .bss.*)
    }

    _kernel_end = .;

    /DISCARD/ :
    {
        *(.eh_frame)
        *(.comment)
        *(.note.*)
    }
}
