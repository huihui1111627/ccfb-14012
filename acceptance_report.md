# DICOM 网关验收报告

## 1. 协商矩阵(下行设备 × 上级平台,经网关转发)

| 下行设备 | 上级平台 | 下行协商语法 | 上行协商语法 | 网关转码 | 像素比对 | 标签比对 |
|---|---|---|---|---|---|---|
| CT-1998 (仅隐式LE) | 平台A (仅显式LE) | Implicit VR Little Endian | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-1998 (仅隐式LE) | 平台B (显式LE+无损JPEG) | Implicit VR Little Endian | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-1998 (仅隐式LE) | 平台C (仅RLE) | Implicit VR Little Endian | RLE Lossless | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-1998 (仅隐式LE) | 平台D (隐式+显式LE+BE) | Implicit VR Little Endian | Implicit VR Little Endian | — | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-2005 (隐式+显式LE) | 平台A (仅显式LE) | Implicit VR Little Endian | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-2005 (隐式+显式LE) | 平台B (显式LE+无损JPEG) | Implicit VR Little Endian | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-2005 (隐式+显式LE) | 平台C (仅RLE) | Implicit VR Little Endian | RLE Lossless | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-2005 (隐式+显式LE) | 平台D (隐式+显式LE+BE) | Implicit VR Little Endian | Implicit VR Little Endian | — | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-老机 (仅显式LE) | 平台A (仅显式LE) | Explicit VR Little Endian | Explicit VR Little Endian | — | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-老机 (仅显式LE) | 平台B (显式LE+无损JPEG) | Explicit VR Little Endian | Explicit VR Little Endian | — | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-老机 (仅显式LE) | 平台C (仅RLE) | Explicit VR Little Endian | RLE Lossless | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-老机 (仅显式LE) | 平台D (隐式+显式LE+BE) | Explicit VR Little Endian | Implicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-RLE (RLE优先) | 平台A (仅显式LE) | RLE Lossless | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-RLE (RLE优先) | 平台B (显式LE+无损JPEG) | RLE Lossless | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-RLE (RLE优先) | 平台C (仅RLE) | RLE Lossless | RLE Lossless | — | ✔ 逐位相等 | ✔ 逐项相等 |
| MR-RLE (RLE优先) | 平台D (隐式+显式LE+BE) | RLE Lossless | Implicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-JPEG (无损JPEG优先) | 平台A (仅显式LE) | JPEG Lossless (SV1) | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-JPEG (无损JPEG优先) | 平台B (显式LE+无损JPEG) | JPEG Lossless (SV1) | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-JPEG (无损JPEG优先) | 平台C (仅RLE) | JPEG Lossless (SV1) | RLE Lossless | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CT-JPEG (无损JPEG优先) | 平台D (隐式+显式LE+BE) | JPEG Lossless (SV1) | Implicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| US-混合 (显式LE+RLE) | 平台A (仅显式LE) | Explicit VR Little Endian | Explicit VR Little Endian | — | ✔ 逐位相等 | ✔ 逐项相等 |
| US-混合 (显式LE+RLE) | 平台B (显式LE+无损JPEG) | Explicit VR Little Endian | Explicit VR Little Endian | — | ✔ 逐位相等 | ✔ 逐项相等 |
| US-混合 (显式LE+RLE) | 平台C (仅RLE) | Explicit VR Little Endian | RLE Lossless | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| US-混合 (显式LE+RLE) | 平台D (隐式+显式LE+BE) | Explicit VR Little Endian | Implicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| DR-大端 (显式BE+隐式) | 平台A (仅显式LE) | Explicit VR Big Endian | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| DR-大端 (显式BE+隐式) | 平台B (显式LE+无损JPEG) | Explicit VR Big Endian | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| DR-大端 (显式BE+隐式) | 平台C (仅RLE) | Explicit VR Big Endian | RLE Lossless | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| DR-大端 (显式BE+隐式) | 平台D (隐式+显式LE+BE) | Explicit VR Big Endian | Implicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CR-仅RLE | 平台A (仅显式LE) | RLE Lossless | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CR-仅RLE | 平台B (显式LE+无损JPEG) | RLE Lossless | Explicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |
| CR-仅RLE | 平台C (仅RLE) | RLE Lossless | RLE Lossless | — | ✔ 逐位相等 | ✔ 逐项相等 |
| CR-仅RLE | 平台D (隐式+显式LE+BE) | RLE Lossless | Implicit VR Little Endian | ✔ 已转码 | ✔ 逐位相等 | ✔ 逐项相等 |

说明: 每格发送 4 幅影像(16位有符号单帧 / 12位5帧 / 16位3帧 / 8位RGB),含私有标签与私有序列; 像素比对为解码后逐帧逐字节相等, 标签比对为除像素数据外全部 tag(含私有 tag、序列嵌套)逐项相等。

## 2. 存储 / 查询 / 取回 / 存储提交

- C-FIND: 状态 0x0000, 返回匹配 4 条 → 通过
- C-MOVE: 最终状态 0x0000, 完成子操作 4 个, 挂起(pending)应答 4 次 → 通过
- 存储提交(全部有效): N-ACTION 状态 0x0000, 另起关联收到 N-EVENT-REPORT(成功) → 通过
- 存储提交(含1个不存在实例): N-ACTION 状态 0x0000, 另起关联收到 N-EVENT-REPORT(含失败列表) → 通过

## 3. 异常处理

- 未协商的表示上下文编号: 网关回 A-ABORT(source=2, reason=6) → 通过
- 数据传输期收到意外 PDU: 网关回 A-ABORT(reason=2) → 通过
- 对端异常断开 TCP: 网关检测并记录 aborted → 通过
