"""Association state machine: negotiation, P-DATA fragmentation, aborts.

Implements the parts of PS3.8 the gateway needs:

  * A-ASSOCIATE-RQ/AC: presentation contexts are accepted or rejected one by
    one; for each proposed context the first transfer syntax both sides
    support is chosen.  Maximum PDU length and the asynchronous operations
    window are negotiated in the user information item.
  * P-DATA-TF: command and data sets travel as separate PDV fragment
    sequences (command bit), each fragment bounded by the peer's maximum
    PDU length, the last fragment of a sequence carrying the last-fragment
    flag.
  * A-ABORT: a PDU with an un-negotiated presentation context ID, an
    unexpected PDU type, or a truncated PDU is answered with A-ABORT
    (provider, protocol error) as PS3.8 requires; a peer abort or a dropped
    TCP connection surfaces as AssociationAborted.
"""

import socket
import struct
import threading
import time

from . import dimse
from . import pdu as P
from . import uid as U


class AssociationError(Exception):
    pass


class AssociationAborted(AssociationError):
    pass


class AssociationReleased(AssociationError):
    """Peer released the association gracefully."""


class NullLogger(object):
    def event(self, kind, text, **kw):
        pass

    def progress(self, direction, nbytes, total):
        pass


class Association(object):
    def __init__(self, sock, logger=None):
        self.sock = sock
        self.log = logger or NullLogger()
        self.peer_max_pdu = P.DEFAULT_MAX_PDU
        self.max_pdu = P.DEFAULT_MAX_PDU
        self.presentation_contexts = []     # negotiated (AC view)
        self.async_ops_invoked = None
        self.async_ops_performed = None
        self.calling_ae = ""
        self.called_ae = ""
        self.is_requester = False
        self._send_lock = threading.Lock()
        self._next_msg_id = 1
        self._released = False
        self._aborted = False
        self.bytes_sent = 0
        self.bytes_received = 0

    # ------------------------------------------------------------ transport

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise EOFError("TCP connection closed by peer")
            buf += chunk
        self.bytes_received += n
        return buf

    def _send_pdu(self, data):
        with self._send_lock:
            self.sock.sendall(data)
            self.bytes_sent += len(data)

    # ------------------------------------------------------------ negotiation

    @classmethod
    def request(cls, host, port, calling_ae, called_ae, contexts,
                max_pdu=P.DEFAULT_MAX_PDU, async_ops=None, logger=None,
                timeout=15):
        """Request an association.

        contexts: list of (abstract_syntax, [transfer_syntax, ...]).
        Returns an established Association; raises pdu.Reject or
        AssociationError on failure.
        """
        log = logger or NullLogger()
        rq = P.AssociateRequest()
        rq.calling_ae = calling_ae
        rq.called_ae = called_ae
        rq.max_pdu = max_pdu
        if async_ops:
            rq.async_ops_invoked, rq.async_ops_performed = async_ops
        pcid = 1
        for abstract, tss in contexts:
            rq.presentation_contexts.append(
                P.PresentationContext(pcid, abstract, list(tss)))
            pcid += 2
        log.event("negotiate", "A-ASSOCIATE-RQ -> %s:%d  called=%s calling=%s"
                  % (host, port, called_ae, calling_ae))
        for pc in rq.presentation_contexts:
            log.event("negotiate", "  propose PC-%d %s: %s"
                      % (pc.pcid, U.sop_name(pc.abstract_syntax),
                         " | ".join(U.ts_name(t) for t in pc.transfer_syntaxes)))
        if async_ops:
            log.event("negotiate", "  async ops window: invoked=%d performed=%d"
                      % async_ops)
        log.event("negotiate", "  max PDU length offered: %d" % max_pdu)

        sock = socket.create_connection((host, port), timeout=timeout)
        assoc = cls(sock, log)
        assoc.is_requester = True
        assoc.calling_ae = calling_ae
        assoc.called_ae = called_ae
        assoc.max_pdu = max_pdu
        sock.sendall(P.encode_associate(rq, P.A_ASSOCIATE_RQ))
        try:
            pdu_type, body = P.read_pdu(assoc._recv_exact)
        except EOFError:
            sock.close()
            raise AssociationError("connection closed during negotiation")
        if pdu_type == P.A_ASSOCIATE_RJ:
            sock.close()
            rej = P.decode_reject(body)
            log.event("negotiate", "A-ASSOCIATE-RJ: %s" % rej)
            raise rej
        if pdu_type != P.A_ASSOCIATE_AC:
            sock.sendall(P.encode_abort(P.ABORT_SOURCE_PROVIDER, 2))
            sock.close()
            raise AssociationError("expected A-ASSOCIATE-AC, got PDU 0x%02X"
                                   % pdu_type)
        ac = P.decode_associate(body, P.A_ASSOCIATE_AC)
        # AC presentation context items carry no abstract syntax — correlate
        # with our request by presentation context ID
        offered = {pc.pcid: pc for pc in rq.presentation_contexts}
        for pc in ac.presentation_contexts:
            if pc.pcid in offered:
                pc.abstract_syntax = offered[pc.pcid].abstract_syntax
        assoc.presentation_contexts = ac.presentation_contexts
        assoc.peer_max_pdu = ac.max_pdu
        assoc.async_ops_invoked = ac.async_ops_invoked
        assoc.async_ops_performed = ac.async_ops_performed
        for pc in ac.presentation_contexts:
            log.event("negotiate", "  PC-%d %s" % (pc.pcid, pc))
        log.event("negotiate", "  peer max PDU length: %d" % ac.max_pdu)
        if ac.async_ops_invoked is not None:
            log.event("negotiate",
                      "  async ops window negotiated: invoked=%d performed=%d"
                      % (ac.async_ops_invoked, ac.async_ops_performed))
        if not ac.accepted_contexts():
            sock.close()
            raise AssociationError("no presentation context was accepted")
        log.event("negotiate", "association established")
        return assoc

    @classmethod
    def accept(cls, sock, supported, calling_defaults=None,
               max_pdu=P.DEFAULT_MAX_PDU, async_ops=None, logger=None,
               max_associate_pdu=None):
        """Acceptor side.  supported: {abstract_syntax: [transfer_syntax,...]}.

        Each proposed presentation context is answered individually:
        accepted with the first transfer syntax we also support, rejected
        with the proper reason code otherwise.
        """
        log = logger or NullLogger()
        assoc = cls(sock, log)
        try:
            pdu_type, body = P.read_pdu(assoc._recv_exact)
        except EOFError:
            raise AssociationError("connection closed before A-ASSOCIATE-RQ")
        if pdu_type != P.A_ASSOCIATE_RQ:
            sock.sendall(P.encode_abort(P.ABORT_SOURCE_PROVIDER, 2))
            raise AssociationError("expected A-ASSOCIATE-RQ, got PDU 0x%02X"
                                   % pdu_type)
        rq = P.decode_associate(body, P.A_ASSOCIATE_RQ)
        assoc.calling_ae = rq.calling_ae
        assoc.called_ae = rq.called_ae
        assoc.peer_max_pdu = rq.max_pdu
        assoc.max_pdu = max_pdu
        log.event("negotiate", "A-ASSOCIATE-RQ from %s (called=%s)"
                  % (rq.calling_ae, rq.called_ae))
        log.event("negotiate", "  peer max PDU length: %d" % rq.max_pdu)
        if rq.async_ops_invoked is not None:
            log.event("negotiate",
                      "  peer proposes async ops window: invoked=%d performed=%d"
                      % (rq.async_ops_invoked, rq.async_ops_performed))

        ac = P.AssociateRequest()
        ac.calling_ae = rq.calling_ae
        ac.called_ae = rq.called_ae
        ac.max_pdu = max_pdu
        if async_ops:
            ac.async_ops_invoked, ac.async_ops_performed = async_ops
        elif rq.async_ops_invoked is not None:
            # accept the proposed window (we perform operations serially,
            # which is always legal within any negotiated window)
            ac.async_ops_invoked = rq.async_ops_invoked
            ac.async_ops_performed = rq.async_ops_performed

        for pc in rq.presentation_contexts:
            our_tss = supported.get(pc.abstract_syntax)
            answer = P.PresentationContext(pc.pcid, pc.abstract_syntax)
            if our_tss is None:
                answer.result = P.PC_REJECT_ABSTRACT_SYNTAX
            else:
                common = [t for t in pc.transfer_syntaxes if t in our_tss]
                if common:
                    answer.result = P.PC_ACCEPTANCE
                    answer.transfer_syntaxes = [common[0]]
                else:
                    answer.result = P.PC_REJECT_TRANSFER_SYNTAX
            ac.presentation_contexts.append(answer)
            log.event("negotiate", "  PC-%d %s -> %s"
                      % (pc.pcid, U.sop_name(pc.abstract_syntax or "?"),
                         P.PC_RESULT_NAMES[answer.result]
                         + (" (%s)" % U.ts_name(answer.transfer_syntax)
                            if answer.accepted else "")))
        assoc.presentation_contexts = ac.presentation_contexts
        assoc.async_ops_invoked = ac.async_ops_invoked
        assoc.async_ops_performed = ac.async_ops_performed
        log.event("negotiate", "  our max PDU length: %d" % max_pdu)
        if ac.async_ops_invoked is not None:
            log.event("negotiate",
                      "  async ops window negotiated: invoked=%d performed=%d"
                      % (ac.async_ops_invoked, ac.async_ops_performed))
        sock.sendall(P.encode_associate(ac, P.A_ASSOCIATE_AC))
        log.event("negotiate", "association established")
        return assoc

    # ------------------------------------------------------------ contexts

    def accepted_context(self, pcid):
        for pc in self.presentation_contexts:
            if pc.pcid == pcid and pc.accepted:
                return pc
        return None

    def accepted_contexts(self):
        return [pc for pc in self.presentation_contexts if pc.accepted]

    def context_for(self, abstract_syntax, transfer_syntax=None):
        for pc in self.presentation_contexts:
            if pc.accepted and pc.abstract_syntax == abstract_syntax and (
                    transfer_syntax is None
                    or pc.transfer_syntax == transfer_syntax):
                return pc
        return None

    def next_message_id(self):
        mid = self._next_msg_id
        self._next_msg_id = self._next_msg_id % 0xFFFF + 1
        return mid

    # ------------------------------------------------------------ messaging

    def _send_fragments(self, pcid, payload, is_command, progress=None):
        """Split payload into PDVs bounded by the peer's max PDU length."""
        max_pdv = max(512, self.peer_max_pdu - 6)   # PDV header is 6 bytes
        offset = 0
        first = True
        while first or offset < len(payload):
            first = False
            chunk = payload[offset:offset + max_pdv]
            offset += len(chunk)
            is_last = offset >= len(payload)
            pdu_bytes = P.encode_pdata([(pcid, is_command, is_last, chunk)])
            self._send_pdu(pdu_bytes)
            if progress:
                progress(offset, len(payload))

    def send_message(self, pcid, command, data=None, label=None):
        """Send one DIMSE message: command fragments then data fragments."""
        pc = self.accepted_context(pcid)
        if pc is None:
            raise AssociationError("presentation context %d was not negotiated"
                                   % pcid)
        name = dimse.command_name(dimse.command_field(command))
        label = label or name
        cmd_bytes = dimse.encode_command(command)
        self.log.event("send", "%s (PC-%d, %d bytes command)"
                       % (label, pcid, len(cmd_bytes)))
        self._send_fragments(pcid, cmd_bytes, is_command=True)
        if data is not None:
            total = len(data)

            def progress(off, tot):
                self.log.progress("send", self.bytes_sent, 0)

            self.log.event("send", "  data set: %d bytes in PDVs of <= %d"
                           % (total, max(512, self.peer_max_pdu - 6)))
            self._send_fragments(pcid, data, is_command=False,
                                 progress=progress)
            self.log.progress("send", self.bytes_sent, 0)

    def recv_message(self):
        """Receive one DIMSE message.  Returns (pcid, command, data|None).

        Raises AssociationReleased on A-RELEASE-RQ (A-RELEASE-RP is sent
        automatically), AssociationAborted on A-ABORT or connection loss.
        """
        cmd_fragments = {}
        data_fragments = {}
        while True:
            try:
                pdu_type, body = P.read_pdu(self._recv_exact)
            except EOFError:
                self._aborted = True
                self.log.event("abort",
                               "TCP connection dropped by peer (abnormal)")
                raise AssociationAborted("connection dropped by peer")
            except P.PDUError as exc:
                self.log.event("abort", "malformed PDU: %s -> A-ABORT" % exc)
                self._send_pdu(P.encode_abort(P.ABORT_SOURCE_PROVIDER, 6))
                self._aborted = True
                raise AssociationAborted("malformed PDU: %s" % exc)

            if pdu_type == P.P_DATA_TF:
                try:
                    pdvs = P.decode_pdata(body)
                except P.PDUError as exc:
                    self.log.event("abort",
                                   "malformed P-DATA-TF: %s -> A-ABORT" % exc)
                    self._send_pdu(P.encode_abort(P.ABORT_SOURCE_PROVIDER, 6))
                    self._aborted = True
                    raise AssociationAborted(str(exc))
                for pcid, is_command, is_last, data in pdvs:
                    if self.accepted_context(pcid) is None:
                        # PS3.8: PDV with a presentation context ID that was
                        # not negotiated is a protocol error -> A-ABORT
                        self.log.event(
                            "abort",
                            "PDV references un-negotiated PC-%d -> A-ABORT "
                            "(provider, invalid PDU parameter value)" % pcid)
                        self._send_pdu(P.encode_abort(P.ABORT_SOURCE_PROVIDER,
                                                      6))
                        self._aborted = True
                        raise AssociationAborted(
                            "peer used un-negotiated presentation context %d"
                            % pcid)
                    bucket = cmd_fragments if is_command else data_fragments
                    bucket.setdefault(pcid, []).append(data)
                    self.log.progress("recv", self.bytes_received, 0)
                    if is_last:
                        payload = b"".join(bucket.pop(pcid))
                        if is_command:
                            cmd_fragments[pcid] = [payload]
                            cmd_fragments["__done__" + str(pcid)] = True
                        else:
                            command = dimse.decode_command(
                                cmd_fragments.pop(pcid)[0])
                            cmd_fragments.pop("__done__" + str(pcid), None)
                            self._log_recv(command, pcid, len(payload))
                            return pcid, command, payload
                        # command complete; check whether a data set follows
                        command = dimse.decode_command(
                            cmd_fragments[pcid][0])
                        if not dimse.has_dataset(command):
                            cmd_fragments.pop(pcid)
                            cmd_fragments.pop("__done__" + str(pcid), None)
                            self._log_recv(command, pcid, 0)
                            return pcid, command, None
            elif pdu_type == P.A_RELEASE_RQ:
                self.log.event("release", "A-RELEASE-RQ received, "
                               "replying A-RELEASE-RP")
                self._send_pdu(P.encode_release(P.A_RELEASE_RP))
                self._released = True
                raise AssociationReleased()
            elif pdu_type == P.A_RELEASE_RP:
                self._released = True
                raise AssociationReleased()
            elif pdu_type == P.A_ABORT:
                abort = P.decode_abort(body)
                self._aborted = True
                self.log.event("abort", abort.describe())
                raise AssociationAborted(abort.describe())
            else:
                # unexpected PDU type in data transfer state
                self.log.event("abort",
                               "unexpected PDU 0x%02X in data transfer state "
                               "-> A-ABORT (provider, unexpected PDU)"
                               % pdu_type)
                self._send_pdu(P.encode_abort(P.ABORT_SOURCE_PROVIDER, 2))
                self._aborted = True
                raise AssociationAborted("unexpected PDU 0x%02X" % pdu_type)

    def _log_recv(self, command, pcid, data_len):
        name = dimse.command_name(dimse.command_field(command))
        status = command.get(0x0000, 0x0900, None)
        extra = ""
        if status is not None:
            extra = " status=0x%04X" % status
            if status in (dimse.STATUS_PENDING, dimse.STATUS_PENDING_WARNING):
                extra += " (pending: remaining=%s completed=%s failed=%s)" % (
                    command.get(0x0000, 0x1020, "-"),
                    command.get(0x0000, 0x1021, "-"),
                    command.get(0x0000, 0x1022, "-"))
        self.log.event("recv", "%s (PC-%d, data=%d bytes)%s"
                       % (name, pcid, data_len, extra))

    # ------------------------------------------------------------ teardown

    def release(self):
        if self._released or self._aborted:
            return
        self.log.event("release", "A-RELEASE-RQ sent")
        self._send_pdu(P.encode_release(P.A_RELEASE_RQ))
        try:
            while True:
                pdu_type, body = P.read_pdu(self._recv_exact)
                if pdu_type == P.A_RELEASE_RP:
                    self.log.event("release", "A-RELEASE-RP received")
                    break
                if pdu_type == P.A_ABORT:
                    self.log.event("abort", P.decode_abort(body).describe())
                    break
                # stray P-DATA during release is ignored
        except (EOFError, P.PDUError, OSError):
            pass
        self._released = True
        try:
            self.sock.close()
        except OSError:
            pass

    def abort(self, source=P.ABORT_SOURCE_USER, reason=0):
        if self._aborted:
            return
        self._aborted = True
        try:
            self._send_pdu(P.encode_abort(source, reason))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
