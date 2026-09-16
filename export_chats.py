#!/usr/bin/env python3
"""导出指定会话 — 复用 export_all 的消息解析（zstd 解压、群成员昵称、本人标注）。

用法：
    .venv/bin/python export_chats.py --username wxid_xxx --username 123@chatroom --out ./out
    .venv/bin/python export_chats.py --name "昵称或备注" --out ./out   # 模糊查找
"""
import argparse
import hashlib
import os
import sqlite3

from export_all import (DECRYPTED_DIR, detect_my_wxid, export_one,
                        get_message_dbs, load_contact_map)


def find_contacts(query):
    conn = sqlite3.connect(os.path.join(DECRYPTED_DIR, "contact", "contact.db"))
    rows = conn.execute("""
        SELECT username, nick_name, remark, alias FROM contact
        WHERE username = ? OR nick_name LIKE ? OR remark LIKE ? OR alias LIKE ?
    """, (query, f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
    conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description="导出指定微信会话")
    parser.add_argument("--username", "-u", action="append", default=[],
                        help="精确用户名（wxid / xxx@chatroom），可多次指定")
    parser.add_argument("--name", "-n", action="append", default=[],
                        help="昵称/备注/微信号模糊查找，可多次指定")
    parser.add_argument("--out", "-o", required=True, help="输出根目录")
    args = parser.parse_args()

    usernames = list(dict.fromkeys(args.username))
    for query in args.name:
        rows = find_contacts(query)
        if not rows:
            raise SystemExit(f'[!] 未找到匹配 "{query}" 的联系人')
        if len(rows) > 1:
            print(f'[!] "{query}" 匹配到 {len(rows)} 个联系人，请用 --username 精确指定:')
            for username, nick, remark, alias in rows:
                print(f"    {username}  (昵称: {nick}, 备注: {remark}, 微信号: {alias})")
            raise SystemExit(1)
        usernames.append(rows[0][0])

    if not usernames:
        parser.print_help()
        raise SystemExit(1)

    contact_map = load_contact_map()
    my_wxid, _ = detect_my_wxid()

    # 目标会话的消息表所在的库
    tables = {"Msg_" + hashlib.md5(u.encode()).hexdigest() for u in usernames}
    found = set()
    for db_path in get_message_dbs():
        conn = sqlite3.connect(db_path)
        found |= {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        } & tables
        conn.close()

    seen_dirs = set()
    for username in usernames:
        table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
        if table not in found:
            print(f"[!] {username}: 本地消息库中无此会话"
                  + ("（数据可能只在手机上，需先迁移到电脑微信）" if table in tables else ""))
            continue
        display = contact_map.get(username, username)
        info = export_one(username, display, contact_map, my_wxid, seen_dirs,
                          chats_dir=args.out)
        if info:
            print(f"[+] {display}: {info['消息数']} 条, "
                  f"{info['开始']} ~ {info['结束']} -> {args.out}/{info['目录']}")
        else:
            print(f"[!] {display}: 无消息")


if __name__ == "__main__":
    main()
