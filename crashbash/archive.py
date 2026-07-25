"""CRASHBSH.DAT archive: the file table lives inside the game EXE.

The EXE holds `num` pairs of (sector, size) starting at `ptr`. Both values are
32-bit; the first is a 2048-byte sector index into CRASHBSH.DAT, the second is
a byte length. Which (ptr, num) to use depends on the game build, identified by
the EXE's MD5 -- same table as CTR-tools' bash_dat.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .binreader import SECTOR_SIZE, Reader

DAT_NAME = "CRASHBSH.DAT"


@dataclass(frozen=True)
class GameVersion:
    md5: str
    table_offset: int
    file_count: int
    name: str

    @property
    def has_filelist(self) -> bool:
        """Real file names are only known for the NTSC-U release."""
        return self.file_count == 0x3E0


VERSIONS: dict[str, GameVersion] = {
    v.md5: v
    for v in [
        GameVersion("a45627fa6c3d1768f8ad56fb46569f06", 0x335A0, 0x1CA, "OPSM 38 Demo"),
        GameVersion("ee4963398064c458e9a9b27040d639e0", 0x33784, 0x1E6, "Spyro Split / Winter Jampack 2000 / demo disc 1.3"),
        GameVersion("9d86be91735f06dba059e00324c1dea6", 0x3499C, 0x229, "Aug 23 prototype"),
        GameVersion("98e02493600b898bcacbdcb129e9019f", 0x3483C, 0x241, "Spyro 3 NTSC Demo"),
        GameVersion("0f35ba94f0ce49b0e6fe8e2012e5f1b4", 0x34F40, 0x276, "Spyro 3 PAL Demo"),
        GameVersion("db497990f79454bcbd41a770df3692ef", 0x35238, 0x276, "Euro Demo"),
        GameVersion("b9a576bc33399addb51779c1e2e2bc42", 0x35850, 0x2A6, "Sep 14 preview prototype"),
        GameVersion("e830b0f1b91c1edeef42a60d3160b752", 0x3E97C, 0x3D2, "JPN Trial"),
        GameVersion("f620ac01cd60c55ab0e981104f2b6c48", 0x3E910, 0x3E0, "NTSC Release"),
        GameVersion("ce7e3fe1bf226cc8dd195e025725fdd1", 0x3F988, 0x4D9, "Oct 9 PAL prototype"),
        GameVersion("e9ad2756fe43d11e3d93af05acafef71", 0x3EE30, 0x4DD, "JPN Release"),
        GameVersion("e71360b5c119f87d88acd9964ac56c21", 0x3F264, 0x545, "PAL Release"),
    ]
}


# Magic value found at the start of an entry -> file kind.
# Straight from bash_dat; the readme warns it is a rough NTSC-U analysis and
# will misfire on other builds, so treat the result as a hint, not a fact.
#
# The leading word is only a magic for models. For everything else it is the
# byte length of the entry's own leading offset table, which is why so many
# small multiples of four appear -- and why a text overlay whose table happens
# to be 8 or 12 bytes long otherwise passes for a texture or a sound bank. The
# checks below rule those out by looking at what the table points at.
_MAGIC_KINDS: dict[int, str] = {
    0x08: "tex",
    0x0C160029: "mdl",
    0x09160026: "mdl2",
    0x0C: "sfx1",
    0x10: "sfx2",
    0x14: "sfx3",
    0x20000: "tga",
}
for _m in range(0x5D, 0x6C):
    _MAGIC_KINDS[_m] = "code"

_EXT_BY_KIND = {
    "tex": ".tex",
    "mdl": ".mdl",
    "mdl2": ".mdl2",
    "sfx1": ".sfx1",
    "sfx2": ".sfx2",
    "sfx3": ".sfx3",
    "tga": ".tga",
    "code": ".code",
    "bin": ".bin",
}

# Broad categories used by the GUI to pick a viewer.
KIND_GROUPS = {
    "tex": "texture",
    "mdl": "model",
    "mdl2": "model",
    "sfx1": "audio",
    "sfx2": "audio",
    "sfx3": "audio",
    "tga": "image",
    "code": "code",
    "bin": "binary",
}


@dataclass
class Entry:
    index: int
    offset: int  # byte offset into the DAT
    size: int
    kind: str  # detected from magic
    magic: int
    name: str  # path from the file list, or NNNNN.ext

    @property
    def group(self) -> str:
        return KIND_GROUPS.get(self.kind, "binary")

    @property
    def is_empty(self) -> bool:
        return self.size == 0


def md5_of(path: str | os.PathLike) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_filelist() -> dict[int, str]:
    """Parse the bundled `NNN=path` list shipped with CTR-tools."""
    path = Path(__file__).with_name("data") / "bash_filelist.txt"
    out: dict[int, str] = {}
    if not path.exists():
        return out
    line_re = re.compile(r"^\s*(\d+)\s*=\s*(.+?)\s*$")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = line_re.match(line)
        if m:
            out[int(m.group(1))] = m.group(2).replace("\\", "/")
    return out


class UnknownGameVersion(Exception):
    def __init__(self, md5: str):
        super().__init__(f"Unknown EXE build (md5 {md5}); not in the known version table.")
        self.md5 = md5


class BashArchive:
    """Read-only view over CRASHBSH.DAT, indexed by the table in the EXE.

    Nothing is extracted up front -- `read(entry)` pulls bytes on demand, so the
    73 MB DAT never has to be unpacked to browse it.
    """

    def __init__(self, exe_path: str | os.PathLike, dat_path: str | os.PathLike | None = None):
        self.exe_path = Path(exe_path)
        if dat_path is None:
            dat_path = find_dat(self.exe_path.parent)
            if dat_path is None:
                raise FileNotFoundError(
                    f"No {DAT_NAME} found next to {self.exe_path.name}. "
                    "Put the EXE and the DAT in the same folder, or pass the DAT path."
                )
        self.dat_path = Path(dat_path)

        self.md5 = md5_of(self.exe_path)
        version = VERSIONS.get(self.md5)
        if version is None:
            raise UnknownGameVersion(self.md5)
        self.version = version

        with open(self.exe_path, "rb") as f:
            if f.read(8) != b"PS-X EXE":
                raise ValueError(f"{self.exe_path.name} is not a PS-X EXE.")

        self.entries: list[Entry] = self._read_table()

    # -- table -----------------------------------------------------------

    def _read_table(self) -> list[Entry]:
        exe = Reader.from_file(self.exe_path)
        exe.seek(self.version.table_offset)
        raw = [(exe.i32() * SECTOR_SIZE, exe.i32()) for _ in range(self.version.file_count)]

        names = load_filelist() if self.version.has_filelist else {}
        dat_size = self.dat_path.stat().st_size

        entries: list[Entry] = []
        with open(self.dat_path, "rb") as dat:
            for i, (offset, size) in enumerate(raw):
                magic = 0
                head = b""
                if size >= 8 and 0 <= offset < dat_size:
                    dat.seek(offset)
                    head = dat.read(24)
                    if len(head) >= 4:
                        magic = int.from_bytes(head[:4], "little")
                kind = _classify(magic, head, size)
                name = names.get(i) or f"{i:05d}{_EXT_BY_KIND[kind]}"
                entries.append(Entry(i, offset, size, kind, magic, name))
        return entries

    # -- access ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __getitem__(self, index: int) -> Entry:
        return self.entries[index]

    def read(self, entry: Entry | int) -> bytes:
        if isinstance(entry, int):
            entry = self.entries[entry]
        with open(self.dat_path, "rb") as dat:
            dat.seek(entry.offset)
            return dat.read(entry.size)

    def reader(self, entry: Entry | int) -> Reader:
        return Reader(self.read(entry))

    def by_group(self, group: str) -> list[Entry]:
        return [e for e in self.entries if e.group == group]

    def extract_all(self, out_dir: str | os.PathLike, progress=None) -> int:
        """Write every entry to disk, mirroring the file list's folder layout."""
        out_dir = Path(out_dir)
        count = 0
        with open(self.dat_path, "rb") as dat:
            for entry in self.entries:
                dst = out_dir / entry.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                dat.seek(entry.offset)
                dst.write_bytes(dat.read(entry.size))
                count += 1
                if progress is not None:
                    progress(count, len(self.entries), entry)
        return count


def _classify(magic: int, head: bytes, size: int) -> str:
    """Name the entry's kind, checking the ambiguous ones against their contents.

    A texture pack states its own byte size in the second word, and a sound bank
    reaches a `pBAV` header through the second pointer of its table. Text
    overlays share the same leading words but satisfy neither.
    """
    kind = _MAGIC_KINDS.get(magic, "bin")
    if len(head) < 8:
        return "bin" if kind != "bin" else kind

    second = int.from_bytes(head[4:8], "little")
    if kind == "tex" and second != size:
        return "bin"
    if kind.startswith("sfx") and not (magic <= second < size):
        return "bin"
    return kind


def find_dat(folder: str | os.PathLike) -> Path | None:
    """Locate CRASHBSH.DAT near `folder`, case-insensitively.

    Real discs keep the DAT in a CRASHBSH subfolder next to the EXE, so look one
    level down as well.
    """
    folder = Path(folder)
    candidates = list(folder.glob("*")) + [p for d in folder.glob("*") if d.is_dir() for p in d.glob("*")]
    for path in candidates:
        if path.is_file() and path.name.upper() == DAT_NAME:
            return path
    return None


def find_exe(folder: str | os.PathLike) -> Path | None:
    """Find a PS-X EXE in `folder` whose MD5 is a known Crash Bash build."""
    folder = Path(folder)
    for path in sorted(folder.glob("*")):
        if not path.is_file() or path.stat().st_size > 8 << 20:
            continue
        try:
            with open(path, "rb") as f:
                if f.read(8) != b"PS-X EXE":
                    continue
        except OSError:
            continue
        if md5_of(path) in VERSIONS:
            return path
    return None
