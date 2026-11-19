"""DIMSE command-set codec and message builders.

The command set is always Implicit VR Little Endian (PS3.7).  Command tags
are registered into the dataset dictionary so they decode with proper VRs.
"""

from . import dataset as D
from . import uid as U

# ---------------------------------------------------------------- command tags
_CMD_TAGS = {
    (0x0000, 0x0000): "UL",   # CommandGroupLength
    (0x0000, 0x0002): "UI",   # AffectedSOPClassUID
    (0x0000, 0x0003): "UI",   # RequestedSOPClassUID
    (0x0000, 0x0100): "US",   # CommandField
    (0x0000, 0x0110): "US",   # MessageID
    (0x0000, 0x0120): "US",   # MessageIDBeingRespondedTo
    (0x0000, 0x0600): "AE",   # MoveDestination
    (0x0000, 0x0700): "US",   # Priority
    (0x0000, 0x0800): "US",   # CommandDataSetType
    (0x0000, 0x0900): "US",   # Status
    (0x0000, 0x0902): "LO",   # ErrorComment
    (0x0000, 0x1001): "UI",   # AffectedSOPInstanceUID
    (0x0000, 0x1002): "US",   # EventTypeID
    (0x0000, 0x1003): "US",   # ActionTypeID
    (0x0000, 0x1020): "US",   # NumberOfRemainingSuboperations
    (0x0000, 0x1021): "US",   # NumberOfCompletedSuboperations
    (0x0000, 0x1022): "US",   # NumberOfFailedSuboperations
    (0x0000, 0x1023): "US",   # NumberOfWarningSuboperations
    (0x0000, 0x1030): "AE",   # MoveOriginatorApplicationEntityTitle
    (0x0000, 0x1031): "US",   # MoveOriginatorMessageID
}
for (g, e), vr in _CMD_TAGS.items():
    D.DICT.setdefault(D.tag(g, e), (vr, "Cmd%04X%04X" % (g, e)))

# command fields
C_STORE_RQ = 0x0001
C_STORE_RSP = 0x8001
C_FIND_RQ = 0x0020
C_FIND_RSP = 0x8020
C_MOVE_RQ = 0x0021
C_MOVE_RSP = 0x8021
C_ECHO_RQ = 0x0030
C_ECHO_RSP = 0x8030
N_EVENT_REPORT_RQ = 0x0100
N_EVENT_REPORT_RSP = 0x8100
N_ACTION_RQ = 0x0130
N_ACTION_RSP = 0x8130

COMMAND_NAMES = {
    C_STORE_RQ: "C-STORE-RQ", C_STORE_RSP: "C-STORE-RSP",
    C_FIND_RQ: "C-FIND-RQ", C_FIND_RSP: "C-FIND-RSP",
    C_MOVE_RQ: "C-MOVE-RQ", C_MOVE_RSP: "C-MOVE-RSP",
    C_ECHO_RQ: "C-ECHO-RQ", C_ECHO_RSP: "C-ECHO-RSP",
    N_EVENT_REPORT_RQ: "N-EVENT-REPORT-RQ",
    N_EVENT_REPORT_RSP: "N-EVENT-REPORT-RSP",
    N_ACTION_RQ: "N-ACTION-RQ", N_ACTION_RSP: "N-ACTION-RSP",
}

# statuses
STATUS_SUCCESS = 0x0000
STATUS_PENDING = 0xFF00
STATUS_PENDING_WARNING = 0xFF01

NO_DATASET = 0x0101
HAS_DATASET = 0x0000

PRIORITY_MEDIUM = 0
PRIORITY_HIGH = 1
PRIORITY_LOW = 2


def command_name(field):
    return COMMAND_NAMES.get(field, "0x%04X" % field)


def _cmd(fields):
    """Build a command data set; group length is computed at encode time."""
    ds = D.Dataset()
    for (g, e), value in fields.items():
        vr = _CMD_TAGS[(g, e)]
        ds.set(g, e, vr, value)
    return ds


def encode_command(ds):
    """Encode a command set, computing (0000,0000) CommandGroupLength."""
    body = D.write_dataset(ds, U.ImplicitVRLittleEndian)
    out = D.Dataset()
    out.set(0x0000, 0x0000, "UL", len(body))
    for elem in ds:
        out.elements[elem.tag] = elem
    return D.write_dataset(out, U.ImplicitVRLittleEndian)


def decode_command(data):
    return D.read_dataset(data, U.ImplicitVRLittleEndian)


def cmd_get(cmd, group, elem, default=None):
    return cmd.get(group, elem, default)


def command_field(cmd):
    return cmd.get(0x0000, 0x0100)


def has_dataset(cmd):
    return cmd.get(0x0000, 0x0800, NO_DATASET) != NO_DATASET


# ---------------------------------------------------------------- builders

def c_echo_rq(msg_id):
    return _cmd({(0x0000, 0x0002): U.Verification,
                   (0x0000, 0x0100): C_ECHO_RQ,
                   (0x0000, 0x0110): msg_id,
                   (0x0000, 0x0800): NO_DATASET})


def c_echo_rsp(msg_id, status=STATUS_SUCCESS):
    return _cmd({(0x0000, 0x0002): U.Verification,
                   (0x0000, 0x0100): C_ECHO_RSP,
                   (0x0000, 0x0120): msg_id,
                   (0x0000, 0x0800): NO_DATASET,
                   (0x0000, 0x0900): status})


def c_store_rq(msg_id, sop_class, sop_instance, priority=PRIORITY_MEDIUM,
               move_originator_ae=None, move_originator_id=None):
    fields = {(0x0000, 0x0002): sop_class,
              (0x0000, 0x0100): C_STORE_RQ,
              (0x0000, 0x0110): msg_id,
              (0x0000, 0x0700): priority,
              (0x0000, 0x0800): HAS_DATASET,
              (0x0000, 0x1001): sop_instance}
    if move_originator_ae is not None:
        fields[(0x0000, 0x1030)] = move_originator_ae
        fields[(0x0000, 0x1031)] = move_originator_id
    return _cmd(fields)


def c_store_rsp(msg_id, sop_class, sop_instance, status=STATUS_SUCCESS):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): C_STORE_RSP,
                   (0x0000, 0x0120): msg_id,
                   (0x0000, 0x0800): NO_DATASET,
                   (0x0000, 0x0900): status,
                   (0x0000, 0x1001): sop_instance})


def c_find_rq(msg_id, sop_class, priority=PRIORITY_MEDIUM):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): C_FIND_RQ,
                   (0x0000, 0x0110): msg_id,
                   (0x0000, 0x0700): priority,
                   (0x0000, 0x0800): HAS_DATASET})


def c_find_rsp(msg_id, sop_class, status, has_dataset=False):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): C_FIND_RSP,
                   (0x0000, 0x0120): msg_id,
                   (0x0000, 0x0800): HAS_DATASET if has_dataset else NO_DATASET,
                   (0x0000, 0x0900): status})


def c_move_rq(msg_id, sop_class, destination, priority=PRIORITY_MEDIUM):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): C_MOVE_RQ,
                   (0x0000, 0x0110): msg_id,
                   (0x0000, 0x0700): priority,
                   (0x0000, 0x0800): HAS_DATASET,
                   (0x0000, 0x0600): destination})


def c_move_rsp(msg_id, sop_class, status, remaining=None, completed=None,
               failed=None, warning=None):
    fields = {(0x0000, 0x0002): sop_class,
              (0x0000, 0x0100): C_MOVE_RSP,
              (0x0000, 0x0120): msg_id,
              (0x0000, 0x0800): NO_DATASET,
              (0x0000, 0x0900): status}
    if remaining is not None:
        fields[(0x0000, 0x1020)] = remaining
        fields[(0x0000, 0x1021)] = completed or 0
        fields[(0x0000, 0x1022)] = failed or 0
        fields[(0x0000, 0x1023)] = warning or 0
    return _cmd(fields)


def n_action_rq(msg_id, sop_class, sop_instance, action_type_id):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): N_ACTION_RQ,
                   (0x0000, 0x0110): msg_id,
                   (0x0000, 0x0800): HAS_DATASET,
                   (0x0000, 0x1001): sop_instance,
                   (0x0000, 0x1003): action_type_id})


def n_action_rsp(msg_id, sop_class, sop_instance, action_type_id,
                 status=STATUS_SUCCESS):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): N_ACTION_RSP,
                   (0x0000, 0x0120): msg_id,
                   (0x0000, 0x0800): NO_DATASET,
                   (0x0000, 0x0900): status,
                   (0x0000, 0x1001): sop_instance,
                   (0x0000, 0x1003): action_type_id})


def n_event_report_rq(msg_id, sop_class, sop_instance, event_type_id):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): N_EVENT_REPORT_RQ,
                   (0x0000, 0x0110): msg_id,
                   (0x0000, 0x0800): HAS_DATASET,
                   (0x0000, 0x1001): sop_instance,
                   (0x0000, 0x1002): event_type_id})


def n_event_report_rsp(msg_id, sop_class, sop_instance, event_type_id,
                       status=STATUS_SUCCESS):
    return _cmd({(0x0000, 0x0002): sop_class,
                   (0x0000, 0x0100): N_EVENT_REPORT_RSP,
                   (0x0000, 0x0120): msg_id,
                   (0x0000, 0x0800): NO_DATASET,
                   (0x0000, 0x0900): status,
                   (0x0000, 0x1001): sop_instance,
                   (0x0000, 0x1002): event_type_id})
