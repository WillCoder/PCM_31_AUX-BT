#!/usr/bin/env python3
"""ser_push.py — 通过串口 root shell 把本地文件推到台架(不用插U盘)。

原理: ksh `print -n "\\0nnn\\0nnn..." >> dst` 八进制转义, 只依赖 ksh 自身
(台架没有 uudecode/base64/od/dd)。推完用 FNV-1a 自校验比对。

用法:
  python3 ser_push.py <本地文件> <台架路径> [chunk]
  python3 ser_push.py coexist-app/mvp/gflayer.stripped /tmp/gflayer
"""
import os, sys, time, glob, termios

PORT = (sorted(glob.glob("/dev/cu.usbserial*")) or ["/dev/cu.usbserial-1140"])[0]

def open_port(baud=termios.B57600):
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = a
    iflag = 0; oflag = 0
    cflag = termios.CS8 | termios.CREAD | termios.CLOCAL
    lflag = 0
    cc = list(cc); cc[termios.VMIN] = 0; cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, baud, baud, cc])
    return fd

def wait_prompt(fd, secs=8.0):
    """等 ksh 提示符真正回来。串口无流控, 只靠"静默 60ms"判完成会在回显未排空时
    就发下一条 -> PCM 输入缓冲溢出 -> 整条命令被丢 (实测: chunk192 少1块 / chunk128 少3块)。
    提示符是最后打印的东西, 所以判缓冲区尾部。"""
    out = b""; t0 = time.time()
    while time.time() - t0 < secs:
        try:
            b = os.read(fd, 8192)
            if b: out += b
            else: time.sleep(0.01)
        except (BlockingIOError, OSError): time.sleep(0.01)
        if out.rstrip().endswith(b"/ >"): return True
    return False

def rd(fd, secs, idle=0.35):
    out = b""; t0 = time.time(); last = t0
    while time.time() - t0 < secs:
        try:
            b = os.read(fd, 8192)
            if b: out += b; last = time.time()
            else: time.sleep(0.02)
        except (BlockingIOError, OSError): time.sleep(0.02)
        if out and (time.time() - last) > idle: break
    return out

def cmd(fd, s, wait=3.0):
    os.write(fd, s.encode() + b"\n")
    return rd(fd, wait)

def fnv1a(b):
    h = 2166136261
    for c in b:
        h ^= c; h = (h * 16777619) & 0xFFFFFFFF
    return h

def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    local, dst = sys.argv[1], sys.argv[2]
    chunk = int(sys.argv[3]) if len(sys.argv) > 3 else 512
    data = open(local, "rb").read()
    print(f"push {local} -> {dst}  {len(data)} bytes, chunk={chunk}, fnv=0x{fnv1a(data):08x}")

    fd = open_port()
    try:
        os.write(fd, b"\x11"); time.sleep(0.2); rd(fd, 0.5)
        cmd(fd, f"rm -f {dst}", 2.0)
        n = len(data); sent = 0; t0 = time.time()
        for off in range(0, n, chunk):
            piece = data[off:off+chunk]
            # 可打印且对 ksh 双引号安全的字节直接透传(1字符), 其余才转义(5字符)
            parts=[]
            # ⚠ 每块【第一个字节必须转义】: 若原样透传且恰好是 '-'(0x2d), shell 去引号后
            #   print 会把它当选项 -> "print: -\: unknown option" -> 整块被丢。
            #   实测就是这条: 66516 字节的二进制里第 72 块以 '-' 开头, 每次都少 192 字节。
            first=True
            for b in piece:
                if (not first) and 32 <= b < 127 and b not in (0x22, 0x5c, 0x24, 0x60):   # 排除 " \ $ `
                    parts.append(chr(b))
                else:
                    parts.append("\\0%03o" % b)
                first=False
            esc = "".join(parts)
            os.write(fd, f'print -n "{esc}" >> {dst}\n'.encode())
            if not wait_prompt(fd):
                print(f"  !! 第 {off//chunk} 块等提示符超时, 该块可能丢失", flush=True)
            sent += len(piece)
            if (off // chunk) % 20 == 0:
                el = time.time() - t0
                rate = sent / el if el > 0 else 0
                eta = (n - sent) / rate if rate > 0 else 0
                print(f"  {sent}/{n}  {100*sent//n}%  {rate:.0f} B/s  ETA {eta:.0f}s", flush=True)
        el = time.time() - t0
        print(f"传输完成 {sent} bytes in {el:.0f}s")

        # 台架侧自校验(用 ksh 算 FNV-1a 太慢, 改用 ls 看尺寸 + cksum 若存在)
        out = cmd(fd, f"ls -l {dst}", 3.0)
        print("--- 台架侧 ---"); print(out.decode("latin1", "replace"))
        out = cmd(fd, f"/HBpersistence/QNXTools/cksum {dst} 2>/dev/null", 5.0)
        print(out.decode("latin1", "replace"))
    finally:
        os.write(fd, b"\x11"); os.close(fd)
    return 0

if __name__ == "__main__":
    sys.exit(main())
