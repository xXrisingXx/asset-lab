"""Lossless replacement of ordinary ZIP members; untouched local records stay identical."""
import io
from pathlib import PurePosixPath
import struct
import zipfile
import zlib


def safe_member(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts:
        raise ValueError('Unsafe archive member: ' + name)
    return name


def replace_zip(original: bytes, replacements: dict[str, bytes]) -> bytes:
    """Keep entry order, compression, metadata, comments and all unchanged payloads.

    Deliberately reject layouts not present in this game instead of guessing at them.
    CRCs and offsets for edited records are updated; archive length need not stay fixed.
    """
    end = original.rfind(b'PK\x05\x06', max(0, len(original) - 65557))
    if end < 0 or end + 22 > len(original):
        raise ValueError('Missing ZIP end record')
    disk, cd_disk, count_disk, count, cd_size, cd_start, comment = struct.unpack_from('<4H2IH', original, end + 4)
    if disk or cd_disk or count != count_disk or count == 65535 or cd_start + cd_size != end or end + 22 + comment != len(original):
        raise ValueError('Unsupported ZIP layout (split, ZIP64, or trailing records)')
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        infos = archive.infolist()
        names = [safe_member(i.filename) for i in infos]
        if len(names) != len(set(names)) or len(infos) != count:
            raise ValueError('Duplicate or inconsistent ZIP entries')
        if not replacements.keys() <= set(names):
            raise ValueError('Replacement does not belong to this archive')
        central = []
        pos = cd_start
        for info in infos:
            if original[pos:pos + 4] != b'PK\x01\x02':
                raise ValueError('Invalid central directory')
            a, b, c = struct.unpack_from('<3H', original, pos + 28)
            length = 46 + a + b + c
            central.append(bytearray(original[pos:pos + length]))
            pos += length
        if pos != end:
            raise ValueError('Unexpected central-directory data')
        ordered = sorted(infos, key=lambda i: i.header_offset)
        result = bytearray(original[:ordered[0].header_offset] if ordered else b'')
        offsets, changed = {}, {}
        for index, info in enumerate(ordered):
            start = info.header_offset
            finish = ordered[index + 1].header_offset if index + 1 < len(ordered) else cd_start
            record = original[start:finish]
            if record[:4] != b'PK\x03\x04' or len(record) < 30:
                raise ValueError('Invalid local header')
            offsets[info.filename] = len(result)
            if info.filename not in replacements:
                result.extend(record)
                continue
            if info.flag_bits & (1 | 8) or info.compress_type not in (0, 8):
                raise ValueError('Unsupported encryption, descriptor, or compression')
            fn, extra = struct.unpack_from('<2H', record, 26)
            header_len = 30 + fn + extra
            if header_len + info.compress_size > len(record):
                raise ValueError('Truncated local record')
            data = replacements[info.filename]
            if info.compress_type == 8:
                compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
                packed = compressor.compress(data) + compressor.flush()
            else:
                packed = data
            values = (zlib.crc32(data) & 0xffffffff, len(packed), len(data))
            header = bytearray(record[:header_len])
            struct.pack_into('<3I', header, 14, *values)
            result.extend(header)
            result.extend(packed)
            result.extend(record[header_len + info.compress_size:])
            changed[info.filename] = values
        new_cd = len(result)
        for info, header in zip(infos, central):
            struct.pack_into('<I', header, 42, offsets[info.filename])
            if info.filename in changed:
                struct.pack_into('<3I', header, 16, *changed[info.filename])
            result.extend(header)
        footer = bytearray(original[end:])
        struct.pack_into('<2I', footer, 12, len(result) - new_cd, new_cd)
        result.extend(footer)
        output = bytes(result)
    with zipfile.ZipFile(io.BytesIO(output)) as check:
        if check.testzip() is not None:
            raise ValueError('Rebuilt ZIP failed CRC validation')
        for name, data in replacements.items():
            if check.read(name) != data:
                raise ValueError('Rebuilt ZIP failed replacement validation')
    return output
