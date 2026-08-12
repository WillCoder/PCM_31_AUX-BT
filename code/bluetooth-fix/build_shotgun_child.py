#!/usr/bin/env python3
# 🚨🚨 警告: 本文件的 CODE cave 地址 0x0856484c 【不是死区】🚨🚨
# 它是一张【活的】50x50 音频源仲裁矩阵(基址 0x0856421c, 2500B, 读取者 FUN_080eaa0c
# = B[row*50+col])的稀疏内部; cave 落在 行31(AUDIO_PAUSE, 活行) 列34。
# 往里写代码会污染音频源仲裁。childchain 那次刷进真车"能用"属侥幸, 不是安全。
# ✅ 新工作请用已证明安全的 cave: **0x0860CA58** (1920B, 任意对齐零引用 + 几何闭合 +
#    结构性排除, 内容=链接期删除函数遗留的孤儿调试字符串)。
# 详见 memory: cave-0x0856484c-is-NOT-dead-audio-arbitration-table.md

"""shotgun-CHILD (ROBUST): instrument child-vtable (0x085700e0, 5 methods).

main is a HEAP address that drifts across firmware images / boots (bench working
boot=0x086ed694, another bench dump=0x0872d694, car stock=0x0872f01c). Hardcoding
it blindly can dereference garbage -> fault -> watchdog -> unrecoverable brick.

So this version SELF-CHECKS before acting:
  g_self : r1=*(main); if r1 != 0x085c4c5c (CPSoundPresCtrl vtable) -> skip
  g0     : child=*(main+0x1f0); if child <  0x08600000 -> skip  (null/low)
  g0b    : if child >= 0x09000000 -> skip                       (upper bound, no fault)
  g1     : if *(child+0x68) != -2 -> skip                       (BT connected, not audible)
  g2     : if *(main+0x94c) != 1  -> skip                       (armed / one-shot)
A WRONG main (whatever it points to) fails g_self and becomes a harmless NO-OP:
never a fault, never a brick -- worst case boot-sound just doesn't fire.

On a valid armed bug state: set *(main+0x94c)=0 (recursion guard, since
entertSourceChanged re-enters the instrumented vtable methods), then
entertSourceChanged(main,&24=AUX,&0) + entertSourceChanged(main,&40=BT,&0),
then jmp @r0 to the real method -- transparent (preserves r4-r7/pr/r0)."""
import struct

VT=0x085700e0            # child vtable (5 methods)
CODE=0x0856484c          # RX dead zone (all-zero in both MOPF and 9x1)
ESC=0x082a717c           # entertSourceChanged(this,&src,&flag)
VTAB=0x085c4c5c          # CPSoundPresCtrl vtable (identity check for main)
LO=0x08600000; HI=0x09000000
AUX=24; BT=40
MAIN_BENCH=0x086ed694    # bench shotgun-firmware CPSoundPresCtrl (known-working boot)

def predec(rn,rm): return 0x2006|(rn<<8)|(rm<<4)
def stspr(rn): return 0x4022|(rn<<8)
def ldspr(rn): return 0x4026|(rn<<8)
def postinc(rn,rm): return 0x6006|(rn<<8)|(rm<<4)
def movrr(rn,rm): return 0x6003|(rn<<8)|(rm<<4)
def movi(rn,i): return 0xe000|(rn<<8)|(i&0xff)
def addi(rn,i): return 0x7000|(rn<<8)|(i&0xff)
def cmpeq(rn,rm): return 0x3000|(rn<<8)|(rm<<4)     # T=(Rn==Rm)
def cmphs(rn,rm): return 0x3002|(rn<<8)|(rm<<4)     # cmp/hs Rm,Rn: T=(Rn>=Rm unsigned)
def jsr(rn): return 0x400b|(rn<<8)
def jmp(rn): return 0x402b|(rn<<8)
def nop(): return 0x0009
def movl_r0(rm,rn): return 0x000e|(rm<<8)|(rn<<4)   # mov.l @(r0,Rn),Rm
def movl_to_r0(rm,rn): return 0x0006|(rn<<8)|(rm<<4)# mov.l Rm,@(r0,Rn)

def build(binpath, main_addr=MAIN_BENCH):
    b=open(binpath,'rb').read(); BASE=0x08040000
    slots=[struct.unpack_from('<I',b,VT-BASE+i*4)[0] for i in range(5)]

    # ---- shared routine (r0=method on entry) ----
    prog=[]
    prog+=[predec(15,7),predec(15,6),predec(15,5),predec(15,4),predec(15,0),predec(15,14),stspr(15)]
    prog+=[('pc','Lmain',14)]                              # r14 = main (hardcoded)
    # g_self: r1=*(main); if r1 != CPSoundPresCtrl vtable -> skip  (SAFETY: wrong main = no-op)
    prog+=[movi(0,0),movl_r0(1,14),
           ('pc','Lvt',0),cmpeq(1,0),('bf','Lskip')]
    # g0: child=*(main+0x1f0); if child < 0x08600000 -> skip
    prog+=[('pc','Lx1f0',0),movl_r0(1,14),
           ('pc','Llo',0),cmphs(1,0),('bf','Lskip')]
    # g0b: if child >= 0x09000000 -> skip  (upper bound; garbage never faults g1)
    prog+=[('pc','Lhi',0),cmphs(1,0),('bt','Lskip')]
    # g1: r2=*(child+0x68); if r2 != -2 -> skip
    prog+=[movi(0,0x68),movl_r0(2,1),
           movi(0,-2),cmpeq(2,0),('bf','Lskip')]
    # g2: if *(main+0x94c) != 1 -> skip
    prog+=[('pc','Lx94c',0),movl_r0(2,14),
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
           jmp(0),nop()]                                   # jmp @r0 (= restored method)

    # assemble shared with literal pool at end
    addr={};pc=0;seq=[]
    for x in prog:
        if isinstance(x,tuple) and x[0]=='LABEL': addr[x[1]]=pc
        else: seq.append((pc,x)); pc+=2
    code_end=pc; lit_base=(code_end+3)&~3
    LITS=['Lx1f0','Lx94c','Lesc','Llo','Lmain','Lvt','Lhi']; litaddr={}; p=lit_base
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
            elif x[0]=='bt':
                d=(addr[x[1]]-(pc+4))//2; assert -128<=d<=127,(x,d); emit(pc,0x8900|(d&0xff))
        else: emit(pc,x)
    lv={'Lx1f0':0x1f0,'Lx94c':0x94c,'Lesc':ESC,'Llo':LO,'Lmain':main_addr,'Lvt':VTAB,'Lhi':HI}
    for n in LITS: struct.pack_into('<I',out,litaddr[n],lv[n])
    code=bytearray(out)

    # ---- stubs (each: mov.l .Lm,r0 ; bra shared ; nop ; .long orig_method) ----
    stubs=[]
    for i,m in enumerate(slots):
        so=len(code); sv=CODE+so
        lm=(so+6+3)&~3
        disp=((CODE+lm)-((sv&~3)+4))//4
        assert 0<=disp<=255
        bdisp=(CODE-((sv+2)+4))//2; assert -2048<=bdisp<=2047,(i,bdisp)
        s=bytearray()
        s+=struct.pack('<H',0xd000|disp)               # mov.l .Lm,r0
        s+=struct.pack('<H',0xa000|(bdisp&0xfff))       # bra shared
        s+=struct.pack('<H',0x0009)
        while len(s)<(lm-so): s+=struct.pack('<H',0x0009)
        s+=struct.pack('<I',m)
        code+=s; stubs.append((sv,m))
    return bytes(code),CODE,stubs,slots,shared_len

if __name__=="__main__":
    import sys
    main_addr=int(sys.argv[1],0) if len(sys.argv)>1 else MAIN_BENCH
    binpath=sys.argv[2] if len(sys.argv)>2 else "firmware-cache/patch-lab/chn-fmguard-boot-MOPF/PCM3Root.CHN.fmboot"
    code,base,stubs,slots,slen=build(binpath,main_addr)
    print("shotgun-CHILD ROBUST @0x%08x total=%d shared=%d  main=0x%08x  ESC=0x%08x"%(
        base,len(code),slen,main_addr,ESC))
    print("stubs:",[hex(sv) for sv,_ in stubs])
    try:
        from capstone import Cs,CS_ARCH_SH,CS_MODE_SH4A,CS_MODE_LITTLE_ENDIAN
        md=Cs(CS_ARCH_SH,CS_MODE_SH4A|CS_MODE_LITTLE_ENDIAN)
        print("=== shared 反汇编 ===")
        for insn in md.disasm(bytes(code[:slen-28]),base): print("  0x%08x: %-9s %s"%(insn.address,insn.mnemonic,insn.op_str))
    except Exception as e:
        print("(capstone 不可用,跳过反汇编:%s)"%e)
