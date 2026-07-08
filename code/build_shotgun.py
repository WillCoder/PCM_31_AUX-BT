#!/usr/bin/env python3
"""shotgun: instrument all 20 main-vtable slots (0x085c4c5c) so that whichever one
fires while in the BT-boot bug state triggers the fix (AUX->BT), no need to know
which is the connect handler. Each slot -> 12B stub (load orig method into r0, bra
shared). Shared routine: guard child=*(main+0x1f0); if *(child+0x68)==-2 (BT connected
but not audible) AND *(main+0x94c)==1 (bug/one-shot): set *(main+0x94c)=0 (recursion
guard -- entertSourceChanged re-enters instrumented vtable methods), then
entertSourceChanged(main,&24=AUX,&0) + entertSourceChanged(main,&40=BT,&0). Then jmp
@r0 to the real method -- transparent (preserves r4-r7/pr/r0)."""
import struct
from capstone import Cs,CS_ARCH_SH,CS_MODE_SH4A,CS_MODE_LITTLE_ENDIAN

VT=0x085c4c5c
CODE=0x0856484c          # RX dead zone (869B)
ESC=0x082a717c           # entertSourceChanged(this,&src,&flag)
AUX=24; BT=40

def predec(rn,rm): return 0x2006|(rn<<8)|(rm<<4)
def stspr(rn): return 0x4022|(rn<<8)
def ldspr(rn): return 0x4026|(rn<<8)
def postinc(rn,rm): return 0x6006|(rn<<8)|(rm<<4)   # mov.l @Rm+,Rn  (rn dest, rm src)
def movrr(rn,rm): return 0x6003|(rn<<8)|(rm<<4)
def movi(rn,i): return 0xe000|(rn<<8)|(i&0xff)
def addi(rn,i): return 0x7000|(rn<<8)|(i&0xff)
def cmpeq(rn,rm): return 0x3000|(rn<<8)|(rm<<4)
def cmphs(rn,rm): return 0x3002|(rn<<8)|(rm<<4)   # cmp/hs Rm,Rn: T=(Rn>=Rm unsigned)
def jsr(rn): return 0x400b|(rn<<8)
def jmp(rn): return 0x402b|(rn<<8)
def nop(): return 0x0009
def movl_r0(rm,rn): return 0x000e|(rm<<8)|(rn<<4)   # mov.l @(r0,Rn),Rm
def movl_to_r0(rm,rn): return 0x0006|(rn<<8)|(rm<<4) # mov.l Rm,@(r0,Rn)

def build(binpath):
    b=open(binpath,'rb').read(); BASE=0x08040000
    slots=[struct.unpack_from('<I',b,VT-BASE+i*4)[0] for i in range(20)]

    # ---- shared routine (r0=method, r4=main) ----
    prog=[]
    prog+=[predec(15,7),predec(15,6),predec(15,5),predec(15,4),predec(15,0),predec(15,14),stspr(15)]
    prog+=[movrr(14,4)]                                    # r14=main
    # guard0: child=*(main+0x1f0); if child < 0x08600000 skip (null/uninit safety)
    prog+=[('pc','Lx1f0',0),movl_r0(1,14),                 # r0=0x1f0; r1=child
           ('pc','Llo',0),cmphs(1,0),('bf','Lskip')]       # if child < 0x08600000 skip
    # guard1: r2=*(child+0x68); if r2!=-2 skip
    prog+=[movi(0,0x68),movl_r0(2,1),                      # r0=0x68; r2=*(child+0x68)
           movi(0,-2),cmpeq(2,0),('bf','Lskip')]
    # guard2: if *(main+0x94c)!=1 skip
    prog+=[('pc','Lx94c',0),movl_r0(2,14),                 # r0=0x94c; r2=*(main+0x94c)
           movi(0,1),cmpeq(2,0),('bf','Lskip')]
    # *(main+0x94c)=0
    prog+=[movi(1,0),('pc','Lx94c',0),movl_to_r0(1,14)]
    def call_esc(src):
        return [movi(1,0),predec(15,1),movi(1,src),predec(15,1),
                movrr(4,14),movrr(5,15),movrr(6,15),addi(6,4),
                ('pc','Lesc',0),jsr(0),nop(),addi(15,8)]
    prog+=call_esc(AUX)+call_esc(BT)
    prog+=[('LABEL','Lskip'),ldspr(15),postinc(14,15),postinc(0,15),
           postinc(4,15),postinc(5,15),postinc(6,15),postinc(7,15),
           jmp(0),nop()]                                   # jmp @r0 (=restored method)
    # assemble shared with literal pool at end
    addr={};pc=0;seq=[]
    for x in prog:
        if isinstance(x,tuple) and x[0]=='LABEL': addr[x[1]]=pc
        else: seq.append((pc,x)); pc+=2
    code_end=pc; lit_base=(code_end+3)&~3
    LITS=['Lx1f0','Lx94c','Lesc','Llo']; litaddr={}; p=lit_base
    for n in LITS: litaddr[n]=p; p+=4
    shared_len=p
    out=bytearray(shared_len)
    def emit(o,hw): out[o]=hw&0xff; out[o+1]=(hw>>8)&0xff
    for pc,x in seq:
        if isinstance(x,tuple):
            if x[0]=='pc':
                la=litaddr[x[1]]; disp=(la-((pc&~3)+4))//4
                assert 0<=disp<=255 and (la-((pc&~3)+4))%4==0,(x,disp)
                emit(pc,0xd000|(x[2]<<8)|disp)
            elif x[0]=='bf':
                d=(addr[x[1]]-(pc+4))//2; assert -128<=d<=127,(x,d); emit(pc,0x8b00|(d&0xff))
        else: emit(pc,x)
    # 'Lorig' resolved per-call? No: shared jmp @r0 uses r0=method restored from stack.
    # So Lorig literal is unused for jmp; but we referenced it. Replace: jmp @r0 already uses r0.
    # Fix: we don't need Lorig. But prog referenced ('pc','Lorig',0) before jmp(0). That loaded r0=*(Lorig).
    # We must NOT overwrite r0 (it holds method). Remove that. -> handled below by not emitting Lorig load.
    lv={'Lx1f0':0x1f0,'Lx94c':0x94c,'Lesc':ESC,'Llo':0x08600000}
    for n in LITS: struct.pack_into('<I',out,litaddr[n],lv[n])
    code=bytearray(out)

    # ---- stubs ----
    stubs=[]
    for i,m in enumerate(slots):
        so=len(code); sv=CODE+so
        lm=(so+6+3)&~3
        disp=((CODE+lm)-((sv&~3)+4))//4
        assert 0<=disp<=255
        bdisp=(CODE-((sv+2)+4))//2; assert -2048<=bdisp<=2047,(i,bdisp)
        s=bytearray()
        s+=struct.pack('<H',0xd000|disp)      # mov.l .Lm,r0
        s+=struct.pack('<H',0xa000|(bdisp&0xfff))  # bra shared
        s+=struct.pack('<H',0x0009)
        while len(s)<(lm-so): s+=struct.pack('<H',0x0009)
        s+=struct.pack('<I',m)
        code+=s; stubs.append((sv,m))
    return bytes(code),CODE,stubs,slots,shared_len

if __name__=="__main__":
    code,base,stubs,slots,slen=build("firmware-cache/patch-lab/chn-fmguard-boot-MOPF/PCM3Root.CHN.fmboot")
    print("shotgun code @0x%08x total=%d shared=%d  ESC=0x%08x"%(base,len(code),slen,ESC))
    md=Cs(CS_ARCH_SH,CS_MODE_SH4A|CS_MODE_LITTLE_ENDIAN)
    print("=== shared 反汇编 ===")
    for insn in md.disasm(code[:slen-16],base): print("  0x%08x: %-9s %s"%(insn.address,insn.mnemonic,insn.op_str))
