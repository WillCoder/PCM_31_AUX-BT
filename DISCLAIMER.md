# 免责声明 / Disclaimer

## 中文

**本项目仅供个人学习、研究与技术交流。使用者自负全部风险。**

- **刷写固件有砖机风险,而且可能无法恢复。** 本项目记录的 PCM3.1 尤其如此:一旦刷坏撞上看门狗(watchdog),机器会**无限重启**,复位快到连串口 emergency shell 都停不进去,**彻底报废、救不回来**(本项目作者已因此报废两台台架,见全过程文档开篇"前情")。
- 你对自己的设备做的任何刷写、改机、逆向、内存注入操作,**后果全部自负**。刷挂了、变砖、硬件损坏、数据丢失、车辆功能异常、行车安全隐患——**与本项目作者无关,作者概不负责。**
- 本项目**不提供任何形式的担保**(不担保适销性、适用性、正确性、无害性)。文中的地址、偏移、校验和、cave 字节仅对**特定固件版本 / 硬件变体**有效;换一台机器就可能不同,**照搬有害**。动手前务必自行核对。
- 请遵守你所在地区的法律法规,尊重相关知识产权。改动车载系统可能影响**车辆安全、保修与合规**,风险自担。
- **动手前的最低要求:核对固件版本 → 过刷前预检门(两段 sum-zero)→ 备好救砖手段(如串口)→ 留好原始固件备份。**

> **一句话:别刷挂了怪我。**

## English

**This project is for personal study, research, and technical exchange only. Use entirely at your own risk.**

- **Flashing firmware can brick your device, possibly beyond recovery.** This is especially true for the PCM3.1 documented here: a bad flash that hits the **watchdog** reboots the unit **endlessly**, resetting too fast to ever hold the serial emergency shell — **dead for good, unrecoverable** (the author has already scrapped two bench units this way; see the "Prologue" in the journey docs).
- Any flashing, modification, reverse-engineering, or memory injection you perform on your own equipment is **entirely your own responsibility**. Bricking, hardware damage, data loss, malfunctioning vehicle features, or driving-safety hazards are **not the author's concern, and the author accepts no liability whatsoever.**
- This project comes with **no warranty of any kind** (no merchantability, fitness, correctness, or safety). The addresses, offsets, checksums, and cave bytes here are valid only for a **specific firmware version / hardware variant**; another unit may differ, and **copying them blindly is harmful**. Always verify for yourself first.
- Obey the laws and regulations of your jurisdiction and respect the relevant intellectual property. Modifying an in-vehicle system may affect **vehicle safety, warranty, and compliance** — the risk is entirely yours.
- **Minimum before you start:** verify the firmware version → pass the pre-flash preflight (two-segment sum-zero) → prepare a recovery method (e.g. serial) → keep a backup of the original firmware.

> **In one line: don't blame me if you brick it. Use at your own risk.**
