"""Little-endian binary reader, modeled on CTRFramework's BinaryReaderEx.

Bash data is PS1 data: everything is little endian, fixed point values are
either 8.8 (`GTE_SCALE_SMALL`) or 20.12 (`GTE_SCALE_LARGE`).
"""

from __future__ import annotations

import struct
from typing import BinaryIO

GTE_SCALE_LARGE = 1.0 / (1 << 12)  # 4096 = 1.0, 20.12 fixed point
GTE_SCALE_SMALL = 1.0 / (1 << 8)  # 256 = 1.0, 8.8 fixed point

SECTOR_SIZE = 2048


class Reader:
    """Random-access reader over an in-memory buffer."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = bytes(data)
        self.pos = 0

    @classmethod
    def from_file(cls, path) -> "Reader":
        with open(path, "rb") as f:
            return cls(f.read())

    @classmethod
    def from_stream(cls, stream: BinaryIO) -> "Reader":
        return cls(stream.read())

    def __len__(self) -> int:
        return len(self.data)

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def seek(self, pos: int) -> None:
        self.pos = pos

    def skip(self, count: int) -> None:
        self.pos += count

    def hexpos(self) -> str:
        return f"0x{self.pos:X}"

    # -- primitives ------------------------------------------------------

    def _unpack(self, fmt: str, size: int):
        if self.pos + size > len(self.data):
            raise EOFError(
                f"read past end of buffer at 0x{self.pos:X} "
                f"(want {size} bytes, {self.remaining} left)"
            )
        value = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return value

    def u8(self) -> int:
        return self._unpack("<B", 1)[0]

    def i8(self) -> int:
        return self._unpack("<b", 1)[0]

    def u16(self) -> int:
        return self._unpack("<H", 2)[0]

    def i16(self) -> int:
        return self._unpack("<h", 2)[0]

    def u32(self) -> int:
        return self._unpack("<I", 4)[0]

    def i32(self) -> int:
        return self._unpack("<i", 4)[0]

    def f32(self) -> float:
        return self._unpack("<f", 4)[0]

    def bytes(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise EOFError(
                f"read past end of buffer at 0x{self.pos:X} "
                f"(want {count} bytes, {self.remaining} left)"
            )
        out = self.data[self.pos : self.pos + count]
        self.pos += count
        return out

    def string_fixed(self, count: int) -> str:
        return self.bytes(count).decode("ascii", errors="replace")

    # -- arrays ----------------------------------------------------------

    def array_u16(self, count: int) -> tuple[int, ...]:
        return self._unpack(f"<{count}H", count * 2)

    def array_i16(self, count: int) -> tuple[int, ...]:
        return self._unpack(f"<{count}h", count * 2)

    def array_u32(self, count: int) -> tuple[int, ...]:
        return self._unpack(f"<{count}I", count * 4)

    def array_i32(self, count: int) -> tuple[int, ...]:
        return self._unpack(f"<{count}i", count * 4)

    # -- vectors ---------------------------------------------------------

    def vec3s(self, scale: float = 1.0) -> tuple[float, float, float]:
        x, y, z = self.array_i16(3)
        return (x * scale, y * scale, z * scale)

    def vec3s_padded(self, scale: float = 1.0) -> tuple[float, float, float]:
        """3 int16 components followed by one int16 of padding."""
        x, y, z, _pad = self.array_i16(4)
        return (x * scale, y * scale, z * scale)

    # -- peeking ---------------------------------------------------------

    def peek_u32(self, at: int | None = None) -> int:
        at = self.pos if at is None else at
        return struct.unpack_from("<I", self.data, at)[0]

    def peek_i32(self, at: int | None = None) -> int:
        at = self.pos if at is None else at
        return struct.unpack_from("<i", self.data, at)[0]
