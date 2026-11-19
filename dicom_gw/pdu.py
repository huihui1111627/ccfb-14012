"""DICOM Upper Layer PDUs: encode/decode and association item structures."""

import struct

from . import uid as U

# PDU types
A_ASSOCIATE_RQ = 0x01
A_ASSOCIATE_AC = 0x02
A_ASSOCIATE_RJ = 0x03
P_DATA_TF = 0x04
A_RELEASE_RQ = 0x05
A_RELEASE_RP = 0x06
A_ABORT = 0x07

PDU_NAMES = {
    A_ASSOCIATE_RQ: "A-ASSOCIATE-RQ",
    A_ASSOCIATE_AC: "A-ASSOCIATE-AC",
    A_ASSOCIATE_RJ: "A-ASSOCIATE-RJ",
    P_DATA_TF: "P-DATA-TF",
    A_RELEASE_RQ: "A-RELEASE-RQ",
    A_RELEASE_RP: "A-RELEASE-RP",
    A_ABORT: "A-ABORT",
}

# item types
ITEM_APPLICATION_CONTEXT = 0x10
ITEM_PRESENTATION_CONTEXT_RQ = 0x20
ITEM_PRESENTATION_CONTEXT_AC = 0x21
ITEM_ABSTRACT_SYNTAX = 0x30
ITEM_TRANSFER_SYNTAX = 0x40
ITEM_USER_INFORMATION = 0x50
ITEM_MAXIMUM_LENGTH = 0x51
ITEM_IMPLEMENTATION_CLASS = 0x52
ITEM_ASYNC_OPS_WINDOW = 0x53
ITEM_IMPLEMENTATION_VERSION = 0x55

# presentation context results (A-ASSOCIATE-AC)
PC_ACCEPTANCE = 0
PC_REJECT_USER = 1
PC_REJECT_NO_REASON = 2
PC_REJECT_ABSTRACT_SYNTAX = 3
PC_REJECT_TRANSFER_SYNTAX = 4

PC_RESULT_NAMES = {
    PC_ACCEPTANCE: "acceptance",
    PC_REJECT_USER: "rejected (user)",
    PC_REJECT_NO_REASON: "rejected (no reason)",
    PC_REJECT_ABSTRACT_SYNTAX: "rejected (abstract syntax not supported)",
    PC_REJECT_TRANSFER_SYNTAX: "rejected (transfer syntaxes not supported)",
}

# A-ABORT source / reason
ABORT_SOURCE_USER = 0
ABORT_SOURCE_PROVIDER = 2
ABORT_REASONS = {
    (ABORT_SOURCE_USER, 0): "reason not specified",
    (ABORT_SOURCE_USER, 2): "protocol error",
    (ABORT_SOURCE_USER, 4): "temporary congestion",
    (ABORT_SOURCE_PROVIDER, 0): "reason not specified",
    (ABORT_SOURCE_PROVIDER, 1): "unrecognized PDU",
    (ABORT_SOURCE_PROVIDER, 2): "unexpected PDU",
    (ABORT_SOURCE_PROVIDER, 4): "unrecognized PDU parameter",
    (ABORT_SOURCE_PROVIDER, 5): "unexpected PDU parameter",
    (ABORT_SOURCE_PROVIDER, 6): "invalid PDU parameter value",
}

PROTOCOL_VERSION = 0x0001
DEFAULT_MAX_PDU = 16384
MAX_PDU_LIMIT = 131072


class PresentationContext(object):
    """One proposed presentation context (RQ) or negotiated result (AC)."""

    def __init__(self, pcid, abstract_syntax=None, transfer_syntaxes=None,
                 result=None):
        self.pcid = pcid
        self.abstract_syntax = abstract_syntax
        self.transfer_syntaxes = list(transfer_syntaxes or [])
        self.result = result                    # None in RQ

    @property
    def accepted(self):
        return self.result == PC_ACCEPTANCE

    @property
    def transfer_syntax(self):
        """The negotiated transfer syntax (accepted contexts)."""
        return self.transfer_syntaxes[0] if self.transfer_syntaxes else None

    def __repr__(self):
        if self.result is not None:
            detail = PC_RESULT_NAMES.get(self.result, "?")
            if self.accepted:
                detail += " -> " + U.ts_name(self.transfer_syntax)
        else:
            detail = ",".join(U.ts_name(t) for t in self.transfer_syntaxes)
        return "<PC %d %s: %s>" % (self.pcid,
                                   U.sop_name(self.abstract_syntax or "?"),
                                   detail)


class AssociateRequest(object):
    """Parsed A-ASSOCIATE-RQ / outgoing parameters (AC reuses the fields)."""

    def __init__(self):
        self.called_ae = ""
        self.calling_ae = ""
        self.application_context = U.ApplicationContext
        self.presentation_contexts = []         # list[PresentationContext]
        self.max_pdu = DEFAULT_MAX_PDU
        self.implementation_class = U.ImplementationClassUID
        self.implementation_version = U.ImplementationVersionName
        self.async_ops_invoked = None           # None = not negotiated
        self.async_ops_performed = None

    def accepted_contexts(self):
        return [pc for pc in self.presentation_contexts if pc.accepted]

    def context_for(self, abstract_syntax, transfer_syntax=None):
        for pc in self.accepted_contexts():
            if pc.abstract_syntax == abstract_syntax and (
                    transfer_syntax is None
                    or pc.transfer_syntax == transfer_syntax):
                return pc
        return None


class Abort(object):
    def __init__(self, source, reason):
        self.source = source
        self.reason = reason

    def describe(self):
        src = "service-user" if self.source == ABORT_SOURCE_USER else "provider"
        return "A-ABORT from %s: %s" % (
            src, ABORT_REASONS.get((self.source, self.reason),
                                 "reason %d" % self.reason))


class Reject(Exception):
    def __init__(self, result, source, reason):
        super().__init__("association rejected: result=%d source=%d reason=%d"
                         % (result, source, reason))
        self.result, self.source, self.reason = result, source, reason


# ---------------------------------------------------------------- item codec

def _item(item_type, payload):
    return struct.pack(">BBH", item_type, 0, len(payload)) + payload


def _uid_item(item_type, uid_str):
    return _item(item_type, uid_str.encode("ascii"))


def _encode_presentation_context_rq(pc):
    payload = struct.pack(">BBBB", pc.pcid, 0, 0, 0)
    payload += _uid_item(ITEM_ABSTRACT_SYNTAX, pc.abstract_syntax)
    for ts in pc.transfer_syntaxes:
        payload += _uid_item(ITEM_TRANSFER_SYNTAX, ts)
    return _item(ITEM_PRESENTATION_CONTEXT_RQ, payload)


def _encode_presentation_context_ac(pc):
    payload = struct.pack(">BBBB", pc.pcid, 0, pc.result, 0)
    payload += _uid_item(ITEM_TRANSFER_SYNTAX,
                         pc.transfer_syntax or pc.transfer_syntaxes[0])
    return _item(ITEM_PRESENTATION_CONTEXT_AC, payload)


def _encode_user_information(assoc):
    payload = _item(ITEM_MAXIMUM_LENGTH, struct.pack(">L", assoc.max_pdu))
    payload += _uid_item(ITEM_IMPLEMENTATION_CLASS, assoc.implementation_class)
    if assoc.async_ops_invoked is not None:
        payload += _item(ITEM_ASYNC_OPS_WINDOW,
                         struct.pack(">HH", assoc.async_ops_invoked,
                                     assoc.async_ops_performed))
    payload += _uid_item(ITEM_IMPLEMENTATION_VERSION,
                         assoc.implementation_version)
    return _item(ITEM_USER_INFORMATION, payload)


def encode_associate(assoc, pdu_type):
    """Encode an A-ASSOCIATE-RQ or A-ASSOCIATE-AC PDU."""
    body = struct.pack(">HH", PROTOCOL_VERSION, 0)
    body += assoc.called_ae.ljust(16)[:16].encode("ascii")
    body += assoc.calling_ae.ljust(16)[:16].encode("ascii")
    body += b"\x00" * 32
    body += _uid_item(ITEM_APPLICATION_CONTEXT, assoc.application_context)
    for pc in assoc.presentation_contexts:
        if pdu_type == A_ASSOCIATE_RQ:
            body += _encode_presentation_context_rq(pc)
        else:
            body += _encode_presentation_context_ac(pc)
    body += _encode_user_information(assoc)
    return struct.pack(">BBL", pdu_type, 0, len(body)) + body


def encode_pdata(pdv_list):
    """Encode a P-DATA-TF PDU from [(pcid, is_command, is_last, data), ...]."""
    body = b""
    for pcid, is_command, is_last, data in pdv_list:
        mch = (0x02 if is_command else 0x00) | (0x01 if is_last else 0x00)
        pdv = struct.pack(">LBB", len(data) + 2, pcid, mch) + data
        body += pdv
    return struct.pack(">BBL", P_DATA_TF, 0, len(body)) + body


def encode_release(pdu_type=A_RELEASE_RQ):
    return struct.pack(">BBL", pdu_type, 0, 4) + b"\x00" * 4


def encode_abort(source=ABORT_SOURCE_PROVIDER, reason=0):
    return struct.pack(">BBLBBBB", A_ABORT, 0, 4, 0, 0, source, reason)


def encode_reject(result=1, source=1, reason=1):
    return struct.pack(">BBLBBBB", A_ASSOCIATE_RJ, 0, 4, 0, result,
                       source, reason)


# ---------------------------------------------------------------- decoding

class PDUError(Exception):
    pass


def _read_items(data, offset=0):
    """Yield (item_type, payload) pairs."""
    while offset < len(data):
        if offset + 4 > len(data):
            raise PDUError("truncated item header")
        itype, _, ilen = struct.unpack(">BBH", data[offset:offset + 4])
        payload = data[offset + 4:offset + 4 + ilen]
        if len(payload) != ilen:
            raise PDUError("truncated item payload")
        yield itype, payload
        offset += 4 + ilen


def _parse_user_information(payload, assoc):
    for itype, p in _read_items(payload):
        if itype == ITEM_MAXIMUM_LENGTH:
            (assoc.max_pdu,) = struct.unpack(">L", p)
        elif itype == ITEM_IMPLEMENTATION_CLASS:
            assoc.implementation_class = p.decode("ascii")
        elif itype == ITEM_ASYNC_OPS_WINDOW:
            assoc.async_ops_invoked, assoc.async_ops_performed = \
                struct.unpack(">HH", p)
        elif itype == ITEM_IMPLEMENTATION_VERSION:
            assoc.implementation_version = p.decode("ascii").rstrip()
        # unknown user sub-items are ignored per PS3.8


def decode_associate(body, pdu_type):
    if len(body) < 68:
        raise PDUError("A-ASSOCIATE PDU too short")
    assoc = AssociateRequest()
    assoc.called_ae = body[4:20].decode("ascii").strip()
    assoc.calling_ae = body[20:36].decode("ascii").strip()
    pc_item = (ITEM_PRESENTATION_CONTEXT_RQ if pdu_type == A_ASSOCIATE_RQ
               else ITEM_PRESENTATION_CONTEXT_AC)
    for itype, payload in _read_items(body, 68):
        if itype == ITEM_APPLICATION_CONTEXT:
            assoc.application_context = payload.decode("ascii")
        elif itype == pc_item:
            pcid, _, result, _ = struct.unpack(">BBBB", payload[:4])
            pc = PresentationContext(pcid)
            if pdu_type == A_ASSOCIATE_AC:
                pc.result = result
            for stype, sp in _read_items(payload, 4):
                if stype == ITEM_ABSTRACT_SYNTAX:
                    pc.abstract_syntax = sp.decode("ascii")
                elif stype == ITEM_TRANSFER_SYNTAX:
                    pc.transfer_syntaxes.append(sp.decode("ascii"))
            assoc.presentation_contexts.append(pc)
        elif itype == ITEM_USER_INFORMATION:
            _parse_user_information(payload, assoc)
    return assoc


def decode_pdata(body):
    """Parse a P-DATA-TF body into [(pcid, is_command, is_last, data), ...]."""
    pdvs = []
    offset = 0
    while offset < len(body):
        if offset + 6 > len(body):
            raise PDUError("truncated PDV header")
        length, pcid, mch = struct.unpack(">LBB", body[offset:offset + 6])
        data = body[offset + 6:offset + 4 + length]
        if len(data) != length - 2:
            raise PDUError("truncated PDV data")
        pdvs.append((pcid, bool(mch & 0x02), bool(mch & 0x01), data))
        offset += 4 + length
    return pdvs


def decode_abort(body):
    if len(body) < 4:
        raise PDUError("truncated A-ABORT")
    return Abort(body[2], body[3])


def decode_reject(body):
    if len(body) < 4:
        raise PDUError("truncated A-ASSOCIATE-RJ")
    return Reject(body[1], body[2], body[3])


def read_pdu(recv_exact):
    """Read one PDU header+body. recv_exact(n) -> bytes or raises EOFError."""
    hdr = recv_exact(6)
    pdu_type, _, length = struct.unpack(">BBL", hdr)
    if length > 16 * 1024 * 1024:
        raise PDUError("implausible PDU length %d" % length)
    body = recv_exact(length) if length else b""
    return pdu_type, body
