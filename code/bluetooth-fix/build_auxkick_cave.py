#!/usr/bin/env python3
"""AUX->BT kick cave (GUARDED) for CPSoundPresCtrl vtable +0x44 hook (->0x082a7b40).
On call: child=*(this+0x1f0); if *(child+0x68)==-2 (BT-connect deadlock / "connected
source NONE"), do FUN_082a4838(this,&24,&0)[AUX] then FUN_082a4838(this,&40,&0)[BT] —
the proven manual AUX->BT that restores BT sound. Self-limiting: once BT establishes
(child+0x68!=-2) it stops, so no flapping and no dead .data flag needed. Tail-jmp 0x082a7b40.
FIX vs v1: saves r14 (was clobbering caller's callee-saved reg)."""
import struct
ORIG=0x082a7b40; F4838=0x082a4838; CHILD_OFF=0x1f0; CONN_OFF=0x68; DEADLOCK=-2; AUX=24; BT=40

def asm(cave):
    def predec(rn,rm): return 0x2006|(rn<<8)|(rm<<4)     # mov.l Rm,@-Rn
    def stspr(rn): return 0x4022|(rn<<8)                 # sts.l pr,@-Rn
    def postinc(rn,rm): return 0x6006|(rn<<8)|(rm<<4)    # mov.l @Rm+,Rn
    def ldspr(rn): return 0x4026|(rn<<8)                 # lds.l @Rn+,pr
    def movrr(rn,rm): return 0x6003|(rn<<8)|(rm<<4)
    def movi(rn,i): return 0xe000|(rn<<8)|(i&0xff)
    def addi(rn,i): return 0x7000|(rn<<8)|(i&0xff)
    def tst(rn,rm): return 0x2008|(rn<<8)|(rm<<4)
    def cmpeq(rn,rm): return 0x3000|(rn<<8)|(rm<<4)      # cmp/eq Rm,Rn  (T = Rn==Rm)
    def jsr(rn): return 0x400b|(rn<<8)
    def jmp(rn): return 0x402b|(rn<<8)
    def nop(): return 0x0009
    def movl_at(rn,rm): return 0x6002|(rn<<8)|(rm<<4)    # mov.l @Rm,Rn
    def movl_r0idx(rn,rb): return 0x000e|(rn<<8)|(rb<<4) # mov.l @(r0,Rb),Rn
    def kick(src):  # FUN_082a4838(r14=this, &src, &0)
        return [('movi',1,0),('predec',15,1),('movi',1,src),('predec',15,1),
                ('movrr',4,14),('movrr',5,15),('movrr',6,15),('addi',6,4),
                ('movl_pc',0,'L4838'),('jsr',0),('nop',),('addi',15,8)]
    prog=[('predec',15,7),('predec',15,6),('predec',15,5),('predec',15,4),
          ('predec',15,14),('stspr',15),           # save r7,r6,r5,r4,r14,pr
          ('movrr',14,4),                          # r14=this
          # guard: r2=child=*(this+0x1f0); r1=*(child+0x68); if r1!=-2 skip
          ('movl_pc',0,'L1f0'),('movl_r0idx',2,14), # r0=0x1f0; r2=@(r0,this)=child
          ('movi',0,CONN_OFF),('movl_r0idx',1,2),   # r0=0x68; r1=@(r0,child)=*(child+0x68)
          ('movi',0,DEADLOCK),('cmpeq',1,0),('bf','Ltail')]  # r0=-2; T=(r1==-2); bf skip
    prog+=kick(AUX)+kick(BT)
    prog+=[('LABEL','Ltail'),('ldspr',15),('postinc',14,15),
           ('postinc',4,15),('postinc',5,15),('postinc',6,15),('postinc',7,15),
           ('movl_pc',0,'Lorig'),('jmp',0),('nop',)]
    addr={};pc=0;seq=[]
    for ins in prog:
        if ins[0]=='LABEL': addr[ins[1]]=pc
        else: seq.append((pc,ins)); pc+=2
    code_end=pc; lit_base=(code_end+3)&~3
    litaddr={};p=lit_base
    for n in ('L1f0','L4838','Lorig'): litaddr[n]=p;p+=4
    total=p
    out=bytearray(total)
    def emit(o,hw): out[o]=hw&0xff;out[o+1]=(hw>>8)&0xff
    lits={'L1f0':CHILD_OFF,'L4838':F4838,'Lorig':ORIG}
    for pc,ins in seq:
        op=ins[0]
        if op=='predec':hw=predec(ins[1],ins[2])
        elif op=='stspr':hw=stspr(ins[1])
        elif op=='postinc':hw=postinc(ins[1],ins[2])
        elif op=='ldspr':hw=ldspr(ins[1])
        elif op=='movrr':hw=movrr(ins[1],ins[2])
        elif op=='movi':hw=movi(ins[1],ins[2])
        elif op=='addi':hw=addi(ins[1],ins[2])
        elif op=='tst':hw=tst(ins[1],ins[2])
        elif op=='cmpeq':hw=cmpeq(ins[1],ins[2])
        elif op=='jsr':hw=jsr(ins[1])
        elif op=='jmp':hw=jmp(ins[1])
        elif op=='nop':hw=nop()
        elif op=='movl_at':hw=movl_at(ins[1],ins[2])
        elif op=='movl_r0idx':hw=movl_r0idx(ins[1],ins[2])
        elif op=='movl_pc':
            la=litaddr[ins[2]];disp=(la-((pc&~3)+4))//4
            assert 0<=disp<=255 and (la-((pc&~3)+4))%4==0,(ins,disp)
            hw=0xd000|(ins[1]<<8)|disp
        elif op=='bf':
            disp=(addr[ins[1]]-(pc+4))//2;assert -128<=disp<=127,(ins,disp);hw=0x8b00|(disp&0xff)
        else:raise Exception(op)
        emit(pc,hw)
    for n,v in lits.items(): struct.pack_into('<i',out,litaddr[n],v)
    return bytes(out),dict(code_end=code_end,total=total,litaddr=litaddr)

if __name__=="__main__":
    import sys
    CAVE=int(sys.argv[1],16) if len(sys.argv)>1 else 0x085babb0
    blob,meta=asm(CAVE)
    print("guarded auxkick cave @0x%08x total=%d code=%d"%(CAVE,meta['total'],meta['code_end']))
    print("hex:",blob.hex())
    from capstone import Cs,CS_ARCH_SH,CS_MODE_SH4A,CS_MODE_LITTLE_ENDIAN
    md=Cs(CS_ARCH_SH,CS_MODE_SH4A|CS_MODE_LITTLE_ENDIAN)
    for insn in md.disasm(blob[:meta['code_end']],CAVE):
        print("  0x%08x: %-9s %s"%(insn.address,insn.mnemonic,insn.op_str))
