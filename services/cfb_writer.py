"""OLE Compound File 재구성. 원본 HWP의 디렉터리 트리를 유지하고 스트림만 교체한다."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import olefile

FREE = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF
SECTOR = 512
MINI_SECTOR = 64
MINI_CUTOFF = 4096
DIR_SIZE = 128
ENTRIES_PER_SECTOR = SECTOR // DIR_SIZE


@dataclass
class _Dir:
    name: str
    entry_type: int
    color: int
    sid_left: int
    sid_right: int
    sid_child: int
    clsid: bytes
    user_flags: int
    create_time: int
    modify_time: int
    start: int = 0
    size: int = 0
    data: bytes = b""


def _align(data: bytes, size: int) -> bytes:
    extra = (-len(data)) % size
    return data if extra == 0 else data + b"\x00" * extra


def _pack_dir(entry: _Dir) -> bytes:
    name_utf16 = entry.name.encode("utf-16le") + b"\x00\x00"
    if len(name_utf16) > 64:
        name_utf16 = name_utf16[:64]
    name_field = name_utf16.ljust(64, b"\x00")
    clsid = entry.clsid if len(entry.clsid) == 16 else bytes(16)
    return struct.pack(
        "<64sHBBIII16sIQQIQ",
        name_field,
        len(name_utf16),
        entry.entry_type,
        entry.color,
        entry.sid_left,
        entry.sid_right,
        entry.sid_child,
        clsid,
        entry.user_flags,
        entry.create_time,
        entry.modify_time,
        entry.start,
        entry.size,
    )


def _chain(count: int, start: int) -> list[int]:
    if count <= 0:
        return []
    fat = [start + i + 1 for i in range(count)]
    fat[-1] = ENDOFCHAIN
    return fat


def _clsid_bytes(value) -> bytes:
    if not value:
        return bytes(16)
    if isinstance(value, bytes) and len(value) == 16:
        return value
    text = str(value).replace("-", "")
    if len(text) == 32:
        try:
            return bytes.fromhex(text)
        except ValueError:
            return bytes(16)
    return bytes(16)


def read_streams(path) -> dict[str, bytes]:
    ole = olefile.OleFileIO(str(path))
    streams = {}
    for parts in ole.listdir():
        key = "/".join(parts)
        streams[key] = ole.openstream(parts).read()
    ole.close()
    return streams


def rebuild_ole(template_path, replacements: dict[str, bytes]) -> bytes:
    """템플릿 OLE를 읽고 replacements 스트림을 덮어쓴 뒤 새 CFB 바이트를 만든다."""
    ole = olefile.OleFileIO(str(template_path))
    dirs: list[_Dir | None] = []
    for item in ole.direntries:
        if item is None:
            dirs.append(None)
            continue
        path = None
        data = b""
        if item.entry_type == 2:
            if item.sid == 0:
                data = b""
            else:
                # find stream path
                data = b""
        dirs.append(
            _Dir(
                name=item.name,
                entry_type=item.entry_type,
                color=item.color,
                sid_left=item.sid_left,
                sid_right=item.sid_right,
                sid_child=item.sid_child,
                clsid=_clsid_bytes(item.clsid),
                user_flags=item.dwUserFlags,
                create_time=item.createTime,
                modify_time=item.modifyTime,
                size=item.size,
            )
        )

    path_by_sid: dict[int, str] = {}
    for parts in ole.listdir(streams=True, storages=False):
        sid = ole._find(parts)
        path_by_sid[sid] = "/".join(parts)
        data = ole.openstream(parts).read()
        key = "/".join(parts)
        if key in replacements:
            data = replacements[key]
        dirs[sid].data = data
        dirs[sid].size = len(data)
    ole.close()

    mini_payloads: list[tuple[int, bytes]] = []
    fat_payloads: list[tuple[int, bytes]] = []
    for index, entry in enumerate(dirs):
        if entry is None or entry.entry_type != 2:
            continue
        if entry.size < MINI_CUTOFF:
            mini_payloads.append((index, _align(entry.data, MINI_SECTOR)))
        else:
            fat_payloads.append((index, _align(entry.data, SECTOR)))

    mini_blob = b"".join(blob for _, blob in mini_payloads)
    mini_blob = _align(mini_blob, SECTOR)

    mini_fat: list[int] = []
    mini_cursor = 0
    for index, blob in mini_payloads:
        count = len(blob) // MINI_SECTOR
        dirs[index].start = mini_cursor
        mini_fat.extend(_chain(count, mini_cursor))
        mini_cursor += count
    if mini_fat:
        mini_fat = _align_fat(mini_fat, SECTOR // 4)

    # sector allocation: [mini stream][regular streams][miniFAT][directory][FAT]
    sectors: list[bytes] = []

    def add_blob(blob: bytes, sector_size: int = SECTOR) -> int:
        start = len(sectors)
        aligned = _align(blob, sector_size)
        for offset in range(0, len(aligned), sector_size):
            sectors.append(aligned[offset : offset + sector_size])
        return start

    fat_next: list[int] = []

    def add_chain(blob: bytes) -> int:
        start = len(sectors)
        aligned = _align(blob, SECTOR)
        count = len(aligned) // SECTOR
        for offset in range(0, len(aligned), SECTOR):
            sectors.append(aligned[offset : offset + SECTOR])
        fat_next.extend(_chain(count, start))
        return start

    # We'll build FAT after knowing layout; use placeholder lists
    stream_sectors: dict[int, tuple[int, int]] = {}  # sid -> (start, count)

    # Mini-stream as root data
    mini_start = 0
    mini_count = len(mini_blob) // SECTOR if mini_blob else 0
    if mini_count:
        for offset in range(0, len(mini_blob), SECTOR):
            sectors.append(mini_blob[offset : offset + SECTOR])
        if dirs[0]:
            dirs[0].start = mini_start
            dirs[0].size = sum(len(blob) for _, blob in mini_payloads)
    else:
        if dirs[0]:
            dirs[0].start = ENDOFCHAIN
            dirs[0].size = 0

    for index, blob in fat_payloads:
        start = len(sectors)
        count = len(blob) // SECTOR
        for offset in range(0, len(blob), SECTOR):
            sectors.append(blob[offset : offset + SECTOR])
        stream_sectors[index] = (start, count)
        dirs[index].start = start

    mini_fat_start = ENDOFCHAIN
    mini_fat_count = 0
    if mini_fat:
        packed = b"".join(struct.pack("<I", value) for value in mini_fat)
        mini_fat_start = len(sectors)
        packed = _align(packed, SECTOR)
        mini_fat_count = len(packed) // SECTOR
        for offset in range(0, len(packed), SECTOR):
            sectors.append(packed[offset : offset + SECTOR])

    dir_bytes = b""
    slot_count = max(len(dirs), ENTRIES_PER_SECTOR)
    while slot_count % ENTRIES_PER_SECTOR:
        slot_count += 1
    for index in range(slot_count):
        entry = dirs[index] if index < len(dirs) else None
        if entry is None:
            dir_bytes += bytes(DIR_SIZE)
        else:
            dir_bytes += _pack_dir(entry)
    dir_start = len(sectors)
    dir_bytes = _align(dir_bytes, SECTOR)
    dir_count = len(dir_bytes) // SECTOR
    for offset in range(0, len(dir_bytes), SECTOR):
        sectors.append(dir_bytes[offset : offset + SECTOR])

    # FAT: number of sectors currently, plus FAT itself
    data_sector_count = len(sectors)

    def build_fat(fat_sector_count: int) -> list[int]:
        fat = [FREE] * (data_sector_count + fat_sector_count)
        # mini stream
        if mini_count:
            for i in range(mini_count):
                fat[mini_start + i] = mini_start + i + 1 if i + 1 < mini_count else ENDOFCHAIN
        for index, (start, count) in stream_sectors.items():
            for i in range(count):
                fat[start + i] = start + i + 1 if i + 1 < count else ENDOFCHAIN
        if mini_fat_count:
            for i in range(mini_fat_count):
                fat[mini_fat_start + i] = mini_fat_start + i + 1 if i + 1 < mini_fat_count else ENDOFCHAIN
        for i in range(dir_count):
            fat[dir_start + i] = dir_start + i + 1 if i + 1 < dir_count else ENDOFCHAIN
        fat_start = data_sector_count
        for i in range(fat_sector_count):
            fat[fat_start + i] = FATSECT
        return fat

    fat_sector_count = 1
    while True:
        needed = (data_sector_count + fat_sector_count + 127) // 128
        if needed <= fat_sector_count:
            break
        fat_sector_count = needed
    fat = build_fat(fat_sector_count)
    # 128 entries per FAT sector
    fat_table = fat + [FREE] * (fat_sector_count * 128 - len(fat))
    fat_blob = b"".join(struct.pack("<I", value) for value in fat_table[: fat_sector_count * 128])
    for offset in range(0, len(fat_blob), SECTOR):
        sectors.append(fat_blob[offset : offset + SECTOR])

    fat_start = data_sector_count
    difat = [fat_start + i for i in range(fat_sector_count)] + [FREE] * (109 - fat_sector_count)
    header = bytearray(SECTOR)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHHH", header, 0x18, 0x003E, 3, 0xFFFE, 9, 6, 0)
    struct.pack_into("<II", header, 0x28, 0, fat_sector_count)
    struct.pack_into("<I", header, 0x30, dir_start)
    struct.pack_into("<I", header, 0x38, MINI_CUTOFF)
    struct.pack_into("<I", header, 0x3C, mini_fat_start)
    struct.pack_into("<I", header, 0x40, mini_fat_count)
    struct.pack_into("<I", header, 0x44, ENDOFCHAIN)
    struct.pack_into("<I", header, 0x48, 0)
    for i, value in enumerate(difat):
        struct.pack_into("<I", header, 0x4C + i * 4, value)

    return bytes(header) + b"".join(sectors)


def _align_fat(entries: list[int], count: int) -> list[int]:
    extra = (-len(entries)) % count
    if extra:
        entries = entries + [FREE] * extra
    return entries
