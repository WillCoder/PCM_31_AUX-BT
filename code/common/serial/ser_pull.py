#!/usr/bin/env python3
"""ser_pull.py — 从台架串口拉文本文件回本地。
用法: python3 ser_pull.py <台架路径> <本地路径> [超时秒]
注意: 发 XON 后必须先发回车清行, 否则 XON 会混进命令(踩过)。
哨兵用引号拼接, 避免命令回显里出现哨兵导致提前结束。
"""
import os, sys, time, glob, termios

PORT = (sorted(glob.glob("/dev/cu.usbserial*")) or ["/dev/cu.usbserial-1140"])[0]

def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    remote, local = sys.argv[1], sys.argv[2]
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 150.0

    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd); i, o, c, l, isp, osp, cc = a
    i = 0; o = 0; c = termios.CS8 | termios.CREAD | termios.CLOCAL; l = 0
    cc = list(cc); cc[termios.VMIN] = 0; cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, [i, o, c, l, termios.B57600, termios.B57600, cc])

    def drain(sec):
        t = time.time()
        while time.time() - t < sec:
            try: os.read(fd, 32768)
            except Exception: pass
            time.sleep(0.02)

    try:
        os.write(fd, b"\x11"); time.sleep(0.2)
        os.write(fd, b"\n"); time.sleep(0.3)      # 清掉残留 XON
        drain(0.7)
        os.write(fd, f'cat {remote}; echo ===PULL""EOF===\n'.encode())
        out = b""; t0 = time.time(); last = t0
        while time.time() - t0 < timeout:
            try:
                b = os.read(fd, 32768)
                if b: out += b; last = time.time()
                else: time.sleep(0.01)
            except Exception: time.sleep(0.01)
            if b"===PULLEOF===" in out.replace(b'===PULL""EOF===', b''): break
            if out and time.time() - last > 10: break
    finally:
        os.write(fd, b"\x11"); os.close(fd)

    open(local, "wb").write(out)
    print(f"{remote} -> {local}: {len(out)} bytes")
    return 0

if __name__ == "__main__":
    sys.exit(main())
