"""Pixel data codecs: native (uncompressed), RLE Lossless, JPEG Lossless (SV1).

Canonical form inside the gateway is *native* frame bytes: a list of frames,
each frame the interleaved pixel bytes exactly as an uncompressed DICOM data
set would carry them.  Transcoding between any two transfer syntaxes is
decode-to-canonical followed by encode; both lossless codecs here are exact,
so pixels compare equal bit for bit afterwards.
"""

import struct
from heapq import heappush, heappop

from . import uid as U


class Params(object):
    """Image pixel parameters needed by the codecs."""

    def __init__(self, rows, cols, spp=1, bits_alloc=16, bits_stored=16,
                 pixrep=0, planar=0, nframes=1):
        self.rows = rows
        self.cols = cols
        self.spp = spp
        self.bits_alloc = bits_alloc
        self.bits_stored = bits_stored
        self.pixrep = pixrep
        self.planar = planar
        self.nframes = nframes

    @property
    def bytes_per_sample(self):
        return self.bits_alloc // 8

    @property
    def frame_size(self):
        return self.rows * self.cols * self.spp * self.bytes_per_sample

    @classmethod
    def from_dataset(cls, ds):
        nf = ds.get(0x0028, 0x0008, None)
        return cls(
            rows=ds.get(0x0028, 0x0010),
            cols=ds.get(0x0028, 0x0011),
            spp=ds.get(0x0028, 0x0002, 1),
            bits_alloc=ds.get(0x0028, 0x0100, 16),
            bits_stored=ds.get(0x0028, 0x0101, 16),
            pixrep=ds.get(0x0028, 0x0103, 0),
            planar=ds.get(0x0028, 0x0006, 0),
            nframes=int(nf) if nf else 1,
        )


# ---------------------------------------------------------------- native

def _native_decode(value, params):
    if isinstance(value, list):          # encapsulated but syntax says native?
        raise ValueError("encapsulated pixel data in native transfer syntax")
    expected = params.frame_size * params.nframes
    if len(value) < expected:
        raise ValueError("pixel data too short: %d < %d" % (len(value), expected))
    return [value[i * params.frame_size:(i + 1) * params.frame_size]
            for i in range(params.nframes)]


def _native_encode(frames, params):
    return b"".join(frames)


# ---------------------------------------------------------------- RLE lossless

def _packbits_decode(data):
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        i += 1
        if b <= 127:
            out += data[i:i + b + 1]
            i += b + 1
        elif b >= 129:
            out += data[i:i + 1] * (257 - b)
            i += 1
        # 128 = noop
    return bytes(out)


def _packbits_encode(data):
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        # look for a run of >= 3 identical bytes
        run = 1
        while i + run < n and data[i + run] == data[i] and run < 128:
            run += 1
        if run >= 3:
            out.append(257 - run)
            out.append(data[i])
            i += run
            continue
        # literal run until a run of 3, or exactly 128 bytes (a literal
        # header of 128 would read as PackBits noop, so never exceed it)
        start = i
        i += run
        while i < n and i - start < 128:
            r = 1
            while i + r < n and data[i + r] == data[i] and r < 128:
                r += 1
            if r >= 3 or i + r - start > 128:
                break
            i += r
        lit = data[start:i]
        out.append(len(lit) - 1)
        out += lit
    return bytes(out)


def _rle_planes(frame, params):
    """Split one native frame into RLE byte planes (per sample, MSB first)."""
    bps = params.bytes_per_sample
    spp = params.spp
    npix = params.rows * params.cols
    planes = []
    if bps == 1:
        if spp == 1:
            planes.append(frame)
        else:
            for c in range(spp):
                planes.append(bytes(frame[p * spp + c] for p in range(npix)))
    else:
        for c in range(spp):
            for byte_idx in range(bps - 1, -1, -1):
                planes.append(bytes(
                    frame[(p * spp + c) * bps + byte_idx] for p in range(npix)))
    return planes


def _rle_merge(planes, params):
    """Inverse of _rle_planes: rebuild one native frame from byte planes."""
    bps = params.bytes_per_sample
    spp = params.spp
    npix = params.rows * params.cols
    out = bytearray(npix * spp * bps)
    idx = 0
    for c in range(spp):
        for byte_idx in range(bps - 1, -1, -1):
            plane = planes[idx]
            idx += 1
            for p in range(npix):
                out[(p * spp + c) * bps + byte_idx] = plane[p]
    return bytes(out)


def _rle_decode(fragments, params):
    # fragments[0] = basic offset table, then one fragment per frame
    frames = []
    nseg = params.spp * params.bytes_per_sample
    for frag in fragments[1:]:
        (count,) = struct.unpack("<L", frag[:4])
        offsets = struct.unpack("<%dL" % count, frag[4:4 + 4 * count])
        if count != nseg:
            raise ValueError("RLE segment count %d != expected %d"
                             % (count, nseg))
        planes = []
        expected = params.rows * params.cols
        for s in range(count):
            start = offsets[s]
            end = offsets[s + 1] if s + 1 < count else len(frag)
            plane = _packbits_decode(frag[start:end])
            planes.append(plane[:expected])   # drop any even-length pad byte
        frames.append(_rle_merge(planes, params))
    return frames


def _rle_encode(frames, params):
    fragments = [b""]                        # empty basic offset table
    for frame in frames:
        planes = [_packbits_encode(p) for p in _rle_planes(frame, params)]
        # segment header: 16 little-endian uint32 (count + up to 15 offsets)
        offsets = []
        pos = 64
        for pl in planes:
            offsets.append(pos)
            pos += len(pl)
        seg = struct.pack("<L", len(planes))
        seg += struct.pack("<%dL" % len(planes), *offsets)
        seg += b"\x00" * (64 - len(seg))
        seg += b"".join(planes)
        if len(seg) % 2:
            seg += b"\x00"
        fragments.append(seg)
    return fragments


# ---------------------------------------------------------------- JPEG lossless (ISO 10918-1, process 14, SV1)

def _category(v):
    return abs(v).bit_length()


class _HuffmanTable(object):
    def __init__(self, lengths):
        # lengths: dict symbol -> code length; canonical code assignment
        self.lengths = lengths
        self.codes = {}
        code = 0
        prev_len = 0
        for length, sym in sorted((l, s) for s, l in lengths.items()):
            code <<= (length - prev_len)
            self.codes[sym] = (code, length)
            code += 1
            prev_len = length
        # decode map: (length, code) -> symbol
        self.dec = {(l, c): s for s, (c, l) in self.codes.items()}
        self.max_len = max(lengths.values()) if lengths else 0

    @classmethod
    def from_frequencies(cls, freqs):
        """Build Huffman code lengths from a symbol->count histogram."""
        items = [(s, f) for s, f in freqs.items() if f > 0]
        if not items:
            return cls({0: 1})
        if len(items) == 1:
            return cls({items[0][0]: 1})
        heap = []
        uid_c = 0
        for s, f in items:
            heappush(heap, (f, uid_c, ("leaf", s)))
            uid_c += 1
        while len(heap) > 1:
            f1, _, n1 = heappop(heap)
            f2, _, n2 = heappop(heap)
            heappush(heap, (f1 + f2, uid_c, ("node", n1, n2)))
            uid_c += 1
        lengths = {}

        def walk(node, depth):
            if node[0] == "leaf":
                lengths[node[1]] = max(1, depth)
            else:
                walk(node[1], depth + 1)
                walk(node[2], depth + 1)

        walk(heap[0][2], 0)
        if max(lengths.values()) > 16:
            # pathological histogram: fall back to a flat (incomplete) code,
            # which is still decodable by any table-driven decoder
            import math
            n = len(lengths)
            flat = max(1, math.ceil(math.log(n, 2)))
            lengths = {s: flat for s in lengths}
        return cls(lengths)

    def to_dht(self):
        counts = [0] * 16
        for l in self.lengths.values():
            counts[l - 1] += 1
        symbols = [s for l, s in sorted((l, s) for s, l in self.lengths.items())]
        return bytes(counts) + bytes(symbols)

    @classmethod
    def from_dht(cls, payload):
        counts = payload[:16]
        symbols = payload[16:]
        lengths = {}
        i = 0
        for l in range(1, 17):
            for _ in range(counts[l - 1]):
                lengths[symbols[i]] = l
                i += 1
        return cls(lengths)


class _BitWriter(object):
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.nbits = 0

    def write(self, value, nbits):
        if nbits == 0:
            return
        self.acc = (self.acc << nbits) | (value & ((1 << nbits) - 1))
        self.nbits += nbits
        while self.nbits >= 8:
            b = (self.acc >> (self.nbits - 8)) & 0xFF
            self.out.append(b)
            if b == 0xFF:
                self.out.append(0x00)        # byte stuffing
            self.nbits -= 8
        self.acc &= (1 << self.nbits) - 1

    def finish(self):
        if self.nbits:
            pad = 8 - self.nbits
            self.write((1 << pad) - 1, pad)  # pad with 1-bits
        return bytes(self.out)


class _BitReader(object):
    def __init__(self, data):
        # remove byte stuffing
        out = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            out.append(b)
            if b == 0xFF and i + 1 < len(data) and data[i + 1] == 0x00:
                i += 1
            i += 1
        self.data = bytes(out)
        self.pos = 0

    def read(self, nbits):
        v = 0
        for _ in range(nbits):
            byte = self.data[self.pos >> 3]
            bit = (byte >> (7 - (self.pos & 7))) & 1
            v = (v << 1) | bit
            self.pos += 1
        return v

    def decode_symbol(self, table):
        code = 0
        for length in range(1, table.max_len + 1):
            code = (code << 1) | self.read(1)
            sym = table.dec.get((length, code))
            if sym is not None:
                return sym
        raise ValueError("invalid Huffman code in scan")


def _jpeg_lossless_encode_frame(frame, params):
    """Encode one frame as a JPEG lossless (SOF3, predictor 1) stream."""
    P = params.bits_stored
    spp = params.spp
    bps = params.bytes_per_sample
    rows, cols = params.rows, params.cols
    npix = rows * cols

    # deinterleave into per-component sample lists
    comps = []
    for c in range(spp):
        plane = [0] * npix
        if bps == 1:
            for p in range(npix):
                plane[p] = frame[p * spp + c]
        else:
            for p in range(npix):
                o = (p * spp + c) * bps
                plane[p] = int.from_bytes(frame[o:o + bps], "little")
        comps.append(plane)

    init_pred = 1 << (P - 1)
    mod = 1 << P
    half = 1 << (P - 1)

    def diffs():
        """Yield prediction differences in interleaved MCU order."""
        prevs = [init_pred] * spp
        for i in range(npix):
            if i % cols == 0:
                prevs = [init_pred] * spp
            for c in range(spp):
                x = comps[c][i]
                d = x - prevs[c]
                if d >= half:
                    d -= mod
                elif d < -half:
                    d += mod
                prevs[c] = x
                yield d

    # first pass: histogram of difference categories
    freqs = {}
    for d in diffs():
        cat = _category(d)
        freqs[cat] = freqs.get(cat, 0) + 1
    table = _HuffmanTable.from_frequencies(freqs)

    # second pass: emit
    bw = _BitWriter()
    for d in diffs():
        cat = _category(d)
        code, length = table.codes[cat]
        bw.write(code, length)
        if cat:
            bw.write(d if d >= 0 else d + (1 << cat) - 1, cat)
    scan = bw.finish()

    out = bytearray()
    out += b"\xFF\xD8"                                     # SOI
    dht = table.to_dht()
    out += b"\xFF\xC4" + struct.pack(">H", 2 + 1 + len(dht)) + b"\x00" + dht
    sof = struct.pack(">BHHB", P, rows, cols, spp)
    for c in range(spp):
        sof += struct.pack(">BBB", c + 1, 0x11, 0)
    out += b"\xFF\xC3" + struct.pack(">H", 2 + len(sof)) + sof
    sos = struct.pack(">B", spp)
    for c in range(spp):
        sos += struct.pack(">BB", c + 1, 0x00)
    sos += struct.pack(">BBB", 1, 0, 0)                    # predictor 1, Pt=0
    out += b"\xFF\xDA" + struct.pack(">H", 2 + len(sos)) + sos
    out += scan
    out += b"\xFF\xD9"                                     # EOI
    return bytes(out)


def _jpeg_lossless_decode_frame(data, params):
    """Decode a JPEG lossless stream produced by _jpeg_lossless_encode_frame."""
    if data[:2] != b"\xFF\xD8":
        raise ValueError("not a JPEG stream")
    pos = 2
    table = None
    P = rows = cols = spp = None
    scan = None
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError("expected marker at offset %d" % pos)
        marker = data[pos + 1]
        pos += 2
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:                                 # EOI
            break
        (seglen,) = struct.unpack(">H", data[pos:pos + 2])
        payload = data[pos + 2:pos + seglen]
        pos += seglen
        if marker == 0xC4:                                 # DHT
            table = _HuffmanTable.from_dht(payload[1:])
        elif marker == 0xC3:                               # SOF3 lossless
            P, rows, cols, spp = struct.unpack(">BHHB", payload[:6])
        elif marker == 0xDA:                               # SOS: rest is scan
            # scan data runs to the EOI marker
            end = len(data) - 2
            scan = data[pos:end]
            break
        # other markers (APPn, COM, DRI, ...) ignored
    if table is None or scan is None or P is None:
        raise ValueError("incomplete JPEG lossless stream")

    npix = rows * cols
    comps = [[0] * npix for _ in range(spp)]
    br = _BitReader(scan)
    init_pred = 1 << (P - 1)
    mod = 1 << P
    half = 1 << (P - 1)
    prevs = [init_pred] * spp
    for i in range(npix):
        if i % cols == 0:
            prevs = [init_pred] * spp
        for c in range(spp):
            cat = br.decode_symbol(table)
            if cat == 0:
                d = 0
            else:
                r = br.read(cat)
                d = r if r >= (1 << (cat - 1)) else r - (1 << cat) + 1
            x = (prevs[c] + d) % mod
            comps[c][i] = x
            prevs[c] = x

    bps = params.bytes_per_sample
    out = bytearray(npix * spp * bps)
    for c in range(spp):
        plane = comps[c]
        if bps == 1:
            for p in range(npix):
                out[p * spp + c] = plane[p] & 0xFF
        else:
            for p in range(npix):
                o = (p * spp + c) * bps
                out[o:o + bps] = plane[p].to_bytes(bps, "little")
    return bytes(out)


def _jpeg_decode(fragments, params):
    return [_jpeg_lossless_decode_frame(f, params) for f in fragments[1:]]


def _jpeg_encode(frames, params):
    return [b""] + [_jpeg_lossless_encode_frame(f, params) for f in frames]


# ---------------------------------------------------------------- dispatch

_DECODERS = {
    U.ImplicitVRLittleEndian: _native_decode,
    U.ExplicitVRLittleEndian: _native_decode,
    U.ExplicitVRBigEndian: None,          # handled specially (byte swap)
    U.RLELossless: _rle_decode,
    U.JPEGLosslessSV1: _jpeg_decode,
}

_ENCODERS = {
    U.ImplicitVRLittleEndian: _native_encode,
    U.ExplicitVRLittleEndian: _native_encode,
    U.ExplicitVRBigEndian: None,
    U.RLELossless: _rle_encode,
    U.JPEGLosslessSV1: _jpeg_encode,
}


def _swap16(data):
    out = bytearray(len(data))
    out[0::2] = data[1::2]
    out[1::2] = data[0::2]
    return bytes(out)


def decode_frames(pixel_value, params, transfer_syntax):
    """Decode pixel element value to a list of native (little-endian,
    interleaved) frame byte strings."""
    if transfer_syntax == U.ExplicitVRBigEndian:
        frames = _native_decode(pixel_value, params)
        if params.bytes_per_sample == 2:
            frames = [_swap16(f) for f in frames]
        return frames
    return _DECODERS[transfer_syntax](pixel_value, params)


def encode_frames(frames, params, transfer_syntax):
    """Encode native frames to the pixel element value for a transfer syntax."""
    if transfer_syntax == U.ExplicitVRBigEndian:
        if params.bytes_per_sample == 2:
            frames = [_swap16(f) for f in frames]
        return _native_encode(frames, params)
    return _ENCODERS[transfer_syntax](frames, params)
