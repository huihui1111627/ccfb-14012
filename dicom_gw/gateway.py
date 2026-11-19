"""The gateway: SCP server, SCU client, store/query/retrieve services,
storage commitment, and transfer-syntax transcoding router."""

import socket
import threading
import time
import traceback

from . import dataset as D
from . import dimse
from . import pixel
from . import pdu as P
from . import uid as U
from .association import (Association, AssociationAborted, AssociationError,
                          AssociationReleased)


# ---------------------------------------------------------------- monitoring

class AssociationRecord(object):
    _counter = [0]
    _lock = threading.Lock()

    def __init__(self, monitor, direction, peer, role):
        with AssociationRecord._lock:
            AssociationRecord._counter[0] += 1
            self.id = AssociationRecord._counter[0]
        self.monitor = monitor
        self.direction = direction            # 'in' (SCP) or 'out' (SCU)
        self.peer = peer
        self.role = role                      # e.g. 'C-STORE SCP', 'forward'
        self.calling_ae = ""
        self.called_ae = ""
        self.started = time.time()
        self.ended = None
        self.status = "open"
        self.events = []                      # (t, kind, text)
        self.progress_send = 0
        self.progress_recv = 0
        self.transfer_syntaxes = []           # negotiated, for the UI
        self.monitor._add(self)

    def event(self, kind, text, **kw):
        self.events.append((time.time(), kind, text))
        self.monitor._notify(self)

    def progress(self, direction, nbytes, total):
        if direction == "send":
            self.progress_send = nbytes
        else:
            self.progress_recv = nbytes
        self.monitor._notify(self)

    def close(self, status="closed"):
        self.ended = time.time()
        self.status = status
        self.monitor._notify(self)

    def to_dict(self):
        return {
            "id": self.id,
            "direction": self.direction,
            "peer": self.peer,
            "role": self.role,
            "calling_ae": self.calling_ae,
            "called_ae": self.called_ae,
            "started": self.started,
            "ended": self.ended,
            "status": self.status,
            "progress_send": self.progress_send,
            "progress_recv": self.progress_recv,
            "transfer_syntaxes": self.transfer_syntaxes,
            "events": [(t, k, x) for t, k, x in self.events],
        }


class Monitor(object):
    """Collects per-association records for the web UI."""

    def __init__(self, keep=200):
        self.records = []
        self.keep = keep
        self._lock = threading.Lock()
        self._subscribers = []
        self._seq = 0

    def _add(self, rec):
        with self._lock:
            self.records.append(rec)
            del self.records[:-self.keep]
        self._notify(rec)

    def _notify(self, rec):
        with self._lock:
            self._seq += 1
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put((self._seq, rec))
            except Exception:
                pass

    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def list(self):
        with self._lock:
            return list(self.records)

    def new_record(self, direction, peer, role):
        return AssociationRecord(self, direction, peer, role)


# ---------------------------------------------------------------- device profiles

class DeviceProfile(object):
    """A simulated device's capabilities: which SOP classes it offers and
    with which transfer syntaxes (in preference order)."""

    def __init__(self, name, ae_title, storage_syntaxes,
                 query_syntaxes=None, supports_find=True, supports_move=True,
                 supports_commitment=True, max_pdu=P.DEFAULT_MAX_PDU,
                 async_ops=None):
        self.name = name
        self.ae_title = ae_title
        self.storage_syntaxes = list(storage_syntaxes)
        self.query_syntaxes = list(query_syntaxes or [U.ImplicitVRLittleEndian])
        self.supports_find = supports_find
        self.supports_move = supports_move
        self.supports_commitment = supports_commitment
        self.max_pdu = max_pdu
        self.async_ops = async_ops

    def scp_contexts(self):
        """Presentation contexts this device accepts as an SCP."""
        ctx = {}
        for sop in U.STORAGE_SOP_CLASSES:
            ctx[sop] = list(self.storage_syntaxes)
        if self.supports_find:
            ctx[U.StudyRootFind] = list(self.query_syntaxes)
            ctx[U.PatientRootFind] = list(self.query_syntaxes)
        if self.supports_move:
            ctx[U.StudyRootMove] = list(self.query_syntaxes)
            ctx[U.PatientRootMove] = list(self.query_syntaxes)
        if self.supports_commitment:
            ctx[U.StorageCommitmentPushModel] = list(self.query_syntaxes)
        ctx[U.Verification] = [U.ImplicitVRLittleEndian,
                               U.ExplicitVRLittleEndian]
        return ctx

    def store_offer(self):
        """Contexts this device proposes when pushing storage."""
        return [(sop, list(self.storage_syntaxes))
                for sop in U.STORAGE_SOP_CLASSES]


# ---------------------------------------------------------------- instance store

class InstanceStore(object):
    def __init__(self):
        self._lock = threading.Lock()
        self.instances = {}                   # sop_instance_uid -> (Dataset, ts)

    def put(self, ds, transfer_syntax):
        uid_ = ds.get(0x0008, 0x0018)
        with self._lock:
            self.instances[uid_] = (ds, transfer_syntax)

    def get(self, sop_instance_uid):
        with self._lock:
            return self.instances.get(sop_instance_uid)

    def all(self):
        with self._lock:
            return list(self.instances.values())

    def contains(self, sop_instance_uid):
        with self._lock:
            return sop_instance_uid in self.instances

    def __len__(self):
        with self._lock:
            return len(self.instances)


# ---------------------------------------------------------------- transcoding

def transcode_dataset(ds, from_ts, to_ts):
    """Return a new Dataset re-encoded for to_ts; pixel data is decoded to
    canonical native frames and re-encoded, so lossless<->lossless
    conversions are pixel-exact.  All other elements (including unknown
    private tags) are carried over unchanged."""
    out = D.Dataset()
    pix_tag = D.tag(0x7FE0, 0x0010)
    for elem in ds:
        if elem.tag == pix_tag:
            continue
        out.elements[elem.tag] = D.DataElement(elem.tag, elem.VR, elem.value)
    if pix_tag in ds:
        params = pixel.Params.from_dataset(ds)
        frames = pixel.decode_frames(ds[pix_tag].value, params, from_ts)
        value = pixel.encode_frames(frames, params, to_ts)
        if to_ts in U.ENCAPSULATED:
            out.elements[pix_tag] = D.DataElement(pix_tag, "OB", value)
        else:
            vr = "OW" if params.bits_alloc > 8 else "OB"
            out.elements[pix_tag] = D.DataElement(pix_tag, vr, value)
    return out


def pixels_equal(ds_a, ts_a, ds_b, ts_b):
    """Decode both data sets to canonical frames and compare bit-exactly."""
    pa = pixel.Params.from_dataset(ds_a)
    pb = pixel.Params.from_dataset(ds_b)
    if (pa.rows, pa.cols, pa.spp, pa.bits_alloc, pa.nframes) != \
            (pb.rows, pb.cols, pb.spp, pb.bits_alloc, pb.nframes):
        return False
    fa = pixel.decode_frames(ds_a[D.tag(0x7FE0, 0x0010)].value, pa, ts_a)
    fb = pixel.decode_frames(ds_b[D.tag(0x7FE0, 0x0010)].value, pb, ts_b)
    return fa == fb


# ---------------------------------------------------------------- SCU helpers

def scu_store(assoc, ds, ts, msg_id=None, move_originator=None):
    """Send one C-STORE; returns the response status."""
    sop_class = ds.get(0x0008, 0x0016)
    sop_inst = ds.get(0x0008, 0x0018)
    pc = assoc.context_for(sop_class, ts)
    if pc is None:
        raise AssociationError("no accepted context for %s / %s"
                               % (U.sop_name(sop_class), U.ts_name(ts)))
    mid = msg_id or assoc.next_message_id()
    mo_ae, mo_id = move_originator or (None, None)
    cmd = dimse.c_store_rq(mid, sop_class, sop_inst,
                           move_originator_ae=mo_ae, move_originator_id=mo_id)
    data = D.write_dataset(ds, ts)
    assoc.send_message(pc.pcid, cmd, data,
                       label="C-STORE-RQ %s" % U.sop_name(sop_class))
    _, rsp, _ = assoc.recv_message()
    return rsp.get(0x0000, 0x0900)


def scu_find(assoc, identifier, sop_class=U.StudyRootFind):
    pc = assoc.context_for(sop_class)
    if pc is None:
        raise AssociationError("no accepted FIND context")
    mid = assoc.next_message_id()
    data = D.write_dataset(identifier, pc.transfer_syntax)
    assoc.send_message(pc.pcid, dimse.c_find_rq(mid, sop_class), data,
                       label="C-FIND-RQ")
    results = []
    while True:
        _, rsp, rsp_data = assoc.recv_message()
        status = rsp.get(0x0000, 0x0900)
        if status in (dimse.STATUS_PENDING, dimse.STATUS_PENDING_WARNING):
            if rsp_data:
                results.append(D.read_dataset(rsp_data, pc.transfer_syntax))
            continue
        return status, results


def scu_move(assoc, identifier, destination, sop_class=U.StudyRootMove):
    pc = assoc.context_for(sop_class)
    if pc is None:
        raise AssociationError("no accepted MOVE context")
    mid = assoc.next_message_id()
    data = D.write_dataset(identifier, pc.transfer_syntax)
    assoc.send_message(pc.pcid, dimse.c_move_rq(mid, sop_class, destination),
                       data, label="C-MOVE-RQ dest=%s" % destination)
    final = None
    pendings = 0
    while True:
        _, rsp, _ = assoc.recv_message()
        status = rsp.get(0x0000, 0x0900)
        if status in (dimse.STATUS_PENDING, dimse.STATUS_PENDING_WARNING):
            pendings += 1
            continue
        final = rsp
        break
    return final, pendings


def scu_commit(assoc, transaction_uid, refs, msg_id=None):
    """Send N-ACTION storage commitment request. refs = [(class, instance)]."""
    pc = assoc.context_for(U.StorageCommitmentPushModel)
    if pc is None:
        raise AssociationError("no accepted storage commitment context")
    mid = msg_id or assoc.next_message_id()
    info = D.Dataset()
    info.set(0x0008, 0x1195, "UI", transaction_uid)
    seq = D.Dataset()
    ref_items = []
    for cls, inst in refs:
        item = D.Dataset()
        item.set(0x0008, 0x1150, "UI", cls)
        item.set(0x0008, 0x1155, "UI", inst)
        ref_items.append(item)
    info.set(0x0008, 0x1199, "SQ", ref_items)
    cmd = dimse.n_action_rq(mid, U.StorageCommitmentPushModel,
                            U.StorageCommitmentPushModel + ".1", 1)
    assoc.send_message(pc.pcid, cmd,
                       D.write_dataset(info, pc.transfer_syntax),
                       label="N-ACTION-RQ storage commitment")
    _, rsp, _ = assoc.recv_message()
    return rsp.get(0x0000, 0x0900)


# ---------------------------------------------------------------- gateway

class Gateway(object):
    """A DICOM gateway: SCP towards downstream modalities, SCU towards the
    upstream platform, transcoding between transfer syntaxes as needed."""

    def __init__(self, ae_title="GATEWAY", port=11112,
                 scp_contexts=None, monitor=None,
                 upstream=None, move_destinations=None,
                 commitment_targets=None, max_pdu=P.DEFAULT_MAX_PDU,
                 async_ops=None):
        """
        scp_contexts: {abstract: [ts,...]} offered to downstream devices.
        upstream: dict(host, port, called_ae, contexts=[(abstract,[ts])])
            where received instances are forwarded, or None.
        move_destinations: {ae_title: dict(host, port, contexts=[...])}.
        commitment_targets: {ae_title: dict(host, port, contexts=[...])} used
            to call back N-EVENT-REPORT to a storage-commitment requester.
        """
        self.ae_title = ae_title
        self.port = port
        self.scp_contexts = scp_contexts or {}
        self.monitor = monitor or Monitor()
        self.upstream = upstream
        self.move_destinations = move_destinations or {}
        self.commitment_targets = commitment_targets or {}
        self.max_pdu = max_pdu
        self.async_ops = async_ops
        self.store = InstanceStore()
        self.on_n_event_report = None         # optional hook(cmd, info_ds, ts)
        self._server = None
        self._threads = []
        self._running = False

    # ------------------------------------------------------------ server

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", self.port))
        self._server.listen(16)
        self.port = self._server.getsockname()[1]
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        return self.port

    def stop(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def _accept_loop(self):
        while self._running:
            try:
                sock, addr = self._server.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle_connection,
                                 args=(sock, addr), daemon=True)
            t.start()
            self._threads.append(t)

    def _handle_connection(self, sock, addr):
        rec = self.monitor.new_record("in", "%s:%d" % addr, "SCP")
        try:
            assoc = Association.accept(
                sock, self.scp_contexts, max_pdu=self.max_pdu,
                async_ops=self.async_ops, logger=rec)
            rec.calling_ae = assoc.calling_ae
            rec.called_ae = assoc.called_ae
            rec.transfer_syntaxes = [
                "%s / %s" % (U.sop_name(pc.abstract_syntax),
                             U.ts_name(pc.transfer_syntax))
                for pc in assoc.accepted_contexts()]
            self._serve(assoc, rec)
        except AssociationReleased:
            rec.close("released")
        except AssociationAborted as exc:
            rec.event("abort", "association aborted: %s" % exc)
            rec.close("aborted")
        except (AssociationError, EOFError, OSError) as exc:
            rec.event("error", "association error: %s" % exc)
            rec.close("error")
        except Exception as exc:               # keep the server alive
            rec.event("error", "internal error: %s" % exc)
            rec.event("error", traceback.format_exc(limit=5))
            rec.close("error")
        finally:
            try:
                sock.close()
            except OSError:
                pass
            if rec.status == "open":
                rec.close("closed")

    def _serve(self, assoc, rec):
        while True:
            pcid, cmd, data = assoc.recv_message()
            field = dimse.command_field(cmd)
            pc = assoc.accepted_context(pcid)
            ts = pc.transfer_syntax if pc else U.ImplicitVRLittleEndian
            if field == dimse.C_ECHO_RQ:
                assoc.send_message(pcid, dimse.c_echo_rsp(
                    cmd.get(0x0000, 0x0110)))
            elif field == dimse.C_STORE_RQ:
                self._on_store(assoc, rec, pcid, cmd, data, ts)
            elif field == dimse.C_FIND_RQ:
                self._on_find(assoc, rec, pcid, cmd, data, ts)
            elif field == dimse.C_MOVE_RQ:
                self._on_move(assoc, rec, pcid, cmd, data, ts)
            elif field == dimse.N_ACTION_RQ:
                self._on_action(assoc, rec, pcid, cmd, data, ts)
            elif field == dimse.N_EVENT_REPORT_RQ:
                # we are being told a commitment result (as requester)
                if self.on_n_event_report and data:
                    self.on_n_event_report(cmd, D.read_dataset(data, ts), ts)
                assoc.send_message(pcid, dimse.n_event_report_rsp(
                    cmd.get(0x0000, 0x0110), cmd.get(0x0000, 0x0002),
                    cmd.get(0x0000, 0x1001), cmd.get(0x0000, 0x1002)))
            else:
                rec.event("error", "unsupported command 0x%04X" % field)
                assoc.abort(P.ABORT_SOURCE_USER, 2)
                raise AssociationAborted("unsupported command")

    # ------------------------------------------------------------ C-STORE

    def _on_store(self, assoc, rec, pcid, cmd, data, ts):
        mid = cmd.get(0x0000, 0x0110)
        sop_class = cmd.get(0x0000, 0x0002)
        sop_inst = cmd.get(0x0000, 0x1001)
        status = dimse.STATUS_SUCCESS
        try:
            ds = D.read_dataset(data, ts)
            self.store.put(ds, ts)
            rec.event("store", "stored %s SOPInstanceUID=%s (%s)"
                      % (U.sop_name(sop_class), sop_inst, U.ts_name(ts)))
            if self.upstream:
                self._forward_upstream(ds, ts, rec)
        except Exception as exc:
            rec.event("error", "C-STORE failed: %s" % exc)
            status = 0xC000
        assoc.send_message(pcid, dimse.c_store_rsp(mid, sop_class, sop_inst,
                                                   status))

    def _forward_upstream(self, ds, from_ts, rec):
        up = self.upstream
        rec.event("forward", "forwarding to upstream %s:%d (%s)"
                  % (up["host"], up["port"], up["called_ae"]))
        out_rec = self.monitor.new_record(
            "out", "%s:%d" % (up["host"], up["port"]), "forward")
        try:
            assoc = Association.request(
                up["host"], up["port"], self.ae_title, up["called_ae"],
                up["contexts"], max_pdu=self.max_pdu,
                async_ops=self.async_ops, logger=out_rec)
            out_rec.calling_ae = self.ae_title
            out_rec.called_ae = up["called_ae"]
            sop_class = ds.get(0x0008, 0x0016)
            pc = assoc.context_for(sop_class)
            if pc is None:
                raise AssociationError("upstream accepted no storage context")
            to_ts = pc.transfer_syntax
            out_rec.transfer_syntaxes = [
                "%s / %s" % (U.sop_name(p.abstract_syntax),
                             U.ts_name(p.transfer_syntax))
                for p in assoc.accepted_contexts()]
            if to_ts != from_ts:
                out_rec.event("transcode", "%s -> %s"
                              % (U.ts_name(from_ts), U.ts_name(to_ts)))
                ds = transcode_dataset(ds, from_ts, to_ts)
            status = scu_store(assoc, ds, to_ts)
            out_rec.event("store", "upstream C-STORE-RSP status=0x%04X"
                          % status)
            assoc.release()
            out_rec.close("released")
        except Exception as exc:
            out_rec.event("error", "forward failed: %s" % exc)
            out_rec.close("error")
            raise

    # ------------------------------------------------------------ C-FIND

    def _on_find(self, assoc, rec, pcid, cmd, data, ts):
        mid = cmd.get(0x0000, 0x0110)
        sop_class = cmd.get(0x0000, 0x0002)
        query = D.read_dataset(data, ts)
        level = query.get(0x0008, 0x0052, "STUDY")
        rec.event("find", "C-FIND level=%s keys=%s"
                  % (level, ", ".join(D.tag_str(t) for t in query.tags())))
        matches = self._match(query)
        for ds, stored_ts in matches:
            rsp_ds = D.Dataset()
            for t in query.tags():
                if t in ds.elements:
                    rsp_ds.elements[t] = ds.elements[t]
                else:
                    vr = D.DICT.get(t, ("LO", ""))[0]
                    rsp_ds.elements[t] = D.DataElement(t, vr, "")
            rsp_ds.set(0x0008, 0x0052, "CS", level)
            assoc.send_message(
                pcid, dimse.c_find_rsp(mid, sop_class, dimse.STATUS_PENDING,
                                       has_dataset=True),
                D.write_dataset(rsp_ds, ts),
                label="C-FIND-RSP pending")
        assoc.send_message(pcid, dimse.c_find_rsp(mid, sop_class,
                                                  dimse.STATUS_SUCCESS))

    def _match(self, query):
        """Universal/exact string matching of query keys against the store."""
        keys = [(e.tag, e.value) for e in query
                if e.value not in (None, "", [])
                and e.tag != D.tag(0x0008, 0x0052)]
        out = []
        for ds, ts in self.store.all():
            ok = True
            for t, qv in keys:
                have = ds.elements.get(t)
                hv = have.value if have else None
                if isinstance(qv, list):
                    if hv not in qv:
                        ok = False
                        break
                elif str(hv) != str(qv):
                    ok = False
                    break
            if ok:
                out.append((ds, ts))
        return out

    # ------------------------------------------------------------ C-MOVE

    def _on_move(self, assoc, rec, pcid, cmd, data, ts):
        mid = cmd.get(0x0000, 0x0110)
        sop_class = cmd.get(0x0000, 0x0002)
        destination = cmd.get(0x0000, 0x0600)
        query = D.read_dataset(data, ts)
        rec.event("move", "C-MOVE destination=%s" % destination)
        dest = self.move_destinations.get(destination)
        if dest is None:
            rec.event("error", "unknown move destination %s" % destination)
            assoc.send_message(pcid, dimse.c_move_rsp(
                mid, sop_class, 0xA801, remaining=0, completed=0, failed=0))
            return
        matches = self._match(query)
        total = len(matches)
        completed = failed = 0
        sub_rec = self.monitor.new_record(
            "out", "%s:%d" % (dest["host"], dest["port"]),
            "C-MOVE sub-association -> %s" % destination)
        try:
            sub = Association.request(
                dest["host"], dest["port"], self.ae_title, destination,
                dest["contexts"], max_pdu=self.max_pdu,
                async_ops=self.async_ops, logger=sub_rec)
            sub_rec.calling_ae = self.ae_title
            sub_rec.called_ae = destination
            sub_rec.transfer_syntaxes = [
                "%s / %s" % (p.abstract_syntax and U.sop_name(p.abstract_syntax),
                             U.ts_name(p.transfer_syntax))
                for p in sub.accepted_contexts()]
            for i, (ds, stored_ts) in enumerate(matches):
                sop = ds.get(0x0008, 0x0016)
                pc = sub.context_for(sop)
                if pc is None:
                    failed += 1
                else:
                    to_ts = pc.transfer_syntax
                    send_ds = ds
                    if to_ts != stored_ts:
                        sub_rec.event("transcode", "%s -> %s"
                                      % (U.ts_name(stored_ts),
                                         U.ts_name(to_ts)))
                        send_ds = transcode_dataset(ds, stored_ts, to_ts)
                    status = scu_store(sub, send_ds, to_ts,
                                       move_originator=(assoc.calling_ae, mid))
                    if status == dimse.STATUS_SUCCESS:
                        completed += 1
                    else:
                        failed += 1
                # pending response with sub-operation counters
                assoc.send_message(pcid, dimse.c_move_rsp(
                    mid, sop_class, dimse.STATUS_PENDING,
                    remaining=total - i - 1, completed=completed,
                    failed=failed, warning=0))
            sub.release()
            sub_rec.close("released")
        except (AssociationError, OSError) as exc:
            rec.event("error", "C-MOVE sub-association failed: %s" % exc)
            sub_rec.close("error")
            failed = total - completed
        final = (dimse.STATUS_SUCCESS if failed == 0 else
                 0xB000 if completed else 0xA702)
        assoc.send_message(pcid, dimse.c_move_rsp(
            mid, sop_class, final, remaining=0, completed=completed,
            failed=failed, warning=0))

    # ------------------------------------------------------------ storage commitment

    def _on_action(self, assoc, rec, pcid, cmd, data, ts):
        mid = cmd.get(0x0000, 0x0110)
        sop_inst = cmd.get(0x0000, 0x1001)
        info = D.read_dataset(data, ts)
        transaction_uid = info.get(0x0008, 0x1195)
        refs = []
        for item in info.get(0x0008, 0x1199, []) or []:
            refs.append((item.get(0x0008, 0x1150), item.get(0x0008, 0x1155)))
        rec.event("commit", "N-ACTION storage commitment, transaction=%s, "
                  "%d referenced instance(s)" % (transaction_uid, len(refs)))
        ok, bad = [], []
        for cls, inst in refs:
            (ok if self.store.contains(inst) else bad).append((cls, inst))
        assoc.send_message(pcid, dimse.n_action_rsp(
            mid, U.StorageCommitmentPushModel, sop_inst, 1,
            dimse.STATUS_SUCCESS))
        # the report travels on a *new* association back to the requester
        requester_ae = assoc.calling_ae
        threading.Thread(target=self._send_commitment_report,
                         args=(requester_ae, transaction_uid, ok, bad),
                         daemon=True).start()

    def _send_commitment_report(self, requester_ae, transaction_uid, ok, bad):
        target = self.commitment_targets.get(requester_ae)
        if target is None:
            return
        time.sleep(0.2)                        # let the N-ACTION finish first
        rec = self.monitor.new_record(
            "out", "%s:%d" % (target["host"], target["port"]),
            "N-EVENT-REPORT -> %s" % requester_ae)
        try:
            assoc = Association.request(
                target["host"], target["port"], self.ae_title, requester_ae,
                target["contexts"], max_pdu=self.max_pdu, logger=rec)
            pc = assoc.context_for(U.StorageCommitmentPushModel)
            info = D.Dataset()
            info.set(0x0008, 0x1195, "UI", transaction_uid)
            if ok:
                info.set(0x0008, 0x1199, "SQ", [
                    self._ref_item(c, i) for c, i in ok])
            if bad:
                info.set(0x0008, 0x1198, "SQ", [
                    self._ref_item(c, i, failed=True) for c, i in bad])
            event_type = 1 if not bad else 2
            cmd = dimse.n_event_report_rq(
                assoc.next_message_id(), U.StorageCommitmentPushModel,
                U.StorageCommitmentPushModel + ".1", event_type)
            assoc.send_message(pc.pcid, cmd,
                               D.write_dataset(info, pc.transfer_syntax),
                               label="N-EVENT-REPORT-RQ (%s)"
                               % ("success" if event_type == 1 else "failure"))
            assoc.recv_message()               # N-EVENT-REPORT-RSP
            assoc.release()
            rec.close("released")
        except Exception as exc:
            rec.event("error", "commitment report failed: %s" % exc)
            rec.close("error")

    @staticmethod
    def _ref_item(cls, inst, failed=False):
        item = D.Dataset()
        item.set(0x0008, 0x1150, "UI", cls)
        item.set(0x0008, 0x1155, "UI", inst)
        if failed:
            item.set(0x0008, 0x1156, "US", 0x0112)   # no such object instance
        return item
