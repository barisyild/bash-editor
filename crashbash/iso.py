"""Master a disc tree into a PlayStation .bin/.cue, with no external tool.

A PS1 disc is CD-XA: 2352-byte sectors carrying 2048 bytes of user data each,
wrapped in a sync pattern, an address header, a subheader, and the error
detection and correction the drive checks. On top of that sits an ordinary
ISO9660 filesystem, which is all the game needs -- it finds its archive by name
(`CdSearchFile("\\CRASHBSH\\CRASHBSH.DAT;1")`), so nothing depends on where a
file lands.

Two sector forms are used:

* **Form 1** for everything the game reads as data: 2048 bytes plus EDC and ECC.
* **Form 2** for streams: 2324 bytes, no ECC, because a dropped frame of audio
  matters less than the bandwidth. A file whose length divides exactly by 2352
  is already a run of raw sectors and is copied through as-is.

No licence sector is written. That data is Sony's; pass `-l` a real one if you
need the image to boot on hardware rather than in an emulator.
"""

from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RAW_SECTOR = 2352
USER_DATA = 2048
FORM2_DATA = 2324
SYSTEM_AREA_SECTORS = 16
PVD_SECTOR = 16

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"

# --- EDC / ECC -------------------------------------------------------------
# The tables below are the ones burned into every CD-ROM: a reflected CRC-32
# with polynomial 0x8001801B for the EDC, and a GF(2^8) log/antilog pair for the
# Reed-Solomon P and Q parity.

_EDC_TABLE: list[int] = []
for _i in range(256):
    _edc = _i
    for _ in range(8):
        _edc = (_edc >> 1) ^ (0xD8018001 if _edc & 1 else 0)
    _EDC_TABLE.append(_edc)

_ECC_F_LUT = [0] * 256
_ECC_B_LUT = [0] * 256
_j = 0
for _i in range(256):
    _ECC_F_LUT[_i] = (_i << 1) ^ (0x11D if _i & 0x80 else 0)
for _i in range(256):
    _ECC_B_LUT[_i ^ _ECC_F_LUT[_i]] = _i


_EDC_LUT = np.array(_EDC_TABLE, dtype=np.uint32)
_F_LUT = np.array(_ECC_F_LUT, dtype=np.uint8)
_B_LUT = np.array(_ECC_B_LUT, dtype=np.uint8)


def _edc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = (crc >> 8) ^ _EDC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def _edc_batch(block: np.ndarray) -> np.ndarray:
    """EDC for every row of an (n, length) byte array.

    The CRC is sequential over the bytes of one sector, so the loop runs over
    the columns and the whole batch advances together -- 2056 vector steps for
    any number of sectors, instead of 2056 scalar steps each.
    """
    crc = np.zeros(block.shape[0], dtype=np.uint32)
    for column in range(block.shape[1]):
        index = (crc ^ block[:, column]) & 0xFF
        crc = (crc >> np.uint32(8)) ^ _EDC_LUT[index]
    return crc


def _ecc_indices(major_count: int, minor_count: int, major_mult: int,
                 minor_inc: int) -> np.ndarray:
    size = major_count * minor_count
    table = np.empty((major_count, minor_count), dtype=np.int32)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        for minor in range(minor_count):
            table[major, minor] = index
            index += minor_inc
            if index >= size:
                index -= size
    return table


_P_INDEX = _ecc_indices(86, 24, 2, 86)
_Q_INDEX = _ecc_indices(52, 43, 86, 88)


def _ecc_parity(work: np.ndarray, index: np.ndarray) -> np.ndarray:
    """Reed-Solomon parity for every row of an (n, size) byte array.

    `work` is the address followed by the data the pass covers. For Mode 2 the
    address counts as zero, which is why the caller passes four zero columns
    rather than the sector's own header.
    """
    taken = work[:, index]  # (n, major, minor)
    a = np.zeros(taken.shape[:2], dtype=np.uint8)
    b = np.zeros_like(a)
    for minor in range(taken.shape[2]):
        value = taken[:, :, minor]
        a ^= value
        b ^= value
        a = _F_LUT[a]
    a = _B_LUT[_F_LUT[a] ^ b]
    return np.concatenate([a, a ^ b], axis=1)


def _finish_batch(sectors: np.ndarray) -> np.ndarray:
    """Recompute EDC and both parity passes over sectors whose data is set."""
    count = sectors.shape[0]
    edc = _edc_batch(sectors[:, 16:2072])
    sectors[:, 2072:2076] = edc[:, None].view(np.uint8).reshape(count, 4)

    # The Q pass covers the P parity as well, so P has to land first.
    zeros = np.zeros((count, 4), dtype=np.uint8)
    sectors[:, 2076:2248] = _ecc_parity(
        np.concatenate([zeros, sectors[:, 16:2076]], axis=1), _P_INDEX
    )
    sectors[:, 2248:2352] = _ecc_parity(
        np.concatenate([zeros, sectors[:, 16:2248]], axis=1), _Q_INDEX
    )
    return sectors


def _form1_batch(users: np.ndarray, first_lba: int, submodes: np.ndarray) -> np.ndarray:
    """Turn an (n, 2048) array of user data into (n, 2352) Mode 2 Form 1 sectors."""
    count = users.shape[0]
    sectors = np.zeros((count, RAW_SECTOR), dtype=np.uint8)
    sectors[:, 0:12] = np.frombuffer(SYNC, dtype=np.uint8)
    for row in range(count):
        sectors[row, 12:16] = np.frombuffer(_address(first_lba + row), dtype=np.uint8)
    sectors[:, 18] = submodes
    sectors[:, 22] = submodes
    sectors[:, 24 : 24 + USER_DATA] = users
    return _finish_batch(sectors)


def _write_form1(user: bytes, lba: int, submode: int = 0x08) -> bytes:
    users = np.zeros((1, USER_DATA), dtype=np.uint8)
    payload = np.frombuffer(user[:USER_DATA], dtype=np.uint8)
    users[0, : payload.size] = payload
    return _form1_batch(users, lba, np.array([submode], dtype=np.uint8)).tobytes()


def _address(lba: int) -> bytes:
    """The sector's own address, as minutes/seconds/frames plus the mode byte."""
    total = lba + 150  # the 2-second lead-in every CD address is offset by
    minutes, rest = divmod(total, 60 * 75)
    seconds, frames = divmod(rest, 75)
    to_bcd = lambda v: ((v // 10) << 4) | (v % 10)  # noqa: E731
    return bytes((to_bcd(minutes), to_bcd(seconds), to_bcd(frames), 2))


# --- ISO9660 ---------------------------------------------------------------


def _both(value: int, width: int) -> bytes:
    """ISO9660 stores its integers twice, once each way round."""
    code = "H" if width == 2 else "I"
    return struct.pack(f"<{code}", value) + struct.pack(f">{code}", value)


# A Mode 2 disc carries a 14-byte XA attribute in each directory record's system
# use area. The BIOS does not need it, but every pressed disc has one and some
# tools read it to tell a streamed file from a plain one.
XA_ATTRIB_FILE = 0x0D55
XA_ATTRIB_DIR = 0x8D55
XA_ATTRIB_FORM2 = 0x2555  # the value Crash Bash's own disc gives SPEECH.STR

# Extensions that are Mode 2 Form 2 streams by convention on PS1.
STREAM_SUFFIXES = {".STR", ".XA", ".IKI"}


def _dir_record(name: bytes, lba: int, size: int, is_dir: bool,
                xa: bool = True, form2: bool = False) -> bytes:
    length = 33 + len(name)
    if length % 2:
        length += 1
    if xa:
        length += 14
    record = bytearray(length)
    record[0] = length
    record[1] = 0
    record[2:10] = _both(lba, 4)
    record[10:18] = _both(size, 4)
    # A date is required; the epoch is as good as any and keeps builds
    # reproducible, which matters more here than the real time.
    record[18:25] = bytes((80, 1, 1, 0, 0, 0, 0))
    record[25] = 0x02 if is_dir else 0x00
    record[26] = 0
    record[27] = 0
    record[28:32] = _both(1, 2)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    if xa:
        if is_dir:
            attributes = XA_ATTRIB_DIR
        elif form2:
            attributes = XA_ATTRIB_FORM2
        else:
            attributes = XA_ATTRIB_FILE
        struct.pack_into(">HHH2sB5x", record, length - 14, 0, 0, attributes, b"XA", 0)
    return bytes(record)


@dataclass
class _Node:
    name: str
    path: Path | None = None
    children: list["_Node"] = field(default_factory=list)
    lba: int = 0
    size: int = 0
    sectors: int = 0
    number: int = 0  # path-table index, 1-based
    parent_number: int = 1
    parent: "_Node | None" = None
    stream: bool = False

    @property
    def is_dir(self) -> bool:
        return self.path is None

    @property
    def iso_name(self) -> bytes:
        return self.name.encode("ascii") if self.is_dir else f"{self.name};1".encode()


def _scan(folder: Path, name: str = "") -> _Node:
    node = _Node(name)
    for entry in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.upper())):
        if entry.name == ".DS_Store":
            continue
        if entry.is_dir():
            child = _scan(entry, entry.name.upper())
        else:
            child = _Node(entry.name.upper(), entry)
            child.size = entry.stat().st_size
            child.stream = child.size > 0 and child.size % RAW_SECTOR == 0
            if child.stream:
                with open(entry, "rb") as source:
                    child.stream = source.read(12) == SYNC
        child.parent = node
        node.children.append(child)
    return node


def _is_stream(node: _Node) -> bool:
    """True for a file that is already whole raw sectors, to be copied verbatim.

    Length alone does not settle it -- a file can divide by 2352 by chance -- so
    the sectors have to actually start with the CD sync pattern. `BASHY.` is the
    case that makes the distinction matter: its length says stream, its contents
    are 31 MB of zeroes with no sync anywhere, so it is written as plain data.
    """
    return node.stream


def _directory_bytes(node: _Node) -> bytes:
    parent = node.parent or node
    out = bytearray()
    out += _dir_record(b"\x00", node.lba, node.sectors * USER_DATA, True)
    out += _dir_record(b"\x01", parent.lba, parent.sectors * USER_DATA, True)
    for child in node.children:
        record = _dir_record(
            child.iso_name,
            child.lba,
            child.sectors * RAW_SECTOR if _is_stream(child) else child.size,
            child.is_dir,
            form2=_is_stream(child),
        )
        # A directory record may not straddle a sector boundary.
        if len(out) % USER_DATA + len(record) > USER_DATA:
            out += b"\x00" * (USER_DATA - len(out) % USER_DATA)
        out += record
    return bytes(out)


def _path_table(nodes: list[_Node], little: bool) -> bytes:
    pack = "<" if little else ">"
    out = bytearray()
    for node in nodes:
        name = node.iso_name if node.number > 1 else b"\x00"
        out += bytes((len(name), 0))
        out += struct.pack(f"{pack}I", node.lba)
        out += struct.pack(f"{pack}H", node.parent_number)
        out += name
        if len(name) % 2:
            out += b"\x00"
    return bytes(out)


class _SectorWriter:
    """Collects user data and encodes it a batch of sectors at a time.

    EDC and ECC are cheap per batch and ruinous per sector, so nothing is
    encoded until either the batch fills or the run of consecutive addresses
    breaks.
    """

    BATCH = 512

    def __init__(self, out, total: int, progress=None):
        self._out = out
        self._total = total
        self._progress = progress
        self._users = np.zeros((self.BATCH, USER_DATA), dtype=np.uint8)
        self._submodes = np.zeros(self.BATCH, dtype=np.uint8)
        self._count = 0
        self._first = 0
        self.written = 0

    def add(self, user: bytes, lba: int, submode: int = 0x08) -> None:
        if self._count and lba != self._first + self._count:
            self.flush()
        if self._count == 0:
            self._first = lba
        row = self._users[self._count]
        row[:] = 0
        payload = np.frombuffer(user[:USER_DATA], dtype=np.uint8)
        row[: payload.size] = payload
        self._submodes[self._count] = submode
        self._count += 1
        if self._count == self.BATCH:
            self.flush()

    def add_raw(self, raw: bytes) -> None:
        """Pass a sector through untouched, for a file that is already sectors."""
        self.flush()
        self._out.write(raw)
        self.written += len(raw) // RAW_SECTOR

    def flush(self) -> None:
        if not self._count:
            return
        block = _form1_batch(
            self._users[: self._count], self._first, self._submodes[: self._count]
        )
        self._out.write(block.tobytes())
        self.written += self._count
        self._count = 0
        if self._progress is not None:
            self._progress(self.written, self._total)


def build_iso(
    disc: str | Path,
    image: str | Path,
    volume: str = "",
    progress=None,
) -> dict:
    """Master the folder `disc` as a .bin plus its .cue, and report what went where.

    Self-contained, but a folder is less than a disc. Two things cannot come out
    of one and are reported as warnings rather than silently approximated: the
    licence area, which is Sony's and is not in this repository, and any Form 2
    stream, whose per-sector channel numbers a dump does not keep. Use
    `patch_image` against an original disc image when either matters.
    """
    disc = Path(disc)
    image = Path(image)
    root = _scan(disc)
    root.name = ""

    # Number the directories breadth first: the path table wants parents first.
    directories: list[_Node] = [root]
    root.number = 1
    root.parent_number = 1
    cursor = 0
    while cursor < len(directories):
        node = directories[cursor]
        cursor += 1
        for child in node.children:
            if child.is_dir:
                child.number = len(directories) + 1
                child.parent_number = node.number
                directories.append(child)

    for node in directories:
        node.size = len(_directory_bytes(node))
        node.sectors = max(1, -(-node.size // USER_DATA))

    path_table_size = len(_path_table(directories, True))
    path_sectors = max(1, -(-path_table_size // USER_DATA))

    lba = PVD_SECTOR + 2
    l_path_lba, lba = lba, lba + path_sectors
    m_path_lba, lba = lba, lba + path_sectors
    for node in directories:
        node.lba, lba = lba, lba + node.sectors
    files = [n for d in directories for n in d.children if not n.is_dir]
    for node in files:
        unit = RAW_SECTOR if _is_stream(node) else USER_DATA
        node.sectors = max(1, -(-node.size // unit))
        node.lba, lba = lba, lba + node.sectors
    total_sectors = lba

    image.parent.mkdir(parents=True, exist_ok=True)
    with open(image, "wb") as out:
        writer = _SectorWriter(out, total_sectors, progress)

        for sector in range(SYSTEM_AREA_SECTORS):
            writer.add(b"", sector)
        writer.add(
            _pvd(volume, total_sectors, path_table_size, l_path_lba, m_path_lba, root),
            PVD_SECTOR,
        )
        writer.add(b"\xffCD001\x01" + b"\x00" * 2041, PVD_SECTOR + 1)

        for table, base in ((_path_table(directories, True), l_path_lba),
                            (_path_table(directories, False), m_path_lba)):
            for i in range(path_sectors):
                writer.add(table[i * USER_DATA : (i + 1) * USER_DATA], base + i)

        for node in directories:
            payload = _directory_bytes(node)
            for i in range(node.sectors):
                writer.add(payload[i * USER_DATA : (i + 1) * USER_DATA], node.lba + i)

        for node in files:
            with open(node.path, "rb") as source:
                if _is_stream(node):
                    # Already whole raw sectors: pass them through untouched,
                    # only correcting the address each one carries.
                    for i in range(node.sectors):
                        raw = bytearray(source.read(RAW_SECTOR))
                        raw[12:16] = _address(node.lba + i)
                        writer.add_raw(bytes(raw))
                else:
                    for i in range(node.sectors):
                        last = i == node.sectors - 1
                        writer.add(source.read(USER_DATA), node.lba + i,
                                   0x89 if last else 0x08)
        writer.flush()

    cue = image.with_suffix(".cue")
    cue.write_text(
        f'FILE "{image.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="utf-8",
    )
    warnings = ["no licence area: the image runs in emulators, not on hardware"]
    warnings += [
        f"{node.name} is a stream by its extension but the folder holds it as "
        "plain data, so it is written as plain data and will not play"
        for node in files
        if node.path.suffix.upper() in STREAM_SUFFIXES and not _is_stream(node)
    ]
    return {
        "image": image,
        "cue": cue,
        "sectors": total_sectors,
        "bytes": total_sectors * RAW_SECTOR,
        "files": len(files),
        "directories": len(directories),
        "warnings": warnings,
    }


@dataclass
class ImageFile:
    """A file located inside an existing disc image."""

    path: str
    lba: int
    size: int
    form2: bool
    # Where the directory record naming it lives, so the file can be moved.
    record_lba: int = 0
    record_offset: int = 0  # byte offset inside that directory's extent


def read_image_files(image: str | Path) -> dict[str, ImageFile]:
    """Walk an existing .bin and report where every file sits.

    Only enough of ISO9660 to find files by path: the primary volume descriptor
    at sector 16, then the directory tree from its root record.
    """
    image = Path(image)
    with open(image, "rb") as source:

        def raw(lba: int) -> bytes:
            source.seek(lba * RAW_SECTOR)
            return source.read(RAW_SECTOR)

        def user(lba: int) -> bytes:
            sector = raw(lba)
            if sector[:12] != SYNC:
                raise ValueError(f"{image.name}: sector {lba} has no sync pattern")
            return sector[24 : 24 + USER_DATA]

        def block(lba: int, size: int) -> bytes:
            out = bytearray()
            while len(out) < size:
                out += user(lba + len(out) // USER_DATA)
            return bytes(out[:size])

        pvd = user(PVD_SECTOR)
        if pvd[:6] != b"\x01CD001":
            raise ValueError(f"{image.name}: no volume descriptor at sector 16")

        found: dict[str, ImageFile] = {}

        def walk(lba: int, size: int, prefix: str) -> None:
            data = block(lba, size)
            offset = 0
            while offset < len(data):
                at = offset
                length = data[offset]
                if length == 0:
                    offset = (offset // USER_DATA + 1) * USER_DATA
                    continue
                record = data[offset : offset + length]
                offset += length
                name_length = record[32]
                name = record[33 : 33 + name_length]
                if name in (b"\x00", b"\x01"):
                    continue
                child_lba = struct.unpack_from("<I", record, 2)[0]
                child_size = struct.unpack_from("<I", record, 10)[0]
                text = name.decode("ascii").split(";")[0]
                if record[25] & 0x02:
                    walk(child_lba, child_size, f"{prefix}{text}/")
                else:
                    form2 = bool(raw(child_lba)[18] & 0x20)
                    found[prefix + text] = ImageFile(
                        prefix + text, child_lba, child_size, form2,
                        record_lba=lba, record_offset=at,
                    )

        root = pvd[156:190]
        walk(
            struct.unpack_from("<I", root, 2)[0],
            struct.unpack_from("<I", root, 10)[0],
            "",
        )
    return found


def _read_sector(target, lba: int) -> np.ndarray:
    target.seek(lba * RAW_SECTOR)
    return np.frombuffer(bytearray(target.read(RAW_SECTOR)), dtype=np.uint8).copy()


def _write_sector(target, lba: int, sector: np.ndarray) -> None:
    target.seek(lba * RAW_SECTOR)
    target.write(_finish_batch(sector.reshape(1, RAW_SECTOR)).tobytes())


def _rewrite_record(target, entry: ImageFile, lba: int, size: int) -> None:
    """Point a file's directory record at where the file now is."""
    sector = entry.record_lba + entry.record_offset // USER_DATA
    at = 24 + entry.record_offset % USER_DATA
    raw = _read_sector(target, sector)
    patch = bytearray(_both(lba, 4) + _both(size, 4))
    raw[at + 2 : at + 18] = np.frombuffer(bytes(patch), dtype=np.uint8)
    _write_sector(target, sector, raw)


def _rewrite_volume_size(target, sectors: int) -> None:
    """The volume descriptor states how long the disc is; growing it changes that."""
    raw = _read_sector(target, PVD_SECTOR)
    raw[24 + 80 : 24 + 88] = np.frombuffer(_both(sectors, 4), dtype=np.uint8)
    _write_sector(target, PVD_SECTOR, raw)


def patch_image(
    original: str | Path,
    output: str | Path,
    contents: dict[str, bytes],
    progress=None,
) -> dict:
    """Copy a disc image and rewrite named files in place.

    This is the faithful route, and the reason it exists is that an extracted
    folder is not the whole disc. The licence area, `SPEECH.STR`'s interleaved
    XA channels and every sector's own subheader live outside the files a dump
    produces, and none of them can be reconstructed from one. Copying the image
    and swapping only the sectors that change keeps all of it.

    A replacement that fits the space the original file occupies is written
    there and zero-padded up to it, so no directory record has to move, and the
    sector's own subheader is kept -- only the user data, the EDC and the parity
    are recomputed. One that does not fit goes past the end of the image, which
    grows, and its directory record and the volume's stated length follow it.
    Nothing else on the disc moves either way.
    """
    original = Path(original)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    files = read_image_files(original)
    for name, payload in contents.items():
        if name not in files:
            raise ValueError(f"{original.name} has no file named {name}")
        if files[name].form2:
            raise ValueError(f"{name} is a Form 2 stream and is not rewritable here")

    shutil.copyfile(original, output)
    end_of_image = original.stat().st_size // RAW_SECTOR

    written = 0
    total = sum(
        -(-max(files[name].size, len(payload)) // USER_DATA)
        for name, payload in contents.items()
    )
    report: dict = {"image": output, "files": {}, "sectors": total, "moved": []}

    with open(output, "r+b") as target:
        for name, payload in contents.items():
            entry = files[name]
            count = -(-len(payload) // USER_DATA)
            if len(payload) <= entry.size:
                # Fits where it is: pad out to the reserved span so no directory
                # record has to change.
                lba, count = entry.lba, -(-entry.size // USER_DATA)
                padding = entry.size - len(payload)
            else:
                # Too big for its slot, so it goes past the end of the image and
                # its directory record follows it. Growing the image is the only
                # move that disturbs nothing else on the disc.
                lba, end_of_image = end_of_image, end_of_image + count
                padding = count * USER_DATA - len(payload)
                report["moved"].append(name)
            padded = payload.ljust(count * USER_DATA, b"\x00")

            for start in range(0, count, _SectorWriter.BATCH):
                span = min(_SectorWriter.BATCH, count - start)
                target.seek((lba + start) * RAW_SECTOR)
                existing = target.read(span * RAW_SECTOR)
                if len(existing) < span * RAW_SECTOR:
                    existing = _form1_batch(
                        np.zeros((span, USER_DATA), dtype=np.uint8), lba + start,
                        np.full(span, 0x08, dtype=np.uint8),
                    ).tobytes()
                sectors = np.frombuffer(
                    bytearray(existing), dtype=np.uint8
                ).reshape(span, RAW_SECTOR).copy()
                for row in range(span):
                    sectors[row, 0:12] = np.frombuffer(SYNC, dtype=np.uint8)
                    sectors[row, 12:16] = np.frombuffer(
                        _address(lba + start + row), dtype=np.uint8
                    )
                sectors[:, 24 : 24 + USER_DATA] = np.frombuffer(
                    padded[start * USER_DATA : (start + span) * USER_DATA],
                    dtype=np.uint8,
                ).reshape(span, USER_DATA)
                target.seek((lba + start) * RAW_SECTOR)
                target.write(_finish_batch(sectors).tobytes())
                written += span
                if progress is not None:
                    progress(written, total)

            if lba != entry.lba:
                _rewrite_record(target, entry, lba, len(payload))
            report["files"][name] = {
                "lba": lba,
                "sectors": count,
                "bytes": len(payload),
                "padding": padding,
                "moved": lba != entry.lba,
            }

        if report["moved"]:
            _rewrite_volume_size(target, end_of_image)
            report["sectors_total"] = end_of_image

    cue = output.with_suffix(".cue")
    cue.write_text(
        f'FILE "{output.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="utf-8",
    )
    report["cue"] = cue
    return report


def _pvd(volume: str, total: int, path_size: int, l_path: int, m_path: int,
         root: _Node) -> bytes:
    pvd = bytearray(USER_DATA)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    # Identifiers as the game's own disc has them: the two PLAYSTATION strings
    # the BIOS looks at, and everything else left blank.
    pvd[8:40] = b"PLAYSTATION".ljust(32)
    pvd[40:72] = volume.encode("ascii").ljust(32)
    pvd[80:88] = _both(total, 4)
    pvd[120:124] = _both(1, 2)
    pvd[124:128] = _both(1, 2)
    pvd[128:132] = _both(USER_DATA, 2)
    pvd[132:140] = _both(path_size, 4)
    struct.pack_into("<I", pvd, 140, l_path)
    struct.pack_into(">I", pvd, 148, m_path)
    # The PVD's copy of the root record is a fixed 34-byte field, so it is the
    # one directory record that carries no XA attribute.
    record = _dir_record(b"\x00", root.lba, root.sectors * USER_DATA, True, xa=False)
    pvd[156 : 156 + len(record)] = record
    pvd[190:318] = b" " * 128
    pvd[318:446] = b" " * 128
    pvd[446:574] = b" " * 128
    pvd[574:702] = b"PLAYSTATION".ljust(128)
    pvd[702:813] = b" " * 111
    for offset in (813, 830, 847, 864):
        pvd[offset : offset + 17] = b"0" * 16 + b"\x00"
    pvd[881] = 1
    # The signature that marks the disc as CD-XA, in the application use area.
    pvd[1024:1032] = b"CD-XA001"
    return bytes(pvd)
