#!/usr/bin/env python3
"""push_uidef.py — 把本地 ui.def 推到台架 /tmp/ui.def(引擎 1 秒内热加载)。

台架没有 scp/nc; ser_push 的分块二进制协议对几百字节文本太重, 直接拼 echo 最省事。
⚠ 坑: 串口一条命令太长会被**静默截断**(实测一次性 31 行 -> 尾巴 3 行丢了,
   引擎读到的是残缺 def)。所以按 ~450 字符分块, 每块一条命令, 推完回读校验行数。
"""
import subprocess, sys

def ser(cmd):
    r = subprocess.run(['python3', 'scratchpad/ser2.py', cmd], capture_output=True, text=True)
    return (r.stdout or '') + (r.stderr or '')

src = sys.argv[1] if len(sys.argv) > 1 else 'coexist-app/mvp/ui.def'
lines = [l.rstrip('\n') for l in open(src) if l.strip() and not l.lstrip().startswith('#')]

chunks, cur, first = [], [], True
for l in lines:
    cur.append(l)
    if sum(len(x) + 22 for x in cur) > 450:
        chunks.append(cur); cur = []
if cur: chunks.append(cur)

for ci, ch in enumerate(chunks):
    op = '>' if ci == 0 else '>>'
    cmd = '; '.join('echo "%s" %s /tmp/ui.def' % (l, '>' if (ci == 0 and i == 0) else '>>')
                    for i, l in enumerate(ch))
    ser(cmd)

out = ser('grep -c "=" /tmp/ui.def')
print(f'推了 {len(lines)} 行 / {len(chunks)} 块; 台架回读 "=" 行数:', out.strip().splitlines()[-2:])
