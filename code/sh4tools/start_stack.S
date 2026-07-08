.text
.global _start
_start:
    mov.l @r15, r4
    mov r15, r5
    add #4, r5
    mov.l .Lmain, r0
    jsr @r0
    nop
    mov r0, r4
    mov.l .Lexit, r0
    jsr @r0
    nop
1:  bra 1b
    nop
.align 2
.Lmain: .long main
.Lexit: .long exit
