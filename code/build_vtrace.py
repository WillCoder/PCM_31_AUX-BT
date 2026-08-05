#!/usr/bin/env python3
# 🚨🚨 警告: 本文件的 CODE cave 地址 0x0856484c 【不是死区】🚨🚨
# 它是一张【活的】50x50 音频源仲裁矩阵(基址 0x0856421c, 2500B, 读取者 FUN_080eaa0c
# = B[row*50+col])的稀疏内部; cave 落在 行31(AUDIO_PAUSE, 活行) 列34。
# 往里写代码会污染音频源仲裁。childchain 那次刷进真车"能用"属侥幸, 不是安全。
# ✅ 新工作请用已证明安全的 cave: **0x0860CA58** (1920B, 任意对齐零引用 + 几何闭合 +
#    结构性排除, 内容=链接期删除函数遗留的孤儿调试字符串)。
# 详见 memory: cave-0x0856484c-is-NOT-dead-audio-arbitration-table.md

"""vtrace: instrument all 20 slots of CPSoundPresCtrl main vtable (0x085c4c5c) to
log which methods fire (e.g. on BT connect). Each slot -> a 12-byte stub that loads
its original method addr into r0 and bra's to a shared logger. The logger appends
the method addr to a .data ring buffer (widx@BUF, 64 entries) then jmp @r0 to the
real method -- fully transparent (preserves r1-r3 on stack, never touches r4-r7/pr,
r0 holds the method which the method overwrites as its return anyway).
Read the buffer over serial after a connect to see the fire sequence."""
import struct
from capstone import Cs,CS_ARCH_SH,CS_MODE_SH4A,CS_MODE_LITTLE_ENDIAN

VT=0x085c4c5c
CODE=0x0856484c          # RX dead zone (869B @0x0856484a)
BUF=0x08645e14           # .data dead zone (340B): widx@BUF, entries@BUF+4 (64*4=256)
WIDX=BUF; ENTRIES=BUF+4; MASK=63

def hw(v): return struct.pack('<H',v)
def word(v): return struct.pack('<I',v)

def build(binpath):
    b=open(binpath,'rb').read(); BASE=0x08040000
    slots=[struct.unpack_from('<I',b,VT-BASE+i*4)[0] for i in range(20)]

    # ---- logger (r0=method on entry) ----
    L=[]
    L+=[0x2f16,0x2f26,0x2f36]                 # save r1,r2,r3
    # r1=&widx; r2=widx; r3=entries; r3+=widx*4; *(r3)=r0
    L+=[('movl','Lwidx',1),0x6212,('movl','Lbuf',3),0x4208,0x332c,0x2302]
    # reload widx, +1, &MASK, store
    L+=[0x6212,0x7201,0xe300|MASK,0x2239,0x2122]
    L+=[0x63f6,0x62f6,0x61f6]                  # restore r3,r2,r1
    L+=[0x402b,0x0009]                          # jmp @r0 ; nop (tail, pr untouched)
    # place code, resolve logger pool at end
    # first pass: compute logger length in halfwords (non-tuple=1hw)
    def hwlen(seq): return sum(1 for x in seq)
    logger_hw=hwlen(L)
    # logger literals go right after logger code, 4-aligned
    code=bytearray()
    def emit_seq(seq, base_off, litnames, litvals):
        # base_off = byte offset of seq start within `code`
        out=bytearray(); pcbytes=base_off
        # place code
        body=bytearray()
        # first compute where literals sit: after body, 4-aligned
        nhw=len(seq)
        body_end=base_off+nhw*2
        lit_base=(body_end+3)&~3
        litaddr={}; p=lit_base
        for n in litnames: litaddr[n]=p; p+=4
        for i,x in enumerate(seq):
            pc=base_off+i*2
            if isinstance(x,tuple):
                _,name,rn=x
                la=litaddr[name]; disp=(la-((pc&~3)+4))//4
                assert 0<=disp<=255 and (la-((pc&~3)+4))%4==0,(name,disp,hex(pc))
                body+=hw(0xd000|(rn<<8)|disp)
            else:
                body+=hw(x)
        # pad to lit_base
        while base_off+len(body)<lit_base: body+=hw(0x0009)
        for n in litnames: body+=word(litvals[n])
        return body,litaddr,lit_base+4*len(litnames)
    logger_body,_,after_logger=emit_seq(L,0,['Lwidx','Lbuf'],{'Lwidx':WIDX,'Lbuf':ENTRIES})
    code+=logger_body
    LOGGER=CODE

    # ---- stubs ----
    stubs=[]   # (stub_va, method)
    for i,m in enumerate(slots):
        stub_off=len(code)
        stub_va=CODE+stub_off
        # layout: mov.l .Lm,r0 ; bra logger ; nop ; [pad] ; .Lm .long m  (.Lm 4-aligned)
        # .Lm at stub_off aligned to 4 after 3 hw (6 bytes) -> pad to 8
        lm_off=(stub_off+6+3)&~3   # 4-aligned, >= stub_off+6
        # but must be relative to CODE base for alignment; stub_va based
        lm_va=CODE+lm_off
        # mov.l disp
        movpc=stub_va
        disp=(lm_va-((movpc&~3)+4))//4
        assert 0<=disp<=255 and (lm_va-((movpc&~3)+4))%4==0,(i,disp)
        # bra logger from stub_va+2
        brapc=stub_va+2
        bdisp=(LOGGER-(brapc+4))//2
        assert -2048<=bdisp<=2047,(i,bdisp)
        seq=bytearray()
        seq+=hw(0xd000|(0<<8)|disp)     # mov.l .Lm,r0
        seq+=hw(0xa000|(bdisp&0xfff))   # bra logger
        seq+=hw(0x0009)                  # nop (delay slot)
        while len(seq)<(lm_off-stub_off): seq+=hw(0x0009)
        seq+=word(m)                     # .Lm
        code+=seq
        stubs.append((stub_va,m))
    return bytes(code), CODE, stubs, slots

if __name__=="__main__":
    code,base,stubs,slots=build("firmware-cache/patch-lab/chn-fmguard-boot-MOPF/PCM3Root.CHN.fmboot")
    print("vtrace code @0x%08x  总长=%d字节  logger@0x%08x  buf@0x%08x"%(base,len(code),base,BUF))
    print("20 stubs:")
    for i,(sv,m) in enumerate(stubs): print("  slot+0x%02x: stub@0x%08x -> method 0x%08x"%(i*4,sv,m))
    md=Cs(CS_ARCH_SH,CS_MODE_SH4A|CS_MODE_LITTLE_ENDIAN)
    print("=== logger 反汇编 ===")
    for insn in md.disasm(code[:0x2e],base): print("  0x%08x: %-9s %s"%(insn.address,insn.mnemonic,insn.op_str))
