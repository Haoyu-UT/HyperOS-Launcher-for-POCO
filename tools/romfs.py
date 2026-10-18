"""Read files out of a fastboot ROM's split sparse super image without unpacking it to disk.

Standard library only: sparse images -> dynamic partition table (liblp) -> EROFS filesystem.

    python tools/romfs.py IMAGES_DIR partitions
    python tools/romfs.py IMAGES_DIR ls product /priv-app/MiuiHome
    python tools/romfs.py IMAGES_DIR get product /priv-app/MiuiHome OUT_DIR
"""
import bisect
import glob
import os
import struct
import sys

# --- Android sparse images ---------------------------------------------------------------------

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4


class SparseDisk:
    """The union of several sparse images of one block device, readable at byte offsets."""

    def __init__(self, paths):
        self.files = [open(p, "rb") for p in paths]
        runs = []  # (start_byte, length, file_index, file_offset or None, fill bytes or None)
        for fi, f in enumerate(self.files):
            magic, _, _, fhs, chs, blk, total, nchunks, _ = struct.unpack("<IHHHHIIII", f.read(28))
            if magic != SPARSE_MAGIC:
                raise ValueError(f"{paths[fi]} is not a sparse image")
            self.block_size, self.size = blk, total * blk
            pos, block = fhs, 0
            for _ in range(nchunks):
                f.seek(pos)
                ctype, _, nblk, csize = struct.unpack("<HHII", f.read(12))
                data = pos + chs
                if ctype == CHUNK_RAW:
                    runs.append((block * blk, nblk * blk, fi, data, None))
                elif ctype == CHUNK_FILL:
                    f.seek(data)
                    runs.append((block * blk, nblk * blk, fi, None, f.read(4)))
                if ctype != CHUNK_CRC:
                    block += nblk
                pos += csize
        runs.sort()
        self.runs = runs
        self.starts = [r[0] for r in runs]

    def read(self, offset, size):
        out = bytearray()
        while size > 0:
            i = bisect.bisect_right(self.starts, offset) - 1
            if i >= 0 and offset < self.runs[i][0] + self.runs[i][1]:
                start, length, fi, foff, fill = self.runs[i]
                n = min(size, start + length - offset)
                if fill is None:
                    f = self.files[fi]
                    f.seek(foff + offset - start)
                    out += f.read(n)
                else:
                    out += (fill * (n // 4 + 2))[(offset - start) % 4:][:n]
            else:  # a hole no image covers reads as zeros
                nxt = self.starts[i + 1] if i + 1 < len(self.starts) else offset + size
                n = min(size, nxt - offset)
                out += bytes(n)
            offset += n
            size -= n
        return bytes(out)


class Slice:
    """A byte range of another readable, made of (offset_in_parent, length) extents."""

    def __init__(self, parent, extents):
        self.parent, self.extents = parent, extents
        self.size = sum(n for _, n in extents)

    def read(self, offset, size):
        out = bytearray()
        pos = 0
        for start, length in self.extents:
            if size <= 0:
                break
            if offset < pos + length:
                n = min(size, pos + length - offset)
                out += self.parent.read(start + offset - pos, n)
                offset += n
                size -= n
            pos += length
        return bytes(out)


# --- Dynamic partitions (liblp) ----------------------------------------------------------------

def lp_partitions(disk):
    """Return {name: Slice} from the primary metadata of slot 0."""
    geo = disk.read(4096, 52)
    magic, _, _, max_size, slots, _ = struct.unpack("<II32sIII", geo)
    if magic != 0x616C4467:
        raise ValueError("no liblp geometry at 4096")
    base = 4096 + 4096 * 2
    hdr = disk.read(base, 256)
    magic, _, _, hsize = struct.unpack_from("<IHHI", hdr)
    if magic != 0x414C5030:
        raise ValueError("no liblp metadata header")
    tables = struct.unpack_from("<12I", hdr, 80)  # partitions, extents, groups, block devices
    blob = disk.read(base + hsize, max_size)

    def table(i):
        off, n, size = tables[i * 3:i * 3 + 3]
        return [blob[off + k * size:off + (k + 1) * size] for k in range(n)]

    extents = [struct.unpack_from("<QIQI", e) for e in table(1)]
    parts = {}
    for p in table(0):
        name = p[:36].rstrip(b"\0").decode()
        _, first, count, _ = struct.unpack_from("<IIII", p, 36)
        ext = [(sector * 512, nsec * 512) for nsec, ttype, sector, _ in extents[first:first + count] if ttype == 0]
        parts[name] = Slice(disk, ext)
    return parts


# --- EROFS -------------------------------------------------------------------------------------

S_IFMT, S_IFDIR, S_IFREG, S_IFLNK = 0o170000, 0o040000, 0o100000, 0o120000
FLAT_PLAIN, COMPRESSED_FULL, FLAT_INLINE, COMPRESSED_COMPACT, CHUNK_BASED = range(5)
Z_PLAIN, Z_HEAD1, Z_NONHEAD, Z_HEAD2 = range(4)
Z_ADVISE_COMPACTED_2B, Z_ADVISE_BIG_PCLUSTER_1, Z_ADVISE_BIG_PCLUSTER_2 = 0x1, 0x2, 0x4
Z_ADVISE_INLINE_PCLUSTER, Z_ADVISE_INTERLACED_PCLUSTER, Z_ADVISE_FRAGMENT_PCLUSTER = 0x8, 0x10, 0x20


def lz4_block_decompress(src, out_len):
    """Decode a raw LZ4 block, stopping once out_len bytes are produced."""
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n or len(dst) >= out_len:
            break
        off = src[i] | src[i + 1] << 8
        i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(dst) - off
        if start < 0 or off == 0:
            raise ValueError("corrupt LZ4 stream")
        if off >= ml:
            dst += dst[start:start + ml]
        else:  # overlapping match repeats the last `off` bytes
            dst += (dst[start:] * (ml // off + 1))[:ml]
    if len(dst) < out_len:
        raise ValueError(f"LZ4 stream ended at {len(dst)} of {out_len} bytes")
    return bytes(dst[:out_len])


class Inode:
    def __init__(self, fs, nid):
        self.fs, self.nid = fs, nid
        pos = fs.meta + nid * 32
        raw = fs.dev.read(pos, 64)
        fmt, icount, self.mode = struct.unpack_from("<HHH", raw)
        self.layout = (fmt >> 1) & 7
        if fmt & 1:
            self.size, self.u = struct.unpack_from("<QI", raw, 8)
            isize = 64
        else:
            self.size, _, self.u = struct.unpack_from("<III", raw, 8)
            isize = 32
        xattr = 12 + (icount - 1) * 4 if icount else 0
        self.inline = pos + isize + xattr  # where inline data / compression indexes start

    def is_dir(self):
        return self.mode & S_IFMT == S_IFDIR

    def is_reg(self):
        return self.mode & S_IFMT == S_IFREG

    def is_link(self):
        return self.mode & S_IFMT == S_IFLNK

    def data(self):
        fs, bs = self.fs, self.fs.block_size
        if self.layout == FLAT_PLAIN:
            return fs.dev.read(self.u * bs, self.size)
        if self.layout == FLAT_INLINE:
            full = self.size // bs * bs
            return fs.dev.read(self.u * bs, full) + fs.dev.read(self.inline, self.size - full)
        if self.layout in (COMPRESSED_FULL, COMPRESSED_COMPACT):
            return self._compressed_data()
        raise NotImplementedError(f"EROFS layout {self.layout}")

    # Compressed files: a table of logical clusters, each either the head of an extent (with the
    # physical block holding its pcluster) or a continuation of the previous extent.

    def _compressed_data(self):
        fs, bs = self.fs, self.fs.block_size
        hdr_pos = (self.inline + 7) // 8 * 8
        _, advise, algo, cbits = struct.unpack("<IHBB", fs.dev.read(hdr_pos, 8))
        if advise & (Z_ADVISE_BIG_PCLUSTER_1 | Z_ADVISE_BIG_PCLUSTER_2 | Z_ADVISE_INLINE_PCLUSTER
                     | Z_ADVISE_INTERLACED_PCLUSTER | Z_ADVISE_FRAGMENT_PCLUSTER) or cbits & 0x80:
            raise NotImplementedError(f"EROFS compression features 0x{advise:x} not supported")
        lbits = (bs.bit_length() - 1) + (cbits & 7)
        if lbits != bs.bit_length() - 1:
            raise NotImplementedError("logical clusters larger than a block")
        count = (self.size + (1 << lbits) - 1) >> lbits
        if self.layout == COMPRESSED_FULL:
            clusters = self._full_clusters(hdr_pos + 16, count)
        else:
            clusters = self._compact_clusters(hdr_pos + 8, count, lbits, advise)

        heads = [((lcn << lbits) + ofs, ctype, pblk)
                 for lcn, (ctype, ofs, pblk) in enumerate(clusters) if ctype != Z_NONHEAD]
        out = bytearray()
        for i, (start, ctype, pblk) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else self.size
            if start >= self.size:
                break
            if len(out) != start:
                raise ValueError(f"extent gap at {start} (have {len(out)})")
            raw = fs.dev.read(pblk * bs, bs)
            if ctype == Z_PLAIN:
                out += raw[:end - start]
                continue
            method = algo & 0xF if ctype == Z_HEAD1 else algo >> 4
            if method != 0:
                raise NotImplementedError(f"EROFS compression algorithm {method} (only LZ4 is supported)")
            out += lz4_block_decompress(raw.lstrip(b"\0"), end - start)
        if len(out) != self.size:
            raise ValueError(f"decompressed {len(out)} bytes, expected {self.size}")
        return bytes(out)

    def _full_clusters(self, pos, count):
        out = []
        raw = self.fs.dev.read(pos, count * 8)
        for i in range(count):
            advise, ofs, u = struct.unpack_from("<HHI", raw, i * 8)
            out.append((advise & 3, ofs, u))
        return out

    def _compact_clusters(self, ebase, count, lbits, advise):
        """Decode compact (4B/2B) lcluster indexes, as in the kernel's z_erofs_load_compact_lcluster."""
        initial_4b = ((32 - ebase % 32) // 4) & 7
        compacted_2b = 0
        if advise & Z_ADVISE_COMPACTED_2B and initial_4b < count:
            compacted_2b = (count - initial_4b) // 16 * 16
        groups = [(min(initial_4b, count), 2), (compacted_2b, 1),
                  (count - min(initial_4b, count) - compacted_2b, 2)]
        out, pos = [], ebase
        for n, shift in groups:
            if n <= 0:
                continue
            vcnt = 2 if shift == 2 else 16
            pack = vcnt << shift
            lobits = max(lbits, 12)  # ilog2(Z_EROFS_LI_D0_CBLKCNT) + 1
            encodebits = (pack - 4) * 8 // vcnt
            total = n << shift
            raw = self.fs.dev.read(pos, (total + pack - 1) // pack * pack)
            for p in range(0, len(raw), pack):
                blob = raw[p:p + pack]
                base = struct.unpack_from("<I", blob, pack - 4)[0]
                ents = []
                for i in range(vcnt):
                    bit = encodebits * i
                    v = int.from_bytes(blob[bit // 8:bit // 8 + 4].ljust(4, b"\0"), "little") >> (bit & 7)
                    ents.append(((v >> lobits) & 3, v & ((1 << lobits) - 1)))
                for i, (ctype, lo) in enumerate(ents):
                    if ctype == Z_NONHEAD:
                        out.append((ctype, lo, None))
                        continue
                    # Walk back through the pack counting extents, jumping over each one's
                    # continuation clusters; the kernel's nblk starts at 1.
                    nblk, j = 1, i
                    while j > 0:
                        j -= 1
                        t, d = ents[j]
                        if t == Z_NONHEAD:
                            j -= d
                        if j >= 0:
                            nblk += 1
                    out.append((ctype, lo, base + nblk))
            pos += total
        return out[:count]


class Erofs:
    def __init__(self, dev):
        self.dev = dev
        sb = dev.read(1024, 128)
        magic, _, _, blkszbits, _, root_nid = struct.unpack_from("<IIIBBH", sb)
        if magic != 0xE0F5E1E2:
            raise ValueError("not an EROFS filesystem")
        self.block_size = 1 << blkszbits
        self.meta = struct.unpack_from("<I", sb, 40)[0] * self.block_size
        self.root = root_nid

    def listdir(self, inode):
        data, bs, out = inode.data(), self.block_size, {}
        for b in range(0, len(data), bs):
            blk = data[b:b + bs]
            count = struct.unpack_from("<H", blk, 8)[0] // 12
            ents = [struct.unpack_from("<QHBB", blk, i * 12) for i in range(count)]
            for i, (nid, off, ftype, _) in enumerate(ents):
                end = ents[i + 1][1] if i + 1 < count else len(blk)
                name = blk[off:end].split(b"\0", 1)[0].decode("utf-8", "replace")
                if name not in (".", ".."):
                    out[name] = nid
        return out

    def lookup(self, path):
        ino = Inode(self, self.root)
        for part in [p for p in path.split("/") if p]:
            if not ino.is_dir():  # symlinks (e.g. /etc on system-as-root) aren't followed
                raise KeyError(path)
            ino = Inode(self, self.listdir(ino)[part])
        return ino


# --- CLI ---------------------------------------------------------------------------------------

def open_partition(images, name):
    paths = sorted(glob.glob(os.path.join(images, "super.img*")),
                   key=lambda p: int(p.rsplit(".", 1)[1]) if p.rsplit(".", 1)[1].isdigit() else -1)
    parts = lp_partitions(SparseDisk(paths))
    if name not in parts:
        name = next((n for n in (f"{name}_a", f"{name}_b") if n in parts and parts[n].size), name)
    return parts, parts.get(name)


def copy_tree(fs, ino, dest, quiet=False):
    """Copy a file or directory out of the image; returns the number of files written."""
    if ino.is_dir():
        os.makedirs(dest, exist_ok=True)
        return sum(copy_tree(fs, Inode(fs, nid), os.path.join(dest, name), quiet)
                   for name, nid in fs.listdir(ino).items())
    if ino.is_reg():
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(ino.data())
        if not quiet:
            print(f"  {dest} ({ino.size} bytes)")
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    images, cmd = args[0], args[1]
    if cmd == "partitions":
        parts, _ = open_partition(images, "")
        for name, sl in parts.items():
            kind = sl.read(1024, 4) if sl.size else b""
            fs = "erofs" if kind == struct.pack("<I", 0xE0F5E1E2) else "?"
            print(f"{name:24} {sl.size / 2**20:10.1f} MiB  {fs}")
        return
    _, part = open_partition(images, args[2])
    fs = Erofs(part)
    ino = fs.lookup(args[3])
    if cmd == "ls":
        for name, nid in sorted(fs.listdir(ino).items()):
            child = Inode(fs, nid)
            kind = "d" if child.is_dir() else "l" if child.is_link() else "-"
            print(f"{kind} {child.size:12} layout={child.layout} {name}")
    elif cmd == "get":
        copy_tree(fs, ino, args[4])


if __name__ == "__main__":
    main()
