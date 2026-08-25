#!/usr/bin/env python3
"""批量导出全部会话 — TXT/CSV/JSON 三种格式。

特性：
  - zstd 压缩消息解码（微信 4.x 约一半消息为 WCDB_CT=4 压缩存储）
  - 群聊发言人解析为昵称（通过联系人库）
  - 账号主人自动识别（跨会话出现频率最高的发送者）
  - 输出 index.csv 总索引
"""
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta

import zstandard

from config import load_config

_cfg = load_config()
BASE = os.path.dirname(os.path.abspath(__file__))
DECRYPTED_DIR = _cfg["decrypted_dir"]
CONTACT_DB = os.path.join(DECRYPTED_DIR, "contact", "contact.db")
OUT_ROOT = os.path.join(BASE, "exported_all")
CHATS_DIR = os.path.join(OUT_ROOT, "chats")
CST = timezone(timedelta(hours=8))

MSG_TYPES = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频",
    47: "表情", 48: "位置", 49: "链接/文件/小程序", 50: "语音/视频通话",
    51: "系统消息", 10000: "系统提示", 10002: "撤回消息",
}
MEDIA_TYPES = {3, 34, 43, 47}
SYSTEM_TYPES = {10000, 10002, 51}

_zdec = zstandard.ZstdDecompressor()


def get_message_dbs():
    msg_dir = os.path.join(DECRYPTED_DIR, "message")
    dbs = []
    for f in sorted(os.listdir(msg_dir)):
        if re.fullmatch(r"message_\d+\.db", f):
            dbs.append(os.path.join(msg_dir, f))
    return dbs


def load_contact_map():
    conn = sqlite3.connect(CONTACT_DB)
    m = {}
    for username, nick, remark in conn.execute(
            "SELECT username, nick_name, remark FROM contact"):
        m[username] = (remark or nick or username).strip() or username
    conn.close()
    return m


def detect_my_wxid():
    """账号主人 = 在最多不同会话中作为发送者出现的 wxid。"""
    table_senders = []
    for db_path in get_message_dbs():
        conn = sqlite3.connect(db_path)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")]
            name2id = {}
            try:
                name2id = dict(conn.execute("SELECT rowid, user_name FROM Name2Id"))
            except sqlite3.Error:
                pass
            for t in tables:
                try:
                    senders = conn.execute(
                        f"SELECT DISTINCT real_sender_id FROM {t}").fetchall()
                    wxids = {name2id.get(r[0], "") for r in senders} - {""}
                    if wxids:
                        table_senders.append(wxids)
                except sqlite3.Error:
                    continue
        finally:
            conn.close()
    counter = Counter()
    for senders in table_senders:
        for w in senders:
            counter[w] += 1
    if not counter:
        return None, 0
    wxid, n = counter.most_common(1)[0]
    return wxid, n


def decode_content(raw, ct, type_name):
    if raw is None:
        return None
    if isinstance(raw, bytes):
        if ct == 4:
            try:
                raw = _zdec.stream_reader(io.BytesIO(raw)).read()
            except zstandard.ZstdError:
                return "[无法解压的消息]"
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return f"[{type_name}]"
    return raw


def sanitize(name, fallback):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")[:80]
    return name or fallback


def export_one(username, display, contact_map, my_wxid, seen_dirs):
    table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
    out_name = sanitize(display, username)
    if out_name in seen_dirs:
        out_name = sanitize(f"{out_name}_{username}", username)
    seen_dirs.add(out_name)
    out_dir = os.path.join(CHATS_DIR, out_name)
    os.makedirs(out_dir, exist_ok=True)

    messages = []
    for db_path in get_message_dbs():
        conn = sqlite3.connect(db_path)
        try:
            name2id = {}
            try:
                name2id = dict(conn.execute("SELECT rowid, user_name FROM Name2Id"))
            except sqlite3.Error:
                pass
            try:
                rows = conn.execute(f"""
                    SELECT local_type, create_time, real_sender_id,
                           message_content, WCDB_CT_message_content
                    FROM {table} ORDER BY create_time ASC
                """).fetchall()
            except sqlite3.Error:
                rows = []
        finally:
            conn.close()
        for type_id, ts, sender_id, content, ct in rows:
            sender_wxid = name2id.get(sender_id, "")
            if my_wxid and sender_wxid == my_wxid:
                sender = "我"
            elif type_id in SYSTEM_TYPES:
                sender = "系统"
            elif sender_wxid and sender_wxid in contact_map:
                sender = contact_map[sender_wxid]
            elif sender_wxid:
                sender = sender_wxid
            else:
                sender = display
            type_name = MSG_TYPES.get(type_id, f"未知({type_id})")
            text = decode_content(content, ct, type_name)
            if type_id in MEDIA_TYPES or not text:
                text = f"[{type_name}]"
            messages.append({
                "time": datetime.fromtimestamp(ts, tz=CST).strftime(
                    "%Y-%m-%d %H:%M:%S") if ts else "",
                "timestamp": ts,
                "sender": sender,
                "type": type_id,
                "type_name": type_name,
                "content": text,
            })

    messages.sort(key=lambda x: x["timestamp"] or 0)
    if not messages:
        return None

    with open(os.path.join(out_dir, "chat.txt"), "w", encoding="utf-8") as f:
        f.write(f"微信聊天记录: {display} ({username})\n")
        f.write(f"总消息数: {len(messages)}\n")
        f.write(f"时间范围: {messages[0]['time']} ~ {messages[-1]['time']}\n")
        f.write("=" * 60 + "\n\n")
        for m in messages:
            f.write(f"[{m['time']}] {m['sender']}: {m['content']}\n")

    with open(os.path.join(out_dir, "chat.csv"), "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "发送者", "类型", "内容"])
        for m in messages:
            w.writerow([m["time"], m["sender"], m["type_name"], m["content"]])

    with open(os.path.join(out_dir, "chat.json"), "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False)

    return {"目录": out_name, "消息数": len(messages),
            "开始": messages[0]["time"], "结束": messages[-1]["time"]}


def main():
    os.makedirs(CHATS_DIR, exist_ok=True)
    contact_map = load_contact_map()
    print(f"[+] 联系人表: {len(contact_map)} 条")

    my_wxid, n_tables = detect_my_wxid()
    if my_wxid:
        print(f"[+] 自动识别账号主人: {contact_map.get(my_wxid, my_wxid)} "
              f"({my_wxid})，出现于 {n_tables} 个会话")
    else:
        print("[!] 未能识别账号主人，群发送者标注可能不完全")
        my_wxid = ""

    md5_to_username = {}
    for username in contact_map:
        md5_to_username[hashlib.md5(username.encode()).hexdigest()] = username

    conv_tables = set()
    for db_path in get_message_dbs():
        conn = sqlite3.connect(db_path)
        for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"):
            conv_tables.add(t)
        conn.close()

    usernames = [md5_to_username.get(t[4:], t[4:]) for t in conv_tables]
    print(f"[+] 待导出会话: {len(usernames)} 个")

    index_rows = []
    seen_dirs = set()
    done = 0
    for username in usernames:
        display = contact_map.get(username, username)
        try:
            info = export_one(username, display, contact_map, my_wxid, seen_dirs)
        except Exception as e:
            print(f"[!] {username} 导出失败: {e}")
            continue
        done += 1
        if info:
            index_rows.append({
                "显示名": display, "用户名": username,
                "消息数": info["消息数"], "开始": info["开始"],
                "结束": info["结束"], "目录": info["目录"],
            })
        if done % 40 == 0:
            print(f"  已导出 {done}/{len(usernames)}")

    index_rows.sort(key=lambda r: -r["消息数"])
    with open(os.path.join(OUT_ROOT, "index.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["显示名", "用户名", "消息数", "开始", "结束", "目录"])
        w.writeheader()
        w.writerows(index_rows)

    total = sum(r["消息数"] for r in index_rows)
    print(f"\n[+] 导出完成: {len(index_rows)} 个会话, 共 {total} 条消息")
    print(f"[+] 输出目录: {OUT_ROOT}")


if __name__ == "__main__":
    main()
