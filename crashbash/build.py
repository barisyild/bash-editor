"""Rebuild CRASHBSH.DAT and patch the EXE that indexes it.

The DAT has no directory of its own: the game finds an entry through a table of
992 `(sector, size)` pairs compiled into the executable, and loads entries a
*group* at a time through a second table of 130 `(first_index, count, bytes)`
records. Writing an entry therefore means rewriting both files, and the two must
agree exactly or the game reads the wrong bytes.

What this builder guarantees:

* Entries keep their index, so anything that refers to one by number still works.
* Groups keep their membership, so a level still loads the same set.
* Every entry starts on a sector boundary and takes `ceil(size/2048)` sectors,
  laid down in index order with no gaps.

That last point makes the output tighter than the disc's own layout, and
deliberately so. The original reserves an extra sector for 12 entries and leaves
interior padding inside 8 groups, which makes a group's span disagree with the
`bytes` field the loader reads with. Packing tight makes the two identical, so
the group read covers exactly the entries it is splitting.

The disc position of the DAT does not matter: the game locates it by name
(`CdSearchFile("\\CRASHBSH\\CRASHBSH.DAT;1")`) and stores the resulting LBA at
0x800637B8, so a rebuilt disc may put the file anywhere.
"""

from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .archive import SECTOR_SIZE, BashArchive

GROUP_RECORD_SIZE = 12
FILE_RECORD_SIZE = 8


def sectors_for(size: int) -> int:
    return (size + SECTOR_SIZE - 1) // SECTOR_SIZE


@dataclass
class Group:
    index: int
    first: int
    count: int
    byte_length: int


@dataclass
class BuildReport:
    entries: int = 0
    replaced: list[int] = field(default_factory=list)
    dat_size: int = 0
    original_dat_size: int = 0
    groups: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return self.original_dat_size - self.dat_size


def read_groups(exe: bytes, table_offset: int, entry_count: int) -> list[Group]:
    """The group table sits immediately after the file table.

    Its length is not stored either; the records run until one has a zero count,
    and the counts must add up to the entry count.
    """
    base = table_offset + entry_count * FILE_RECORD_SIZE
    groups: list[Group] = []
    covered = 0
    while covered < entry_count and base + GROUP_RECORD_SIZE <= len(exe):
        first, count, byte_length = struct.unpack_from("<3I", exe, base)
        if count == 0 or first != covered:
            break
        groups.append(Group(len(groups), first, count, byte_length))
        covered += count
        base += GROUP_RECORD_SIZE
    return groups


def build(
    archive: BashArchive,
    output: str | Path,
    replacements: dict[int, bytes] | None = None,
    progress=None,
) -> BuildReport:
    """Write a disc tree to `output` with the given entries replaced.

    `replacements` maps a file-table index to its new contents. Everything else
    is copied through byte for byte.
    """
    replacements = replacements or {}
    output = Path(output)
    report = BuildReport(entries=len(archive))

    exe = bytearray(archive.exe_path.read_bytes())
    groups = read_groups(exe, archive.version.table_offset, len(archive))
    if sum(g.count for g in groups) != len(archive):
        report.warnings.append(
            f"group table covers {sum(g.count for g in groups)} of {len(archive)} "
            "entries; the DAT will be rebuilt but the group table left alone"
        )
        groups = []
    report.groups = len(groups)

    dat_dir = output / archive.dat_path.parent.name
    dat_dir.mkdir(parents=True, exist_ok=True)
    dat_out = dat_dir / archive.dat_path.name

    sizes: list[int] = []
    sector_of: list[int] = []
    cursor = 0

    with open(archive.dat_path, "rb") as source, open(dat_out, "wb") as target:
        for entry in archive:
            payload = replacements.get(entry.index)
            if payload is None:
                source.seek(entry.offset)
                payload = source.read(entry.size)
                if len(payload) != entry.size:
                    report.warnings.append(
                        f"entry {entry.index} is short in the source DAT "
                        f"({len(payload)} of {entry.size} bytes)"
                    )
            else:
                report.replaced.append(entry.index)

            sector_of.append(cursor)
            sizes.append(len(payload))
            target.write(payload)
            padding = sectors_for(len(payload)) * SECTOR_SIZE - len(payload)
            if padding:
                target.write(b"\x00" * padding)
            cursor += sectors_for(len(payload))
            if progress is not None:
                progress(entry.index + 1, len(archive), entry)

    report.dat_size = dat_out.stat().st_size
    report.original_dat_size = archive.dat_path.stat().st_size

    table = archive.version.table_offset
    for index, (sector, size) in enumerate(zip(sector_of, sizes)):
        struct.pack_into("<2i", exe, table + index * FILE_RECORD_SIZE, sector, size)

    group_base = table + len(archive) * FILE_RECORD_SIZE
    for group in groups:
        span = sum(
            sectors_for(sizes[i]) for i in range(group.first, group.first + group.count)
        )
        struct.pack_into(
            "<3I",
            exe,
            group_base + group.index * GROUP_RECORD_SIZE,
            group.first,
            group.count,
            span * SECTOR_SIZE,
        )

    (output / archive.exe_path.name).write_bytes(bytes(exe))
    _copy_siblings(archive, output)
    return report


def _copy_siblings(archive: BashArchive, output: Path) -> None:
    """Everything else on the disc, copied through untouched."""
    root = archive.exe_path.parent
    skip = {archive.exe_path.resolve(), archive.dat_path.resolve()}
    for source in root.rglob("*"):
        if not source.is_file() or source.name == ".DS_Store":
            continue
        if source.resolve() in skip:
            continue
        destination = output / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


# A PS1 disc is Mode 2. Files whose length divides exactly by the raw sector size
# are streams the drive reads as Form 2 -- the FMV and the streamed speech -- and
# a mastering tool has to be told so, or it will pad them as ordinary data.
RAW_SECTOR_SIZE = 2352


def _is_stream(path: Path) -> bool:
    size = path.stat().st_size
    return size > 0 and size % RAW_SECTOR_SIZE == 0 and path.suffix.upper() != ".EXE"


def write_iso_config(
    disc: str | Path, config: str | Path, image_name: str = "crashbash"
) -> Path:
    """Write an mkpsxiso project describing the built disc tree.

    Kept as a separate step from `build` so a disc tree is useful on its own:
    an emulator will run it straight from a folder, and mastering it is the last
    mile rather than a precondition.

    No licence sector is written. That data belongs to Sony and is not in this
    repository, so the image boots in emulators but not on hardware; pass the
    original disc's licence to mkpsxiso with `-l` if you need it to.
    """
    disc = Path(disc)
    config = Path(config)

    def entries(folder: Path, indent: str) -> list[str]:
        lines: list[str] = []
        for path in sorted(folder.iterdir(), key=lambda p: (p.is_dir(), p.name)):
            if path.name == ".DS_Store":
                continue
            relative = path.relative_to(disc)
            if path.is_dir():
                lines.append(f'{indent}<dir name="{path.name}">')
                lines += entries(path, indent + "  ")
                lines.append(f"{indent}</dir>")
            else:
                kind = "mixed" if _is_stream(path) else "data"
                lines.append(
                    f'{indent}<file name="{path.name}" type="{kind}" '
                    f'source="{relative}"/>'
                )
        return lines

    body = "\n".join(entries(disc, "      "))
    config.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<iso_project image_name="{image_name}.bin" cue_sheet="{image_name}.cue">
  <track type="data">
    <identifiers
      system="PLAYSTATION"
      application="PLAYSTATION"
      volume="CRASHBSH"
      volume_set="CRASHBSH"
      publisher="SONY COMPUTER ENTERTAINMENT INC."
    />
    <directory_tree>
{body}
    </directory_tree>
  </track>
</iso_project>
""",
        encoding="utf-8",
    )
    return config


def verify(original: BashArchive, built_exe: str | Path) -> tuple[int, list[str]]:
    """Re-read the built disc and compare every entry with the original.

    A builder that writes a table it cannot read back is worse than useless, so
    this goes through the same parser the viewer uses rather than trusting the
    numbers that were just written.
    """
    rebuilt = BashArchive(Path(built_exe), version=original.version)
    problems: list[str] = []
    if len(rebuilt) != len(original):
        problems.append(f"entry count changed: {len(original)} -> {len(rebuilt)}")
        return 0, problems

    matched = 0
    with open(original.dat_path, "rb") as source, open(rebuilt.dat_path, "rb") as target:
        for old, new in zip(original, rebuilt):
            if old.name != new.name:
                problems.append(f"entry {old.index}: name changed")
                continue
            source.seek(old.offset)
            target.seek(new.offset)
            if source.read(old.size) == target.read(new.size):
                matched += 1
            else:
                problems.append(f"entry {old.index} ({old.name}) differs")
    return matched, problems
