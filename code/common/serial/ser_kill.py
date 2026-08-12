#!/usr/bin/env python3
"""ser_kill.py — 杀掉台架上指定名字的进程。
台架 slay 是交互式问 y/N(-f 不管用), 所以要自动回答 y。
用法: python3 ser_kill.py gflayer
"""
import os, sys, time, glob, termios

PORT = (sorted(glob.glob("/dev/cu.usbserial*")) or ["/dev/cu.usbserial-1140"])[0]

def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "gflayer"
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd); i, o, c, l, isp, osp, cc = a
    i = 0; o = 0; c = termios.CS8 | termios.CREAD | termios.CLOCAL; l = 0
    cc = list(cc); cc[termios.VMIN] = 0; cc[termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, [i, o, c, l, termios.B57600, termios.B57600, cc])

    def drain(s):
        t = time.time()
        while time.time() - t < s:
            try: os.read(fd, 32768)
            except Exception: pass
            time.sleep(0.03)

    try:
        os.write(fd, b"\x11"); time.sleep(0.2)
        os.write(fd, b"\n"); time.sleep(0.3); drain(0.6)
        os.write(fd, f"slay {name}\n".encode()); time.sleep(1.0)
        for _ in range(15):                    # 对每个确认提示回答 y
            os.write(fd, b"y\n"); time.sleep(0.18)
        os.write(fd, b"\x03\n"); time.sleep(0.5)   # Ctrl-C 收尾
        drain(0.8)
        os.write(fd, b"\n"); time.sleep(0.3); drain(0.4)
        os.write(fd, f"pidin | grep {name}\n".encode()); time.sleep(2.0)
        out = b""; t0 = time.time()
        while time.time() - t0 < 3:
            try:
                b = os.read(fd, 32768)
                if b: out += b
                else: time.sleep(0.03)
            except Exception: time.sleep(0.03)
        txt = out.decode("latin1", "replace")
        left = [ln for ln in txt.split("\n") if name in ln and "grep" not in ln]
        print(f"残留 {name} 进程: {len(left)}")
        for ln in left: print("  ", ln.strip())
    finally:
        os.write(fd, b"\x11"); os.close(fd)

if __name__ == "__main__":
    main()
