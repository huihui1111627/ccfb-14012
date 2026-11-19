"""Acceptance run: pairwise device-capability matrix through the gateway.

Simulates downstream modalities and upstream platforms with different
transfer-syntax capabilities, pushes a mixed study through the gateway for
every pair, and records which transfer syntax each leg negotiated and
whether pixels and tags survived transcoding bit-exactly.  Also exercises
C-FIND, C-MOVE (with pending responses), storage commitment (N-ACTION +
N-EVENT-REPORT on a separate association) and abort handling.

Run:  python -m dicom_gw.acceptance
"""

import socket
import threading
import time

from . import dataset as D
from . import dimse
from . import imggen
from . import pdu as P
from . import uid as U
from .association import Association
from .gateway import DeviceProfile, Gateway, Monitor, scu_find, scu_move, \
    scu_store, scu_commit, transcode_dataset
from .pixel import Params, decode_frames

PIXEL_TAG = D.tag(0x7FE0, 0x0010)

# ---------------------------------------------------------------- profiles

DOWNSTREAM_PROFILES = [
    DeviceProfile("CT-1998 (仅隐式LE)", "CT1998",
                  [U.ImplicitVRLittleEndian], max_pdu=16384),
    DeviceProfile("CT-2005 (隐式+显式LE)", "CT2005",
                  [U.ImplicitVRLittleEndian, U.ExplicitVRLittleEndian]),
    DeviceProfile("MR-老机 (仅显式LE)", "MROLD",
                  [U.ExplicitVRLittleEndian], max_pdu=8192),
    DeviceProfile("MR-RLE (RLE优先)", "MRRLE",
                  [U.RLELossless, U.ImplicitVRLittleEndian]),
    DeviceProfile("CT-JPEG (无损JPEG优先)", "CTJPEG",
                  [U.JPEGLosslessSV1, U.ExplicitVRLittleEndian]),
    DeviceProfile("US-混合 (显式LE+RLE)", "USMIX",
                  [U.ExplicitVRLittleEndian, U.RLELossless],
                  max_pdu=4096, async_ops=(4, 2)),
    DeviceProfile("DR-大端 (显式BE+隐式)", "DRBE",
                  [U.ExplicitVRBigEndian, U.ImplicitVRLittleEndian]),
    DeviceProfile("CR-仅RLE", "CRRLE", [U.RLELossless]),
]

UPSTREAM_PROFILES = [
    DeviceProfile("平台A (仅显式LE)", "PACSA", [U.ExplicitVRLittleEndian]),
    DeviceProfile("平台B (显式LE+无损JPEG)", "PACSB",
                  [U.ExplicitVRLittleEndian, U.JPEGLosslessSV1]),
    DeviceProfile("平台C (仅RLE)", "PACSC", [U.RLELossless]),
    DeviceProfile("平台D (隐式+显式LE+BE)", "PACSD",
                  [U.ImplicitVRLittleEndian, U.ExplicitVRLittleEndian,
                   U.ExplicitVRBigEndian]),
]

# the gateway itself accepts everything on its downstream side
GATEWAY_SCP_CONTEXTS = DeviceProfile(
    "gw", "GATEWAY", list(U.ALL_TRANSFER_SYNTAXES)).scp_contexts()


# ---------------------------------------------------------------- comparison

def _canon_scalar(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("latin-1").rstrip(" \x00")
    if isinstance(v, str):
        return v.rstrip(" \x00")
    return str(v)


def _canon(value):
    """Canonical comparable form: numbers and their string renderings
    compare equal, multi-values compare element-wise, padding is ignored."""
    if isinstance(value, (list, tuple)):
        return tuple(_canon_scalar(v) for v in value)
    return (_canon_scalar(value),)


def compare_datasets(a, b, path=""):
    """Recursively compare two data sets (pixel data excluded).  Returns a
    list of human-readable differences; empty means identical."""
    diffs = []
    for t in sorted(set(a.tags()) | set(b.tags())):
        if t == PIXEL_TAG:
            continue
        where = path + D.tag_str(t)
        ea, eb = a.elements.get(t), b.elements.get(t)
        if ea is None:
            diffs.append("%s: missing in received" % where)
            continue
        if eb is None:
            diffs.append("%s: extra in received" % where)
            continue
        va, vb = ea.value, eb.value
        a_sq = isinstance(va, (list, tuple)) and va and isinstance(va[0], D.Dataset)
        b_sq = isinstance(vb, (list, tuple)) and vb and isinstance(vb[0], D.Dataset)
        a_raw = isinstance(va, D.RawUNItems)
        b_raw = isinstance(vb, D.RawUNItems)
        if a_sq and b_sq:
            if len(va) != len(vb):
                diffs.append("%s: SQ item count %d != %d"
                             % (where, len(va), len(vb)))
            else:
                for i, (ia, ib) in enumerate(zip(va, vb)):
                    diffs += compare_datasets(ia, ib, "%s/item%d/" % (where, i))
        elif a_raw and b_raw:
            if va.items != vb.items:
                diffs.append("%s: raw UN items differ" % where)
        elif (a_sq and b_raw) or (a_raw and b_sq):
            # a private sequence crossed implicit VR: compare the raw item
            # bytes against the implicit-VR encoding of the known items
            sq_items = va if a_sq else vb
            raw_items = (vb if a_sq else va).items
            enc = [D.write_dataset(it, U.ImplicitVRLittleEndian)
                   for it in sq_items]
            if [bytes(r) for r in raw_items] != enc:
                diffs.append("%s: private sequence content changed" % where)
        elif _canon(va) != _canon(vb):
            diffs.append("%s: %r != %r" % (where, va, vb))
    return diffs


def frames_of(ds, ts):
    params = Params.from_dataset(ds)
    return decode_frames(ds[PIXEL_TAG].value, params, ts)


# ---------------------------------------------------------------- matrix cell

def run_combo(down, up, study, monitor):
    """Push one study from `down` through the gateway to `up`."""
    upstream = Gateway(ae_title=up.ae_title, port=0,
                       scp_contexts=up.scp_contexts(), monitor=monitor)
    upstream.start()
    gw = Gateway(ae_title="GATEWAY", port=0,
                 scp_contexts=GATEWAY_SCP_CONTEXTS, monitor=monitor,
                 upstream=dict(host="127.0.0.1", port=upstream.port,
                               called_ae=up.ae_title,
                               contexts=up.store_offer()))
    gw.start()
    cell = {"down": down.name, "up": up.name, "images": []}
    try:
        assoc = Association.request(
            "127.0.0.1", gw.port, down.ae_title, "GATEWAY",
            down.store_offer(), max_pdu=down.max_pdu,
            async_ops=down.async_ops)
        cell["async_ops"] = (assoc.async_ops_invoked,
                             assoc.async_ops_performed)
        cell["peer_max_pdu"] = assoc.peer_max_pdu
        for ds, truth_frames in study:
            sop_class = ds.get(0x0008, 0x0016)
            pc = assoc.context_for(sop_class)
            down_ts = pc.transfer_syntax
            wire = transcode_dataset(ds, U.ExplicitVRLittleEndian, down_ts) \
                if down_ts not in (U.ExplicitVRLittleEndian,) else ds
            status = scu_store(assoc, wire, down_ts)
            sop_inst = ds.get(0x0008, 0x0018)
            got = upstream.store.get(sop_inst)
            img = {"sop": U.sop_name(sop_class), "status": status,
                   "down_ts": down_ts}
            if got is None:
                img["error"] = "not forwarded"
            else:
                up_ds, up_ts = got
                img["up_ts"] = up_ts
                img["pixel_ok"] = frames_of(up_ds, up_ts) == [
                    bytes(f) for f in truth_frames]
                img["tag_diffs"] = compare_datasets(ds, up_ds)
                img["tag_ok"] = not img["tag_diffs"]
            cell["images"].append(img)
        assoc.release()
        cell["down_ts"] = cell["images"][0]["down_ts"] if cell["images"] else None
        up_tss = {i.get("up_ts") for i in cell["images"]}
        cell["up_ts"] = up_tss.pop() if len(up_tss) == 1 else up_tss
        cell["ok"] = all(i.get("pixel_ok") and i.get("tag_ok")
                         and i["status"] == 0 for i in cell["images"])
    finally:
        gw.stop()
        upstream.stop()
    return cell


# ---------------------------------------------------------------- service tests

def test_find_move_commit(monitor):
    """C-FIND, C-MOVE (pending responses) and storage commitment."""
    results = {}
    sink = Gateway(ae_title="WORKSTATION", port=0,
                   scp_contexts=GATEWAY_SCP_CONTEXTS, monitor=monitor)
    sink.start()
    listener = Gateway(ae_title="CT1998", port=0,
                       scp_contexts=GATEWAY_SCP_CONTEXTS, monitor=monitor)
    listener.start()
    commit_events = []
    listener.on_n_event_report = lambda cmd, info, ts: \
        commit_events.append((cmd, info))

    gw = Gateway(
        ae_title="GATEWAY", port=0, scp_contexts=GATEWAY_SCP_CONTEXTS,
        monitor=monitor,
        move_destinations={"WORKSTATION": dict(
            host="127.0.0.1", port=sink.port,
            contexts=DeviceProfile("sink", "WORKSTATION",
                                   list(U.ALL_TRANSFER_SYNTAXES)).store_offer())},
        commitment_targets={"CT1998": dict(
            host="127.0.0.1", port=listener.port,
            contexts=[(U.StorageCommitmentPushModel,
                       [U.ImplicitVRLittleEndian, U.ExplicitVRLittleEndian])])})
    gw.start()
    try:
        study = imggen.make_study()
        study_uid = study[0][0].get(0x0020, 0x000D)
        profile = DOWNSTREAM_PROFILES[0]
        contexts = profile.store_offer() + [
            (U.StudyRootFind, [U.ImplicitVRLittleEndian]),
            (U.StudyRootMove, [U.ImplicitVRLittleEndian]),
            (U.StorageCommitmentPushModel, [U.ImplicitVRLittleEndian]),
        ]
        assoc = Association.request("127.0.0.1", gw.port, "CT1998", "GATEWAY",
                                    contexts)
        truth = {}
        for ds, frames in study:
            pc = assoc.context_for(ds.get(0x0008, 0x0016))
            scu_store(assoc, ds, pc.transfer_syntax)
            truth[ds.get(0x0008, 0x0018)] = (ds, frames)

        # ---- C-FIND
        q = D.Dataset()
        q.set(0x0008, 0x0052, "CS", "STUDY")
        q.set(0x0020, 0x000D, "UI", study_uid)
        q.set(0x0010, 0x0010, "PN", "")
        q.set(0x0010, 0x0020, "LO", "")
        status, found = scu_find(assoc, q)
        results["find"] = {
            "status": status, "matches": len(found),
            "ok": status == 0 and len(found) == len(study)
            and all(f.get(0x0010, 0x0010) for f in found)}

        # ---- C-MOVE: gateway opens a second association to WORKSTATION
        mq = D.Dataset()
        mq.set(0x0008, 0x0052, "CS", "STUDY")
        mq.set(0x0020, 0x000D, "UI", study_uid)
        final, pendings = scu_move(assoc, mq, "WORKSTATION")
        moved_ok = len(sink.store) == len(study)
        pixel_ok = True
        for sop_inst, (ds, frames) in truth.items():
            got = sink.store.get(sop_inst)
            if got is None or frames_of(got[0], got[1]) != \
                    [bytes(f) for f in frames]:
                pixel_ok = False
        results["move"] = {
            "final_status": final.get(0x0000, 0x0900),
            "completed": final.get(0x0000, 0x1021),
            "pending_responses": pendings,
            "ok": final.get(0x0000, 0x0900) == 0
            and final.get(0x0000, 0x1021) == len(study)
            and pendings >= len(study) and moved_ok and pixel_ok}

        # ---- storage commitment, all instances valid
        refs = [(ds.get(0x0008, 0x0016), ds.get(0x0008, 0x0018))
                for ds, _ in study]
        tx1 = imggen.new_uid()
        st = scu_commit(assoc, tx1, refs)
        ok1 = _wait_for(lambda: len(commit_events) >= 1, 5)
        ev_ok = False
        if ok1:
            cmd, info = commit_events[0]
            ev_ok = (cmd.get(0x0000, 0x1002) == 1
                     and info.get(0x0008, 0x1195) == tx1
                     and len(info.get(0x0008, 0x1199, [])) == len(study))
        results["commit_success"] = {"n_action_status": st,
                                     "event_received": ok1,
                                     "event_ok": ev_ok,
                                     "ok": st == 0 and ok1 and ev_ok}

        # ---- storage commitment with one unknown instance -> failure event
        tx2 = imggen.new_uid()
        refs_bad = refs + [(U.CTImageStorage, "9.9.9.999")]
        st2 = scu_commit(assoc, tx2, refs_bad)
        ok2 = _wait_for(lambda: len(commit_events) >= 2, 5)
        ev2_ok = False
        if ok2:
            cmd, info = commit_events[1]
            failed = info.get(0x0008, 0x1198, [])
            ev2_ok = (cmd.get(0x0000, 0x1002) == 2
                      and info.get(0x0008, 0x1195) == tx2
                      and len(failed) == 1
                      and failed[0].get(0x0008, 0x1155) == "9.9.9.999")
        results["commit_partial_failure"] = {"n_action_status": st2,
                                             "event_received": ok2,
                                             "event_ok": ev2_ok,
                                             "ok": st2 == 0 and ok2 and ev2_ok}
        assoc.release()
    finally:
        gw.stop()
        sink.stop()
        listener.stop()
    return results


def _wait_for(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- abort tests

def test_aborts(monitor):
    results = {}
    gw = Gateway(ae_title="GATEWAY", port=0,
                 scp_contexts=GATEWAY_SCP_CONTEXTS, monitor=monitor)
    gw.start()
    try:
        # 1. PDV referencing a presentation context that was never negotiated
        assoc = Association.request(
            "127.0.0.1", gw.port, "CT1998", "GATEWAY",
            DOWNSTREAM_PROFILES[0].store_offer())
        assoc.sock.sendall(P.encode_pdata([(99, True, True, b"\x00" * 8)]))
        pdu_type, body = P.read_pdu(assoc._recv_exact)
        abort = P.decode_abort(body) if pdu_type == P.A_ABORT else None
        results["unnegotiated_pcid"] = {
            "got_abort": pdu_type == P.A_ABORT,
            "source": abort.source if abort else None,
            "reason": abort.reason if abort else None,
            "ok": pdu_type == P.A_ABORT and abort.source == P.ABORT_SOURCE_PROVIDER}
        assoc.close()

        # 2. unexpected PDU in data-transfer state (A-ASSOCIATE-RQ again)
        assoc = Association.request(
            "127.0.0.1", gw.port, "CT1998", "GATEWAY",
            DOWNSTREAM_PROFILES[0].store_offer())
        rq = P.AssociateRequest()
        rq.calling_ae = "CT1998"
        rq.called_ae = "GATEWAY"
        rq.presentation_contexts.append(P.PresentationContext(
            1, U.Verification, [U.ImplicitVRLittleEndian]))
        assoc.sock.sendall(P.encode_associate(rq, P.A_ASSOCIATE_RQ))
        pdu_type, body = P.read_pdu(assoc._recv_exact)
        abort = P.decode_abort(body) if pdu_type == P.A_ABORT else None
        results["unexpected_pdu"] = {
            "got_abort": pdu_type == P.A_ABORT,
            "reason": abort.reason if abort else None,
            "ok": pdu_type == P.A_ABORT and abort.reason == 2}
        assoc.close()

        # 3. peer drops the TCP connection mid-transfer (abnormal release)
        assoc = Association.request(
            "127.0.0.1", gw.port, "CT1998", "GATEWAY",
            DOWNSTREAM_PROFILES[0].store_offer())
        pc = assoc.presentation_contexts[0]
        cmd = dimse.c_store_rq(1, U.CTImageStorage, "1.2.3.4")
        data = dimse.encode_command(cmd)
        assoc.sock.sendall(P.encode_pdata([(pc.pcid, True, False, data[:5])]))
        assoc.sock.close()                     # vanish mid-fragment-sequence
        before = len(monitor.list())
        ok = _wait_for(
            lambda: any(r.status == "aborted" for r in monitor.list()), 5)
        results["connection_dropped"] = {"detected": ok, "ok": ok}
    finally:
        gw.stop()
    return results


# ---------------------------------------------------------------- report

def fmt_ts(ts):
    return U.ts_name(ts) if ts else "-"


def run_all(verbose=True, monitor=None):
    monitor = monitor or Monitor()
    study = imggen.make_study()
    cells = []
    for down in DOWNSTREAM_PROFILES:
        for up in UPSTREAM_PROFILES:
            cell = run_combo(down, up, study, monitor)
            cells.append(cell)
            if verbose:
                print("[matrix] %s -> %s : %s" % (
                    down.name, up.name,
                    "OK" if cell["ok"] else "FAIL"))
    services = test_find_move_commit(monitor)
    aborts = test_aborts(monitor)
    report = render_report(cells, services, aborts)
    return report, cells, services, aborts, monitor


def render_report(cells, services, aborts):
    lines = []
    lines.append("# DICOM 网关验收报告")
    lines.append("")
    lines.append("## 1. 协商矩阵(下行设备 × 上级平台,经网关转发)")
    lines.append("")
    lines.append("| 下行设备 | 上级平台 | 下行协商语法 | 上行协商语法 "
                 "| 网关转码 | 像素比对 | 标签比对 |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in cells:
        down_ts = fmt_ts(c.get("down_ts"))
        up_ts = fmt_ts(c.get("up_ts"))
        transcode = "—" if c.get("down_ts") == c.get("up_ts") else "✔ 已转码"
        pixel = "✔ 逐位相等" if all(
            i.get("pixel_ok") for i in c["images"]) else "✗ 不一致"
        tags = "✔ 逐项相等" if all(i.get("tag_ok") for i in c["images"]) \
            else "✗ " + "; ".join(c["images"][0].get("tag_diffs", [])[:2])
        lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                     % (c["down"], c["up"], down_ts, up_ts, transcode,
                        pixel, tags))
    lines.append("")
    lines.append("说明: 每格发送 4 幅影像(16位有符号单帧 / 12位5帧 / "
                 "16位3帧 / 8位RGB),含私有标签与私有序列; "
                 "像素比对为解码后逐帧逐字节相等, 标签比对为除像素数据外"
                 "全部 tag(含私有 tag、序列嵌套)逐项相等。")
    lines.append("")
    lines.append("## 2. 存储 / 查询 / 取回 / 存储提交")
    lines.append("")
    f = services["find"]
    lines.append("- C-FIND: 状态 0x%04X, 返回匹配 %d 条 → %s"
                 % (f["status"], f["matches"], "通过" if f["ok"] else "失败"))
    m = services["move"]
    lines.append("- C-MOVE: 最终状态 0x%04X, 完成子操作 %d 个, "
                 "挂起(pending)应答 %d 次 → %s"
                 % (m["final_status"], m["completed"], m["pending_responses"],
                    "通过" if m["ok"] else "失败"))
    c1 = services["commit_success"]
    lines.append("- 存储提交(全部有效): N-ACTION 状态 0x%04X, "
                 "另起关联收到 N-EVENT-REPORT(成功) → %s"
                 % (c1["n_action_status"], "通过" if c1["ok"] else "失败"))
    c2 = services["commit_partial_failure"]
    lines.append("- 存储提交(含1个不存在实例): N-ACTION 状态 0x%04X, "
                 "另起关联收到 N-EVENT-REPORT(含失败列表) → %s"
                 % (c2["n_action_status"], "通过" if c2["ok"] else "失败"))
    lines.append("")
    lines.append("## 3. 异常处理")
    lines.append("")
    a1 = aborts["unnegotiated_pcid"]
    lines.append("- 未协商的表示上下文编号: 网关回 A-ABORT"
                 "(source=%s, reason=%s) → %s"
                 % (a1["source"], a1["reason"], "通过" if a1["ok"] else "失败"))
    a2 = aborts["unexpected_pdu"]
    lines.append("- 数据传输期收到意外 PDU: 网关回 A-ABORT(reason=%s) → %s"
                 % (a2["reason"], "通过" if a2["ok"] else "失败"))
    a3 = aborts["connection_dropped"]
    lines.append("- 对端异常断开 TCP: 网关检测并记录 aborted → %s"
                 % ("通过" if a3["ok"] else "失败"))
    lines.append("")
    return "\n".join(lines)


def main():
    report, cells, services, aborts, monitor = run_all()
    print()
    print(report)
    with open("acceptance_report.md", "w") as fp:
        fp.write(report)
    n_ok = sum(1 for c in cells if c["ok"])
    print("\n矩阵: %d/%d 组合通过; 服务: %s; 异常: %s"
          % (n_ok, len(cells),
             all(v["ok"] for v in services.values()) and "全部通过" or "有失败",
             all(v["ok"] for v in aborts.values()) and "全部通过" or "有失败"))
    print("报告已写入 acceptance_report.md")


if __name__ == "__main__":
    main()
