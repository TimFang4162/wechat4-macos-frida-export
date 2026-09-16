"""
WeChat 4.x 数据库解密器

使用抓取到的 per-DB enc_key 解密 SQLCipher 4 加密的数据库
参数: SQLCipher 4, AES-256-CBC, HMAC-SHA512, reserve=80, page_size=4096
密钥来源: all_keys.json (由 hunt_keys.py 抓取、map_keys.py 映射)
"""
import argparse, hashlib, struct, os, sys, json
import hmac as hmac_mod
from Crypto.Cipher import AES

import functools
print = functools.partial(print, flush=True)

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80  # IV(16) + HMAC(64)
SQLITE_HDR = b'SQLite format 3\x00'

from config import load_config
from key_utils import get_key_info, strip_key_metadata
_cfg = load_config()
DB_DIR = _cfg["db_dir"]
OUT_DIR = _cfg["decrypted_dir"]
KEYS_FILE = _cfg["keys_file"]


def derive_mac_key(enc_key, salt):
    """从enc_key派生HMAC密钥"""
    mac_salt = bytes(b ^ 0x3a for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def decrypt_page(enc_key, page_data, pgno):
    """解密单个页面，输出4096字节的标准SQLite页面"""
    iv = page_data[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]

    if pgno == 1:
        encrypted = page_data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        page = bytearray(SQLITE_HDR + decrypted + b'\x00' * RESERVE_SZ)
        # 保留 reserve=80, B-tree 基于 usable_size=4016 构建
        return bytes(page)
    else:
        encrypted = page_data[:PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b'\x00' * RESERVE_SZ


def _wal_cksum(s1, s2, data, big_endian):
    fmt = ">%dI" if big_endian else "<%dI"
    words = struct.unpack(fmt % (len(data) // 4), data)
    for i in range(0, len(words), 2):
        s1 = (s1 + words[i] + s2) & 0xFFFFFFFF
        s2 = (s2 + words[i + 1] + s1) & 0xFFFFFFFF
    return s1, s2


def merge_wal(db_path, out_path, enc_key):
    """把 -wal 中未合并进主文件的页解密后写入已解密库，否则最新消息会缺失。

    按 SQLite 读端语义取舍帧：仅接受与 WAL 头同代 salt 且校验和连续成立的
    帧，止于最后一个提交帧（帧头 dbsize>0）。
    """
    wal_path = db_path + "-wal"
    if not os.path.exists(wal_path):
        return
    wal = open(wal_path, "rb").read()
    if len(wal) < 32 + 24 + PAGE_SZ:
        return
    magic, _ver, page_sz, _ckpt, salt1, salt2, hc1, hc2 = struct.unpack(">8I", wal[:32])
    if page_sz != PAGE_SZ:
        print(f"  [WARN] WAL 页大小 {page_sz} != {PAGE_SZ}，跳过 WAL 合并")
        return
    if _wal_cksum(0, 0, wal[:24], magic & 1) != (hc1, hc2):
        print("  [WARN] WAL 头校验和不符，跳过 WAL 合并")
        return

    frames, pending, commit_size = {}, [], 0
    s1, s2 = hc1, hc2  # 帧校验和从 WAL 头校验和起链
    off = 32
    while off + 24 + PAGE_SZ <= len(wal):
        pgno, dbsz, fs1, fs2, fc1, fc2 = struct.unpack(">6I", wal[off:off + 24])
        if (fs1, fs2) != (salt1, salt2):
            break  # 上一代残留帧
        page = wal[off + 24: off + 24 + PAGE_SZ]
        s1, s2 = _wal_cksum(s1, s2, wal[off:off + 8], magic & 1)
        s1, s2 = _wal_cksum(s1, s2, page, magic & 1)
        if (s1, s2) != (fc1, fc2):
            break  # 撕裂/损坏的尾部
        pending.append((pgno, page))
        if dbsz > 0:
            for p, pg in pending:
                frames[p] = pg
            commit_size = dbsz
            pending = []
        off += 24 + PAGE_SZ

    if not frames:
        return
    with open(out_path, "r+b") as f:
        for pgno, page in frames.items():
            f.seek((pgno - 1) * PAGE_SZ)
            f.write(decrypt_page(enc_key, page, pgno))
        if commit_size:
            f.truncate(commit_size * PAGE_SZ)
    print(f"  WAL 合并: {len(frames)} 页")


def decrypt_database(db_path, out_path, enc_key):
    """解密整个数据库文件"""
    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ

    if file_size % PAGE_SZ != 0:
        print(f"  [WARN] 文件大小 {file_size} 不是 {PAGE_SZ} 的倍数")
        total_pages += 1

    with open(db_path, 'rb') as fin:
        page1 = fin.read(PAGE_SZ)

    if len(page1) < PAGE_SZ:
        print(f"  [ERROR] 文件太小")
        return False

    # 提取salt并派生mac_key, 验证page 1
    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    p1_hmac_data = page1[SALT_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    p1_stored_hmac = page1[PAGE_SZ - HMAC_SZ : PAGE_SZ]
    hm = hmac_mod.new(mac_key, p1_hmac_data, hashlib.sha512)
    hm.update(struct.pack('<I', 1))
    if hm.digest() != p1_stored_hmac:
        print(f"  [ERROR] Page 1 HMAC验证失败! salt: {salt.hex()}")
        return False

    print(f"  HMAC OK, {total_pages} pages")

    # 解密所有页面
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(db_path, 'rb') as fin, open(out_path, 'wb') as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if len(page) > 0:
                    page = page + b'\x00' * (PAGE_SZ - len(page))
                else:
                    break

            decrypted = decrypt_page(enc_key, page, pgno)
            fout.write(decrypted)

            if pgno == 1:
                if decrypted[:16] != SQLITE_HDR:
                    print(f"  [WARN] 解密后header不匹配!")

            if pgno % 10000 == 0:
                print(f"  进度: {pgno}/{total_pages} ({100*pgno/total_pages:.1f}%)")

    return True


def main():
    print("=" * 60)
    print("  WeChat 4.x 数据库解密器")
    print("=" * 60)

    parser = argparse.ArgumentParser(description="解密 SQLCipher 4 数据库")
    parser.add_argument("--db-dir", default=None,
                        help="覆盖 config 中的数据库源目录（如离线快照目录）")
    args = parser.parse_args()
    db_dir = args.db_dir or DB_DIR

    # 加载密钥
    if not os.path.exists(KEYS_FILE):
        print(f"[ERROR] 密钥文件不存在: {KEYS_FILE}")
        print("请先运行 hunt_keys.py 和 map_keys.py（或直接运行 ./run.sh）")
        sys.exit(1)

    with open(KEYS_FILE) as f:
        keys = json.load(f)

    keys = strip_key_metadata(keys)
    print(f"\n加载 {len(keys)} 个数据库密钥")
    print(f"输出目录: {OUT_DIR}")
    if db_dir != DB_DIR:
        print(f"源目录: {db_dir}（快照）")
    os.makedirs(OUT_DIR, exist_ok=True)

    # 收集所有DB文件
    db_files = []
    for root, dirs, files in os.walk(db_dir):
        for f in files:
            if f.endswith('.db') and not f.endswith('-wal') and not f.endswith('-shm'):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, db_dir)
                sz = os.path.getsize(path)
                db_files.append((rel, path, sz))

    db_files.sort(key=lambda x: x[2])  # 从小到大

    print(f"找到 {len(db_files)} 个数据库文件\n")

    success = 0
    failed = 0
    total_bytes = 0

    for rel, path, sz in db_files:
        key_info = get_key_info(keys, rel)
        if not key_info:
            print(f"SKIP: {rel} (无密钥)")
            failed += 1
            continue

        enc_key = bytes.fromhex(key_info["enc_key"])
        out_path = os.path.join(OUT_DIR, rel)

        print(f"解密: {rel} ({sz/1024/1024:.1f}MB) ...", end=" ")

        ok = decrypt_database(path, out_path, enc_key)
        if ok:
            merge_wal(path, out_path, enc_key)
            # SQLite验证
            try:
                import sqlite3
                conn = sqlite3.connect(out_path)
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                conn.close()
                table_names = [t[0] for t in tables]
                print(f"  OK! 表: {', '.join(table_names[:5])}", end="")
                if len(table_names) > 5:
                    print(f" ...共{len(table_names)}个", end="")
                print()
                success += 1
                total_bytes += sz
            except Exception as e:
                print(f"  [WARN] SQLite验证失败: {e}")
                failed += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"结果: {success} 成功, {failed} 失败, 共 {len(db_files)} 个")
    print(f"解密数据量: {total_bytes/1024/1024/1024:.1f}GB")
    print(f"解密文件在: {OUT_DIR}")


if __name__ == '__main__':
    main()
