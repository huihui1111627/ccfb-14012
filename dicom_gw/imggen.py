"""Synthetic DICOM image generator for testing and acceptance runs.

Produces data sets with varied bit depths (8 / 12-in-16 / 16 signed),
single- and multi-frame, mono and RGB, private tags (including a private
sequence with undefined-length items) and both defined- and
undefined-length elements.
"""

import math
import random
import uuid

from . import dataset as D
from . import uid as U

_ROOT = "1.2.826.0.1.3680043.9.7433"


def new_uid():
    return "2.25.%d" % uuid.uuid4().int


def _pixels_mono(params, seed):
    """Deterministic gradient + texture pattern, values spanning the full
    stored range so transcoding is exercised at the extremes."""
    rng = random.Random(seed)
    rows, cols = params["rows"], params["cols"]
    bits_stored = params["bits_stored"]
    signed = params.get("signed", False)
    nframes = params.get("frames", 1)
    bps = 2 if bits_stored > 8 else 1
    lo = -(1 << (bits_stored - 1)) if signed else 0
    hi = (1 << (bits_stored - 1)) - 1 if signed else (1 << bits_stored) - 1
    frames = []
    for f in range(nframes):
        buf = bytearray()
        phase = rng.random() * math.pi
        for r in range(rows):
            for c in range(cols):
                v = int((math.sin(r * 0.11 + phase) * 0.5 + 0.5) *
                        (hi - lo) * 0.6)
                v += int((c * (hi - lo) * 0.4) // max(1, cols - 1))
                v += rng.randint(0, max(1, (hi - lo) // 64))
                v = max(lo, min(hi, v + lo))
                # two's complement within Bits Stored, high bits zero —
                # the canonical form every lossless codec preserves
                buf += (v & ((1 << bits_stored) - 1)).to_bytes(bps, "little")
        frames.append(bytes(buf))
    return frames


def _pixels_rgb(params, seed):
    rng = random.Random(seed)
    rows, cols = params["rows"], params["cols"]
    frames = []
    for f in range(params.get("frames", 1)):
        buf = bytearray()
        for r in range(rows):
            for c in range(cols):
                buf.append((r * 3 + f * 7) % 256)
                buf.append((c * 5 + rng.randint(0, 3)) % 256)
                buf.append((r + c) % 256)
        frames.append(bytes(buf))
    return frames


def make_image(sop_class=U.CTImageStorage, rows=64, cols=64, bits_stored=16,
               signed=False, frames=1, rgb=False, seed=42,
               with_private=True, patient="GATEWAY^TEST",
               study_uid=None, series_uid=None):
    """Build one image data set; returns (Dataset, frames) where frames are
    the canonical native frame bytes (ground truth for comparisons)."""
    params = {"rows": rows, "cols": cols, "bits_stored": bits_stored,
              "signed": signed, "frames": frames}
    bits_alloc = 8 if (bits_stored <= 8 and not rgb) else 16
    if rgb:
        bits_alloc = 8
        params["bits_stored"] = 8
    frame_list = _pixels_rgb(params, seed) if rgb else _pixels_mono(params, seed)

    ds = D.Dataset()
    study_uid = study_uid or new_uid()
    series_uid = series_uid or new_uid()
    ds.set(0x0008, 0x0016, "UI", sop_class)
    ds.set(0x0008, 0x0018, "UI", new_uid())
    ds.set(0x0008, 0x0008, "CS", ["ORIGINAL", "PRIMARY", "AXIAL"])
    ds.set(0x0008, 0x0020, "DA", "20261120")
    ds.set(0x0008, 0x0030, "TM", "103000")
    ds.set(0x0008, 0x0060, "CS", "CT" if sop_class == U.CTImageStorage
           else "MR" if sop_class == U.MRImageStorage else "OT")
    ds.set(0x0008, 0x1030, "LO", "Gateway acceptance study")
    ds.set(0x0008, 0x103E, "LO", "Synthetic series")
    ds.set(0x0010, 0x0010, "PN", patient)
    ds.set(0x0010, 0x0020, "LO", "GW%06d" % (seed % 1000000))
    ds.set(0x0010, 0x0030, "DA", "19700101")
    ds.set(0x0010, 0x0040, "CS", "O")
    ds.set(0x0020, 0x000D, "UI", study_uid)
    ds.set(0x0020, 0x000E, "UI", series_uid)
    ds.set(0x0020, 0x0010, "SH", "GW")
    ds.set(0x0020, 0x0011, "IS", 1)
    ds.set(0x0020, 0x0013, "IS", seed % 100)
    ds.set(0x0028, 0x0002, "US", 3 if rgb else 1)
    ds.set(0x0028, 0x0004, "CS", "RGB" if rgb else "MONOCHROME2")
    if rgb:
        ds.set(0x0028, 0x0006, "US", 0)
    if frames > 1:
        ds.set(0x0028, 0x0008, "IS", frames)
    ds.set(0x0028, 0x0010, "US", rows)
    ds.set(0x0028, 0x0011, "US", cols)
    ds.set(0x0028, 0x0030, "DS", [0.5, 0.5])
    ds.set(0x0028, 0x0100, "US", bits_alloc)
    ds.set(0x0028, 0x0101, "US", 8 if rgb else bits_stored)
    ds.set(0x0028, 0x0102, "US", (7 if rgb else bits_stored - 1))
    ds.set(0x0028, 0x0103, "US", 1 if signed else 0)
    ds.set(0x0028, 0x1050, "DS", 600)
    ds.set(0x0028, 0x1051, "DS", 1500)
    ds.set(0x0028, 0x1052, "DS", -1024 if signed else 0)
    ds.set(0x0028, 0x1053, "DS", 1)

    # a defined-length sequence (referenced image-ish) and an undefined-
    # length sequence with undefined-length items
    ref = D.Dataset()
    ref.set(0x0008, 0x1150, "UI", sop_class)
    ref.set(0x0008, 0x1155, "UI", new_uid())
    ds.set(0x0008, 0x1140, "SQ", [ref])       # ReferencedImageSequence

    if with_private:
        # odd group = private; unknown to the dictionary, must pass through
        ds.set(0x0011, 0x1001, "LO", "COUNTY_HOSPITAL")
        ds.set(0x0011, 0x1010, "UN", b"\xDE\xAD\xBE\xEF" * 3)
        priv_item = D.Dataset()
        priv_item.set(0x0011, 0x1002, "IS", 7)
        priv_item.set(0x0011, 0x1003, "UN", bytes(range(16)))
        ds.set(0x0011, 0x1004, "SQ", [priv_item])

    pix = b"".join(frame_list)
    ds.set(0x7FE0, 0x0010, "OW" if bits_alloc > 8 else "OB", pix)
    return ds, frame_list


def make_study(count=4, seed=100, study_uid=None, series_uid=None):
    """A small study mixing modalities, bit depths, frame counts, color."""
    study_uid = study_uid or new_uid()
    series_uid = series_uid or new_uid()
    specs = [
        dict(sop_class=U.CTImageStorage, rows=64, cols=64, bits_stored=16,
             signed=True, frames=1, seed=seed + 1),
        dict(sop_class=U.CTImageStorage, rows=48, cols=48, bits_stored=12,
             frames=5, seed=seed + 2),
        dict(sop_class=U.MRImageStorage, rows=64, cols=64, bits_stored=16,
             frames=3, seed=seed + 3),
        dict(sop_class=U.SecondaryCaptureImageStorage, rows=40, cols=56,
             bits_stored=8, frames=1, rgb=True, seed=seed + 4),
    ]
    out = []
    for spec in specs[:count]:
        ds, frames = make_image(study_uid=study_uid, series_uid=series_uid,
                                **spec)
        out.append((ds, frames))
    return out
