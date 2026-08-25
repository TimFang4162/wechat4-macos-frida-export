#!/usr/bin/env python3
"""密钥映射 — 把 hunt_keys.py 抓到的候选密钥逐一验证并映射到数据库，
生成 decrypt_db.py 使用的 all_keys.json。

验证方式与解密器一致：用候选 enc_key 派生 mac key（PBKDF2-SHA512,
salt^0x3a, 2 轮），对数据库第 1 页做 HMAC-SHA512 校验。
候选来源：hunted_keys.txt（CCCryptorCreate 抓到的 raw key）
       + hunt.log 的 PBKDF pw= 字段（同为 raw enc_key）。
"""
import hashlib
import hmac
import json
import os
import re
import struct

from config import load_config

PAGE_SZ, KEY_SZ, SALT_SZ, IV_SZ, HMAC_SZ, RESERVE_SZ = 4096, 32, 16, 16, 64, 80


def page1_hmac_ok(enc_key, page1):
    salt = page1[:SALT_SZ]
    mac_key = hashlib.pbkdf2_hmac(
        "sha512", enc_key, bytes(b ^ 0x3A for b in salt), 2, dklen=KEY_SZ)
    hm = hmac.new(mac_key, page1[SALT_SZ:PAGE_SZ - RESERVE_SZ + IV_SZ],
                  hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == page1[PAGE_SZ - HMAC_SZ:PAGE_SZ]


def main():
    cfg = load_config()
    db_dir = cfg["db_dir"]
    base = os.path.dirname(os.path.abspath(__file__))
    hunted = os.path.join(base, "hunted_keys.txt")
    hunt_log = os.path.join(base, "hunt.log")
    out_file = cfg["keys_file"]

    if not os.path.exists(hunted):
        print(f"[ERROR] 未找到 {hunted}，请先运行 hunt_keys.py")
        raise SystemExit(1)

    keys = [bytes.fromhex(l.strip()) for l in open(hunted) if l.strip()]
    try:
        for m in re.finditer(r"PBKDF pw=([0-9a-f]{64}) ", open(hunt_log).read()):
            keys.append(bytes.fromhex(m.group(1)))
    except FileNotFoundError:
        pass
    keys = list(dict.fromkeys(keys))
    print(f"[+] {len(keys)} 个候选密钥")

    mapping = {}
    db_files = []
    for root, dirs, files in os.walk(db_dir):
        for f in files:
            if not f.endswith(".db"):
                continue
            path = os.path.join(root, f)
            with open(path, "rb") as fh:
                page1 = fh.read(PAGE_SZ)
            if len(page1) < PAGE_SZ:
                continue
            for k in keys:
                if page1_hmac_ok(k, page1):
                    rel = os.path.relpath(path, db_dir)
                    mapping[rel] = {"enc_key": k.hex()}
                    break
            db_files.append(os.path.relpath(path, db_dir))

    with open(out_file, "w") as f:
        json.dump(mapping, f, indent=4, ensure_ascii=False)

    print(f"[+] 匹配 {len(mapping)}/{len(db_files)} 个数据库，已写入 {out_file}")
    missing = [d for d in sorted(db_files) if d not in mapping]
    if missing:
        print(f"[!] 未覆盖 {len(missing)} 个（缺少密钥的库可在微信中打开对应功能后重跑 hunt_keys.py）:")
        for d in missing:
            print(f"    {d}")


if __name__ == "__main__":
    main()
