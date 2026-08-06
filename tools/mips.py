"""A MIPS R3000 disassembler, enough of one to read this game's loaders.

Written because the resident-boundary question ran out of static reading in
`docs/FORMAT.md` §2.1 -- "the trigger for the tail becoming garbage is still
untraced" -- and the next instrument is the executable itself. Only the opcodes
this image actually uses are decoded; anything else comes back as a word, which
is honest and still lets a walk continue.

    .venv/bin/python tools/mips.py game/SCUS_945.70 0x80013650 40
    .venv/bin/python tools/mips.py game/SCUS_945.70 --refs 0x80011498
"""

from __future__ import annotations

import struct
import sys

TEXT_FILE_OFFSET = 0x800

REGISTERS = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
)

SPECIAL = {
    0x00: "sll", 0x02: "srl", 0x03: "sra", 0x04: "sllv", 0x06: "srlv",
    0x07: "srav", 0x08: "jr", 0x09: "jalr", 0x0C: "syscall", 0x0D: "break",
    0x10: "mfhi", 0x11: "mthi", 0x12: "mflo", 0x13: "mtlo",
    0x18: "mult", 0x19: "multu", 0x1A: "div", 0x1B: "divu",
    0x20: "add", 0x21: "addu", 0x22: "sub", 0x23: "subu",
    0x24: "and", 0x25: "or", 0x26: "xor", 0x27: "nor",
    0x2A: "slt", 0x2B: "sltu",
}

OPCODES = {
    0x02: "j", 0x03: "jal", 0x04: "beq", 0x05: "bne", 0x06: "blez", 0x07: "bgtz",
    0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu", 0x0C: "andi",
    0x0D: "ori", 0x0E: "xori", 0x0F: "lui",
    0x20: "lb", 0x21: "lh", 0x22: "lwl", 0x23: "lw", 0x24: "lbu", 0x25: "lhu",
    0x26: "lwr", 0x28: "sb", 0x29: "sh", 0x2A: "swl", 0x2B: "sw", 0x2E: "swr",
}

LOADS_STORES = {
    "lb", "lh", "lwl", "lw", "lbu", "lhu", "lwr", "sb", "sh", "swl", "sw", "swr",
}


class Image:
    """The executable, addressable by virtual address."""

    def __init__(self, path: str):
        self.data = open(path, "rb").read()
        if self.data[:8] != b"PS-X EXE":
            raise ValueError(f"{path} is not a PS-X EXE")
        self.base, self.size = struct.unpack_from("<2I", self.data, 0x18)

    def holds(self, address: int) -> bool:
        return self.base <= address < self.base + self.size

    def word(self, address: int) -> int:
        at = address - self.base + TEXT_FILE_OFFSET
        return struct.unpack_from("<I", self.data, at)[0]

    def words(self):
        """Every (address, word) in the text section."""
        for at in range(0, self.size, 4):
            yield (self.base + at,
                   struct.unpack_from("<I", self.data, TEXT_FILE_OFFSET + at)[0])


def decode(address: int, word: int) -> str:
    """One instruction, in the shape the format notes are written in."""
    op = word >> 26
    rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    shift, funct = (word >> 6) & 31, word & 63
    imm = word & 0xFFFF
    signed = imm - 0x10000 if imm & 0x8000 else imm
    r = REGISTERS

    if word == 0:
        return "nop"
    if op == 0:
        name = SPECIAL.get(funct)
        if name is None:
            return f".word {word:#010x}"
        if name in ("sll", "srl", "sra"):
            return f"{name:6s}${r[rd]}, ${r[rt]}, {shift}"
        if name in ("jr",):
            return f"{name:6s}${r[rs]}"
        if name in ("jalr",):
            return f"{name:6s}${r[rd]}, ${r[rs]}"
        if name in ("mfhi", "mflo"):
            return f"{name:6s}${r[rd]}"
        if name in ("mthi", "mtlo"):
            return f"{name:6s}${r[rs]}"
        if name in ("mult", "multu", "div", "divu"):
            return f"{name:6s}${r[rs]}, ${r[rt]}"
        return f"{name:6s}${r[rd]}, ${r[rs]}, ${r[rt]}"
    if op == 1:
        name = {0: "bltz", 1: "bgez", 16: "bltzal", 17: "bgezal"}.get(rt, "bcond")
        return f"{name:6s}${r[rs]}, {address + 4 + signed * 4:#010x}"
    if op in (0x10, 0x11, 0x12, 0x13):
        return f"cop{op & 3:<3d}{word & 0x1FFFFFF:#09x}"

    name = OPCODES.get(op)
    if name is None:
        return f".word {word:#010x}"
    if name in ("j", "jal"):
        return f"{name:6s}{((address + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2):#010x}"
    if name in ("beq", "bne"):
        return f"{name:6s}${r[rs]}, ${r[rt]}, {address + 4 + signed * 4:#010x}"
    if name in ("blez", "bgtz"):
        return f"{name:6s}${r[rs]}, {address + 4 + signed * 4:#010x}"
    if name == "lui":
        return f"{name:6s}${r[rt]}, {imm:#06x}"
    if name in LOADS_STORES:
        return f"{name:6s}${r[rt]}, {signed:#x}(${r[rs]})"
    return f"{name:6s}${r[rt]}, ${r[rs]}, {signed}"


def listing(image: Image, start: int, count: int) -> list[str]:
    out = []
    for n in range(count):
        address = start + n * 4
        if not image.holds(address):
            break
        word = image.word(address)
        out.append(f"{address:08x}  {word:08x}  {decode(address, word)}")
    return out


def callers(image: Image, target: int) -> list[int]:
    """Every `jal` to an address, and every `j` to it."""
    found = []
    for address, word in image.words():
        op = word >> 26
        if op in (2, 3):
            dest = ((address + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
            if dest == target:
                found.append(address)
    return found


def constants(image: Image, value: int) -> list[int]:
    """Every instruction whose 16-bit immediate is this value.

    Useful for a landmark like a structure offset: an `lw $v0, 0x50($s0)`
    reading the resident size would carry 0x50 as its immediate.
    """
    found = []
    for address, word in image.words():
        if (word & 0xFFFF) == (value & 0xFFFF) and (word >> 26) in OPCODES:
            found.append(address)
    return found


def self_relative(image: "Image", field: int) -> list[tuple[int, int]]:
    """Sites resolving a self-relative pointer at `field`.

    The idiom this format is written in, and the one the notes quote:

        lw    $v0, 0x18($v1)      ; the field
        addiu $v0, $v0, 0x18      ; bias it by its own offset
        addu  $v1, $v1, $v0       ; and add the base

    So the tell is a load of the field followed, within a short window and on
    the same register, by an `addiu` of exactly that offset. Finding none for a
    field is the strongest statement this tool can make about it.
    """
    found = []
    window = 8
    text = list(image.words())
    by_index = {address: n for n, (address, _) in enumerate(text)}
    for n, (address, word) in enumerate(text):
        if (word >> 26) != 0x23:                      # lw
            continue
        if (word & 0xFFFF) != (field & 0xFFFF):
            continue
        rt = (word >> 16) & 31
        for m in range(n + 1, min(n + 1 + window, len(text))):
            later = text[m][1]
            if (later >> 26) != 0x09:                 # addiu
                continue
            if ((later >> 21) & 31) != rt or ((later >> 16) & 31) != rt:
                continue
            if (later & 0xFFFF) == (field & 0xFFFF):
                found.append((address, text[m][0]))
                break
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    image = Image(argv[1])
    if argv[2] == "--refs":
        target = int(argv[3], 0)
        for site in callers(image, target):
            print(f"{site:#010x}  calls {target:#010x}")
        return 0
    if argv[2] == "--imm":
        value = int(argv[3], 0)
        for site in constants(image, value):
            print(f"{site:#010x}  {decode(site, image.word(site))}")
        return 0
    start = int(argv[2], 0)
    count = int(argv[3]) if len(argv) > 3 else 32
    print("\n".join(listing(image, start, count)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
