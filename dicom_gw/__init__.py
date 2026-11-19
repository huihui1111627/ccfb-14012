"""dicom_gw - a DICOM network gateway implemented with the Python standard library.

Modules:
    uid          well-known UIDs (transfer syntaxes, SOP classes)
    dataset      DICOM data set codec (implicit/explicit VR, LE/BE,
                 undefined-length SQ/items, private tag passthrough)
    pixel        pixel data codecs: native, RLE lossless, JPEG lossless (SV1)
    pdu          UL PDUs (A-ASSOCIATE, P-DATA-TF, A-RELEASE, A-ABORT, ...)
    dimse        DIMSE command/message codec (C-STORE, C-FIND, C-MOVE,
                 N-ACTION / N-EVENT-REPORT for storage commitment)
    association  association state machine: negotiation, fragmentation,
                 abort handling
    gateway      the gateway itself: SCP + SCU, store/query/retrieve,
                 storage commitment, transcoding router
    imggen       synthetic DICOM image generator (bit depths, frames,
                 private tags)
    web          monitoring UI (association log + transfer progress)
    acceptance   pairwise device-capability matrix acceptance run
"""

__version__ = "0.1.0"
