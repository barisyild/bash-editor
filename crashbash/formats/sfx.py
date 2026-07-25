"""Crash Bash SFX containers: a PS1 sound bank plus its sequences.

The file opens with a pointer table -- VB sample data, VH bank header, then one
pointer per SEQ. The table's own length gives the sequence count, since the first
pointer is also the offset where the data begins.

VB + VH together are a standard PS1 VAB: the VH describes programs and tones, the
VB holds SPU-ADPCM samples. One detail is not standard -- the VAG offset table is
trimmed to the samples that exist instead of always being 256 entries, so the VH
is exactly

    0x20 + 128*16 + programs*16*32 + (vags + 1)*2

bytes long. That holds for every bank in the NTSC-U release; the music banks pad
after it to align the sequence that follows.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binreader import Reader

MAX_SEQUENCES = 5
VAB_MAGIC = b"pBAV"  # 'VABp' as a big-endian word
SEQ_MAGIC = b"pQES"  # 'SEQp'

VAB_HEADER_SIZE = 0x20
PROGRAM_COUNT = 128  # always 128 slots, whatever the header says is in use
PROGRAM_SIZE = 16
TONES_PER_PROGRAM = 16
TONE_SIZE = 32

# SPU-ADPCM predictor coefficients, in 1/64ths.
ADPCM_FILTERS = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))

ADPCM_BLOCK = 16
ADPCM_SAMPLES_PER_BLOCK = 28

# The rate a VAG plays at when its tone is struck at its own centre note.
BASE_SAMPLE_RATE = 44100


@dataclass
class Tone:
    """One tone attribute: a sample plus how it should be pitched and shaped."""

    priority: int
    mode: int
    volume: int
    pan: int
    centre_note: int
    fine_tune: int
    min_note: int
    max_note: int
    adsr1: int
    adsr2: int
    program: int
    vag: int  # 1-based index into the bank's samples; 0 means none

    @property
    def sample_rate(self) -> int:
        """Playback rate at middle C, from the tone's centre note and fine tune.

        The SPU pitches a sample by note distance from its centre, so a sample
        whose centre note is 60 plays at the base rate.
        """
        semitones = (self.centre_note - 60) + self.fine_tune / 128.0
        return max(1000, min(96000, round(BASE_SAMPLE_RATE * 2.0 ** (-semitones / 12.0))))


@dataclass
class Program:
    index: int
    tone_count: int
    volume: int
    pan: int
    attribute: int
    tones: list[Tone] = field(default_factory=list)


@dataclass
class Sample:
    """One VAG: a run of 16-byte SPU-ADPCM blocks."""

    index: int
    data: bytes
    rate: int = BASE_SAMPLE_RATE

    @property
    def frame_count(self) -> int:
        return len(self.data) // ADPCM_BLOCK * ADPCM_SAMPLES_PER_BLOCK

    @property
    def duration(self) -> float:
        return self.frame_count / self.rate if self.rate else 0.0

    def decode(self) -> bytes:
        """Decode to little-endian signed 16-bit mono PCM."""
        return decode_adpcm(self.data)

    def to_wav(self) -> bytes:
        return build_wav(self.decode(), self.rate)


def decode_adpcm(data: bytes) -> bytes:
    """SPU-ADPCM -> PCM16.

    Each 16-byte block is a shift/filter byte, a flags byte, then 28 four-bit
    samples packed low nibble first. A sample is the nibble scaled up by the
    block's shift plus a two-tap prediction from the previous two outputs.
    """
    out = bytearray()
    prev1 = prev2 = 0
    for base in range(0, len(data) - ADPCM_BLOCK + 1, ADPCM_BLOCK):
        header = data[base]
        shift = header & 0x0F
        filter_index = (header >> 4) & 0x0F
        if filter_index >= len(ADPCM_FILTERS):
            filter_index = 0
        f0, f1 = ADPCM_FILTERS[filter_index]
        # Shifts above 12 are the hardware's "silence" encoding.
        shift = 12 if shift > 12 else shift

        for i in range(ADPCM_SAMPLES_PER_BLOCK):
            byte = data[base + 2 + (i >> 1)]
            nibble = (byte >> 4) if (i & 1) else (byte & 0x0F)
            if nibble > 7:
                nibble -= 16
            value = (nibble << 12) >> shift
            value += (prev1 * f0 + prev2 * f1) >> 6
            value = -32768 if value < -32768 else (32767 if value > 32767 else value)
            prev2, prev1 = prev1, value
            out += struct.pack("<h", value)
    return bytes(out)


def build_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Wrap PCM16 in a RIFF/WAVE container."""
    byte_rate = rate * channels * 2
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + len(pcm)),
            b"WAVEfmt ",
            struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, channels * 2, 16),
            b"data",
            struct.pack("<I", len(pcm)),
            pcm,
        ]
    )


@dataclass
class SoundBank:
    vb: bytes = b""  # raw ADPCM sample data
    vh: bytes = b""  # VAB header: programs, tones, VAG offsets
    sequences: list[bytes] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Parsed from the VH.
    version: int = 0
    bank_id: int = 0
    master_volume: int = 0
    master_pan: int = 0
    programs: list[Program] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)

    @property
    def is_vab(self) -> bool:
        return self.vh[:4] == VAB_MAGIC

    def files(self, stem: str = "data") -> list[tuple[str, bytes]]:
        out = [(f"{stem}.vb", self.vb), (f"{stem}.vh", self.vh)]
        out += [(f"{stem}_{i}.seq", s) for i, s in enumerate(self.sequences)]
        return [(name, blob) for name, blob in out if blob]

    def wav_files(self, stem: str = "sample") -> list[tuple[str, bytes]]:
        return [(f"{stem}_{s.index:03d}.wav", s.to_wav()) for s in self.samples]


def _parse_vab(bank: SoundBank) -> None:
    """Read the VAB header and slice the VB into samples."""
    vh = bank.vh
    if len(vh) < VAB_HEADER_SIZE or vh[:4] != VAB_MAGIC:
        return

    bank.version, bank.bank_id, _size = struct.unpack_from("<3I", vh, 4)
    _reserved, num_programs, _num_tones, num_vags = struct.unpack_from("<4H", vh, 0x10)
    bank.master_volume, bank.master_pan = vh[0x18], vh[0x19]

    if not (0 < num_programs <= PROGRAM_COUNT and 0 < num_vags <= 254):
        bank.warnings.append(
            f"implausible VAB counts: {num_programs} programs, {num_vags} samples"
        )
        return

    tone_base = VAB_HEADER_SIZE + PROGRAM_COUNT * PROGRAM_SIZE
    vag_base = tone_base + num_programs * TONES_PER_PROGRAM * TONE_SIZE
    if vag_base + (num_vags + 1) * 2 > len(vh):
        bank.warnings.append("VAB header is shorter than its own tables")
        return

    for index in range(num_programs):
        offset = VAB_HEADER_SIZE + index * PROGRAM_SIZE
        tone_count, volume, pan, attribute = vh[offset : offset + 4]
        program = Program(index, tone_count, volume, pan, attribute)
        for slot in range(min(tone_count, TONES_PER_PROGRAM)):
            at = tone_base + (index * TONES_PER_PROGRAM + slot) * TONE_SIZE
            fields = vh[at : at + 8]
            adsr1, adsr2 = struct.unpack_from("<2H", vh, at + 16)
            prog, vag = struct.unpack_from("<2h", vh, at + 20)
            program.tones.append(
                Tone(
                    priority=fields[0],
                    mode=fields[1],
                    volume=fields[2],
                    pan=fields[3],
                    centre_note=fields[4],
                    fine_tune=fields[5],
                    min_note=fields[6],
                    max_note=fields[7],
                    adsr1=adsr1,
                    adsr2=adsr2,
                    program=prog,
                    vag=vag,
                )
            )
        bank.programs.append(program)

    # Offsets are in 8-byte units and cumulative; entry 0 is always empty.
    sizes = struct.unpack_from(f"<{num_vags + 1}H", vh, vag_base)
    cursor = 0
    for index in range(1, num_vags + 1):
        length = sizes[index] * 8
        if cursor + length > len(bank.vb):
            bank.warnings.append(f"sample {index} runs past the end of the VB")
            break
        bank.samples.append(Sample(index, bank.vb[cursor : cursor + length]))
        cursor += length

    # A tone names the rate its sample should play at; take the first that does.
    for program in bank.programs:
        for tone in program.tones:
            slot = tone.vag - 1
            if 0 <= slot < len(bank.samples):
                bank.samples[slot].rate = tone.sample_rate


def read_bank(data: bytes | Reader) -> SoundBank:
    reader = data if isinstance(data, Reader) else Reader(data)
    bank = SoundBank()

    if len(reader) < 12:
        bank.warnings.append("file too small to be an SFX container")
        return bank

    # The table's own first entry is where the data starts, so it also gives the
    # entry count. Slot 0 is the VB, slot 1 the VH, and the rest are sequences --
    # except that a bank with no sequences still has a trailing entry, holding
    # the end of the data rather than a pointer to anything.
    first = reader.i32()
    if first % 4 or not 12 <= first <= 4 * (3 + MAX_SEQUENCES):
        bank.warnings.append(f"implausible pointer table size {first}")
        return bank

    reader.seek(0)
    pointers = list(reader.array_i32(first // 4))
    ptr_vb, ptr_vh = pointers[0], pointers[1]

    if not 0 <= ptr_vb <= ptr_vh <= len(reader):
        bank.warnings.append("pointer table out of order")
        return bank

    starts = [p for p in pointers[2:] if 0 < p < len(reader)]
    bounds = starts + [len(reader)]

    reader.seek(ptr_vb)
    bank.vb = reader.bytes(ptr_vh - ptr_vb)

    reader.seek(ptr_vh)
    bank.vh = reader.bytes(max(0, min(bounds[0], len(reader)) - ptr_vh))

    for i, start in enumerate(starts):
        end = bounds[i + 1]
        reader.seek(start)
        blob = reader.bytes(max(0, end - start))
        if blob[:4] != SEQ_MAGIC:
            bank.warnings.append(f"sequence {i} does not start with SEQp")
            continue
        bank.sequences.append(blob)

    _parse_vab(bank)
    return bank
