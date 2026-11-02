ENTRY(_start)
SECTIONS
{
    . = 0x50000000000;
    .text : { *(.text._start) *(.text .text.*) }
    .rodata : ALIGN(4096) { *(.rodata .rodata.*) *(.got .got.plt) }
    .data : ALIGN(4096) { *(.data .data.*) }
    .bss : ALIGN(4096) { *(.bss .bss.*) *(COMMON) }
    /DISCARD/ : { *(.eh_frame) *(.comment) *(.note.*) }
}
