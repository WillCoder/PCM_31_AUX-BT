# PCM_31_AUX-BT

**Porsche PCM3.1 蓝牙音频修复 / PCM3.1 Bluetooth Audio Fix**

> Porsche PCM3.1(CHN/MOPF 台架件)· QNX 6.3.x · SH-4A · 2026-07

> ⚠️ **免责 / Disclaimer**:仅供学习研究,**刷写有砖机风险且可能救不回来(撞看门狗无限重启),后果自负,别刷挂了怪我。** For study/research only; flashing can brick your device beyond recovery — **use at your own risk, don't blame me.** 全文见 [DISCLAIMER.md](DISCLAIMER.md) · 授权 [MIT LICENSE](LICENSE)

我这条痛点链的两章 / Two chapters of my chain of pain:
- **① 锁BT / lock-BT** — 熄火后每次上车都回到 FM,要手动切蓝牙 → 修复 / reverts to FM every trip, must manually re-select BT → fixed
- **② 开机出声 / boot-sound** — 蓝牙保持了却没声音,要手动 AUX→BT → 修复 / BT held but silent, must manually AUX→BT → fixed

![工作台架 / working bench](images/01-bench-working-fm.jpg)

> **代价 / The cost**:之前**报废两台台架、救不回来**——撞看门狗、无限重启,快到停不进 emergency shell。这套方法论是那两台换来的:startup+imagefs 两段各 sum-zero 预检门,加上认清"稳定砖能串口救、看门狗砖救不了",之后再没砖过。详见全过程文档开篇"前情"。
> *Two bench units were **bricked and unrecoverable** earlier — a watchdog endless-reboot too fast to ever hold the emergency shell. That paid for the methodology: the two-segment sum-zero preflight, plus knowing a "stable" brick is serial-recoverable but a "watchdog" brick is not. Nothing has bricked since. See the "Prologue" in the journey docs.*

## 中文

**第一部分 · 锁BT(最初的痛点)**
- 问题:蓝牙放着歌熄火,下次上车**每次都回到 FM**,得手动切回蓝牙。
- 根因/解法:真正的源仲裁在运行时(不在 OnOff/fallback 死代码层)。**fmguard = 18 字节 cave**(池槽 0x082ac898)守卫:要提交的源==FM 就不提交 → FM 不再自动抢占。台架 + 真车 911/9x1 双双实刷确认。

**第二部分 · 开机出声(Issue-1)**
- 问题:锁住蓝牙后,开机手机连上了**却没声音、没歌名**,得手动 AUX→BT 才出声。
- 根因:开机从不"申请音频焦点"→ getter 返 -2 → establishment 短路 → DSP 不路由 → 无声。只有真正的源切换才产生这次申请。
- 解法:**child-vtable shotgun** —— 插桩 child 对象 vtable 全 5 方法,连接时被调到的方法带门(bug 态)触发一次 AUX→BT → establishment 激活 → 出声。

**成果**:两个修复合成一个干净固件(stock + 锁BT + 开机出声,失败实验全清)+ USB autorun 零串口部署,台架实刷确认。

📄 **完整全过程(两章 · 含所有死路及原因):[全过程_中文.md](全过程_中文.md)**

## English

**Part 1 · lock-BT (the original pain)**
- Problem: playing Bluetooth, turn the car off; next trip it **always reverts to FM**, must manually re-select Bluetooth.
- Root cause / fix: the real source arbitration is at runtime (not the OnOff/fallback dead-code layer). **fmguard = an 18-byte cave** (pool slot 0x082ac898) guarding: if the source being submitted is FM, don't submit → FM no longer auto-seizes. Confirmed by flashing on both the bench and the real car 911/9x1.

**Part 2 · boot-sound (Issue-1)**
- Problem: with BT locked, the phone connects at boot **but there is no sound or track name**; I must manually AUX→BT.
- Root cause: at boot, audio focus is never requested → the getter returns -2 → the establishment short-circuits → the DSP is not routed → silence. Only a genuine source change produces that request.
- Solution: **child-vtable shotgun** — instrument all 5 methods of the child object's vtable; whichever the connect event calls triggers, in the bug state, one AUX→BT → the establishment activates → sound.

**Outcome**: the two fixes combined into one clean firmware (stock + lock-BT + boot-sound, all failed experiments removed) + zero-serial USB autorun, confirmed by flashing on the bench.

📄 **Full journey (two chapters, every dead end and why): [journey_English.md](journey_English.md)**

---

## 交付物 / Deliverables

| | 路径 / Path |
|---|---|
| 干净固件 / Clean firmware (cksum 4237630296) | `firmware-cache/patch-lab/chn-clean-MOPF/PCM3_IFS1_MOPF.CHN.clean.ifs` |
| USB autorun 包 / bundle | `firmware-cache/usb-builds/flash_clean_bt_fix/` |
| 解法汇编器 / Solution assembler | `dev/build_shotgun_child.py` |

**组成 / Composition** = stock + fmguard(锁BT / lock-BT)+ child-shotgun(开机出声 / boot-sound)
**下一步 / Next** = 移植到真车 911/9x1 / port to the real car 911/9x1
