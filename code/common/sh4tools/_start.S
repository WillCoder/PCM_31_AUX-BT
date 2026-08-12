.text
.global _start
_start:
    mov.l .L_main, r0   ! ldqnx 已置 r4=argc, r5=argv
    jsr @r0
    nop
    mov r0, r4
    mov.l .L_exit, r0
    jsr @r0
    nop
.L_hang:
    bra .L_hang
    nop
.align 2
.L_main: .long main
.L_exit: .long exit
