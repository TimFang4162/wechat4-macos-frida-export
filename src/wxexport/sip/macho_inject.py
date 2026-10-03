#!/usr/bin/env python3
"""向 Mach-O 的全部 64 位 slice 注入一个 LC_LOAD_DYLIB。"""

import pathlib
import struct
import sys

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
MH_MAGIC_64 = 0xFEEDFACF
LC_LOAD_DYLIB = 0xC
LC_SEGMENT_64 = 0x19


def _align8(value):
    return (value + 7) & ~7


def _slices(data):
    if len(data) < 4:
        raise ValueError("Mach-O 文件过小")
    magic_be = struct.unpack_from(">I", data, 0)[0]
    magic_le = struct.unpack_from("<I", data, 0)[0]
    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack_from(">I", data, 4)[0]
        cursor = 8
        result = []
        for _ in range(count):
            if magic_be == FAT_MAGIC:
                _, _, offset, size, _ = struct.unpack_from(">iiIII", data, cursor)
                cursor += 20
            else:
                _, _, offset, size, _, _, _ = struct.unpack_from(
                    ">iiQQIII", data, cursor
                )
                cursor += 32
            result.append((offset, size))
        return result
    if magic_le == MH_MAGIC_64:
        return [(0, len(data))]
    raise ValueError("仅支持 64 位 Mach-O 或通用 Mach-O")


def _commands(data, base, count):
    cursor = base + 32
    for _ in range(count):
        command, size = struct.unpack_from("<II", data, cursor)
        if size < 8:
            raise ValueError(f"非法 load command: offset={cursor}, size={size}")
        yield cursor, command, size
        cursor += size


def _first_section_offset(data, base, count):
    minimum = None
    for cursor, command, _ in _commands(data, base, count):
        if command != LC_SEGMENT_64:
            continue
        section_count = struct.unpack_from("<I", data, cursor + 64)[0]
        section = cursor + 72
        for _ in range(section_count):
            offset = struct.unpack_from("<I", data, section + 48)[0]
            if offset and (minimum is None or offset < minimum):
                minimum = offset
            section += 80
    return minimum


def _already_loaded(data, base, count, dylib):
    expected = dylib.encode()
    for cursor, command, size in _commands(data, base, count):
        if command != LC_LOAD_DYLIB:
            continue
        name_offset = struct.unpack_from("<I", data, cursor + 8)[0]
        raw = data[cursor + name_offset:cursor + size]
        if raw.split(b"\0", 1)[0] == expected:
            return True
    return False


def _inject_slice(buffer, base, dylib):
    if struct.unpack_from("<I", buffer, base)[0] != MH_MAGIC_64:
        raise ValueError(f"slice {base} 不是 64 位 Mach-O")
    _, _, _, _, count, commands_size, _, _ = struct.unpack_from(
        "<IiiIIIII", buffer, base
    )
    if _already_loaded(buffer, base, count, dylib):
        return False

    name = dylib.encode() + b"\0"
    command_size = _align8(24 + len(name))
    command = struct.pack("<IIIIII", LC_LOAD_DYLIB, command_size, 24, 2, 0, 0)
    command += name + b"\0" * (command_size - 24 - len(name))

    insert_at = base + 32 + commands_size
    first_section = _first_section_offset(buffer, base, count)
    if first_section is None:
        raise ValueError(f"slice {base} 未发现 section")
    available = base + first_section - insert_at
    if available < command_size:
        raise ValueError(
            f"slice {base} 的 Mach-O header padding 不足："
            f"需要 {command_size}，实际 {available}"
        )
    if any(buffer[insert_at:insert_at + command_size]):
        raise ValueError(f"slice {base} 的 header padding 非空，拒绝覆盖")

    buffer[insert_at:insert_at + command_size] = command
    struct.pack_into("<II", buffer, base + 16, count + 1,
                     commands_size + command_size)
    return True


def inject(path, dylib):
    path = pathlib.Path(path)
    original = path.read_bytes()
    buffer = bytearray(original)
    changed = False
    for base, _ in _slices(original):
        changed |= _inject_slice(buffer, base, dylib)
    if changed:
        path.write_bytes(buffer)
    return changed


def main():
    if len(sys.argv) != 3:
        print(f"用法: {sys.argv[0]} MACHO DYLIB_LOAD_PATH", file=sys.stderr)
        return 2
    try:
        changed = inject(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, struct.error) as exc:
        print(f"[!] 注入失败: {exc}", file=sys.stderr)
        return 1
    print("[+] 已注入" if changed else "[*] 已存在", sys.argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
