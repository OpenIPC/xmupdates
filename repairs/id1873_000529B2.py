#!/usr/bin/env python3
"""Repair catalog id 1873 (000529B2 / IPC_HI3516EV300_85H50AI, V5.00.R02,
build 2021-03-03) from public inputs only.

XM published this build with a damaged user-x.cramfs.img: one 77,576-byte
span of its squashfs (0x3a05f0-0x3b34f8) was altered after the image's
checksums were taken, 52 bytes longer than the four xz blocks it replaced
(hi3516ev200_vpss.ko block 5, hi_osal.ko blocks 0-1, /res/fd.bin block 0).
The camera's own updater refuses the package (DVRIP OPSystemUpgrade Ret 514).

Those three files are byte-identical in the 2020-05-07 build of the same
catalog id (archive/000529B2/ in this repo), and mksquashfs' xz blocks are
reproducible bit for bit with liblzma's lzma_stream_buffer_encode (LZMA2
preset 6, 64 KiB dictionary, CRC32). Recompressing the four blocks from the
2020-05-07 files and splicing them in gives exactly the length and CRC
(0x8a2e2b3e) the vendor's zip records for the image, i.e. the image XM built.
Every other entry, the signed InstallDesc included, is copied byte for byte.

usage: id1873_000529B2.py <id1873 catalog zip> <2020-05-07 General_...bin> <out.zip>
"""
import io, os, struct, subprocess, sys, tempfile, zipfile, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xzsq import sq_xz

BS = 65536
SPAN = (0x3a05f0, 0x3b34f8)          # squashfs offsets of the altered span
BLOCKS = [('lib/modules/hi3516ev200_vpss.ko', 5, 13684),
          ('lib/modules/hi_osal.ko', 0, 17456),
          ('lib/modules/hi_osal.ko', 1, 8432),
          ('res/classifier/fd.bin', 0, 37952)]


def entry(raw, zi):
    lh = raw[zi.header_offset:zi.header_offset + 30]
    n, x = struct.unpack('<HH', lh[26:30])
    s = zi.header_offset + 30 + n + x
    return lh, raw[zi.header_offset + 30:s], raw[s:s + zi.compress_size]


def rezip(raw, z, replace):
    """Copy every entry raw; deflate anew only those in `replace`."""
    o, cd = bytearray(), bytearray()
    for zi in z.infolist():
        lh, ne, data = entry(raw, zi)
        crc, usize = zi.CRC, zi.file_size
        if zi.filename in replace:
            plain = replace[zi.filename]
            crc, usize = zlib.crc32(plain), len(plain)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            data = c.compress(plain) + c.flush()
        lh = bytearray(lh)
        struct.pack_into('<III', lh, 14, crc, len(data), usize)
        off = len(o)
        o += lh + ne + data
        cd += struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, zi.create_version | (zi.create_system << 8),
                          zi.extract_version, zi.flag_bits, zi.compress_type, *struct.unpack('<HH', lh[10:14]),
                          crc, len(data), usize, len(zi.filename.encode()), len(zi.extra),
                          len(zi.comment), 0, zi.internal_attr, zi.external_attr, off)
        cd += zi.filename.encode() + zi.extra + zi.comment
    n = len(z.infolist())
    return bytes(o + cd + struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, n, n, len(cd), len(o), 0))


catalog_zip, donor_pkg, out = sys.argv[1:4]
craw = open(catalog_zip, 'rb').read(); cz = zipfile.ZipFile(io.BytesIO(craw))
inner_name = next(n for n in cz.namelist() if n.endswith('_all.bin'))
praw = cz.read(inner_name); pz = zipfile.ZipFile(io.BytesIO(praw))
ui = pz.getinfo('user-x.cramfs.img')
img = zlib.decompress(entry(praw, ui)[2], -15)
if len(img) == ui.file_size and zlib.crc32(img) == ui.CRC:
    sys.exit('user-x.cramfs.img is intact: nothing to repair')
assert len(img) == 4325492, 'not the known-broken id1873 package'

donor = zipfile.ZipFile(donor_pkg).read('user-x.cramfs.img')[64:]
with tempfile.TemporaryDirectory() as d:
    open(f'{d}/usr.sqfs', 'wb').write(donor)
    subprocess.run(['unsquashfs', '-q', '-d', f'{d}/u', f'{d}/usr.sqfs'], check=True, stdout=subprocess.DEVNULL)
    new = b''
    for path, blk, want in BLOCKS:
        c = sq_xz(open(f'{d}/u/{path}', 'rb').read()[blk * BS:(blk + 1) * BS], 6)
        assert len(c) == want, f'{path} block {blk}: {len(c)} != {want}'
        new += c

fixed = img[:64 + SPAN[0]] + new + img[64 + SPAN[1]:]
assert len(fixed) == ui.file_size and zlib.crc32(fixed) == ui.CRC, 'does not reproduce the vendor CRC'

inner = rezip(praw, pz, {'user-x.cramfs.img': fixed})
outer = rezip(craw, cz, {inner_name: inner})
assert zipfile.ZipFile(io.BytesIO(outer)).testzip() is None
assert zipfile.ZipFile(io.BytesIO(inner)).testzip() is None
open(out, 'wb').write(outer)
print(f'{out}: user-x.cramfs.img restored to crc {ui.CRC:08x}, {ui.file_size} bytes')
