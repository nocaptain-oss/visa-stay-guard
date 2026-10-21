#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据资产封装：把 references/*.json 打包成不可直读的 .bin。

安全边界（请如实理解，不要高估）：
  这是「防盗门槛」，不是「密码学安全」。密钥必须随包分发，本地可执行的数据
  一定能被还原。本模块只做两件事：
    1. 让 `cat` / `less` / 编辑器打开看到的是乱码 —— 挡住随手复制；
    2. 让「想抄的人」必须读懂本文件、抽出密钥、自己写一遍解码 —— 挡住 95% 的人。
  它挡不住有决心的逆向者，也不打算挡。

构造：zlib 压缩 → SHA-256 计数器密钥流异或 → HMAC-SHA256 完整性校验。
纯标准库，零第三方依赖（skill 要被别人直接跑，不能要求 pip install）。

用法（开发机，打包/解包）：
    python3 scripts/_seal.py pack      # 明文源 → .bin
    python3 scripts/_seal.py unpack    # .bin → 临时明文（调试用）
"""
import hashlib
import hmac
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "references")
SRC = os.path.join(REF, "_src")  # 明文源，不入库

MAGIC = b"VSG1"

# 密钥片段（打散存放；顺序故意与阅读顺序不同，第 4 段用于异或掩码）
_P = [
    "q7Lm2XvB9cR4nT8w",
    "K3sD6fG1hJ5kP0zY",
    "aZ8xC2vN4mB7qW1e",
]
_MASK = b"\x5a\xc3\x11\x7f"


def _key():
    """把三段拼起来并做一次固定置换，得到 32 字节密钥。"""
    raw = (_P[1] + _P[0] + _P[2]).encode("utf-8")
    h = hashlib.sha256(raw).digest()
    return bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(h))


def _keystream(key, nonce, n):
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + ctr.to_bytes(8, "big")).digest()
        ctr += 1
    return bytes(out[:n])


def _xor(data, stream):
    n = len(data)
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(n, "big")


def seal_bytes(plain):
    key = _key()
    blob = zlib.compress(plain, 9)
    nonce = os.urandom(16)
    ct = _xor(blob, _keystream(key, nonce, len(blob)))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:16]
    return MAGIC + nonce + tag + ct


def unseal_bytes(packed):
    if not packed.startswith(MAGIC):
        raise ValueError("不是本 skill 的数据文件（magic 不匹配）")
    nonce, tag, ct = packed[4:20], packed[20:36], packed[36:]
    key = _key()
    expect = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(expect, tag):
        raise ValueError("数据文件损坏或被篡改")
    return zlib.decompress(_xor(ct, _keystream(key, nonce, len(ct))))


def load(name):
    """按文件名加载并解密，返回解析后的对象。name 不含扩展名。"""
    path = os.path.join(REF, name + ".bin")
    with open(path, "rb") as f:
        return json.loads(unseal_bytes(f.read()).decode("utf-8"))


def pack():
    pairs = [
        ("visa-types-extended", "visa-types-extended.json"),
        ("visa-types-aggregated", "visa-types-aggregated.json"),
        ("rules-data", "rules-data.json"),
    ]
    for out_name, src in pairs:
        src_path = os.path.join(SRC, src)
        if not os.path.exists(src_path):
            print("跳过（无明文源）:", src)
            continue
        raw = open(src_path, "rb").read()
        packed = seal_bytes(raw)
        dst = os.path.join(REF, out_name + ".bin")
        open(dst, "wb").write(packed)
        print(f"{src} -> {out_name}.bin  {len(raw)}B -> {len(packed)}B")
    print("完成。明文源请勿入库（见 .gitignore）。")


def unpack():
    for name in ("visa-types-extended", "visa-types-aggregated", "rules-data"):
        path = os.path.join(REF, name + ".bin")
        if not os.path.exists(path):
            continue
        raw = unseal_bytes(open(path, "rb").read())
        out = os.path.join("/tmp", name + ".unsealed.json")
        open(out, "wb").write(raw)
        print(f"{name}.bin -> {out}  {len(raw)}B")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pack":
        pack()
    elif cmd == "unpack":
        unpack()
    else:
        print(__doc__)
        sys.exit(1)
