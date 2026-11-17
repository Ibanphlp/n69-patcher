.section .text.hook,"ax",%progbits
.global pd_hook
pd_hook:
    stp x29, x30, [sp, #-0x40]!
    mov x29, sp
    stp x0, x1, [sp, #0x10]
    stp x2, x3, [sp, #0x20]
    ldr x4, [sp]
    add x5, sp, #0x40
    bl pd_capture
    ldp x0, x1, [sp, #0x10]
    ldp x2, x3, [sp, #0x20]
    ldp x29, x30, [sp], #0x40
.global nested_stolen
nested_stolen:
    sub sp, sp, #0x50
    b pd_nested_resume

.global pd_save_hook
pd_save_hook:
    stp x29, x30, [sp, #-0x20]!
    mov x29, sp
    stp x0, x1, [sp, #0x10]
    bl pd_render
    ldp x0, x1, [sp, #0x10]
    ldp x29, x30, [sp], #0x20
.global save_stolen
save_stolen:
    stp x29, x30, [sp, #-0x10]!
    b pd_save_resume

.global pd_context
pd_context:
    adr x0, pd_context
    ldr x1, pd_context_anchor
    sub x0, x0, x1
    ldr x1, pd_context_target
    add x0, x0, x1
    ret
.balign 8
.global pd_context_anchor
pd_context_anchor:
    .quad 0x1111222233334444
.global pd_context_target
pd_context_target:
    .quad 0

.global pd_vinfo
pd_vinfo:
    adr x0, pd_vinfo
    ldr x1, pd_vinfo_anchor
    sub x0, x0, x1
    ldr x1, pd_vinfo_target
    add x0, x0, x1
    ret
.balign 8
.global pd_vinfo_anchor
pd_vinfo_anchor:
    .quad 0x2222333344445555
.global pd_vinfo_target
pd_vinfo_target:
    .quad 0

.global pd_doprnt
pd_doprnt:
    adr x16, pd_doprnt
    ldr x17, pd_doprnt_anchor
    sub x16, x16, x17
    ldr x17, pd_doprnt_target
    add x16, x16, x17
    br x16
.balign 8
.global pd_doprnt_anchor
pd_doprnt_anchor:
    .quad 0x3333444455556666
.global pd_doprnt_target
pd_doprnt_target:
    .quad 0

pd_nested_resume:
    adr x16, pd_nested_resume
    ldr x17, pd_nested_anchor
    sub x16, x16, x17
    ldr x17, pd_nested_target
    add x16, x16, x17
    br x16
.balign 8
.global pd_nested_anchor
pd_nested_anchor:
    .quad 0x4444555566667777
.global pd_nested_target
pd_nested_target:
    .quad 0

.global pd_nested_resume
pd_save_resume:
    adr x16, pd_save_resume
    ldr x17, pd_save_anchor
    sub x16, x16, x17
    ldr x17, pd_save_target
    add x16, x16, x17
    br x16
.balign 8
.global pd_save_anchor
pd_save_anchor:
    .quad 0x5555666677778888
.global pd_save_target
pd_save_target:
    .quad 0

.global pd_slide
pd_slide:
    adr x0, pd_slide
    ldr x1, pd_slide_anchor
    sub x0, x0, x1
    ret
.balign 8
.global pd_slide_anchor
pd_slide_anchor:
    .quad 0x6666777788889999
