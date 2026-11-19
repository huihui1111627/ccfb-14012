"""DICOM data set codec.

Supports:
  * Implicit VR Little Endian, Explicit VR Little Endian, Explicit VR Big Endian
  * undefined-length sequences and items terminated by delimitation items
  * encapsulated (undefined-length OB) pixel data: value is a list of
    fragments, fragment[0] is the Basic Offset Table
  * unknown / private tags: kept as VR 'UN' raw bytes and passed through
    verbatim (implicit VR) or with their original VR (explicit VR)
"""

import struct
from io import BytesIO

from . import uid as U

# ---------------------------------------------------------------- tag helpers

def tag(group, elem):
    return (group << 16) | elem


def tag_group(t):
    return t >> 16


def tag_elem(t):
    return t & 0xFFFF


def tag_str(t):
    return "(%04X,%04X)" % (t >> 16, t & 0xFFFF)


ITEM_TAG = tag(0xFFFE, 0xE000)
ITEM_DELIM_TAG = tag(0xFFFE, 0xE00D)
SEQ_DELIM_TAG = tag(0xFFFE, 0xE0DD)

UNDEFINED_LENGTH = 0xFFFFFFFF

# ---------------------------------------------------------------- data dictionary (subset)
# tag -> (VR, keyword).  Only what the gateway itself needs; anything not
# listed here decodes as UN under implicit VR and is carried through as-is.

DICT = {
    # command group handled in dimse.py
    # file meta
    tag(0x0002, 0x0000): ("UL", "FileMetaInformationGroupLength"),
    tag(0x0002, 0x0001): ("OB", "FileMetaInformationVersion"),
    tag(0x0002, 0x0002): ("UI", "MediaStorageSOPClassUID"),
    tag(0x0002, 0x0003): ("UI", "MediaStorageSOPInstanceUID"),
    tag(0x0002, 0x0010): ("UI", "TransferSyntaxUID"),
    tag(0x0002, 0x0012): ("UI", "ImplementationClassUID"),
    tag(0x0002, 0x0013): ("SH", "ImplementationVersionName"),
    # patient / study
    tag(0x0008, 0x0005): ("CS", "SpecificCharacterSet"),
    tag(0x0008, 0x0008): ("CS", "ImageType"),
    tag(0x0008, 0x0016): ("UI", "SOPClassUID"),
    tag(0x0008, 0x0018): ("UI", "SOPInstanceUID"),
    tag(0x0008, 0x0020): ("DA", "StudyDate"),
    tag(0x0008, 0x0030): ("TM", "StudyTime"),
    tag(0x0008, 0x0050): ("SH", "AccessionNumber"),
    tag(0x0008, 0x0060): ("CS", "Modality"),
    tag(0x0008, 0x1030): ("LO", "StudyDescription"),
    tag(0x0008, 0x103E): ("LO", "SeriesDescription"),
    tag(0x0010, 0x0010): ("PN", "PatientName"),
    tag(0x0010, 0x0020): ("LO", "PatientID"),
    tag(0x0010, 0x0030): ("DA", "PatientBirthDate"),
    tag(0x0010, 0x0040): ("CS", "PatientSex"),
    tag(0x0018, 0x0050): ("DS", "SliceThickness"),
    tag(0x0018, 0x0088): ("DS", "SpacingBetweenSlices"),
    tag(0x0020, 0x000D): ("UI", "StudyInstanceUID"),
    tag(0x0020, 0x000E): ("UI", "SeriesInstanceUID"),
    tag(0x0020, 0x0010): ("SH", "StudyID"),
    tag(0x0020, 0x0011): ("IS", "SeriesNumber"),
    tag(0x0020, 0x0013): ("IS", "InstanceNumber"),
    tag(0x0020, 0x0032): ("DS", "ImagePositionPatient"),
    tag(0x0020, 0x0037): ("DS", "ImageOrientationPatient"),
    tag(0x0020, 0x0052): ("UI", "FrameOfReferenceUID"),
    # query keys
    tag(0x0008, 0x0052): ("CS", "QueryRetrieveLevel"),
    # image pixel module
    tag(0x0028, 0x0002): ("US", "SamplesPerPixel"),
    tag(0x0028, 0x0004): ("CS", "PhotometricInterpretation"),
    tag(0x0028, 0x0006): ("US", "PlanarConfiguration"),
    tag(0x0028, 0x0008): ("IS", "NumberOfFrames"),
    tag(0x0028, 0x0010): ("US", "Rows"),
    tag(0x0028, 0x0011): ("US", "Columns"),
    tag(0x0028, 0x0030): ("DS", "PixelSpacing"),
    tag(0x0028, 0x0100): ("US", "BitsAllocated"),
    tag(0x0028, 0x0101): ("US", "BitsStored"),
    tag(0x0028, 0x0102): ("US", "HighBit"),
    tag(0x0028, 0x0103): ("US", "PixelRepresentation"),
    tag(0x0028, 0x1050): ("DS", "WindowCenter"),
    tag(0x0028, 0x1051): ("DS", "WindowWidth"),
    tag(0x0028, 0x1052): ("DS", "RescaleIntercept"),
    tag(0x0028, 0x1053): ("DS", "RescaleSlope"),
    tag(0x0028, 0x1054): ("LO", "RescaleType"),
    tag(0x0008, 0x1140): ("SQ", "ReferencedImageSequence"),
    tag(0x7FE0, 0x0010): ("OB", "PixelData"),
    # storage commitment
    tag(0x0008, 0x1195): ("UI", "TransactionUID"),
    tag(0x0008, 0x1198): ("SQ", "FailedSOPSequence"),
    tag(0x0008, 0x1199): ("SQ", "ReferencedSOPSequence"),
    tag(0x0008, 0x1150): ("UI", "ReferencedSOPClassUID"),
    tag(0x0008, 0x1155): ("UI", "ReferencedSOPInstanceUID"),
    tag(0x0008, 0x1156): ("CS", "FailureReason"),
}

# VRs that use a 32-bit length with 2 reserved bytes in explicit VR
LONG_VRS = {"OB", "OD", "OF", "OL", "OW", "SQ", "UC", "UR", "UT", "UN"}

STRING_VRS = {"AE", "AS", "CS", "DA", "DS", "DT", "IS", "LO", "LT", "PN",
              "SH", "ST", "TM", "UC", "UI", "UR", "UT"}

_STRUCT_FMT = {
    "US": "H", "SS": "h", "UL": "L", "SL": "l",
    "FL": "f", "FD": "d", "AT": "L",
}
_STRUCT_SIZE = {"US": 2, "SS": 2, "UL": 4, "SL": 4, "FL": 4, "FD": 8, "AT": 4,
                "OB": 1, "OD": 8, "OF": 4, "OL": 4, "OW": 2, "UN": 1}


class RawUNItems(object):
    """Raw content of an undefined-length UN element that is not pixel data
    (typically a private sequence under implicit VR): a list of raw item
    contents, re-encoded verbatim with delimitation items."""

    __slots__ = ("items",)

    def __init__(self, items):
        self.items = items

    def __eq__(self, other):
        return isinstance(other, RawUNItems) and self.items == other.items

    def __repr__(self):
        return "<RawUNItems %d item(s)>" % len(self.items)


class DataElement(object):
    __slots__ = ("tag", "VR", "value")

    def __init__(self, tag_, vr, value):
        self.tag = tag_
        self.VR = vr
        self.value = value

    def __repr__(self):
        kw = DICT.get(self.tag, (None, None))[1] or ""
        v = self.value
        if isinstance(v, bytes) and len(v) > 32:
            v = "<%d bytes>" % len(v)
        if isinstance(v, list) and self.VR == "SQ":
            v = "<%d item(s)>" % len(v)
        return "%s %s %s = %r" % (tag_str(self.tag), self.VR, kw, v)


class Dataset(object):
    """Ordered collection of data elements keyed by 32-bit tag."""

    def __init__(self):
        self.elements = {}   # tag -> DataElement (insertion ordered)

    def __contains__(self, t):
        return t in self.elements

    def __getitem__(self, t):
        return self.elements[t]

    def __setitem__(self, t, elem):
        self.elements[t] = elem

    def __iter__(self):
        return iter(self.elements.values())

    def __len__(self):
        return len(self.elements)

    def set(self, group, elem, vr, value):
        t = tag(group, elem)
        self.elements[t] = DataElement(t, vr, value)
        return self

    def get(self, group, elem, default=None):
        e = self.elements.get(tag(group, elem))
        return e.value if e is not None else default

    def keyword(self, kw, default=None):
        for t, (vr, k) in DICT.items():
            if k == kw:
                e = self.elements.get(t)
                return e.value if e is not None else default
        return default

    def tags(self):
        return sorted(self.elements)


# ---------------------------------------------------------------- value codecs

def _decode_string(raw):
    return raw.decode("latin-1").rstrip(" \x00")


def _encode_string(value):
    if isinstance(value, (list, tuple)):
        s = "\\".join(str(v) for v in value)
    else:
        s = str(value)
    raw = s.encode("latin-1")
    if len(raw) % 2:
        raw += b" "
    return raw


def decode_value(vr, raw, little_endian):
    if vr in STRING_VRS:
        s = _decode_string(raw)
        if "\\" in s:
            return s.split("\\")
        return s
    if vr in _STRUCT_FMT:
        fmt = _STRUCT_FMT[vr]
        size = _STRUCT_SIZE[vr]
        n = len(raw) // size
        if n == 0:
            return 0 if vr != "AT" else []
        vals = list(struct.unpack(("<" if little_endian else ">") + fmt * n,
                                  raw[:n * size]))
        return vals[0] if n == 1 else vals
    # OB OW OF UN SQ etc: raw bytes (SQ handled separately)
    return raw


def encode_value(vr, value, little_endian):
    if vr in STRING_VRS:
        return _encode_string(value)
    if vr in _STRUCT_FMT:
        fmt = _STRUCT_FMT[vr]
        vals = value if isinstance(value, (list, tuple)) else [value]
        raw = struct.pack(("<" if little_endian else ">") + fmt * len(vals), *vals)
        if len(raw) % 2:
            raw += b"\x00"
        return raw
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, bytearray):
        raw = bytes(value)
    else:
        raise TypeError("cannot encode VR %s value of type %s" % (vr, type(value)))
    if len(raw) % 2:
        raw += b"\x00"
    return raw


# ---------------------------------------------------------------- reading

class _Reader(object):
    def __init__(self, fp, explicit, little_endian):
        self.fp = fp
        self.explicit = explicit
        self.endian = "<" if little_endian else ">"

    def read(self, n):
        data = self.fp.read(n)
        if len(data) != n:
            raise EOFError("unexpected end of stream (wanted %d, got %d)"
                           % (n, len(data)))
        return data

    def read_tag(self):
        g, e = struct.unpack(self.endian + "HH", self.read(4))
        return tag(g, e)

    def read_element(self):
        """Read one element. Returns (DataElement-ish tuple) or None at EOF."""
        hdr = self.fp.read(4)
        if not hdr:
            return None
        if len(hdr) != 4:
            raise EOFError("truncated tag")
        g, e = struct.unpack(self.endian + "HH", hdr)
        t = tag(g, e)
        if t in (ITEM_TAG, ITEM_DELIM_TAG, SEQ_DELIM_TAG):
            length = struct.unpack(self.endian + "L", self.read(4))[0]
            return (t, None, length)          # delimiter / item header
        if self.explicit:
            vr = self.read(2).decode("ascii")
            if vr in LONG_VRS:
                self.read(2)                   # reserved
                length = struct.unpack(self.endian + "L", self.read(4))[0]
            else:
                length = struct.unpack(self.endian + "H", self.read(2))[0]
        else:
            vr = DICT.get(t, ("UN", ""))[0]
            length = struct.unpack(self.endian + "L", self.read(4))[0]
        return (t, vr, length)

    def read_value(self, vr, length, elem_tag=None):
        if vr == "SQ":
            return self.read_sequence(length)
        if length == UNDEFINED_LENGTH:
            if vr == "UN" and elem_tag != tag(0x7FE0, 0x0010):
                # unknown element with undefined length (e.g. a private
                # sequence under implicit VR): try to parse it as a
                # sequence of items in the current encoding; if that fails
                # fall back to delimiter-scanning raw passthrough
                return self.read_un_undefined()
            # encapsulated data (e.g. compressed pixel data)
            fragments = []
            while True:
                t, _, ilen = self.read_element()
                if t == SEQ_DELIM_TAG:
                    break
                if t != ITEM_TAG:
                    raise ValueError("expected item in encapsulated data, got %s"
                                     % tag_str(t))
                fragments.append(self.read(ilen))
            return fragments
        raw = self.read(length)
        return decode_value(vr, raw, self.endian == "<")

    def read_un_undefined(self):
        """Read an undefined-length UN element.  Returns a list of Datasets
        when the content parses as a sequence (the usual case for private
        tags), otherwise RawUNItems with opaque raw item contents."""
        pos = self.fp.tell()
        try:
            items = []
            while True:
                t, _, ilen = self.read_element()
                if t == SEQ_DELIM_TAG:
                    break
                if t != ITEM_TAG:
                    raise ValueError("not an item header")
                items.append(self.read_item(ilen))
            return items
        except (ValueError, EOFError, struct.error):
            self.fp.seek(pos)
            return RawUNItems(self.read_raw_items_scanned())

    def read_raw_items_scanned(self):
        """Fallback: read items of an undefined-length UN element as opaque
        bytes, locating item ends by scanning for delimitation items.
        (Limitation: a nested undefined-length item inside opaque content
        would end the scan early — only used when sequence parsing failed.)"""
        items = []
        delim = struct.pack(self.endian + "HHL", 0xFFFE, 0xE00D, 0)
        while True:
            hdr = self.read(8)
            t = struct.unpack(self.endian + "HH", hdr[:4])
            t = tag(t[0], t[1])
            ilen = struct.unpack(self.endian + "L", hdr[4:])[0]
            if t == SEQ_DELIM_TAG:
                break
            if t != ITEM_TAG:
                raise ValueError("expected item, got %s" % tag_str(t))
            if ilen != UNDEFINED_LENGTH:
                items.append(self.read(ilen))
                continue
            buf = bytearray()
            while not buf.endswith(delim):
                b = self.fp.read(1)
                if not b:
                    raise EOFError("unterminated undefined-length item")
                buf += b
            items.append(bytes(buf[:-8]))
        return items

    def read_sequence(self, length):
        items = []
        if length == UNDEFINED_LENGTH:
            while True:
                t, _, ilen = self.read_element()
                if t == SEQ_DELIM_TAG:
                    break
                items.append(self.read_item(ilen))
        else:
            end = self.fp.tell() + length
            while self.fp.tell() < end:
                t, _, ilen = self.read_element()
                items.append(self.read_item(ilen))
        return items

    def read_item(self, length):
        ds = Dataset()
        if length == UNDEFINED_LENGTH:
            while True:
                rec = self.read_element()
                if rec[0] == ITEM_DELIM_TAG:
                    break
                t, vr, vlen = rec
                ds.elements[t] = DataElement(t, vr, self.read_value(vr, vlen, t))
        else:
            end = self.fp.tell() + length
            while self.fp.tell() < end:
                t, vr, vlen = self.read_element()
                ds.elements[t] = DataElement(t, vr, self.read_value(vr, vlen, t))
        return ds

    def read_dataset(self):
        ds = Dataset()
        while True:
            rec = self.read_element()
            if rec is None:
                break
            t, vr, vlen = rec
            if t in (ITEM_DELIM_TAG, SEQ_DELIM_TAG):
                break
            ds.elements[t] = DataElement(t, vr, self.read_value(vr, vlen, t))
        return ds


def read_dataset(data, transfer_syntax=U.ExplicitVRLittleEndian):
    """Decode a data set from bytes or a file object."""
    if isinstance(data, (bytes, bytearray)):
        fp = BytesIO(bytes(data))
    else:
        fp = data
    explicit = transfer_syntax != U.ImplicitVRLittleEndian
    little = transfer_syntax != U.ExplicitVRBigEndian
    return _Reader(fp, explicit, little).read_dataset()


# ---------------------------------------------------------------- writing

class _Writer(object):
    def __init__(self, explicit, little_endian):
        self.explicit = explicit
        self.endian = "<" if little_endian else ">"
        self.out = BytesIO()

    def write_element(self, elem):
        t = elem.tag
        self.out.write(struct.pack(self.endian + "HH", t >> 16, t & 0xFFFF))
        vr = elem.VR
        if self.explicit:
            if vr in LONG_VRS:
                self.out.write(vr.encode("ascii") + b"\x00\x00")
                len_pos = self.out.tell()
                self.out.write(b"\xFF\xFF\xFF\xFF")
                if not self._write_value(elem):
                    self._patch_length(len_pos)
            else:
                self.out.write(vr.encode("ascii"))
                len_pos = self.out.tell()
                self.out.write(b"\xFF\xFF")
                self._write_value(elem)
                self._patch_length(len_pos, short=True)
        else:
            len_pos = self.out.tell()
            self.out.write(b"\xFF\xFF\xFF\xFF")
            if not self._write_value(elem):
                self._patch_length(len_pos)

    def _patch_length(self, pos, short=False):
        end = self.out.tell()
        self.out.seek(pos)
        if short:
            self.out.write(struct.pack(self.endian + "H", end - pos - 2))
        else:
            self.out.write(struct.pack(self.endian + "L", end - pos - 4))
        self.out.seek(end)

    def _write_value(self, elem):
        """Write the value; returns True if an undefined length was used
        (caller must not patch the length field)."""
        vr, value = elem.VR, elem.value
        if isinstance(value, RawUNItems):
            self._write_raw_un(value)
            return True
        if vr == "SQ" or (vr == "UN" and isinstance(value, list) and value
                          and isinstance(value[0], Dataset)):
            if not value:
                return False               # empty sequence, zero length
            self._write_undefined_sequence(value)
            return True
        if isinstance(value, list) and vr in ("OB", "OW", "UN") and value \
                and all(isinstance(f, (bytes, bytearray)) for f in value) \
                and elem.tag == tag(0x7FE0, 0x0010):
            self._write_encapsulated(value)
            return True
        self.out.write(encode_value(vr, value, self.endian == "<"))
        return False

    def _write_raw_un(self, value):
        # rewind the length placeholder and mark undefined
        pos = self.out.tell() - 4
        self.out.seek(pos)
        self.out.write(struct.pack(self.endian + "L", UNDEFINED_LENGTH))
        self.out.seek(0, 2)
        for raw in value.items:
            self.out.write(struct.pack(self.endian + "HHL",
                                       0xFFFE, 0xE000, UNDEFINED_LENGTH))
            self.out.write(raw)
            self.out.write(struct.pack(self.endian + "HHL",
                                       0xFFFE, 0xE00D, 0))
        self.out.write(struct.pack(self.endian + "HHL", 0xFFFE, 0xE0DD, 0))

    def _write_undefined_sequence(self, items):
        # rewind the length we already wrote and mark undefined
        pos = self.out.tell() - 4
        self.out.seek(pos)
        self.out.write(struct.pack(self.endian + "L", UNDEFINED_LENGTH))
        self.out.seek(0, 2)
        for item in items:
            self.out.write(struct.pack(self.endian + "HHL",
                                       0xFFFE, 0xE000, UNDEFINED_LENGTH))
            for el in item:
                self.write_element(el)
            self.out.write(struct.pack(self.endian + "HHL",
                                       0xFFFE, 0xE00D, 0))
        self.out.write(struct.pack(self.endian + "HHL", 0xFFFE, 0xE0DD, 0))

    def _write_encapsulated(self, fragments):
        pos = self.out.tell() - 4
        self.out.seek(pos)
        self.out.write(struct.pack(self.endian + "L", UNDEFINED_LENGTH))
        self.out.seek(0, 2)
        for frag in fragments:
            self.out.write(struct.pack(self.endian + "HHL",
                                       0xFFFE, 0xE000, len(frag)))
            self.out.write(bytes(frag))
        self.out.write(struct.pack(self.endian + "HHL", 0xFFFE, 0xE0DD, 0))

    def write_dataset(self, ds):
        for elem in ds:
            self.write_element(elem)
        return self.out.getvalue()


def write_dataset(ds, transfer_syntax=U.ExplicitVRLittleEndian):
    explicit = transfer_syntax != U.ImplicitVRLittleEndian
    little = transfer_syntax != U.ExplicitVRBigEndian
    return _Writer(explicit, little).write_dataset(ds)


# ---------------------------------------------------------------- DICOM files

def write_file(ds, transfer_syntax=U.ExplicitVRLittleEndian):
    """Serialise a data set as a Part-10 file (preamble + meta + data set)."""
    meta = Dataset()
    sop_class = ds.get(0x0008, 0x0016, "")
    sop_inst = ds.get(0x0008, 0x0018, "")
    body = write_dataset(ds, transfer_syntax)
    meta.set(0x0002, 0x0001, "OB", b"\x00\x01")
    meta.set(0x0002, 0x0002, "UI", sop_class)
    meta.set(0x0002, 0x0003, "UI", sop_inst)
    meta.set(0x0002, 0x0010, "UI", transfer_syntax)
    meta.set(0x0002, 0x0012, "UI", U.ImplementationClassUID)
    meta.set(0x0002, 0x0013, "SH", U.ImplementationVersionName)
    meta_body = write_dataset(meta, U.ExplicitVRLittleEndian)
    full = Dataset()
    full.set(0x0002, 0x0000, "UL", len(meta_body))
    for elem in meta:
        full.elements[elem.tag] = elem
    meta_full = write_dataset(full, U.ExplicitVRLittleEndian)
    return b"\x00" * 128 + b"DICM" + meta_full + body


def read_file(data):
    """Parse a Part-10 file; returns (meta Dataset, data set Dataset, ts uid)."""
    fp = BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    fp.seek(128)
    magic = fp.read(4)
    if magic != b"DICM":
        raise ValueError("not a DICOM Part-10 file")
    r = _Reader(fp, explicit=True, little_endian=True)
    meta = Dataset()
    first = r.read_element()
    if first[0] != tag(0x0002, 0x0000):
        raise ValueError("missing file meta group length")
    glen = r.read_value(first[1], first[2], first[0])
    end = fp.tell() + glen
    while fp.tell() < end:
        t, vr, vlen = r.read_element()
        meta.elements[t] = DataElement(t, vr, r.read_value(vr, vlen, t))
    ts = meta.get(0x0002, 0x0010, U.ExplicitVRLittleEndian)
    ds = read_dataset(fp.read(), ts)
    return meta, ds, ts
