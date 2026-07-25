"""Minimal MIPS R3000 disassembly helper for the Crash Bash PS-X EXE.

Used to check the format notes in crashbash/formats/ against what the game
actually does, rather than inferring everything from the data alone.
"""
from __future__ import annotations
import struct, sys
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

class Exe:
    def __init__(self, path):
        raw = open(path, 'rb').read()
        assert raw[:8] == b'PS-X EXE', 'not a PS-X EXE'
        self.pc, _gp, self.base, self.size = struct.unpack_from('<4I', raw, 0x10)
        self.text = raw[0x800:0x800 + self.size]
        self.md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
        self.md.detail = True

    # `off` is an index into self.text, which starts at EXE file offset 0x800.
    # To go from a raw file offset use file_va() instead.
    def off(self, va): return va - self.base
    def va(self, off): return self.base + off
    def file_va(self, file_offset): return self.base + file_offset - 0x800
    def word(self, va): return struct.unpack_from('<I', self.text, self.off(va))[0]

    def scan(self, predicate):
        """Yield (va, word) for every text word matching predicate(word)."""
        for offset in range(0, len(self.text) - 3, 4):
            word = struct.unpack_from('<I', self.text, offset)[0]
            if predicate(word):
                yield self.va(offset), word

    def dis(self, va, count=40):
        """Disassemble `count` words, stepping over anything capstone rejects.

        GTE (coprocessor 2) opcodes are common in this code and capstone does not
        decode all of them, so a plain disasm() run stops early.
        """
        out = []
        for n in range(count):
            addr = va + n * 4
            chunk = self.text[self.off(addr):self.off(addr) + 4]
            if len(chunk) < 4:
                break
            decoded = list(self.md.disasm(chunk, addr, 1))
            if decoded:
                out.append((addr, decoded[0].mnemonic, decoded[0].op_str))
            else:
                w = struct.unpack('<I', chunk)[0]
                out.append((addr, '.word', gte_name(w)))
        return out

    def show(self, va, count=40, mark=()):
        for addr, mnemonic, ops in self.dis(va, count):
            flag = ' <<<' if addr in mark else ''
            print(f'{addr:08X}  {mnemonic:<9} {ops}{flag}')


# The GTE ops this code leans on; anything else is printed as a raw word.
_GTE_OPS = {
    0x01: 'RTPS', 0x06: 'NCLIP', 0x0C: 'OP', 0x10: 'DPCS', 0x11: 'INTPL',
    0x12: 'MVMVA', 0x13: 'NCDS', 0x14: 'CDP', 0x16: 'NCDT', 0x1B: 'NCCS',
    0x1C: 'CC', 0x1E: 'NCS', 0x20: 'NCT', 0x28: 'SQR', 0x29: 'DCPL',
    0x2A: 'DPCT', 0x2D: 'AVSZ3', 0x2E: 'AVSZ4', 0x30: 'RTPT', 0x3D: 'GPF',
    0x3E: 'GPL', 0x3F: 'NCCT',
}


def gte_name(word: int) -> str:
    if (word >> 25) == 0x25:  # COP2 with the "imm25" command bit set
        return f'gte:{_GTE_OPS.get(word & 0x3F, hex(word & 0x3F))}  (0x{word:08X})'
    if (word >> 26) == 0x12:  # COP2 register moves
        return f'cop2 0x{word:08X}'
    return f'0x{word:08X}'


def addiu(w, imm):   # addiu rt, rs, imm
    return (w >> 26) == 0x09 and (w & 0xFFFF) == imm
def lw(w, imm):      # lw rt, imm(rs)
    return (w >> 26) == 0x23 and (w & 0xFFFF) == imm
def lh(w, imm):
    return (w >> 26) == 0x21 and (w & 0xFFFF) == imm
def lhu(w, imm):
    return (w >> 26) == 0x25 and (w & 0xFFFF) == imm

if __name__ == '__main__':
    e = Exe(sys.argv[1])
    print(f'base 0x{e.base:08X} size 0x{e.size:X} entry 0x{e.pc:08X}')
