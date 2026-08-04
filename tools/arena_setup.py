"""What each minigame configures per arena, read out of the mode overlays.

A level's walkable surface is not in its model (FORMAT.md §8.4): the mode owns
it. The crate game's board lookup at `0x800BAC60` maps a position to an 8x8 grid
of 512-unit cells and, for anything off the grid, returns the object's y minus a
global ground level at `0x8005A5D8`. That global is what a mode writes when it
sets an arena up, so every write to it marks a setup path.

This prints them: the value each site writes, the argument registers loaded
beside it, and -- where the mode really does branch per level -- which jump-table
entry reaches it and where that entry sits in the file, so redirecting one arena
to another's setup is an edit of four bytes.

The case attribution is only printed for a mode that writes the ground more than
once. Every other mode sets one ground for all its arenas, and the nearest
preceding jump table there belongs to something else.

    tools/arena_setup.py game/SCUS_945.70
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crashbash.archive import BashArchive  # noqa: E402

GROUND = 0x8005A5D8
# Every mode overlay is swapped into the same slot, so they share a load address.
# Recovered by requiring an overlay's internal `jal` targets to land on function
# prologues: 22 of crate.bin's 34 do at this base and 3 at the next best.
BASE = 0x800B32B4

STORE_OPS = {0x2B: "sw", 0x29: "sh", 0x28: "sb"}


def _address_uses(blob: bytes, target: int) -> list[tuple[int, int]]:
    """Offsets of instructions reaching `target` through a lui/addiu pair."""
    out: list[tuple[int, int]] = []
    for off in range(0, len(blob) - 8, 4):
        word = struct.unpack_from("<I", blob, off)[0]
        if (word >> 26) != 0x0F:                       # lui
            continue
        reg, high = (word >> 16) & 0x1F, word & 0xFFFF
        for step in range(1, 10):
            follow = struct.unpack_from("<I", blob, off + 4 * step)[0]
            if ((follow >> 21) & 0x1F) != reg:
                continue
            low = follow & 0xFFFF
            address = (high << 16) + (low - 0x10000 if low & 0x8000 else low)
            if address == target:
                out.append((off + 4 * step, follow >> 26))
            break
    return out


def _immediate_before(blob: bytes, at: int, register: int) -> int | None:
    """The nearest `addiu reg, $zero, imm` in the twelve instructions before."""
    for back in range(1, 13):
        off = at - 4 * back
        if off < 0:
            return None
        word = struct.unpack_from("<I", blob, off)[0]
        if ((word >> 26) == 0x09 and ((word >> 21) & 0x1F) == 0
                and ((word >> 16) & 0x1F) == register):
            value = word & 0xFFFF
            return value - 0x10000 if value & 0x8000 else value
    return None


def _jump_tables(blob: bytes) -> dict[int, tuple[int, int, int]]:
    """Case target -> (table address, index, case count), for `sltiu` switches."""
    out: dict[int, tuple[int, int, int]] = {}
    for off in range(0, len(blob) - 4, 4):
        word = struct.unpack_from("<I", blob, off)[0]
        if (word >> 26) != 0x0B:                       # sltiu rt, rs, imm
            continue
        count = word & 0xFFFF
        if not 2 <= count <= 64:
            continue
        for step in range(1, 8):
            probe = struct.unpack_from("<I", blob, off + 4 * step)[0]
            if (probe >> 26) != 0x0F:
                continue
            reg, high = (probe >> 16) & 0x1F, probe & 0xFFFF
            for k in range(1, 4):
                follow = struct.unpack_from("<I", blob, off + 4 * (step + k))[0]
                if (follow >> 26) == 0x09 and ((follow >> 21) & 0x1F) == reg:
                    low = follow & 0xFFFF
                    table = (high << 16) + (low - 0x10000 if low & 0x8000 else low)
                    at = table - BASE
                    if 0 <= at and at + 4 * count <= len(blob):
                        entries = [struct.unpack_from("<I", blob, at + 4 * i)[0]
                                   for i in range(count)]
                        if all(BASE <= e < BASE + len(blob) for e in entries):
                            for index, entry in enumerate(entries):
                                out.setdefault(entry, (table, index, count))
                    break
            break
    return out


def report(archive: BashArchive) -> None:
    byname = {entry.name: entry for entry in archive}
    for name in sorted(n for n in byname if n.startswith("overlays/modes/")):
        blob = archive.read(byname[name])
        writes = [off for off, op in _address_uses(blob, GROUND) if op in STORE_OPS]
        if not writes:
            print(f"{name}: sets no ground level")
            continue
        per_arena = len(writes) > 1
        jump = _jump_tables(blob) if per_arena else {}
        print(f"{name}  ({len(blob):,} bytes, {len(writes)} ground write"
              f"{'s' if per_arena else ''}"
              f"{', per arena' if per_arena else ', one for the whole mode'})")
        for off in sorted(writes):
            word = struct.unpack_from("<I", blob, off)[0]
            source = (word >> 16) & 0x1F
            value = 0 if source == 0 else _immediate_before(blob, off, source)
            args = {}
            for back in range(1, 10):
                prior = off - 4 * back
                if prior < 0:
                    break
                w = struct.unpack_from("<I", blob, prior)[0]
                if (w >> 26) == 0x09 and ((w >> 21) & 0x1F) == 0:
                    rt = (w >> 16) & 0x1F
                    if 4 <= rt <= 7:
                        imm = w & 0xFFFF
                        args[f"$a{rt - 4}"] = imm - 0x10000 if imm & 0x8000 else imm
            line = f"   file 0x{off:05X}  ground {value!s:>6}"
            if args:
                line += "   " + " ".join(f"{k}={v}" for k, v in sorted(args.items()))
            print(line)
            if per_arena:
                best = None
                for entry, row in jump.items():
                    at = entry - BASE
                    if at <= off and (best is None or entry > best[0]):
                        best = (entry, row)
                if best:
                    table, index, count = best[1]
                    print(f"      case {index} of {count}; table file "
                          f"0x{table - BASE:05X}, this entry at "
                          f"0x{table - BASE + 4 * index:05X}")
        print()


if __name__ == "__main__":
    report(BashArchive(sys.argv[1] if len(sys.argv) > 1 else "game/SCUS_945.70"))
