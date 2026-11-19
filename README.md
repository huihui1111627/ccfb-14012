# dicom_gw — 医学影像 DICOM 网关

纯 Python 标准库实现的 DICOM 网络网关,放在县医院设备与上级平台之间:
两头都讲 DICOM(下接 CT/MR 等设备,上接影像平台),两边传输语法对不上时
由网关实时转码。无任何第三方依赖,Python 3.6+ 即可运行。

## 功能对照

| 需求 | 实现 |
|---|---|
| 关联协商 | `association.py` / `pdu.py`:逐个表示上下文接受/拒绝(抽象语法不支持=3,传输语法不支持=4),最大报文长度、异步操作窗口在 User Information 里协商 |
| 数据集编解码 | `dataset.py`:隐式/显式 VR、大端/小端;未定义长度的序列与条目靠定界符条目收尾;不认识的私有标签按 UN 原样透传(私有序列先按序列解析,失败则按原始字节透传) |
| P-DATA 分片 | `association.py`:命令与数据走不同 PDV(命令位区分),按对端最大 PDU 切分,最后一片带 last-fragment 标志 |
| 存储/查询/取回 | `gateway.py`:C-STORE(收下即转发上级)、C-FIND、C-MOVE(网关另开关联把影像推到第三方,期间按进度回 pending 应答,带子操作计数) |
| 转码 | `pixel.py`:不压缩 ↔ RLE 无损 ↔ JPEG 无损(SV1) 互转,另支持显式大端;验收逐帧逐字节比对 |
| 存储提交 | `gateway.py`:N-ACTION 受理后,另起一条关联向请求方回 N-EVENT-REPORT(成功/部分失败两种) |
| 异常处理 | 对端异常断 TCP、协商后发来未协商的上下文编号、数据传输期收到意外 PDU —— 均按规范回 A-ABORT 并记录 |

## 运行

```bash
# 验收:协商矩阵 + 服务 + 异常,生成 acceptance_report.md
python3 -m dicom_gw.acceptance

# 演示:跑一遍验收流量,然后打开监控页面 http://127.0.0.1:8080
python3 -m dicom_gw.demo

# 只起网关 + 监控页(DICOM 端口 11112,HTTP 8080)
python3 -m dicom_gw.web --dicom 11112 --http 8080
```

## 模块

- `uid.py` — 传输语法 / SOP Class UID 常量
- `dataset.py` — 数据集与 Part-10 文件编解码
- `pixel.py` — 原生 / RLE(PackBits)/ JPEG 无损(ISO 10918-1 流程14,预测器1,动态 Huffman)像素编解码
- `pdu.py` — A-ASSOCIATE、P-DATA-TF、A-RELEASE、A-ABORT、A-ASSOCIATE-RJ
- `dimse.py` — 命令集(隐式 VR 小端)与 C-/N- 消息构造
- `association.py` — 关联状态机:协商、分片/重组、中止
- `gateway.py` — SCP 服务、SCU 客户端、转发路由、实例库、监控记录
- `imggen.py` — 合成影像:8/12/16 位、有符号、单帧/多帧、RGB、私有标签、私有序列
- `acceptance.py` — 验收矩阵(8 种下行设备 × 4 种上级平台 = 32 组合)
- `web.py` — 监控页:每条关联的协商过程与传输进度
- `demo.py` — 一键演示

## 验收结果

最近一次运行:32/32 组合通过(像素逐位相等、标签逐项相等),
C-FIND / C-MOVE(4 次 pending)/ 存储提交(成功与部分失败)/ 三类异常
处理全部通过。详见 `acceptance_report.md`。
