"""Well-known DICOM UIDs: transfer syntaxes, SOP classes, presentation contexts."""

# ---------------------------------------------------------------- transfer syntaxes
ImplicitVRLittleEndian = "1.2.840.10008.1.2"
ExplicitVRLittleEndian = "1.2.840.10008.1.2.1"
ExplicitVRBigEndian = "1.2.840.10008.1.2.2"          # retired but still seen in the wild
RLELossless = "1.2.840.10008.1.2.5"
JPEGLosslessSV1 = "1.2.840.10008.1.2.4.70"           # JPEG lossless, non-hierarchical, first-order

ALL_TRANSFER_SYNTAXES = [
    ImplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ExplicitVRBigEndian,
    RLELossless,
    JPEGLosslessSV1,
]

TS_NAMES = {
    ImplicitVRLittleEndian: "Implicit VR Little Endian",
    ExplicitVRLittleEndian: "Explicit VR Little Endian",
    ExplicitVRBigEndian: "Explicit VR Big Endian",
    RLELossless: "RLE Lossless",
    JPEGLosslessSV1: "JPEG Lossless (SV1)",
}

ENCAPSULATED = {RLELossless, JPEGLosslessSV1}

# ---------------------------------------------------------------- SOP classes
Verification = "1.2.840.10008.1.1"
CTImageStorage = "1.2.840.10008.5.1.4.1.1.2"
MRImageStorage = "1.2.840.10008.5.1.4.1.1.4"
SecondaryCaptureImageStorage = "1.2.840.10008.5.1.4.1.1.7"
StudyRootFind = "1.2.840.10008.5.1.4.1.2.2.1"
StudyRootMove = "1.2.840.10008.5.1.4.1.2.2.2"
PatientRootFind = "1.2.840.10008.5.1.4.1.2.1.1"
PatientRootMove = "1.2.840.10008.5.1.4.1.2.1.2"
StorageCommitmentPushModel = "1.2.840.10008.1.20.1"

STORAGE_SOP_CLASSES = [CTImageStorage, MRImageStorage, SecondaryCaptureImageStorage]

SOP_NAMES = {
    Verification: "Verification",
    CTImageStorage: "CT Image Storage",
    MRImageStorage: "MR Image Storage",
    SecondaryCaptureImageStorage: "Secondary Capture",
    StudyRootFind: "Study Root FIND",
    StudyRootMove: "Study Root MOVE",
    PatientRootFind: "Patient Root FIND",
    PatientRootMove: "Patient Root MOVE",
    StorageCommitmentPushModel: "Storage Commitment Push Model",
}

# ---------------------------------------------------------------- misc
ApplicationContext = "1.2.840.10008.3.1.1.1"
ImplementationClassUID = "1.2.826.0.1.3680043.9.7433.1.1"
ImplementationVersionName = "DICOMGW_001"


def ts_name(uid):
    return TS_NAMES.get(uid, uid)


def sop_name(uid):
    return SOP_NAMES.get(uid, uid)
