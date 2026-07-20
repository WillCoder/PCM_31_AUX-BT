# PCM_31_AUX-BT

**Porsche PCM3.1 —— 蓝牙音频修复,以及围绕它长出来的一套改机工具箱**

> Porsche PCM3.1(CHN)· QNX 6.3.x · SH-4A · 2026-07
> **✅ 蓝牙修复已解决 —— MOPF 台架 与 真车(911/9x1)双双实刷确认。**
>
> [English](README.md) · **简体中文**

> ⚠️ **免责声明**:仅供学习研究,**刷写有砖机风险且可能救不回来(撞看门狗无限重启,快到停不进 emergency shell),后果自负,别刷挂了怪我。** 全文见 [DISCLAIMER.md](DISCLAIMER.md) · 授权 [GPL-3.0](LICENSE)

![工作台架](images/01-bench-working-fm.jpg)

> **代价**:之前**报废两台台架、救不回来**——撞看门狗、无限重启,快到停不进 emergency shell。这套方法论是那两台换来的:startup+imagefs 两段各 sum-zero 预检门,加上认清"稳定砖能串口救、看门狗砖救不了",之后再没砖过。详见全过程文档开篇"前情"。

## 这个仓库里有什么

| | 做什么 | 交付方式 | 状态 |
|---|---|---|---|
| **蓝牙修复**(锁BT + 开机出声) | 冷启动后蓝牙保持选中 —— **而且出声**,一个键都不用按 | **刷 IFS1** | ✅ 台架 + 真车(911/9x1) |
| **硬件图层 overlay 框架** | 在完全没动过的原厂 UI 之上叠加自绘弹窗(音量 OSD、提示、对话框) | runtime app,**不刷写** | ✅ 台架 |
| **台架串口工具链** | 57600 root shell 开发闭环 —— 推二进制、跑、拉日志、杀掉;不插 U 盘、不重刷 | 开发工具 | ✅ |
| **逆向 + 构建管线** | 可靠 SH4 反汇编、`sh4emu` 解释器、IFS inflate/patch/deflate、刷前预检门 | 开发工具 | ✅ |

**一个东西"怎么交付"是关于它最该先知道的事。** 刷写补丁能把机器变砖,而且是改原厂代码的唯一途径([为什么](#为什么蓝牙修复必须靠刷写));runtime app 砖不了机器,重启就没了。

---

# 蓝牙修复

我这条痛点链的两章:
- **① 锁BT** — 蓝牙放着歌熄火,下次上车**每次都回到 FM**,要手动切蓝牙 → 修复
- **② 开机出声** — 锁住蓝牙后,开机连上了**却没声音**,要手动 AUX→BT → 修复

**真车上的最终效果**:熄火、带着手机走人、回来冷启动 —— 手机自动重连,**音乐自己就回来了,还在蓝牙上,一个键都不用按。**

## 第一部分 · 锁BT(最初的痛点)
- **问题**:蓝牙放着歌熄火,下次上车**每次都回到 FM**,得手动切回蓝牙。
- **根因/解法**:真正的源仲裁在运行时(不在 OnOff/fallback 死代码层)。**fmguard = 18 字节 cave**(池槽 0x082ac898)守卫:要提交的源==FM 就不提交 → FM 不再自动抢占。台架 + 真车 911/9x1 双双实刷确认。

## 第二部分 · 开机出声(Issue-1)
- **问题**:锁住蓝牙后,开机手机连上了**却没声音、没歌名**,得手动 AUX→BT 才出声。
- **根因**:开机从不"申请音频焦点"→ getter 返 -2 → establishment 短路 → DSP 不路由 → 无声。只有真正的源切换才产生这次申请。
- **解法**:**child-vtable shotgun** —— 插桩 child 对象 vtable 全 5 方法,连接时被调到的方法带门(bug 态)触发一次 AUX→BT → establishment 激活 → 出声。

### 第二部分 b · shotgun 唯一的病,和让它真正成立的那一修

shotgun 在台架上确实出过声,但它**把 `main` 写死了**(`CPSoundPresCtrl` 单例)。`main` 是堆对象,**每次开机都漂** —— 六个 boot,六个不同地址(`0x086ec01c`、`0x086ed694`、`0x086ed01c`、`0x086ef01c`……)。写死的地址一旦落在未映射页,**第一次解引用就 fault** → 看门狗 → 救不回来的那种砖。改成扫堆找它更糟:热路径过载,直接崩。

**真正的修** —— 从那个永远有效的指针出发:真实分发的 `this`(= child 对象)。child 回到 main 有一条结构性的反向链:

```
main = *(*(*(child + 0x38) + 0x08) + 0x70)
```

每一跳在解引用**之前**先做区间检查 `[0x08600000, 0x08f00000)`,拿到结果再用 vtable 校验(`*(main) == 0x085c4c5c`)。所以对象图还没建好/正在重连的状态会**干净地 bail、下次调用再试**,而不是 fault。零扫描、零硬编码、不怕漂 —— 上界取 `0x08f00000` 而不是 `0x09000000`,是为了让"指针+偏移"永远读不出已映射窗口(这是对抗审查揪出的唯一一条 fault 路径)。

这条链是**结构性的,不是巧合**:台架 playing/disconnected/AUX、真实 `-2` 连接bug快照、修好后的工作态、以及**真车**——六个不同的 `main` 地址,全部解得对。

还有一个值得记的坑:`entertSourceChanged` 在 `main+0x944` 上分叉。**0** 走耐久的 child-dispatch 腿(手动切源走的就是这条,不会被拆);**非 0** 走脆弱的 TLAM 连接+重试腿,它的异步处理器约 5 秒后重试耗尽,调 `switchAudio(Default)` 把声音又拆掉。cave 对 `+0x944` 一个字节都不写,所以永远走耐久腿。

**成果**:stock + 锁BT + 开机出声 合成一个干净固件(失败实验全清)+ USB autorun 零串口部署 —— **台架与真车(911/9x1)双双实刷确认**。熄火、带手机走、回来冷启动、手机重连:**声音自己回来,蓝牙保持不掉,全程零手动操作。**

📄 **完整全过程(两章 · 含所有死路及原因):[全过程_中文.md](全过程_中文.md)** · English journey see [journey_English.md](journey_English.md)

## 交付的是什么

**组成** = 原厂 `PCM3Root` + fmguard(锁BT)+ child-chain cave(开机出声),交付方式是**刷 IFS1**。

| | 位置 |
|---|---|
| **解法** | [`code/build_shotgun_child_chain.py`](code/build_shotgun_child_chain.py) —— child-vtable cave,`main` 自派生 |
| 它的前身(作为参照基线保留) | [`code/build_shotgun_child.py`](code/build_shotgun_child.py) —— 同一个 cave,但 `main` 写死会漂 |
| 离线证明 | [`code/validate_shotgun_child_chain.py`](code/validate_shotgun_child_chain.py) —— 在 `sh4emu` 里对真实 `-2` 连接bug快照跑 cave:自派生 `main`、开火、且(含故意喂坏指针)绝不 fault |
| 预检门 | [`code/verify_ifs_flashable.py`](code/verify_ifs_flashable.py) |
| 可刷固件 | **不含**——改过的专有固件;用 `code/` 里的工具 + 你自己 dump 的固件自行构建 |

### 为什么蓝牙修复必须靠刷写

`/proc/<pid>/as` 只写得进 RW 页,**写只读 code/rodata 会失败** —— 这就是 CoW 墙,真机实证([journey 的 Dead end ③](全过程_中文.md))。两个 cave 都在 RX 段,所以这硬件上**做不了运行时代码注入**。串口 poke 循环是**找到**修复的手段,刷 IFS1 才是**交付**它的手段。

> 注:台架(MOPF / `IFS_G1_E2`)和真车(`IFS_9X1`)的 `PCM3Root` **二进制字节完全相同**,只有周围的 imagefs 不同。所以真车进的就是台架上验过的那一份。定位 `PCM3Root` 要走 **imagefs 目录**(`mnt/ifs1/HBproject/PCM3Root`),**别用 ELF 头去搜**——镜像里每个 SH4 可执行文件开头都一样,你一定会抽错文件。

---

# Runtime 功能

与原厂软件并行运行的独立 app。它们**从不写 flash**,所以砖不了机器,重启即消失。

## 硬件图层 overlay 框架(2026-07-20)

![音量 OSD 画在我们自己的硬件图层上, 压在原厂 Jukebox 页之上](images/06-overlay-volume-osd.jpg)

*我们自绘的音量 OSD,画在独立的 Carmine 硬件图层上,压在完全没动过的原厂 UI 之上。注意底下的原厂页面照常工作 —— 进度条在走、时钟在跳、选中的按钮仍然高亮。全程零刷写,弹窗纯运行时。*

- **目标**:在原厂 UI 之上叠加自绘弹窗(音量 OSD、提示、对话框),完全不刷 flash。
- **做法**:作为**第二个 gf 客户端**,抢一个 `layermanager` 从不分配的空闲 Carmine 硬件层。各硬件层有各自独立的 scanout 缓冲 → 物理上碰不到原厂 UI 的缓冲和锁,老方案"共享原厂 surface"导致的残影/撕裂在硬件层面消失。
- **状态**:台架实证通过 —— 全彩、抗锯齿字体、真圆角透明、音量条跟着旋钮实时走、松手 1.4 秒自动收起。
- **三个各值一天的坑**:驱动把**层号反转**(`硬件层 = 7 − gf层`,所以要用 gf5);像素格式是 **RGBA5551 而不是 API 报的 ARGB1555**;以及**每次 `gf_layer_update` 前必须完整重申**,否则永久死锁在 `gdcServerCarmine`。
- 代码:[`code/overlay/`](code/overlay/) · 详细文档:[`HW_overlay_framework.zh-CN.md`](HW_overlay_framework.zh-CN.md)([English](HW_overlay_framework.md))

---

# 工具链

## 台架串口工具链

57600 root shell 开发闭环 —— 推二进制、跑、拉日志、杀掉,**不用插 U 盘、不用重刷**。只改布局的话推 348 字节就行,不必传 66 KB 二进制。

- [`code/serial/`](code/serial/) —— `ser_push.py`(分块上传 + cksum 校验)、`ser_pull.py`、`ser2.py`(执行命令)、`ser_kill.py`。
- [`code/sh4tools/SERIAL_LOOP.md`](code/sh4tools/SERIAL_LOOP.md) —— 与之配套的 `/proc/<pid>/as` poke 循环,以及**它够得着什么、够不着什么**。

## 逆向 + 构建

- [`code/`](code/) —— cave 汇编器、`sh4emu.py`(在内存快照上真执行 `PCM3Root` 函数的 SH4 解释器)、IFS inflate/patch/deflate 管线,以及 `verify_ifs_flashable.py`(两台报废台架换来的刷前门)。
- 完整索引见 [`code/README.md`](code/README.md)。

---

## 仓库结构

```
README.md / README.zh-CN.md         本索引
journey_English.md / 全过程_中文.md   蓝牙修复完整故事 + 所有死路
HW_overlay_framework.md (+.zh-CN)   overlay 框架详文
code/
  build_*.py, sh4emu.py, ...        蓝牙修复:汇编器、模拟器、IFS 管线
  overlay/                          硬件图层 overlay 框架
  serial/                           台架串口工具链
  sh4tools/                         设备端 C 工具(mempoke)+ SERIAL_LOOP.md
  autorun/                          USB autorun 刷写脚本
images/                             台架照片与截图
```

## 加新 feature 的约定

这套结构能吸收新东西,**不需要重新编号**:

1. **代码** → 自己的 `code/<feature>/` 目录(像 `overlay/`、`serial/` 那样),里面放一个短 `README.md`,写清那些不写下来就得让下一个人重新踩的坑。
2. **文档** → 仓库根目录 `<Feature>.md` + `<Feature>.zh-CN.md`。**英文是主文档,中文是译本** —— 和本 README 同一约定。
3. **索引** → 在*这个仓库里有什么*加一行,并在 **Runtime 功能**下加一个 `##` 小节(如果它是靠刷写交付的,就像**蓝牙修复**那样单开一个顶级小节)。
4. **写明怎么交付** —— runtime app 还是刷写补丁。这一条决定风险画像,是任何读者最先需要知道的。
5. **把死路留下。** "我试过什么、为什么不行"是这些文档里最值钱的部分;一个没有死路记录的 feature,会让下一个人再付一遍你付过的那些天。

---

## 致谢

特别感谢以下参考——是它们让整个开发有了好的基础和思路:

- **[dspl1236/PCM-Forge](https://github.com/dspl1236/PCM-Forge)** —— PCM-Forge 项目及其逆向工程的奠基工作。
- **[Rennlist —— "PCM3.1 reboot problem, repair attempt"](https://rennlist.com/forums/991/1484228-pcm3-1-reboot-problem-repair-attempt.html)** —— PCM3.1 重启/维修的社区讨论帖。

而这个问题最终,是与 **Claude(Anthropic)** 的协作一点点磨出来的——逆向、工具(可靠 SH4 反汇编、`sh4emu`、shotgun 打法)、直到最后的修复,都是这一来一回配合的结果,再上台架实测。缺了哪一半,都到不了这儿。

---

## 授权

版权 © 2026 WillCoder

本项目是自由软件:你可以在 **GNU General Public License v3.0**(或你选择的任意更新版本)条款下重新分发和/或修改它——见 [LICENSE](LICENSE)。发布本项目是希望它有用,但**不作任何担保**(另见 [DISCLAIMER.md](DISCLAIMER.md))。
