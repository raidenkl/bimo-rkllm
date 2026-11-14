#!/usr/bin/env python3
"""Dump all GGUF metadata key-values from a .gguf file (header only, no tensor data read).

Uses seek() to skip large arrays so it works on 10GB+ files with tiny RSS.

Usage: python dump_gguf_meta.py /path/to/model.gguf
Exit code 0 = all assertions passed, 3 = assertion failure.
"""
import sys
import struct

GGUF_MAGIC = b"GGUF"

# gguf value types -> (struct fmt, byte size)
SCALAR = {
    0: ("<B", 1),   # UINT8
    1: ("<b", 1),   # INT8
    2: ("<H", 2),   # UINT16
    3: ("<h", 2),   # INT16
    4: ("<I", 4),   # UINT32
    5: ("<i", 4),   # INT32
    6: ("<f", 4),   # FLOAT32
    7: ("<?", 1),   # BOOL
    10: ("<Q", 8),  # UINT64
    11: ("<q", 8),  # INT64
    12: ("<d", 8),  # FLOAT64
}
T_STRING = 8
T_ARRAY = 9

# 大数组不读内容，只记录首尾，避免把 15 万条 tokenizer 词表读进内存
MAX_STR_KEEP = 1_000_000


class Reader:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.truncated = False

    def read(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError(f"truncated: wanted {n} got {len(b)}")
        return b

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.read(8))[0]

    def string(self):
        n = self.u64()
        if n > MAX_STR_KEEP:
            # 超长字符串（不该出现），跳过
            self.f.seek(n, 1)
            return f"<str len={n} skipped>"
        return self.read(n).decode("utf-8", "replace")

    def value(self, t):
        if t == T_STRING:
            return self.string()
        if t in SCALAR:
            fmt, size = SCALAR[t]
            if t == 7:  # bool 单独处理，struct 的 <? 可用
                return bool(struct.unpack(fmt, self.read(size))[0])
            return struct.unpack(fmt, self.read(size))[0]
        if t == T_ARRAY:
            et = self.u32()
            cnt = self.u64()
            if et == T_STRING:
                first = None
                last = None
                for i in range(cnt):
                    v = self.string()
                    if i == 0:
                        first = v
                    last = v
                return f"<array<string> n={cnt} first={first!r} last={last!r}>"
            if et in SCALAR:
                fmt, size = SCALAR[et]
                self.f.seek(cnt * size, 1)
                return f"<array et={et} n={cnt} skipped>"
            raise ValueError(f"unsupported array elem type {et}")
        raise ValueError(f"unsupported value type {t}")


def main(path):
    r = Reader(path)
    if r.read(4) != GGUF_MAGIC:
        print("NOT A GGUF FILE", file=sys.stderr)
        return 1
    version = r.u32()
    n_tensors = r.u64()
    n_kv = r.u64()
    print(f"# gguf_version = {version}")
    print(f"# n_tensors    = {n_tensors}")
    print(f"# n_kv         = {n_kv}")

    meta = {}
    for _ in range(n_kv):
        key = r.string()
        vtype = r.u32()
        val = r.value(vtype)
        meta[key] = val
        sval = val if isinstance(val, str) else str(val)
        if len(sval) > 600:
            sval = sval[:600] + " ...[truncated]"
        print(f"{key} = {sval}")

    print("\n# ---- assertions ----")
    ok = True

    ft = meta.get("general.file_type")
    pass_ft = (ft == 1)
    print(f"[{'PASS' if pass_ft else 'FAIL'}] general.file_type == 1 (MOSTLY_F16)  actual={ft}")
    ok &= pass_ft

    ct = meta.get("tokenizer.chat_template") or ""
    pass_ct = isinstance(ct, str) and len(ct) > 50
    print(f"[{'PASS' if pass_ct else 'FAIL'}] tokenizer.chat_template present  len={len(ct)}")
    ok &= pass_ct

    rs = meta.get("qwen2.rope.scaling.type") or meta.get("general.rope.scaling.type")
    pass_rs = (isinstance(rs, str) and rs.lower() == "yarn")
    print(f"[{'PASS' if pass_rs else 'FAIL'}] rope.scaling.type == yarn  actual={rs}")
    ok &= pass_rs

    arch = meta.get("general.architecture")
    pass_arch = (arch == "qwen2")
    print(f"[{'PASS' if pass_arch else 'FAIL'}] general.architecture == qwen2  actual={arch}")
    ok &= pass_arch

    print(f"\nRESULT: {'ALL_PASS' if ok else 'ASSERT_FAILED'}")
    return 0 if ok else 3


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1]))
    except EOFError as e:
        print(f"EOF while parsing: {e}", file=sys.stderr)
        sys.exit(4)
