#!/usr/bin/env python3
"""Assemble the C1-vhook cave: hook CPSoundPresCtrl vtable slot +0x44 (0x085c4ca0,
currently 0x082a7b40 = processCurrentEntertainmentSource dispatcher). The cave, on
each call: preserve r4-r7+pr; if this(+0x944)==0 kick FUN_082a4838(this,&40,&0)
(one-shot: 4838 sets +0x944=1); restore; tail-jmp 0x082a7b40 (which rts to the
original vtable caller). Parameterized by cave base. Verify with capstone."""
import struct
ORIG   = 0x082a7b40   # vtable +0x44 target (tail-call)
F4838  = 0x082a4838

def asm(cave):
    def movl_predec(rn, rm): return 0x2006|(rn<<8)|(rm<<4)   # mov.l Rm,@-Rn
    def stsl_pr(rn):         return 0x4022|(rn<<8)           # sts.l pr,@-Rn
    def movl_postinc(rn, rm):return 0x6006|(rn<<8)|(rm<<4)   # mov.l @Rm+,Rn (Rn dest, Rm src)
    def ldsl_pr(rn):         return 0x4026|(rn<<8)           # lds.l @Rn+,pr
    def mov_rr(rn, rm):      return 0x6003|(rn<<8)|(rm<<4)   # mov Rm,Rn
    def mov_imm(rn, i):      return 0xe000|(rn<<8)|(i&0xff)  # mov #imm,Rn
    def add_imm(rn, i):      return 0x7000|(rn<<8)|(i&0xff)  # add #imm,Rn
    def tst(rn, rm):         return 0x2008|(rn<<8)|(rm<<4)   # tst Rm,Rn
    def jsr(rn):             return 0x400b|(rn<<8)
    def jmp(rn):             return 0x402b|(rn<<8)
    def nop():               return 0x0009
    def movl_r0base(dest, base): return 0x000e|(dest<<8)|(base<<4)  # mov.l @(R0,base),dest
    prog = [
        ('movl_predec',15,7),  # save r7
        ('movl_predec',15,6),  # save r6
        ('movl_predec',15,5),  # save r5
        ('movl_predec',15,4),  # save r4 (this)
        ('stsl_pr',15),        # save pr
        # guard: r1 = this->+0x944  (r4 still = this)
        ('movw_pc',0,'L944'),
        ('movl_r0base',1,4),   # mov.l @(r0,r4),r1
        ('tst',1,1),
        ('bf','Lrestore'),     # +0x944!=0 -> skip kick
        # kick FUN_082a4838(this, &src40, &flag0)
        ('mov_imm',1,0),
        ('movl_predec',15,1),  # push flag=0
        ('mov_imm',1,40),
        ('movl_predec',15,1),  # push src=40
        ('mov_rr',5,15),       # r5=&src
        ('mov_rr',6,15),
        ('add_imm',6,4),       # r6=&flag
        # r4 still = this
        ('movl_pc',0,'L4838'),
        ('jsr',0),
        ('nop',),
        ('add_imm',15,8),      # pop src,flag
        ('LABEL','Lrestore'),
        ('ldsl_pr',15),        # restore pr
        ('movl_postinc',4,15), # restore r4
        ('movl_postinc',5,15), # restore r5
        ('movl_postinc',6,15), # restore r6
        ('movl_postinc',7,15), # restore r7
        ('movl_pc',0,'Lorig'),
        ('jmp',0),             # tail-call 0x082a7b40
        ('nop',),              # delay
    ]
    addr={}; pc=0; seq=[]
    for ins in prog:
        if ins[0]=='LABEL': addr[ins[1]]=pc
        else: seq.append((pc,ins)); pc+=2
    code_end=pc
    lit_base=(code_end+3)&~3
    litaddr={}; p=lit_base
    for n in ('L4838','Lorig'): litaddr[n]=p; p+=4
    litaddr['L944']=p; p+=2
    total=(p+1)&~1
    out=bytearray(total)
    def emit(o,hw): out[o]=hw&0xff; out[o+1]=(hw>>8)&0xff
    lits={'L4838':('l',F4838),'Lorig':('l',ORIG),'L944':('w',0x0944)}
    for pc,ins in seq:
        op=ins[0]
        if op=='movl_predec': hw=movl_predec(ins[1],ins[2])
        elif op=='stsl_pr': hw=stsl_pr(ins[1])
        elif op=='movl_postinc': hw=movl_postinc(ins[1],ins[2])
        elif op=='ldsl_pr': hw=ldsl_pr(ins[1])
        elif op=='mov_rr': hw=mov_rr(ins[1],ins[2])
        elif op=='mov_imm': hw=mov_imm(ins[1],ins[2])
        elif op=='add_imm': hw=add_imm(ins[1],ins[2])
        elif op=='tst': hw=tst(ins[1],ins[2])
        elif op=='jsr': hw=jsr(ins[1])
        elif op=='jmp': hw=jmp(ins[1])
        elif op=='nop': hw=nop()
        elif op=='movl_r0base': hw=movl_r0base(ins[1],ins[2])
        elif op=='movl_pc':
            la=litaddr[ins[2]]; disp=(la-((pc&~3)+4))//4
            assert 0<=disp<=255 and (la-((pc&~3)+4))%4==0,(ins,disp)
            hw=0xd000|(ins[1]<<8)|disp
        elif op=='movw_pc':
            la=litaddr[ins[2]]; disp=(la-(pc+4))//2
            assert 0<=disp<=255,(ins,disp)
            hw=0x9000|(ins[1]<<8)|disp
        elif op=='bf':
            disp=(addr[ins[1]]-(pc+4))//2; assert -128<=disp<=127,(ins,disp)
            hw=0x8b00|(disp&0xff)
        else: raise Exception(op)
        emit(pc,hw)
    for n,(k,v) in lits.items():
        a=litaddr[n]
        if k=='l': struct.pack_into('<I',out,a,v)
        else: struct.pack_into('<H',out,a,v)
    return bytes(out), dict(code_end=code_end,total=total,litaddr=litaddr,labels=addr)

if __name__=="__main__":
    import sys
    CAVE=int(sys.argv[1],16) if len(sys.argv)>1 else 0x085babb0
    blob,meta=asm(CAVE)
    print("vhook cave @0x%08x total=%d code=%d"%(CAVE,meta['total'],meta['code_end']))
    print("hex:",blob.hex())
    from capstone import Cs,CS_ARCH_SH,CS_MODE_SH4A,CS_MODE_LITTLE_ENDIAN
    md=Cs(CS_ARCH_SH,CS_MODE_SH4A|CS_MODE_LITTLE_ENDIAN)
    for insn in md.disasm(blob[:meta['code_end']],CAVE):
        print("  0x%08x: %-9s %s"%(insn.address,insn.mnemonic,insn.op_str))
