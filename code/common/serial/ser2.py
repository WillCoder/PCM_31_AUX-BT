#!/usr/bin/env python3
"""ser2.py — 台架串口工具 v2。
修正 v1 的致命问题:v1 开了 IXOFF(我方缓冲满就发 XOFF 让对方停),
若关闭端口时没补 XON, PCM 的 UART 会永久停发 -> 串口全哑 + PCM 侧写控制台的进程全阻塞。

v2: 关闭所有流控 + 打开就先发 XON(0x11) 解锁对方。
用法:
  python3 ser2.py                      # 发XON+读(默认57600)
  python3 ser2.py "pidin | grep ui"    # 发XON+跑命令
  BAUD=115200 python3 ser2.py          # 换波特率
  SCAN=1 python3 ser2.py               # 扫常见波特率找有回显的
"""
import os, sys, time, glob, termios

PORT = (sorted(glob.glob("/dev/cu.usbserial*")) or ["/dev/cu.usbserial-1140"])[0]
BAUDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
         57600: termios.B57600, 115200: termios.B115200}

def open_port(baud):
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = a
    iflag = 0                                    # ★无 IXON/IXOFF —— 绝不再发 XOFF
    oflag = 0
    cflag = termios.CS8 | termios.CREAD | termios.CLOCAL   # ★CLOCAL=忽略 modem 线, 无 CRTSCTS 硬流控
    lflag = 0
    cc = list(cc); cc[termios.VMIN] = 0; cc[termios.VTIME] = 0
    b = BAUDS[baud]
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, b, b, cc])
    return fd

def drain(fd, secs, idle_stop=None):
    out = b""; t0 = time.time(); last = t0
    while time.time() - t0 < secs:
        try:
            b = os.read(fd, 8192)
            if b:
                out += b; last = time.time()
            else:
                time.sleep(0.03)
        except (BlockingIOError, OSError):
            time.sleep(0.03)
        if idle_stop and out and (time.time() - last) > idle_stop:
            break
    return out

def try_baud(baud, cmd=None, wait=4.0):
    fd = open_port(baud)
    try:
        os.write(fd, b"\x11")            # ★XON: 解开可能残留的 XOFF 停发状态
        time.sleep(0.3)
        drain(fd, 0.5)                   # 清掉涌出来的积压前缀
        os.write(fd, b"\r\n")
        time.sleep(0.3)
        if cmd:
            os.write(fd, cmd.encode() + b"\r\n")
        return drain(fd, wait, idle_stop=1.5)
    finally:
        os.write(fd, b"\x11")            # 退出前再补一个 XON, 绝不留 XOFF
        os.close(fd)

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if os.environ.get("SCAN"):
        for baud in (57600, 115200, 38400, 19200, 9600):
            out = try_baud(baud, cmd, wait=3.0)
            printable = sum(1 for c in out if 32 <= c < 127 or c in (10, 13))
            print(f"--- baud {baud}: {len(out)} bytes, {printable} printable ---")
            if out:
                sys.stdout.write(out.decode("latin1", "replace")[:800] + "\n")
        return
    baud = int(os.environ.get("BAUD", "57600"))
    out = try_baud(baud, cmd, wait=float(os.environ.get("SER_WAIT", "5")))
    print(f"[{len(out)} bytes @ {baud}]")
    sys.stdout.write(out.decode("latin1", "replace"))

if __name__ == "__main__":
    main()
