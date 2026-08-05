#!/usr/bin/env python3
"""shotdiff.py — 把 pcmshot 抓的两帧(隐藏/显示)做差分, 客观量出 overlay 的实际几何。

由来(2026-08-04): 之前每改一次布局都要让用户盯屏幕拍照, 又慢又容易看漏。
现在有了真截图(coexist-app/mvp/pcmshot.c 用 gf_display_snapshot 抓合成后的画面),
就能自己核对: 抓"弹窗隐藏"和"弹窗显示"两帧, 差分出的外接框 = overlay 在屏上的真实占位。
第一次用它就抓到: 宽 372 正好、**高 77 多了 1 行** -> 证明 dst 视口的高也是 x2-x1+1,
代码里"高=y2-y1(无+1)"的老注释是错的(这就是用户看到的"约1像素溢出")。

用法:
    python3 dev/shotdiff.py <bg_raw.txt> <fg_raw.txt> <origin_x> <origin_y> [期望w 期望h]
两个输入是 ser_pull 拉回来的原始文件(带串口回显, 本脚本自己切干净)。
产出 /tmp/cmp_bg.png /tmp/cmp_fg.png /tmp/cmp_diff.png, 并打印外接框与期望值的比对。
"""
import struct, sys, zlib

def load(p):
    raw = open(p, 'rb').read()
    i = raw.find(b'P5HEX')
    if i < 0: raise SystemExit(f'{p}: 找不到 P5HEX 头')
    j = raw.rfind(b'===PULLEOF===')
    if j < 0: j = len(raw)
    lines = raw[i:j].split(b'\n')
    hd = lines[0].split()
    w, h = int(hd[1]), int(hd[2])
    good = [l.strip() for l in lines[1:] if len(l.strip()) >= w * 8]
    if len(good) < h: raise SystemExit(f'{p}: 只拿到 {len(good)} 行, 期望 {h}')
    px = []
    for y in range(h):
        L = good[y]; row = []
        for x in range(w):
            v = int(L[x*8:(x+1)*8], 16)          # BGRA8888: u32 = A<<24|R<<16|G<<8|B
            row.append(((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff))
        px.append(row)
    return w, h, px

def png(path, w, h, rows):
    raw = b''.join(b'\x00' + bytes(c for p in r for c in p) for r in rows)
    def ck(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n'
        + ck(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
        + ck(b'IDAT', zlib.compress(raw, 9)) + ck(b'IEND', b''))

def main():
    if len(sys.argv) < 5:
        print(__doc__); return 1
    bgp, fgp = sys.argv[1], sys.argv[2]
    ox, oy = int(sys.argv[3]), int(sys.argv[4])
    exp_w = int(sys.argv[5]) if len(sys.argv) > 5 else None
    exp_h = int(sys.argv[6]) if len(sys.argv) > 6 else None

    w, h, bg = load(bgp)
    w2, h2, fg = load(fgp)
    if (w, h) != (w2, h2): raise SystemExit(f'两帧尺寸不同: {w}x{h} vs {w2}x{h2}')
    png('/tmp/cmp_bg.png', w, h, bg)
    png('/tmp/cmp_fg.png', w, h, fg)

    diff = []; changed = 0; x1 = y1 = 10**9; x2 = y2 = -1
    for y in range(h):
        r = []
        for x in range(w):
            a, b = bg[y][x], fg[y][x]
            if abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) > 6:
                changed += 1
                x1 = min(x1, x); y1 = min(y1, y); x2 = max(x2, x); y2 = max(y2, y)
                r.append((255, 255, 255))
            else:
                r.append((0, 0, 0))
        diff.append(r)
    png('/tmp/cmp_diff.png', w, h, diff)

    if x2 < 0:
        print('两帧完全相同 —— overlay 没显示?'); return 2
    gw, gh = x2 - x1 + 1, y2 - y1 + 1
    print(f'变化像素 {changed}')
    print(f'屏幕坐标: x {ox+x1}..{ox+x2}   y {oy+y1}..{oy+y2}')
    print(f'实际占位: {gw} x {gh}')
    if exp_w is not None:
        print(f'期望占位: {exp_w} x {exp_h}   宽 {"OK" if gw==exp_w else f"差 {gw-exp_w}"}'
              f' / 高 {"OK" if gh==exp_h else f"差 {gh-exp_h}"}')
    print('已写 /tmp/cmp_bg.png /tmp/cmp_fg.png /tmp/cmp_diff.png')
    return 0

if __name__ == '__main__':
    sys.exit(main())
