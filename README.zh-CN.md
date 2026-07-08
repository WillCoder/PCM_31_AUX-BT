# PCM_31_AUX-BT

**Porsche PCM3.1 蓝牙音频修复**

> Porsche PCM3.1(CHN/MOPF 台架件)· QNX 6.3.x · SH-4A · 2026-07
>
> [English](README.md) · **简体中文**

> ⚠️ **免责声明**:仅供学习研究,**刷写有砖机风险且可能救不回来(撞看门狗无限重启,快到停不进 emergency shell),后果自负,别刷挂了怪我。** 全文见 [DISCLAIMER.md](DISCLAIMER.md) · 授权 [MIT LICENSE](LICENSE)

![工作台架](images/01-bench-working-fm.jpg)

> **代价**:之前**报废两台台架、救不回来**——撞看门狗、无限重启,快到停不进 emergency shell。这套方法论是那两台换来的:startup+imagefs 两段各 sum-zero 预检门,加上认清"稳定砖能串口救、看门狗砖救不了",之后再没砖过。详见全过程文档开篇"前情"。

我这条痛点链的两章:
- **① 锁BT** — 蓝牙放着歌熄火,下次上车**每次都回到 FM**,要手动切蓝牙 → 修复
- **② 开机出声** — 锁住蓝牙后,开机连上了**却没声音**,要手动 AUX→BT → 修复

## 第一部分 · 锁BT(最初的痛点)
- **问题**:蓝牙放着歌熄火,下次上车**每次都回到 FM**,得手动切回蓝牙。
- **根因/解法**:真正的源仲裁在运行时(不在 OnOff/fallback 死代码层)。**fmguard = 18 字节 cave**(池槽 0x082ac898)守卫:要提交的源==FM 就不提交 → FM 不再自动抢占。台架 + 真车 911/9x1 双双实刷确认。

## 第二部分 · 开机出声(Issue-1)
- **问题**:锁住蓝牙后,开机手机连上了**却没声音、没歌名**,得手动 AUX→BT 才出声。
- **根因**:开机从不"申请音频焦点"→ getter 返 -2 → establishment 短路 → DSP 不路由 → 无声。只有真正的源切换才产生这次申请。
- **解法**:**child-vtable shotgun** —— 插桩 child 对象 vtable 全 5 方法,连接时被调到的方法带门(bug 态)触发一次 AUX→BT → establishment 激活 → 出声。

**成果**:两个修复合成一个干净固件(stock + 锁BT + 开机出声,失败实验全清)+ USB autorun 零串口部署,台架实刷确认。

📄 **完整全过程(两章 · 含所有死路及原因):[全过程_中文.md](全过程_中文.md)** · English journey see [journey_English.md](journey_English.md)

---

## 交付物

| | 路径 |
|---|---|
| 干净固件(cksum 4237630296) | `firmware-cache/patch-lab/chn-clean-MOPF/PCM3_IFS1_MOPF.CHN.clean.ifs` |
| USB autorun 包 | `firmware-cache/usb-builds/flash_clean_bt_fix/` |
| 解法汇编器 | `dev/build_shotgun_child.py` |

**组成** = stock + fmguard(锁BT)+ child-shotgun(开机出声)
**下一步** = 移植到真车 911/9x1
