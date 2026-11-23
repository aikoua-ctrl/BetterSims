"""Reader (and a small writer, for tests and demos) for Sims 4 DBPF 2.1 package files.

Only the header and the resource index are parsed, so even large packages are read
quickly. The index layout follows the format documented by the modding community and
used by Sims Mod Assistant (ISC, (c) 2020 Egor Blagov), which this module is a port of.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

HEADER_SIZE = 96
MAGIC = b"DBPF"

# Resource types we can name. Anything else is shown as its hex id.
TYPE_NAMES: dict[int, str] = {
    0x319E4F1D: "Catalog object",
    0xC0DB5AE7: "Object definition",
    0x0354796A: "Skintone",
    0x034AEECB: "CAS part",
    0x8B18FF6E: "Slider (hotspot control)",
    0x545AC67A: "SimData",
    0x03B33DDF: "Tuning XML",
    0x220557DA: "String table",
    0x0166038C: "Name map",
    0x3453CF95: "RLE texture",
    0xBA856C78: "RLES texture",
    0x00B2D882: "DST texture",
    0xB6C8B6A0: "DDS texture",
    0x2F7D0004: "PNG image",
    0x3C1AF1F2: "CAS thumbnail",
    0x5B282D45: "Object thumbnail",
    0x015A1849: "Geometry (GEOM)",
    0x01661233: "Model",
    0x01D10F34: "Model LOD",
    0xAC16FBEC: "Region map",
    0x9D1AB874: "Script tuning",
}

# Conflict categories. Overrides in these are real gameplay/CAS clashes.
CATEGORY_BY_TYPE: dict[int, str] = {
    0x319E4F1D: "object",
    0xC0DB5AE7: "object",
    0x0354796A: "skintone",
    0x034AEECB: "cas",
    0x8B18FF6E: "slider",
    0x545AC67A: "tuning",
    0x03B33DDF: "tuning",
    0x9D1AB874: "tuning",
}

# Types that collide often and harmlessly (thumbnails, name maps, shared textures).
NOISE_TYPES: set[int] = {0x3C1AF1F2, 0x5B282D45, 0x0166038C, 0x2F7D0004}


def type_name(t: int) -> str:
    return TYPE_NAMES.get(t, f"type 0x{t:08X}")


DELETED = 0xFFE0
ZLIB = 0x5A42


class DbpfError(Exception):
    pass


@dataclass(frozen=True)
class ResourceKey:
    type: int
    group: int
    instance: int

    def __str__(self) -> str:
        return f"{self.type:08X}:{self.group:08X}:{self.instance:016X}"


@dataclass
class IndexEntry:
    key: ResourceKey
    offset: int
    packed_size: int
    mem_size: int
    compression: int


@dataclass
class Package:
    path: Path
    major: int
    minor: int
    entries: list[IndexEntry] = field(default_factory=list)
    _by_key: dict[ResourceKey, IndexEntry] | None = None

    def entry(self, key: ResourceKey) -> IndexEntry:
        if self._by_key is None:
            self._by_key = {}
            for e in self.entries:
                self._by_key.setdefault(e.key, e)
        return self._by_key[key]

    def chunk_hash(self, entry: IndexEntry) -> str:
        """Hash of a resource's content, to tell identical overrides from real ones.

        zlib resources are decompressed first, so the same data saved by two different tools
        still counts as identical. Other compressions are compared as stored."""
        with open(self.path, "rb") as f:
            f.seek(entry.offset)
            raw = f.read(entry.packed_size)
        if entry.compression == ZLIB:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                pass
        return hashlib.sha1(raw).hexdigest()


def read_package(path: Path) -> Package:
    path = Path(path)
    with open(path, "rb") as f:
        header = f.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            raise DbpfError("file is too small to be a package")
        if header[:4] != MAGIC:
            raise DbpfError("not a DBPF package (bad header)")
        major, minor = struct.unpack_from("<II", header, 4)
        if (major, minor) != (2, 1):
            raise DbpfError(f"unsupported package version {major}.{minor} (not a Sims 4 package)")
        count = struct.unpack_from("<I", header, 36)[0]
        index_size = struct.unpack_from("<I", header, 44)[0]
        index_offset = struct.unpack_from("<I", header, 64)[0]
        if index_offset == 0:  # some tools only fill the older index position field
            index_offset = struct.unpack_from("<I", header, 40)[0]
        if count == 0:
            return Package(path, major, minor, [])
        size = path.stat().st_size
        if index_offset + index_size > size or index_size < 4:
            raise DbpfError("package index points outside the file (truncated or corrupt)")
        f.seek(index_offset)
        buf = f.read(index_size)

    pos = 0
    (flags,) = struct.unpack_from("<I", buf, pos)
    pos += 4
    common = [0] * 8
    for i in range(8):
        if flags & (1 << i):
            common[i] = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
    entries: list[IndexEntry] = []
    try:
        for _ in range(count):
            vals = list(common)
            for i in range(8):
                if not flags & (1 << i):
                    vals[i] = struct.unpack_from("<I", buf, pos)[0]
                    pos += 4
            rtype, group, ihi, ilo, off, fsize, msize, comp = vals
            if comp & 0xFFFF == DELETED:
                continue  # deleted record left behind by an editor, not a real resource
            entries.append(
                IndexEntry(
                    key=ResourceKey(rtype, group, (ihi << 32) | ilo),
                    offset=off,
                    packed_size=fsize & 0x7FFFFFFF,
                    mem_size=msize,
                    compression=(comp & 0xFFFF),
                )
            )
    except struct.error as e:
        raise DbpfError("package index is truncated") from e
    return Package(path, major, minor, entries)


def write_package(path: Path, resources: list[tuple[ResourceKey, bytes]]) -> None:
    """Write a minimal valid DBPF 2.1 package. Used by tests and the demo folder."""
    body = bytearray()
    index = bytearray(struct.pack("<I", 0))
    for key, data in resources:
        packed = zlib.compress(data)
        off = HEADER_SIZE + len(body)
        body += packed
        index += struct.pack(
            "<IIIIIIIHH",
            key.type,
            key.group,
            key.instance >> 32,
            key.instance & 0xFFFFFFFF,
            off,
            len(packed) | 0x80000000,
            len(data),
            0x5A42,
            1,
        )
    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    struct.pack_into("<II", header, 4, 2, 1)
    struct.pack_into("<I", header, 36, len(resources))
    struct.pack_into("<I", header, 44, len(index))
    struct.pack_into("<I", header, 60, 3)
    struct.pack_into("<I", header, 64, HEADER_SIZE + len(body))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        f.write(body)
        f.write(index)
