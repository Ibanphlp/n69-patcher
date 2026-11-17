SECTIONS {
  . = 0x0;
  .text : {
    KEEP(*(.text.hook))
    *(.text .text.*)
    *(.rodata .rodata.*)
  }
  /DISCARD/ : {
    *(.interp) *(.dynsym) *(.dynstr) *(.gnu.hash) *(.hash) *(.dynamic)
    *(.eh_frame*) *(.comment) *(.note*) *(.ARM.attributes)
  }
}
